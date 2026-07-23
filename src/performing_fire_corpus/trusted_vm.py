"""One-object trusted-VM acquisition, verification, and exact-key cleanup."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from jsonschema import Draft202012Validator

from performing_fire_corpus.acquisition import (
    HTTPTransport,
    USER_AGENT,
)
from performing_fire_corpus.ledger import Ledger, utc_text
from performing_fire_corpus.policy import validate_public_url
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.storage import (
    REQUIRED_SECRET_NAMES,
    R2Config,
    StorageClient,
    r2_readiness,
    write_readiness_result,
)
from performing_fire_corpus.transfer import (
    HTTPClient,
    TransferError,
    TransferPlan,
    immutable_object_key,
    plan_transfer,
    transfer_approved_asset,
)


_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_ROBOTS_MAX_BYTES = 64 * 1024
_ROBOTS_TIMEOUT_SECONDS = 10.0
_SAFE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")
_NEXT_ACTION = "Review the failed one-object gate and retry only that approved run."
_CLEANUP_NEXT_ACTION = (
    "Retry exact-key cleanup using the durable verified object receipt."
)


class TrustedVMRunError(RuntimeError):
    """A stable, sanitized operator-run failure."""

    def __init__(self, code: str, next_action: str = _NEXT_ACTION) -> None:
        self.code = code
        self.next_action = str(sanitize(next_action, environ={}))
        super().__init__(f"{self.code}: {self.next_action}")


@dataclass(frozen=True)
class TrustedVMApproval:
    transfer_plan: TransferPlan
    staging_bucket: str
    proof_starts_at: datetime
    proof_ends_at: datetime
    cleanup_deadline: datetime
    cleanup_decision: str


_APPROVAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "record_type",
        "asset_id",
        "source_id",
        "public_url",
        "rights",
        "expected_mime_type",
        "maximum_bytes",
        "proof_window",
        "staging_bucket",
        "staging_prefix",
        "cleanup_decision",
        "cleanup_deadline",
        "evidence_ref",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "record_type": {"const": "trusted_vm_acquisition_approval"},
        "asset_id": {"type": "string"},
        "source_id": {"type": "string"},
        "public_url": {"type": "string"},
        "rights": {"type": "object"},
        "expected_mime_type": {"type": "string"},
        "maximum_bytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1024 * 1024 * 1024,
        },
        "proof_window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["starts_at", "ends_at"],
            "properties": {
                "starts_at": {"type": "string"},
                "ends_at": {"type": "string"},
            },
        },
        "staging_bucket": {"type": "string", "minLength": 3, "maxLength": 63},
        "staging_prefix": {"type": "string"},
        "cleanup_decision": {"const": "delete_after_verification"},
        "cleanup_deadline": {"type": "string"},
        "evidence_ref": {"type": "string"},
    },
}


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("UTC timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    selected = datetime.now(timezone.utc) if value is None else value
    if selected.tzinfo is None:
        raise TrustedVMRunError("approval_invalid")
    return selected.astimezone(timezone.utc)


def load_trusted_vm_approval(
    path: str | Path, *, now: datetime | None = None
) -> TrustedVMApproval:
    """Load exactly one strict approval and validate every non-network gate."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        Draft202012Validator(_APPROVAL_SCHEMA).validate(value)
        starts_at = _utc_timestamp(value["proof_window"]["starts_at"])
        ends_at = _utc_timestamp(value["proof_window"]["ends_at"])
        cleanup_deadline = _utc_timestamp(value["cleanup_deadline"])
        current = _now(now)
        if not starts_at <= current <= cleanup_deadline <= ends_at:
            raise ValueError("stale or inconsistent proof window")
        bucket = value["staging_bucket"]
        if (
            not _SAFE_BUCKET.fullmatch(bucket)
            or sanitize(bucket, environ={}) != bucket
        ):
            raise ValueError("invalid bucket")
        transfer_plan = plan_transfer(
            asset_id=value["asset_id"],
            source_id=value["source_id"],
            public_url=value["public_url"],
            rights=value["rights"],
            allowed_media_types=(value["expected_mime_type"],),
            maximum_bytes=value["maximum_bytes"],
            staging_prefix=value["staging_prefix"],
            retention_decision=value["cleanup_decision"],
            evidence_ref=value["evidence_ref"],
        )
        return TrustedVMApproval(
            transfer_plan=transfer_plan,
            staging_bucket=bucket,
            proof_starts_at=starts_at,
            proof_ends_at=ends_at,
            cleanup_deadline=cleanup_deadline,
            cleanup_decision=value["cleanup_decision"],
        )
    except TrustedVMRunError:
        raise
    except Exception:
        raise TrustedVMRunError(
            "approval_invalid",
            "Provide one complete, current, reviewed one-object approval.",
        ) from None


