"""Portable supervisor for one-at-a-time trusted-VM acquisition jobs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from performing_fire_corpus.corpus_objects import (
    bind_object_receipt,
    immutable_create_and_verify,
    raw_object_key,
    reconcile_receipt_commit,
    validate_object_receipt,
)
from performing_fire_corpus.policy import validate_public_url
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.storage import StorageClient


_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(
    r"^performing-fire/v1/raw/[a-z0-9-]+/"
    r"asset_[a-z0-9][a-z0-9._-]{0,127}/[0-9a-f]{64}$"
)
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SOURCE_ID = re.compile(
    r"^(?:source_[a-z0-9][a-z0-9._-]{0,127}|[a-z]+(?:-[a-z]+)*)$"
)
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_PATH_MARKERS = ("/home/", "/Users/", "/tmp/", "file://", "\\")
_GATES = (
    "access",
    "bytes",
    "mime",
    "retention",
    "rights",
    "robots",
    "selection",
    "storage_scope",
)
_CAPABILITY_KEYS = {
    "schema_version",
    "record_type",
    "worker_id",
    "capabilities",
    "max_concurrency",
    "maximum_asset_bytes",
    "issued_at",
    "expires_at",
}
_JOB_KEYS = {
    "schema_version",
    "record_type",
    "job_id",
    "source_id",
    "asset_id",
    "source_locator_id",
    "rights_id",
    "selection_id",
    "run_plan_id",
    "evidence_id",
    "policy_snapshot_sha256",
    "policy_expires_at",
    "expected_mime_type",
    "maximum_bytes",
    "target_object_key",
    "required_capabilities",
}
_LEASE_KEYS = {
    "schema_version",
    "record_type",
    "lease_id",
    "job_id",
    "worker_id",
    "acquired_at",
    "expires_at",
}
_AUTHORITY_KEYS = {
    "job_id",
    "policy_snapshot_sha256",
    "checked_at",
    "expires_at",
    "gates",
}
_HEARTBEAT_KEYS = {
    "schema_version",
    "record_type",
    "lease_id",
    "job_id",
    "worker_id",
    "heartbeat_at",
    "expires_at",
}
_RECEIPT_KEYS = {
    "object_key",
    "sha256",
    "byte_size",
    "object_receipt_id",
    "provenance_receipt_id",
    "downstream_job_ids",
}
_EXECUTION_CONTEXT_KEYS = {
    "public_url",
    "source_locator_id",
    "rights_id",
    "selection_id",
    "run_plan_id",
    "evidence_id",
    "policy_snapshot_sha256",
    "rights_snapshot_sha256",
    "retention_class",
    "creation_run_id",
    "evidence_ref",
    "downstream_job_ids",
    "maximum_elapsed_seconds",
    "request_timeout_seconds",
    "maximum_source_requests",
}
_SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_RUN_ID = re.compile(r"^run_[a-z0-9][a-z0-9._-]{0,127}$")
_EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_CHUNK_SIZE = 64 * 1024


class TrustedVMWorkerError(ValueError):
    """Raised when a worker/control-plane contract is unsafe."""


class TrustedVMExecutionError(RuntimeError):
    """Stable, content-free failure from the bounded acquisition boundary."""

    def __init__(self, code: str, gate: str, next_safe_action: str) -> None:
        self.code = code
        self.gate = gate
        self.next_safe_action = next_safe_action
        _safe(
            {
                "code": code,
                "gate": gate,
                "next_safe_action": next_safe_action,
            }
        )
        super().__init__(f"{code}: {next_safe_action}")


class TrustedVMControlPlane(Protocol):
    def claim_one(
        self, worker: dict[str, object], *, now: datetime
    ) -> Mapping[str, object] | None: ...

    def heartbeat(
        self, lease_id: str, *, now: datetime
    ) -> Mapping[str, object]: ...

    def checkpoint(self, value: dict[str, object]) -> None: ...

    def complete(self, value: dict[str, object]) -> None: ...

    def block(self, value: dict[str, object]) -> None: ...

    def release(self, lease_id: str, *, reason: str) -> None: ...


class TrustedVMAuthorityResolver(Protocol):
    def resolve_current_acquisition_authority(
        self, *, job: dict[str, object], now: datetime
    ) -> Mapping[str, object] | None: ...


class TrustedVMAcquisitionExecutor(Protocol):
    def acquire_one(
        self,
        *,
        job: dict[str, object],
        authority: dict[str, object],
        lease_id: str,
    ) -> Mapping[str, object]: ...


class TrustedVMExecutionContextResolver(Protocol):
    def resolve_execution_context(
        self,
        *,
        job: dict[str, object],
        authority: dict[str, object],
    ) -> Mapping[str, object]: ...


class TrustedVMBoundedHTTPClient(Protocol):
    def open(
        self,
        url: str,
        *,
        timeout_seconds: float,
    ) -> Any: ...


class TrustedVMRatePermit(Protocol):
    def allow(self, *, job_id: str, source_id: str, now: datetime) -> bool: ...


class TrustedVMReceiptAuthority(Protocol):
    def get_corpus_receipt_by_key(
        self, object_key: str
    ) -> Mapping[str, object] | None: ...

    def get_cleanup_tombstone_by_key(
        self, object_key: str
    ) -> Mapping[str, object] | None: ...

    def upsert(
        self,
        record: Mapping[str, Any],
        *,
        operation_id: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _ExecutionContext:
    public_url: str
    rights_snapshot_sha256: str
    retention_class: str
    creation_run_id: str
    evidence_ref: str
    downstream_job_ids: tuple[str, ...]
    maximum_elapsed_seconds: float
    request_timeout_seconds: float


def _time(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise TrustedVMWorkerError(f"{field} is invalid") from error
    if parsed.tzinfo is None:
        raise TrustedVMWorkerError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError) as error:
        raise TrustedVMWorkerError("worker record is not deterministic JSON") from error


def _safe(value: object) -> None:
    if isinstance(value, (bytes, bytearray)):
        raise TrustedVMWorkerError("worker records cannot contain bytes")
    if isinstance(value, str) and any(marker in value for marker in _PATH_MARKERS):
        raise TrustedVMWorkerError("worker records cannot contain machine paths")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {
                "body",
                "cookie",
                "cookies",
                "credential",
                "credentials",
                "headers",
                "prompt",
                "signed_url",
                "source_bytes",
                "machine_path",
            }:
                raise TrustedVMWorkerError("worker record contains a forbidden field")
            _safe(child)
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for child in value:
            _safe(child)
    if sanitize(value, environ={}) != value:
        raise TrustedVMWorkerError("worker record is not privacy-safe")


def _exact(value: Mapping[str, object], keys: set[str], name: str) -> dict[str, object]:
    record = dict(value)
    if set(record) != keys:
        raise TrustedVMWorkerError(f"{name} fields are not exact")
    _safe(record)
    return record


def _ids(values: object, field: str, *, pattern: re.Pattern[str] = _ID) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not pattern.fullmatch(value) for value in values)
        or values != sorted(set(values))
    ):
        raise TrustedVMWorkerError(f"{field} is not a canonical ID list")
    return list(values)


def _validate_capability(value: Mapping[str, object], now: datetime) -> dict[str, object]:
    record = _exact(value, _CAPABILITY_KEYS, "worker capability")
    capabilities = _ids(record["capabilities"], "capabilities", pattern=_CAPABILITY)
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_vm_worker_capability"
        or not isinstance(record["worker_id"], str)
        or not str(record["worker_id"]).startswith("worker_")
        or record["max_concurrency"] != 1
        or not isinstance(record["maximum_asset_bytes"], int)
        or isinstance(record["maximum_asset_bytes"], bool)
        or record["maximum_asset_bytes"] <= 0
        or not _time(record["issued_at"], "capability.issued_at")
        <= now
        < _time(record["expires_at"], "capability.expires_at")
    ):
        raise TrustedVMWorkerError("worker capability is not current and bounded")
    record["capabilities"] = capabilities
    return record


def _validate_job(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _JOB_KEYS, "acquisition job")
    capabilities = _ids(
        record["required_capabilities"],
        "required_capabilities",
        pattern=_CAPABILITY,
    )
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_vm_acquisition_job"
        or any(
            not isinstance(record[field], str) or not _ID.fullmatch(str(record[field]))
            for field in (
                "job_id",
                "source_locator_id",
                "rights_id",
                "selection_id",
                "run_plan_id",
                "evidence_id",
            )
        )
        or not isinstance(record["source_id"], str)
        or not _SOURCE_ID.fullmatch(str(record["source_id"]))
        or not isinstance(record["asset_id"], str)
        or not str(record["asset_id"]).startswith("asset_")
        or not _HASH.fullmatch(str(record["policy_snapshot_sha256"]))
        or not isinstance(record["maximum_bytes"], int)
        or isinstance(record["maximum_bytes"], bool)
        or record["maximum_bytes"] <= 0
        or not _OBJECT_KEY.fullmatch(str(record["target_object_key"]))
        or str(record["target_object_key"]).split("/")[-2] != record["asset_id"]
        or not isinstance(record["expected_mime_type"], str)
        or not _MEDIA_TYPE.fullmatch(str(record["expected_mime_type"]))
    ):
        raise TrustedVMWorkerError("acquisition job is invalid")
    record["required_capabilities"] = capabilities
    return record


def _validate_lease(
    value: Mapping[str, object],
    *,
    job: Mapping[str, object],
    worker: Mapping[str, object],
    now: datetime,
) -> dict[str, object]:
    record = _exact(value, _LEASE_KEYS, "worker lease")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_vm_worker_lease"
        or record["job_id"] != job["job_id"]
        or record["worker_id"] != worker["worker_id"]
        or not isinstance(record["lease_id"], str)
        or not str(record["lease_id"]).startswith("lease_")
        or not _time(record["acquired_at"], "lease.acquired_at")
        <= now
        < _time(record["expires_at"], "lease.expires_at")
    ):
        raise TrustedVMWorkerError("worker lease is not current and exact")
    return record


def _validate_authority(
    value: Mapping[str, object],
    *,
    job: Mapping[str, object],
    now: datetime,
) -> tuple[dict[str, object], str | None]:
    record = _exact(value, _AUTHORITY_KEYS, "acquisition authority")
    gates = record["gates"]
    if not isinstance(gates, Mapping) or set(gates) != set(_GATES):
        raise TrustedVMWorkerError("acquisition authority gates are not exact")
    if (
        record["job_id"] != job["job_id"]
        or record["policy_snapshot_sha256"] != job["policy_snapshot_sha256"]
        or _time(record["checked_at"], "authority.checked_at") > now
        or _time(record["expires_at"], "authority.expires_at") <= now
        or _time(job["policy_expires_at"], "job.policy_expires_at") <= now
    ):
        return record, "authority_not_current"
    for gate in _GATES:
        if gates[gate] is not True:
            return record, f"gate_{gate}_not_approved"
    return record, None


def _validate_heartbeat(
    value: Mapping[str, object],
    *,
    lease: Mapping[str, object],
    worker: Mapping[str, object],
    now: datetime,
) -> dict[str, object]:
    record = _exact(value, _HEARTBEAT_KEYS, "worker heartbeat")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_vm_worker_heartbeat"
        or record["lease_id"] != lease["lease_id"]
        or record["job_id"] != lease["job_id"]
        or record["worker_id"] != worker["worker_id"]
        or _time(record["heartbeat_at"], "heartbeat.heartbeat_at") != now
        or _time(record["expires_at"], "heartbeat.expires_at") <= now
    ):
        raise TrustedVMWorkerError("worker heartbeat is not current and exact")
    return record


def _checkpoint(
    *,
    job_id: str,
    lease_id: str,
    sequence: int,
    stage: str,
    object_key: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "trusted_vm_worker_checkpoint",
        "job_id": job_id,
        "lease_id": lease_id,
        "sequence": sequence,
        "stage": stage,
    }
    if object_key is not None:
        value["object_key"] = object_key
    _safe(value)
    return value


def _blocker(
    *,
    job: Mapping[str, object],
    lease: Mapping[str, object],
    code: str,
    gate: str,
    next_safe_action: str = "Repair only the named gate, then resume this exact job.",
) -> dict[str, object]:
    resume_payload = {
        "job_id": job["job_id"],
        "policy_snapshot_sha256": job["policy_snapshot_sha256"],
        "outcome_code": code,
    }
    resume_token = "resume_" + hashlib.sha256(_canonical(resume_payload)).hexdigest()[:24]
    return {
        "schema_version": 1,
        "record_type": "trusted_vm_worker_blocker",
        "status": "blocked",
        "job_id": job["job_id"],
        "lease_id": lease["lease_id"],
        "outcome_code": code,
        "affected_gate": gate,
        "required_authority_class": "corpus_operator",
        "next_safe_action": next_safe_action,
        "resume_token": resume_token,
    }


def _validate_receipt(
    value: Mapping[str, object],
    *,
    job: Mapping[str, object],
) -> dict[str, object]:
    record = _exact(value, _RECEIPT_KEYS, "acquisition receipt")
    downstream = _ids(record["downstream_job_ids"], "downstream_job_ids")
    if (
        record["object_key"] != job["target_object_key"]
        or not _HASH.fullmatch(str(record["sha256"]))
        or str(record["object_key"]).split("/")[-1] != record["sha256"]
        or not isinstance(record["byte_size"], int)
        or not 0 < record["byte_size"] <= job["maximum_bytes"]
        or any(
            not isinstance(record[field], str) or not _ID.fullmatch(str(record[field]))
            for field in ("object_receipt_id", "provenance_receipt_id")
        )
    ):
        raise TrustedVMWorkerError("acquisition receipt is not exact")
    record["downstream_job_ids"] = downstream
    return record


def _execution_failure(code: str, gate: str, next_safe_action: str) -> None:
    raise TrustedVMExecutionError(code, gate, next_safe_action)


def _normalize_media_type(value: object) -> str:
    return str(value).partition(";")[0].strip().lower()


def _clock_time(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TrustedVMWorkerError("worker clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _validate_execution_context(
    value: Mapping[str, object],
    *,
    job: Mapping[str, object],
) -> _ExecutionContext:
    record = _exact(value, _EXECUTION_CONTEXT_KEYS, "execution context")
    try:
        public_url = validate_public_url(record["public_url"]).url
    except Exception:
        raise TrustedVMWorkerError("execution context public URL is invalid") from None
    downstream = _ids(record["downstream_job_ids"], "downstream_job_ids")
    elapsed = record["maximum_elapsed_seconds"]
    request_timeout = record["request_timeout_seconds"]
    bound_fields = (
        "source_locator_id",
        "rights_id",
        "selection_id",
        "run_plan_id",
        "evidence_id",
        "policy_snapshot_sha256",
    )
    if (
        any(record[field] != job[field] for field in bound_fields)
        or not _HASH.fullmatch(str(record["rights_snapshot_sha256"]))
        or not isinstance(record["retention_class"], str)
        or not _SAFE_LABEL.fullmatch(str(record["retention_class"]))
        or not isinstance(record["creation_run_id"], str)
        or not _RUN_ID.fullmatch(str(record["creation_run_id"]))
        or not isinstance(record["evidence_ref"], str)
        or not _EVIDENCE_REF.fullmatch(str(record["evidence_ref"]))
        or not isinstance(record["maximum_source_requests"], int)
        or isinstance(record["maximum_source_requests"], bool)
        or record["maximum_source_requests"] != 1
        or not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or not 0 < float(elapsed) <= 3600
        or not isinstance(request_timeout, (int, float))
        or isinstance(request_timeout, bool)
        or not math.isfinite(float(request_timeout))
        or not 0 < float(request_timeout) <= float(elapsed)
    ):
        raise TrustedVMWorkerError("execution context is not current and bounded")
    target_key = str(job["target_object_key"])
    digest = target_key.rsplit("/", 1)[-1]
    marker = "v1/raw/"
    if marker not in target_key:
        raise TrustedVMWorkerError("execution context target namespace is invalid")
    prefix = target_key[: target_key.index(marker)]
    try:
        expected_key = raw_object_key(
            prefix,
            str(job["source_id"]),
            str(job["asset_id"]),
            digest,
        )
    except Exception:
        raise TrustedVMWorkerError("execution context target namespace is invalid") from None
    if expected_key != target_key:
        raise TrustedVMWorkerError("execution context target key is not exact")
    return _ExecutionContext(
        public_url=public_url,
        rights_snapshot_sha256=str(record["rights_snapshot_sha256"]),
        retention_class=str(record["retention_class"]),
        creation_run_id=str(record["creation_run_id"]),
        evidence_ref=str(record["evidence_ref"]),
        downstream_job_ids=tuple(downstream),
        maximum_elapsed_seconds=float(elapsed),
        request_timeout_seconds=float(request_timeout),
    )


def _head_facts(
    metadata: Mapping[str, object] | None,
    *,
    job: Mapping[str, object],
) -> tuple[int, str, str] | None:
    if metadata is None:
        return None
    raw_byte_size = metadata.get("byte_size")
    if not isinstance(raw_byte_size, int) or isinstance(raw_byte_size, bool):
        _execution_failure(
            "immutable_object_conflict",
            "storage_scope",
            "Hold this exact object key for operator conflict review.",
        )
    byte_size = raw_byte_size
    media_type = _normalize_media_type(metadata.get("media_type"))
    sha256 = str(metadata.get("sha256", ""))
    expected_sha256 = str(job["target_object_key"]).rsplit("/", 1)[-1]
    if (
        not 0 < byte_size <= int(job["maximum_bytes"])
        or media_type != job["expected_mime_type"]
        or sha256 != expected_sha256
    ):
        _execution_failure(
            "immutable_object_conflict",
            "storage_scope",
            "Hold this exact object key for operator conflict review.",
        )
    return byte_size, media_type, sha256


def _receipt_facts(
    *,
    job: Mapping[str, object],
    context: _ExecutionContext,
    byte_size: int,
    media_type: str,
    sha256: str,
    create_disposition: str,
) -> dict[str, object]:
    return bind_object_receipt(
        {
            "schema_version": 1,
            "record_type": "object_receipt",
            "object_kind": "raw",
            "source_id": job["source_id"],
            "asset_id": job["asset_id"],
            "object_key": job["target_object_key"],
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
            "rights_snapshot_sha256": context.rights_snapshot_sha256,
            "retention_class": context.retention_class,
            "creation_run_id": context.creation_run_id,
            "retrieval_decision": "approved",
            "evidence_ref": context.evidence_ref,
            "verification_state": "verified",
            "create_disposition": create_disposition,
        }
    )


def _provenance_id(
    *,
    job: Mapping[str, object],
    object_receipt: Mapping[str, object],
) -> str:
    payload = {
        "job_id": job["job_id"],
        "source_id": job["source_id"],
        "asset_id": job["asset_id"],
        "rights_id": job["rights_id"],
        "selection_id": job["selection_id"],
        "run_plan_id": job["run_plan_id"],
        "evidence_id": job["evidence_id"],
        "object_receipt_id": object_receipt["receipt_id"],
    }
    return "provenance_" + hashlib.sha256(_canonical(payload)).hexdigest()


class BoundedTrustedVMAcquisitionExecutor:
    """Resolve, fetch, verify, and durably receipt one exact raw object.

    The stable queue job intentionally contains no locator or machine path.
    The resolver supplies the public locator only in trusted process memory.
    A matching pre-existing exact object is recovered by HEAD before any source
    request, so restart after an ambiguous create does not reacquire the source.
    """

    def __init__(
        self,
        *,
        context_resolver: TrustedVMExecutionContextResolver,
        http_client: TrustedVMBoundedHTTPClient,
        storage_client: StorageClient,
        receipt_authority: TrustedVMReceiptAuthority,
        cache_directory: str | Path,
        clock: Callable[[], datetime],
        rate_permit: TrustedVMRatePermit,
    ) -> None:
        self._context_resolver = context_resolver
        self._http_client = http_client
        self._storage = storage_client
        self._receipts = receipt_authority
        self._cache_directory = Path(cache_directory)
        self._clock = clock
        self._rate_permit = rate_permit

    def _elapsed(
        self,
        *,
        started: datetime,
        context: _ExecutionContext,
    ) -> datetime:
        current = _clock_time(self._clock)
        elapsed = (current - started).total_seconds()
        if elapsed < 0:
            raise TrustedVMWorkerError("worker clock moved backwards")
        if elapsed > context.maximum_elapsed_seconds:
            _execution_failure(
                "elapsed_budget_exhausted",
                "acquisition",
                "Resume this exact job only with a fresh bounded execution window.",
            )
        return current

    def _persist(
        self,
        *,
        job: Mapping[str, object],
        context: _ExecutionContext,
        receipt: Mapping[str, object],
    ) -> dict[str, object]:
        checked = validate_object_receipt(receipt)
        try:
            durable = self._receipts.upsert(
                checked,
                operation_id=(
                    f"trusted-vm-object:{job['job_id']}:{checked['receipt_id']}"
                ),
            )
        except Exception:
            _execution_failure(
                "object_receipt_commit_failed",
                "storage_scope",
                "Reconcile this exact object and receipt before resuming the job.",
            )
        if durable != checked:
            _execution_failure(
                "object_receipt_conflict",
                "storage_scope",
                "Hold this exact object and receipt for operator review.",
            )
        value = {
            "object_key": checked["object_key"],
            "sha256": checked["sha256"],
            "byte_size": checked["byte_size"],
            "object_receipt_id": checked["receipt_id"],
            "provenance_receipt_id": _provenance_id(
                job=job,
                object_receipt=checked,
            ),
            "downstream_job_ids": list(context.downstream_job_ids),
        }
        return _validate_receipt(value, job=job)

    def _recover_existing(
        self,
        *,
        job: Mapping[str, object],
        context: _ExecutionContext,
        metadata: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            tombstone = self._receipts.get_cleanup_tombstone_by_key(
                str(job["target_object_key"])
            )
        except Exception:
            _execution_failure(
                "retention_authority_unavailable",
                "retention",
                "Restore current exact-key retention authority before resuming.",
            )
        if tombstone is not None:
            _execution_failure(
                "object_tombstoned",
                "retention",
                "Keep this exact object held and review authority before reacquisition.",
            )
        facts = _head_facts(metadata, job=job)
        if facts is None:
            raise TrustedVMWorkerError("existing object recovery lost exact state")
        byte_size, media_type, sha256 = facts
        durable = self._receipts.get_corpus_receipt_by_key(
            str(job["target_object_key"])
        )
        if durable is None:
            receipt = _receipt_facts(
                job=job,
                context=context,
                byte_size=byte_size,
                media_type=media_type,
                sha256=sha256,
                create_disposition="reused",
            )
        else:
            receipt = validate_object_receipt(durable)
            expected = {
                "object_kind": "raw",
                "source_id": job["source_id"],
                "asset_id": job["asset_id"],
                "object_key": job["target_object_key"],
                "byte_size": byte_size,
                "media_type": media_type,
                "sha256": sha256,
                "rights_snapshot_sha256": context.rights_snapshot_sha256,
                "retention_class": context.retention_class,
                "creation_run_id": context.creation_run_id,
                "retrieval_decision": "approved",
                "evidence_ref": context.evidence_ref,
                "verification_state": "verified",
            }
            if any(receipt.get(key) != value for key, value in expected.items()):
                _execution_failure(
                    "object_receipt_conflict",
                    "storage_scope",
                    "Hold this exact object and receipt for operator review.",
                )
        try:
            reconcile_receipt_commit(
                self._storage,
                expected_receipt=receipt,
                receipt_artifact=receipt if durable is not None else None,
                ledger_record=durable,
            )
        except Exception:
            _execution_failure(
                "exact_key_recovery_failed",
                "storage_scope",
                "Reconcile this exact object key without repeating the source request.",
            )
        return self._persist(job=job, context=context, receipt=receipt)

    def acquire_one(
        self,
        *,
        job: dict[str, object],
        authority: dict[str, object],
        lease_id: str,
    ) -> Mapping[str, object]:
        del lease_id
        checked_job = _validate_job(job)
        started = _clock_time(self._clock)
        _, authority_error = _validate_authority(
            authority,
            job=checked_job,
            now=started,
        )
        if authority_error is not None:
            raise TrustedVMWorkerError(
                "executor received acquisition authority that is not current"
            )
        try:
            raw_context = self._context_resolver.resolve_execution_context(
                job=checked_job,
                authority=authority,
            )
        except TrustedVMWorkerError:
            raise
        except Exception:
            _execution_failure(
                "execution_context_unavailable",
                "policy_snapshot",
                "Restore the reviewed stable-ID resolver, then resume this exact job.",
            )
        if not isinstance(raw_context, Mapping):
            raise TrustedVMWorkerError("execution context resolver returned invalid data")
        context = _validate_execution_context(raw_context, job=checked_job)
        self._elapsed(started=started, context=context)

        try:
            existing = self._storage.head_object(
                str(checked_job["target_object_key"])
            )
        except Exception:
            _execution_failure(
                "exact_head_failed",
                "storage_scope",
                "Retry only the exact-key HEAD before any source request.",
            )
        if existing is not None:
            return self._recover_existing(
                job=checked_job,
                context=context,
                metadata=existing,
            )

        current = self._elapsed(started=started, context=context)
        try:
            permitted = self._rate_permit.allow(
                job_id=str(checked_job["job_id"]),
                source_id=str(checked_job["source_id"]),
                now=current,
            )
        except Exception:
            permitted = False
        if permitted is not True:
            _execution_failure(
                "source_rate_not_ready",
                "access",
                "Keep this job queued until its bounded source rate permit is ready.",
            )

        temporary_path: Path | None = None
        try:
            try:
                response = self._http_client.open(
                    context.public_url,
                    timeout_seconds=context.request_timeout_seconds,
                )
            except Exception:
                _execution_failure(
                    "source_request_failed",
                    "access",
                    "Retry this exact bounded source request after the access gate recovers.",
                )
            status = getattr(response, "status", 200)
            if status in {401, 403}:
                _execution_failure(
                    "source_access_denied",
                    "access",
                    "Keep this source blocked; do not bypass authentication or denial.",
                )
            if status == 429:
                _execution_failure(
                    "source_rate_limited",
                    "access",
                    "Keep this job queued until the reviewed retry window.",
                )
            if status != 200:
                _execution_failure(
                    "source_request_failed",
                    "access",
                    "Review this exact public response status before retrying.",
                )
            try:
                final_url = validate_public_url(
                    getattr(response, "final_url", context.public_url)
                ).url
            except Exception:
                _execution_failure(
                    "source_url_mismatch",
                    "access",
                    "Keep this job blocked on the reviewed exact public locator.",
                )
            if final_url != context.public_url:
                _execution_failure(
                    "source_url_mismatch",
                    "access",
                    "Keep this job blocked on the reviewed exact public locator.",
                )
            media_type = _normalize_media_type(response.media_type)
            if media_type != checked_job["expected_mime_type"]:
                _execution_failure(
                    "source_mime_mismatch",
                    "mime",
                    "Review the exact source MIME before resuming this job.",
                )
            declared = response.content_length
            if declared is not None and (
                not isinstance(declared, int)
                or isinstance(declared, bool)
                or declared <= 0
                or declared > checked_job["maximum_bytes"]
            ):
                _execution_failure(
                    "source_size_exceeded",
                    "bytes",
                    "Keep the object blocked unless a new byte bound is reviewed.",
                )

            self._cache_directory.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".trusted-vm-",
                suffix=".part",
                dir=self._cache_directory,
                delete=False,
            )
            temporary_path = Path(handle.name)
            hasher = hashlib.sha256()
            byte_size = 0
            try:
                with handle:
                    for chunk in response.iter_bytes(_CHUNK_SIZE):
                        if not isinstance(chunk, bytes):
                            _execution_failure(
                                "source_stream_invalid",
                                "access",
                                "Keep this job blocked until the source stream is valid.",
                            )
                        byte_size += len(chunk)
                        if byte_size > checked_job["maximum_bytes"]:
                            _execution_failure(
                                "source_size_exceeded",
                                "bytes",
                                "Keep the object blocked unless a new byte bound is reviewed.",
                            )
                        hasher.update(chunk)
                        handle.write(chunk)
                        self._elapsed(started=started, context=context)
            except TrustedVMExecutionError:
                raise
            except Exception:
                _execution_failure(
                    "source_stream_failed",
                    "access",
                    "Retry this exact bounded source request from its durable job state.",
                )
            if byte_size <= 0 or (
                declared is not None and byte_size != declared
            ):
                _execution_failure(
                    "source_size_mismatch",
                    "bytes",
                    "Review the exact source size before resuming this job.",
                )
            sha256 = hasher.hexdigest()
            expected_sha256 = str(checked_job["target_object_key"]).rsplit("/", 1)[-1]
            if sha256 != expected_sha256:
                _execution_failure(
                    "source_hash_mismatch",
                    "selection",
                    "Requalify this changed source asset before any new object create.",
                )
            create_time = self._elapsed(started=started, context=context)
            _, create_authority_error = _validate_authority(
                authority,
                job=checked_job,
                now=create_time,
            )
            if create_authority_error is not None:
                _execution_failure(
                    "authority_expired_before_create",
                    "policy_snapshot",
                    "Refresh every acquisition authority gate before any object create.",
                )
            try:
                receipt = immutable_create_and_verify(
                    self._storage,
                    key=str(checked_job["target_object_key"]),
                    path=temporary_path,
                    object_kind="raw",
                    source_id=str(checked_job["source_id"]),
                    asset_id=str(checked_job["asset_id"]),
                    byte_size=byte_size,
                    media_type=media_type,
                    sha256=sha256,
                    rights_snapshot_sha256=context.rights_snapshot_sha256,
                    retention_class=context.retention_class,
                    creation_run_id=context.creation_run_id,
                    retrieval_decision="approved",
                    evidence_ref=context.evidence_ref,
                    receipt_authority=self._receipts,
                )
            except TrustedVMExecutionError:
                raise
            except Exception:
                _execution_failure(
                    "immutable_create_or_verify_failed",
                    "storage_scope",
                    "Recover by exact-key HEAD; never overwrite or broadly list storage.",
                )
            self._elapsed(started=started, context=context)
            return self._persist(
                job=checked_job,
                context=context,
                receipt=receipt,
            )
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


def run_trusted_vm_worker_once(
    capability: Mapping[str, object],
    *,
    control_plane: TrustedVMControlPlane,
    authority_resolver: TrustedVMAuthorityResolver,
    executor: TrustedVMAcquisitionExecutor,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Claim and process at most one currently approved acquisition job."""

    if now.tzinfo is None:
        raise TrustedVMWorkerError("worker time must be timezone-aware")
    current = now.astimezone(timezone.utc)
    current_time = (lambda: current) if clock is None else clock
    worker = _validate_capability(capability, current)
    claimed = control_plane.claim_one(worker, now=current)
    if claimed is None:
        return {"status": "idle"}
    if not isinstance(claimed, Mapping) or set(claimed) != {"job", "lease"}:
        raise TrustedVMWorkerError("control-plane claim is invalid")
    if not isinstance(claimed["job"], Mapping) or not isinstance(
        claimed["lease"], Mapping
    ):
        raise TrustedVMWorkerError("control-plane claim records are invalid")
    job = _validate_job(claimed["job"])
    lease = _validate_lease(
        claimed["lease"],
        job=job,
        worker=worker,
        now=current,
    )
    if (
        not set(job["required_capabilities"]) <= set(worker["capabilities"])
        or job["maximum_bytes"] > worker["maximum_asset_bytes"]
    ):
        blocker = _blocker(
            job=job,
            lease=lease,
            code="worker_capability_mismatch",
            gate="worker_capability",
        )
        control_plane.block(blocker)
        return blocker
    try:
        authority_value = authority_resolver.resolve_current_acquisition_authority(
            job=job,
            now=current,
        )
    except Exception:
        authority_value = None
    if not isinstance(authority_value, Mapping):
        blocker = _blocker(
            job=job,
            lease=lease,
            code="authority_unavailable",
            gate="policy_snapshot",
        )
        control_plane.block(blocker)
        return blocker
    authority, authority_error = _validate_authority(
        authority_value,
        job=job,
        now=current,
    )
    if authority_error is not None:
        blocker = _blocker(
            job=job,
            lease=lease,
            code=authority_error,
            gate=authority_error.removeprefix("gate_").removesuffix(
                "_not_approved"
            ),
        )
        control_plane.block(blocker)
        return blocker
    control_plane.checkpoint(
        _checkpoint(
            job_id=str(job["job_id"]),
            lease_id=str(lease["lease_id"]),
            sequence=1,
            stage="authority_confirmed",
        )
    )
    heartbeat_time = _clock_time(current_time)
    try:
        refreshed_authority_value = (
            authority_resolver.resolve_current_acquisition_authority(
                job=job,
                now=heartbeat_time,
            )
        )
    except Exception:
        refreshed_authority_value = None
    if not isinstance(refreshed_authority_value, Mapping):
        blocker = _blocker(
            job=job,
            lease=lease,
            code="authority_unavailable_before_acquisition",
            gate="policy_snapshot",
        )
        control_plane.block(blocker)
        return blocker
    authority, current_authority_error = _validate_authority(
        refreshed_authority_value,
        job=job,
        now=heartbeat_time,
    )
    if current_authority_error is not None:
        blocker = _blocker(
            job=job,
            lease=lease,
            code=current_authority_error,
            gate=current_authority_error.removeprefix("gate_").removesuffix(
                "_not_approved"
            ),
        )
        control_plane.block(blocker)
        return blocker
    try:
        heartbeat = _validate_heartbeat(
            control_plane.heartbeat(
                str(lease["lease_id"]),
                now=heartbeat_time,
            ),
            lease=lease,
            worker=worker,
            now=heartbeat_time,
        )
    except Exception:
        blocker = _blocker(
            job=job,
            lease=lease,
            code="lease_lost_before_acquisition",
            gate="lease",
        )
        control_plane.block(blocker)
        return blocker
    _safe(heartbeat)
    try:
        receipt_value = executor.acquire_one(
            job=job,
            authority=authority,
            lease_id=str(lease["lease_id"]),
        )
        receipt = _validate_receipt(receipt_value, job=job)
    except TrustedVMWorkerError:
        control_plane.release(
            str(lease["lease_id"]),
            reason="executor_contract_failure",
        )
        raise
    except TrustedVMExecutionError as error:
        blocker = _blocker(
            job=job,
            lease=lease,
            code=error.code,
            gate=error.gate,
            next_safe_action=error.next_safe_action,
        )
        control_plane.block(blocker)
        return blocker
    except Exception:
        blocker = _blocker(
            job=job,
            lease=lease,
            code="bounded_executor_failed",
            gate="acquisition",
        )
        control_plane.block(blocker)
        return blocker
    except BaseException:
        control_plane.release(
            str(lease["lease_id"]),
            reason="worker_interrupted",
        )
        raise
    control_plane.checkpoint(
        _checkpoint(
            job_id=str(job["job_id"]),
            lease_id=str(lease["lease_id"]),
            sequence=2,
            stage="exact_key_verified",
            object_key=str(receipt["object_key"]),
        )
    )
    final_time = _clock_time(current_time)
    try:
        final_heartbeat = _validate_heartbeat(
            control_plane.heartbeat(
                str(lease["lease_id"]),
                now=final_time,
            ),
            lease=lease,
            worker=worker,
            now=final_time,
        )
    except Exception:
        blocker = _blocker(
            job=job,
            lease=lease,
            code="lease_lost_after_verification",
            gate="lease",
        )
        control_plane.block(blocker)
        return blocker
    _safe(final_heartbeat)
    result = {
        "schema_version": 1,
        "record_type": "trusted_vm_worker_result",
        "status": "completed",
        "job_id": job["job_id"],
        "lease_id": lease["lease_id"],
        **receipt,
    }
    _safe(result)
    control_plane.complete(result)
    return result
