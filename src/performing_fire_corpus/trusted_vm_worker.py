"""Portable supervisor for one-at-a-time trusted-VM acquisition jobs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from performing_fire_corpus.redaction import sanitize


_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(
    r"^performing-fire/v1/raw/[a-z0-9-]+/"
    r"asset_[a-z0-9][a-z0-9._-]{0,127}/[0-9a-f]{64}$"
)
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
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


class TrustedVMWorkerError(ValueError):
    """Raised when a worker/control-plane contract is unsafe."""


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
        or not isinstance(record["asset_id"], str)
        or not str(record["asset_id"]).startswith("asset_")
        or not _HASH.fullmatch(str(record["policy_snapshot_sha256"]))
        or not isinstance(record["maximum_bytes"], int)
        or record["maximum_bytes"] <= 0
        or not _OBJECT_KEY.fullmatch(str(record["target_object_key"]))
        or str(record["target_object_key"]).split("/")[-2] != record["asset_id"]
        or not isinstance(record["expected_mime_type"], str)
        or "/" not in str(record["expected_mime_type"])
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
        "next_safe_action": "Repair only the named gate, then resume this exact job.",
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


def run_trusted_vm_worker_once(
    capability: Mapping[str, object],
    *,
    control_plane: TrustedVMControlPlane,
    authority_resolver: TrustedVMAuthorityResolver,
    executor: TrustedVMAcquisitionExecutor,
    now: datetime,
) -> dict[str, object]:
    """Claim and process at most one currently approved acquisition job."""

    if now.tzinfo is None:
        raise TrustedVMWorkerError("worker time must be timezone-aware")
    current = now.astimezone(timezone.utc)
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
    heartbeat = _validate_heartbeat(
        control_plane.heartbeat(str(lease["lease_id"]), now=current),
        lease=lease,
        worker=worker,
        now=current,
    )
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
    except Exception:
        blocker = _blocker(
            job=job,
            lease=lease,
            code="bounded_executor_failed",
            gate="acquisition",
        )
        control_plane.block(blocker)
        return blocker
    control_plane.checkpoint(
        _checkpoint(
            job_id=str(job["job_id"]),
            lease_id=str(lease["lease_id"]),
            sequence=2,
            stage="exact_key_verified",
            object_key=str(receipt["object_key"]),
        )
    )
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