def _endpoint_is_valid(environ: Mapping[str, str]) -> bool:
    account_id = environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    endpoint = environ.get("R2_ENDPOINT", "")
    if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
        return False
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == f"{account_id}.r2.cloudflarestorage.com"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


def _readiness(
    config: R2Config,
    *,
    environ: Mapping[str, str],
    storage_client: StorageClient,
) -> dict[str, object]:
    result = r2_readiness(
        config,
        environ=environ,
        storage_client=storage_client if _endpoint_is_valid(environ) else None,
    )
    if not _endpoint_is_valid(environ):
        checks = result["checks"]
        checks["secrets"]["R2_ENDPOINT"] = "missing"
        if not _ACCOUNT_ID.fullmatch(environ.get("CLOUDFLARE_ACCOUNT_ID", "")):
            checks["secrets"]["CLOUDFLARE_ACCOUNT_ID"] = "missing"
        result["ready"] = False
    return result


def _safe_artifact(value: Mapping[str, object], environ: Mapping[str, str]) -> dict[str, object]:
    result = dict(value)
    rendered = json.dumps(result, sort_keys=True, ensure_ascii=True)
    forbidden_fields = {
        "body",
        "cookies",
        "credential",
        "credentials",
        "headers",
        "private_material",
        "signed_url",
        "source_body",
    }

    def unsafe_field(item: object) -> bool:
        if isinstance(item, bytes):
            return True
        if isinstance(item, Mapping):
            return any(
                str(key).lower() in forbidden_fields or unsafe_field(child)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return any(unsafe_field(child) for child in item)
        return False

    secret_values: list[str] = []
    for name in REQUIRED_SECRET_NAMES:
        secret = environ.get(name, "")
        if isinstance(secret, str) and secret:
            secret_values.append(secret)
    if (
        unsafe_field(result)
        or any(secret in rendered for secret in secret_values)
        or any(marker in rendered for marker in ("/home/", "/Users/", "/tmp/", "file://"))
    ):
        raise TrustedVMRunError("unsafe_output")
    return result


def _write_artifact(
    output: Path,
    name: str,
    value: Mapping[str, object],
    environ: Mapping[str, str],
) -> None:
    write_readiness_result(output / name, _safe_artifact(value, environ))


def _manifest(
    *,
    status: str,
    outcome_code: str,
    next_action: str | None,
    recorded_at: datetime,
    receipts: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "trusted_vm_acquisition_run",
        "status": status,
        "outcome_code": outcome_code,
        "recorded_at": utc_text(recorded_at),
        "next_action": next_action,
        "receipt_types": receipts,
    }


def _block(
    output: Path,
    *,
    code: str,
    next_action: str,
    now: datetime,
    environ: Mapping[str, str],
) -> None:
    receipts = sorted(
        path.stem
        for path in output.glob("*.json")
        if path.name != "manifest.json"
    )
    _write_artifact(
        output,
        "manifest.json",
        _manifest(
            status="blocked",
            outcome_code=code,
            next_action=next_action,
            recorded_at=now,
            receipts=receipts,
        ),
        environ,
    )


def persist_blocked_run(
    output: str | Path,
    *,
    code: str,
    next_action: str,
    environ: Mapping[str, str],
    now: datetime | None = None,
) -> None:
    """Persist a sanitized CLI preflight blocker before clients are constructed."""

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    _block(
        output_path,
        code=code,
        next_action=next_action,
        now=_now(now),
        environ=environ,
    )


def _robots_url(public_url: str) -> str:
    parsed = urlsplit(validate_public_url(public_url).url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


def _robots_fact(
    plan: TransferPlan,
    *,
    transport: HTTPTransport,
    recorded_at: datetime,
) -> tuple[dict[str, object], str | None]:
    robots_url = _robots_url(plan.public_url)
    try:
        response = transport.get(
            robots_url,
            timeout_seconds=_ROBOTS_TIMEOUT_SECONDS,
            max_response_bytes=_ROBOTS_MAX_BYTES,
        )
    except Exception:
        return (
            {
                "schema_version": 1,
                "record_type": "request_fact",
                "request_kind": "robots",
                "public_url": robots_url,
                "recorded_at": utc_text(recorded_at),
                "status_class": "network_error",
                "mime_type": "unknown",
                "byte_size": 0,
                "sha256": None,
                "outcome_code": "robots_request_failed",
            },
            "robots_request_failed",
        )
    status_class = (
        f"{response.status // 100}xx"
        if isinstance(response.status, int) and 100 <= response.status <= 599
        else "unknown"
    )
    byte_size = (
        response.observed_bytes
        if isinstance(response.observed_bytes, int) and response.observed_bytes >= 0
        else len(response.body)
    )
    fact: dict[str, object] = {
        "schema_version": 1,
        "record_type": "request_fact",
        "request_kind": "robots",
        "public_url": robots_url,
        "recorded_at": utc_text(recorded_at),
        "status_class": status_class,
        "mime_type": response.mime_type or "unknown",
        "byte_size": byte_size,
        "sha256": (
            None
            if response.oversized
            else hashlib.sha256(response.body).hexdigest()
        ),
        "outcome_code": "robots_allowed",
    }
    if response.url != robots_url:
        fact["outcome_code"] = "robots_redirect_disallowed"
        return fact, "robots_redirect_disallowed"
    if response.status in (401, 403):
        fact["outcome_code"] = "robots_access_denied"
        return fact, "robots_access_denied"
    if response.status == 429:
        fact["outcome_code"] = "robots_rate_limit_exhausted"
        return fact, "robots_rate_limit_exhausted"
    if response.status != 200:
        fact["outcome_code"] = "robots_unexpected_status"
        return fact, "robots_unexpected_status"
    normalized_mime = response.mime_type.partition(";")[0].strip().lower()
    if (
        response.oversized
        or response.declared_bytes is None
        or response.declared_bytes < 0
        or response.declared_bytes > _ROBOTS_MAX_BYTES
        or byte_size > _ROBOTS_MAX_BYTES
        or normalized_mime != "text/plain"
    ):
        fact["outcome_code"] = "robots_ambiguous"
        return fact, "robots_ambiguous"
    try:
        text = response.body.decode("utf-8")
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(text.splitlines())
        allowed = bool(text.strip()) and parser.can_fetch(USER_AGENT, plan.public_url)
    except Exception:
        allowed = False
    if not allowed:
        fact["outcome_code"] = "robots_denied"
        return fact, "robots_denied"
    return fact, None


def _seed_ledger(ledger: Ledger, plan: TransferPlan) -> None:
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "source",
            "source_id": plan.source_id,
            "public_url": plan.public_url,
            "source_kind": "website",
            "metadata": {"operator": "trusted-vm-one-object"},
        },
        operation_id=f"trusted-vm-source:{plan.source_id}",
    )
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "asset",
            "asset_id": plan.asset_id,
            "source_id": plan.source_id,
            "public_url": plan.public_url,
            "media_type": next(iter(plan.allowed_media_types)),
            "metadata": {"operator": "trusted-vm-one-object"},
        },
        operation_id=f"trusted-vm-asset:{plan.asset_id}",
    )
    ledger.upsert(
        plan.rights,
        operation_id=f"trusted-vm-rights:{plan.rights['rights_id']}",
    )


def _verification_receipt(
    plan: TransferPlan,
    receipt: Mapping[str, object],
    metadata: Mapping[str, object] | None,
    recorded_at: datetime,
) -> dict[str, object]:
    expected_key = immutable_object_key(plan, str(receipt.get("sha256", "")))
    matches = (
        receipt.get("asset_id") == plan.asset_id
        and receipt.get("source_id") == plan.source_id
        and receipt.get("object_key") == expected_key
        and metadata is not None
        and metadata.get("byte_size") == receipt.get("byte_size")
        and metadata.get("media_type") == receipt.get("media_type")
        and metadata.get("sha256") == receipt.get("sha256")
    )
    if not matches:
        raise TrustedVMRunError("exact_key_verification_conflict")
    return {
        "schema_version": 1,
        "record_type": "exact_key_verification",
        "asset_id": plan.asset_id,
        "object_key": expected_key,
        "byte_size": receipt["byte_size"],
        "mime_type": receipt["media_type"],
        "sha256": receipt["sha256"],
        "recorded_at": utc_text(recorded_at),
        "outcome_code": "verified",
    }


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _completed_resume(
    approval: TrustedVMApproval, output: Path
) -> dict[str, object] | None:
    manifest = _load_json(output / "manifest.json")
    receipt = _load_json(output / "object.json")
    verification = _load_json(output / "verification.json")
    cleanup = _load_json(output / "cleanup.json")
    plan = approval.transfer_plan
    if not all((manifest, receipt, verification, cleanup)):
        return None
    try:
        expected_key = immutable_object_key(plan, str(receipt["sha256"]))
    except Exception:
        return None
    if (
        manifest.get("status") == "complete"
        and manifest.get("outcome_code") == "complete"
        and receipt.get("asset_id") == plan.asset_id
        and receipt.get("object_key") == expected_key
        and verification.get("asset_id") == plan.asset_id
        and verification.get("object_key") == expected_key
        and verification.get("outcome_code") == "verified"
        and cleanup.get("object_key") == expected_key
        and cleanup.get("state") == "absent"
    ):
        return manifest
    return None


def _resume_cleanup_receipt(
    approval: TrustedVMApproval, output: Path
) -> tuple[dict[str, object], dict[str, object]] | None:
    receipt = _load_json(output / "object.json")
    verification = _load_json(output / "verification.json")
    if not receipt or not verification:
        return None
    plan = approval.transfer_plan
    try:
        expected_key = immutable_object_key(plan, str(receipt["sha256"]))
    except Exception:
        return None
    if (
        receipt.get("asset_id") == plan.asset_id
        and receipt.get("object_key") == expected_key
        and verification.get("asset_id") == plan.asset_id
        and verification.get("object_key") == expected_key
        and verification.get("outcome_code") == "verified"
    ):
        return receipt, verification
    return None


def _cleanup(
    *,
    receipt: Mapping[str, object],
    storage_client: StorageClient,
    recorded_at: datetime,
) -> dict[str, object]:
    key = str(receipt["object_key"])
    try:
        existing = storage_client.head_object(key)
        if existing is not None and storage_client.delete_exact_object(key) is not True:
            raise RuntimeError("delete result was not verified")
        if storage_client.head_object(key) is not None:
            raise RuntimeError("exact key remains")
    except Exception:
        raise TrustedVMRunError("cleanup_failed", _CLEANUP_NEXT_ACTION) from None
    return {
        "schema_version": 1,
        "record_type": "exact_key_cleanup",
        "asset_id": receipt["asset_id"],
        "object_key": key,
        "recorded_at": utc_text(recorded_at),
        "outcome_code": "deleted" if existing is not None else "already_absent",
        "state": "absent",
    }


def acquire_one_to_r2(
    approval: TrustedVMApproval,
    *,
    config: R2Config,
    ledger_path: str | Path,
    cache_directory: str | Path,
    sanitized_output: str | Path,
    environ: Mapping[str, str],
    storage_client: StorageClient,
    robots_transport: HTTPTransport,
    asset_http_client: HTTPClient,
    now: datetime | None = None,
) -> dict[str, object]:
    """Execute one approved public request/upload/verify/delete workflow."""

    current = _now(now)
    output = Path(sanitized_output)
    output.mkdir(parents=True, exist_ok=True)
    completed = _completed_resume(approval, output)
    if completed is not None:
        return completed
    plan = approval.transfer_plan
    try:
        if not (
            approval.proof_starts_at
            <= current
            <= approval.cleanup_deadline
            <= approval.proof_ends_at
        ):
            raise TrustedVMRunError("proof_window_stale")
        if (
            config.bucket != approval.staging_bucket
            or config.staging_prefix != plan.staging_prefix
        ):
            raise TrustedVMRunError("approval_scope_mismatch")
        readiness = _readiness(
            config,
            environ=environ,
            storage_client=storage_client,
        )
        _write_artifact(output, "readiness.json", readiness, environ)
        if not _endpoint_is_valid(environ):
            raise TrustedVMRunError("r2_configuration_invalid")
        if not readiness["ready"]:
            raise TrustedVMRunError("r2_not_ready")

        resumable = _resume_cleanup_receipt(approval, output)
        if resumable is not None:
            receipt, _ = resumable
            cleanup = _cleanup(
                receipt=receipt,
                storage_client=storage_client,
                recorded_at=current,
            )
            _write_artifact(output, "cleanup.json", cleanup, environ)
        else:
            request_fact, robots_error = _robots_fact(
                plan,
                transport=robots_transport,
                recorded_at=current,
            )
            _write_artifact(output, "request-fact.json", request_fact, environ)
            if robots_error is not None:
                raise TrustedVMRunError(robots_error)
            if not (
                approval.proof_starts_at
                <= current
                <= approval.cleanup_deadline
            ):
                raise TrustedVMRunError("proof_window_stale")
            with Ledger(ledger_path) as ledger:
                _seed_ledger(ledger, plan)
                receipt = transfer_approved_asset(
                    plan,
                    http_client=asset_http_client,
                    storage_client=storage_client,
                    ledger=ledger,
                    cache_directory=cache_directory,
                )
            _write_artifact(output, "object.json", receipt, environ)
            verification = _verification_receipt(
                plan,
                receipt,
                storage_client.head_object(str(receipt["object_key"])),
                current,
            )
            _write_artifact(output, "verification.json", verification, environ)
            cleanup = _cleanup(
                receipt=receipt,
                storage_client=storage_client,
                recorded_at=current,
            )
            _write_artifact(output, "cleanup.json", cleanup, environ)

        manifest = _manifest(
            status="complete",
            outcome_code="complete",
            next_action=None,
            recorded_at=current,
            receipts=[
                "readiness",
                "request-fact",
                "object",
                "verification",
                "cleanup",
            ],
        )
        _write_artifact(output, "manifest.json", manifest, environ)
        return manifest
    except TrustedVMRunError as error:
        _block(
            output,
            code=error.code,
            next_action=error.next_action,
            now=current,
            environ=environ,
        )
        raise
    except TransferError as error:
        wrapped = TrustedVMRunError(error.code)
        _block(
            output,
            code=wrapped.code,
            next_action=wrapped.next_action,
            now=current,
            environ=environ,
        )
        raise wrapped from None
    except Exception:
        error = TrustedVMRunError("trusted_vm_run_failed")
        _block(
            output,
            code=error.code,
            next_action=error.next_action,
            now=current,
            environ=environ,
        )
        raise error from None
