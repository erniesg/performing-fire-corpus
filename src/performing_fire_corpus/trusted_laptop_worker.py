"""Portable, outbound-only trusted-laptop transformation worker.

The module deliberately contains no concrete pairing transport, cloud client,
credential lookup, or media tool.  Those boundaries are injected.  Queue and
durable records contain stable identifiers and exact object keys only; corpus
bytes exist only in a marker-bound disposable cache owned by one job.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import multiprocessing
import os
import re
import resource
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from performing_fire_corpus.corpus_objects import (
    CorpusObjectError,
    build_derivation_manifest,
    derived_object_key,
    immutable_create_and_verify,
    manifest_object_key,
    validate_object_receipt,
)
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.storage import dedicated_staging_prefix


_UTC = timezone.utc
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SOURCE_ID = re.compile(
    r"^(?:source_[a-z0-9][a-z0-9._-]{0,127}|[a-z]+(?:-[a-z]+)*)$"
)
_ASSET_ID = re.compile(r"^asset_[a-z0-9][a-z0-9._-]{0,127}$")
_TRANSFORMATION_ID = re.compile(
    r"^transform_[a-z0-9][a-z0-9._-]{0,127}$"
)
_TOOL_ID = re.compile(r"^tool_[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][a-z0-9.-]+)?$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_RUN_ID = re.compile(r"^run_[a-z0-9][a-z0-9._-]{0,127}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_OBJECT_KEY = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*\\)"
    r"[a-z0-9][a-z0-9._/-]{0,511}$"
)
_CACHE_DIR = re.compile(r"^\.performing-fire-laptop-cache-[0-9a-f]{8,64}$")
_CACHE_ID = re.compile(r"^cache_[0-9a-f]{8,64}$")
_PARAMETER_LABEL = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}$")
_PATH_OR_URL = re.compile(
    r"(?:file://|https?://|s3://|r2://|/(?:Users|home|tmp|var)/|"
    r"[A-Za-z]:[\\/]|\\)"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_CHUNK_SIZE = 64 * 1024
_MANIFEST_MAX_BYTES = 64 * 1024
_GATES = (
    "capability",
    "consent",
    "deletion",
    "derivative_rights",
    "privacy",
    "retention",
)
_PARAMETER_KEYS = frozenset(
    {
        "confidence_threshold_milli",
        "language",
        "model_id",
        "output_format",
        "profile_id",
        "sample_rate_hz",
        "segment_seconds",
        "task",
        "temperature_milli",
    }
)
_CAPABILITY_KEYS = {
    "schema_version",
    "record_type",
    "pairing_protocol",
    "capabilities",
    "max_concurrency",
    "maximum_input_bytes",
    "maximum_output_bytes",
    "maximum_cpu_seconds",
    "maximum_memory_bytes",
    "maximum_disk_bytes",
    "maximum_elapsed_seconds",
    "issued_at",
    "expires_at",
}
_PAIRING_KEYS = {
    "schema_version",
    "record_type",
    "pairing_id",
    "transport",
    "direction",
    "paired_at",
    "expires_at",
}
_JOB_KEYS = {
    "schema_version",
    "record_type",
    "job_id",
    "source_id",
    "asset_id",
    "rights_id",
    "transformation_id",
    "input_receipt_id",
    "input_object_key",
    "input_sha256",
    "input_byte_size",
    "input_media_type",
    "input_rights_snapshot_sha256",
    "derivation_authority_sha256",
    "privacy_snapshot_sha256",
    "retention_class",
    "retrieval_decision",
    "required_capability",
    "tool_id",
    "tool_version",
    "contract_version",
    "parameters",
    "parameters_sha256",
    "output_media_type",
    "redaction_state",
    "namespace_prefix",
    "creation_run_id",
    "evidence_ref",
    "attempt",
    "maximum_attempts",
    "maximum_input_bytes",
    "maximum_output_bytes",
    "maximum_cpu_seconds",
    "maximum_memory_bytes",
    "maximum_disk_bytes",
    "maximum_elapsed_seconds",
}
_LEASE_KEYS = {
    "schema_version",
    "record_type",
    "lease_id",
    "pairing_id",
    "job_id",
    "acquired_at",
    "expires_at",
}
_HEARTBEAT_KEYS = {
    "schema_version",
    "record_type",
    "lease_id",
    "pairing_id",
    "job_id",
    "heartbeat_at",
    "expires_at",
}
_CHECKPOINT_KEYS = {
    "schema_version",
    "record_type",
    "checkpoint_id",
    "pairing_id",
    "lease_id",
    "job_id",
    "job_contract_sha256",
    "stage",
    "input_object_key",
    "output_object_key",
    "output_receipt_id",
    "manifest_object_key",
    "manifest_receipt_id",
    "output_sha256",
    "output_byte_size",
    "cpu_seconds",
    "peak_memory_bytes",
    "working_disk_bytes",
    "elapsed_seconds",
    "resume_state_sha256",
    "recorded_at",
}
_RESULT_KEYS = {
    "schema_version",
    "record_type",
    "result_id",
    "job_id",
    "job_contract_sha256",
    "source_id",
    "asset_id",
    "transformation_id",
    "input_receipt_id",
    "output_receipt_id",
    "manifest_receipt_id",
    "output_object_key",
    "manifest_object_key",
    "output_sha256",
    "output_byte_size",
    "rights_snapshot_sha256",
    "privacy_snapshot_sha256",
    "retention_class",
    "retrieval_decision",
    "redaction_state",
    "cpu_seconds",
    "peak_memory_bytes",
    "working_disk_bytes",
    "elapsed_seconds",
    "evidence_ref",
    "completed_at",
}
_BLOCKER_KEYS = {
    "schema_version",
    "record_type",
    "blocker_id",
    "pairing_id",
    "lease_id",
    "job_id",
    "code",
    "gate",
    "required_authority_class",
    "next_safe_action",
    "resume_state_sha256",
    "recorded_at",
}
_AUTHORITY_KEYS = {
    "job_id",
    "input_rights_snapshot_sha256",
    "derivation_authority_sha256",
    "privacy_snapshot_sha256",
    "retention_class",
    "checked_at",
    "expires_at",
    "gates",
}
_HUMAN_CODES = frozenset(
    {
        "capability_not_approved",
        "consent_not_approved",
        "deletion_not_approved",
        "derivative_rights_not_approved",
        "durable_receipt_conflict",
        "durable_receipt_object_absent",
        "immutable_object_conflict",
        "input_object_tombstoned",
        "input_object_tombstoned_before_create",
        "manifest_object_tombstoned",
        "output_object_tombstoned",
        "privacy_not_approved",
        "retention_not_approved",
    }
)
_STAGES = frozenset(
    {
        "claimed",
        "input_verified",
        "transform_started",
        "transform_verified",
        "output_verified",
        "manifest_verified",
    }
)


class TrustedLaptopWorkerError(ValueError):
    """Raised when a portable pairing, queue, or durable record is unsafe."""


class TrustedLaptopExecutionError(RuntimeError):
    """Stable content-free failure at the trusted-laptop execution boundary."""

    def __init__(
        self,
        code: str,
        gate: str,
        next_safe_action: str,
        *,
        required_authority_class: str | None = None,
    ) -> None:
        self.code = _safe_label(code, "execution code")
        self.gate = _safe_label(gate, "execution gate")
        self.next_safe_action = _safe_text(
            next_safe_action, "next safe action"
        )
        self.required_authority_class = (
            required_authority_class
            if required_authority_class is not None
            else ("corpus_operator" if code in _HUMAN_CODES else "none")
        )
        if self.required_authority_class not in {"none", "corpus_operator"}:
            raise TrustedLaptopWorkerError("invalid authority class")
        super().__init__(f"{self.code}: {self.next_safe_action}")


class TrustedLaptopControlPlane(Protocol):
    def pair_outbound(
        self,
        capability: dict[str, object],
        *,
        now: datetime,
    ) -> Mapping[str, object]: ...

    def claim_one(
        self,
        pairing: dict[str, object],
        capability: dict[str, object],
        *,
        now: datetime,
    ) -> Mapping[str, object] | None: ...

    def get_completed_result(
        self, job_id: str
    ) -> Mapping[str, object] | None: ...

    def get_latest_checkpoint(
        self, job_id: str
    ) -> Mapping[str, object] | None: ...

    def heartbeat(
        self,
        lease_id: str,
        pairing_id: str,
        *,
        now: datetime,
    ) -> Mapping[str, object]: ...

    def checkpoint(self, value: dict[str, object]) -> None: ...

    def complete(self, value: dict[str, object]) -> None: ...

    def block(self, value: dict[str, object]) -> None: ...

    def release(
        self,
        lease_id: str,
        pairing_id: str,
        *,
        reason: str,
    ) -> None: ...


class TrustedLaptopAuthorityResolver(Protocol):
    def resolve_current_derivation_authority(
        self,
        *,
        job: dict[str, object],
        now: datetime,
    ) -> Mapping[str, object] | None: ...


class TrustedLaptopObjectStore(Protocol):
    def head_object(self, key: str) -> Mapping[str, object] | None: ...

    def download_exact_to_file(
        self,
        key: str,
        path: Path,
        *,
        maximum_bytes: int,
    ) -> None: ...

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool: ...


class TrustedLaptopReceiptAuthority(Protocol):
    def get_corpus_receipt(
        self, receipt_id: str
    ) -> Mapping[str, object] | None: ...

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


class TrustedLaptopTransformer(Protocol):
    def transform(
        self,
        *,
        input_path: Path,
        output_path: Path,
        job: dict[str, object],
    ) -> Mapping[str, object]: ...


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise TrustedLaptopWorkerError(
            "records must contain deterministic JSON values"
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _job_contract_sha256(job: Mapping[str, object]) -> str:
    """Bind immutable job authority and bounds, excluding retry ordinal."""

    return _digest(
        {
            key: child
            for key, child in job.items()
            if key != "attempt"
        }
    )


def _exact(
    value: Mapping[str, object],
    keys: set[str],
    label: str,
) -> dict[str, object]:
    if set(value) != keys:
        raise TrustedLaptopWorkerError(
            f"{label} must use the strict version-1 field set"
        )
    return dict(value)


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TrustedLaptopWorkerError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustedLaptopWorkerError(
            f"{label} must be a UTC timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise TrustedLaptopWorkerError(f"{label} must be timezone-aware")
    return parsed.astimezone(_UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise TrustedLaptopWorkerError("worker clock must be timezone-aware")
    return value.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _safe_label(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_LABEL.fullmatch(value):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return value


def _safe_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or "\r" in value
        or "\n" in value
        or _PATH_OR_URL.search(value)
        or _EMAIL.search(value)
        or sanitize(value, environ={}) != value
    ):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return value


def _require_pattern(
    value: object,
    pattern: re.Pattern[str],
    label: str,
) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return value


def _positive_int(value: object, label: str, *, allow_zero: bool = False) -> int:
    lower = 0 if allow_zero else 1
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < lower
    ):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return value


def _finite_number(value: object, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return float(value)


def _media_type(value: object, label: str) -> str:
    if not isinstance(value, str) or not _MEDIA_TYPE.fullmatch(value):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    return value


def _string_list(
    value: object,
    *,
    pattern: re.Pattern[str],
    label: str,
) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise TrustedLaptopWorkerError(f"invalid {label}")
    result = [
        _require_pattern(child, pattern, label)
        for child in value
    ]
    if len(result) != len(set(result)):
        raise TrustedLaptopWorkerError(f"{label} must be unique")
    return result


def _validate_parameters(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) - _PARAMETER_KEYS:
        raise TrustedLaptopWorkerError(
            "parameters must use only reviewed bounded fields"
        )
    parameters = dict(value)
    for key, child in parameters.items():
        if key == "language":
            _require_pattern(child, _LANGUAGE, "language")
        elif key in {"model_id", "output_format", "profile_id", "task"}:
            _require_pattern(child, _PARAMETER_LABEL, key)
        elif key == "confidence_threshold_milli":
            number = _positive_int(child, key, allow_zero=True)
            if number > 1000:
                raise TrustedLaptopWorkerError(f"invalid {key}")
        elif key == "temperature_milli":
            number = _positive_int(child, key, allow_zero=True)
            if number > 2000:
                raise TrustedLaptopWorkerError(f"invalid {key}")
        elif key == "sample_rate_hz":
            number = _positive_int(child, key)
            if not 8000 <= number <= 192000:
                raise TrustedLaptopWorkerError(f"invalid {key}")
        elif key == "segment_seconds":
            number = _positive_int(child, key)
            if number > 86400:
                raise TrustedLaptopWorkerError(f"invalid {key}")
    if sanitize(parameters, environ={}) != parameters:
        raise TrustedLaptopWorkerError("unsafe transformation parameters")
    _canonical(parameters)
    return parameters


def transformation_contract_id(
    *,
    tool_id: str,
    tool_version: str,
    contract_version: int,
    parameters: Mapping[str, object],
) -> str:
    """Bind tool, version, contract, and parameters into a derived namespace."""

    tool_id = _require_pattern(tool_id, _TOOL_ID, "tool_id")
    tool_version = _require_pattern(tool_version, _VERSION, "tool_version")
    contract_version = _positive_int(contract_version, "contract_version")
    parameter_values = _validate_parameters(parameters)
    return "transform_" + _digest(
        {
            "tool_id": tool_id,
            "tool_version": tool_version,
            "contract_version": contract_version,
            "parameters": parameter_values,
        }
    )


def _validate_capability(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _CAPABILITY_KEYS, "capability")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_capability"
        or record["pairing_protocol"] != "outbound-https-v1"
    ):
        raise TrustedLaptopWorkerError("invalid capability contract")
    _string_list(
        record["capabilities"],
        pattern=_CAPABILITY,
        label="capabilities",
    )
    if record["max_concurrency"] != 1:
        raise TrustedLaptopWorkerError(
            "trusted-laptop concurrency must remain one"
        )
    for field in (
        "maximum_input_bytes",
        "maximum_output_bytes",
        "maximum_cpu_seconds",
        "maximum_memory_bytes",
        "maximum_disk_bytes",
        "maximum_elapsed_seconds",
    ):
        _positive_int(record[field], field)
    if _time(record["issued_at"], "issued_at") >= _time(
        record["expires_at"], "expires_at"
    ):
        raise TrustedLaptopWorkerError("capability expiry must follow issuance")
    return record


def _validate_pairing(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _PAIRING_KEYS, "pairing")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_pairing"
        or record["transport"] != "outbound_https"
        or record["direction"] != "laptop_initiated"
    ):
        raise TrustedLaptopWorkerError("pairing must be laptop-initiated HTTPS")
    _require_pattern(record["pairing_id"], _ID, "pairing_id")
    if _time(record["paired_at"], "paired_at") >= _time(
        record["expires_at"], "expires_at"
    ):
        raise TrustedLaptopWorkerError("pairing expiry must follow pairing")
    return record


def _validate_job(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _JOB_KEYS, "transformation job")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_transformation_job"
    ):
        raise TrustedLaptopWorkerError("invalid transformation job contract")
    _require_pattern(record["job_id"], _ID, "job_id")
    _require_pattern(record["source_id"], _SOURCE_ID, "source_id")
    _require_pattern(record["asset_id"], _ASSET_ID, "asset_id")
    _require_pattern(record["rights_id"], _ID, "rights_id")
    _require_pattern(record["input_receipt_id"], _ID, "input_receipt_id")
    _require_pattern(
        record["transformation_id"],
        _TRANSFORMATION_ID,
        "transformation_id",
    )
    for field in (
        "input_sha256",
        "input_rights_snapshot_sha256",
        "derivation_authority_sha256",
        "privacy_snapshot_sha256",
        "parameters_sha256",
    ):
        _require_pattern(record[field], _HASH, field)
    input_key = _require_pattern(
        record["input_object_key"], _OBJECT_KEY, "input_object_key"
    )
    prefix = record["namespace_prefix"]
    if not isinstance(prefix, str) or not dedicated_staging_prefix(prefix):
        raise TrustedLaptopWorkerError("invalid namespace_prefix")
    if not input_key.startswith(prefix + "v1/"):
        raise TrustedLaptopWorkerError(
            "input object key is outside the approved namespace"
        )
    _positive_int(record["input_byte_size"], "input_byte_size", allow_zero=True)
    _media_type(record["input_media_type"], "input_media_type")
    _media_type(record["output_media_type"], "output_media_type")
    _safe_label(record["retention_class"], "retention_class")
    if record["retrieval_decision"] != "approved":
        raise TrustedLaptopWorkerError(
            "transformation jobs require approved retrieval"
        )
    _require_pattern(
        record["required_capability"], _CAPABILITY, "required_capability"
    )
    tool_id = _require_pattern(record["tool_id"], _TOOL_ID, "tool_id")
    tool_version = _require_pattern(
        record["tool_version"], _VERSION, "tool_version"
    )
    contract_version = _positive_int(
        record["contract_version"], "contract_version"
    )
    parameter_values = _validate_parameters(record["parameters"])
    if record["parameters_sha256"] != _digest(parameter_values):
        raise TrustedLaptopWorkerError("parameters hash mismatch")
    if record["transformation_id"] != transformation_contract_id(
        tool_id=tool_id,
        tool_version=tool_version,
        contract_version=contract_version,
        parameters=parameter_values,
    ):
        raise TrustedLaptopWorkerError(
            "transformation identifier does not bind its version and parameters"
        )
    _safe_label(record["redaction_state"], "redaction_state")
    _require_pattern(record["creation_run_id"], _RUN_ID, "creation_run_id")
    _require_pattern(record["evidence_ref"], _EVIDENCE_REF, "evidence_ref")
    attempt = _positive_int(record["attempt"], "attempt")
    maximum_attempts = _positive_int(
        record["maximum_attempts"], "maximum_attempts"
    )
    if maximum_attempts > 100:
        raise TrustedLaptopWorkerError("maximum_attempts is unbounded")
    for field in (
        "maximum_input_bytes",
        "maximum_output_bytes",
        "maximum_cpu_seconds",
        "maximum_memory_bytes",
        "maximum_disk_bytes",
        "maximum_elapsed_seconds",
    ):
        _positive_int(record[field], field)
    if int(record["input_byte_size"]) > int(record["maximum_input_bytes"]):
        raise TrustedLaptopWorkerError("declared input exceeds the job bound")
    if attempt > maximum_attempts:
        # This remains a valid durable job shape; execution records the stable
        # retry blocker rather than treating it as malformed transit.
        pass
    return record


def _validate_lease(
    value: Mapping[str, object],
    *,
    pairing: Mapping[str, object] | None = None,
    job: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    record = _exact(value, _LEASE_KEYS, "lease")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_lease"
    ):
        raise TrustedLaptopWorkerError("invalid lease contract")
    for field in ("lease_id", "pairing_id", "job_id"):
        _require_pattern(record[field], _ID, field)
    acquired = _time(record["acquired_at"], "acquired_at")
    expires = _time(record["expires_at"], "expires_at")
    if acquired >= expires:
        raise TrustedLaptopWorkerError("lease expiry must follow acquisition")
    if pairing is not None and record["pairing_id"] != pairing["pairing_id"]:
        raise TrustedLaptopWorkerError("lease pairing mismatch")
    if job is not None and record["job_id"] != job["job_id"]:
        raise TrustedLaptopWorkerError("lease job mismatch")
    if now is not None:
        current = now.astimezone(_UTC)
        if acquired > current:
            raise TrustedLaptopWorkerError("lease acquisition is in the future")
        if expires <= current:
            raise TrustedLaptopWorkerError("lease is expired")
    return record


def _validate_heartbeat(
    value: Mapping[str, object],
    *,
    pairing: Mapping[str, object] | None = None,
    lease: Mapping[str, object] | None = None,
    job: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    record = _exact(value, _HEARTBEAT_KEYS, "heartbeat")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_heartbeat"
    ):
        raise TrustedLaptopWorkerError("invalid heartbeat contract")
    for field in ("lease_id", "pairing_id", "job_id"):
        _require_pattern(record[field], _ID, field)
    heartbeat_at = _time(record["heartbeat_at"], "heartbeat_at")
    expires = _time(record["expires_at"], "expires_at")
    if heartbeat_at >= expires:
        raise TrustedLaptopWorkerError(
            "heartbeat expiry must follow heartbeat time"
        )
    if pairing is not None and record["pairing_id"] != pairing["pairing_id"]:
        raise TrustedLaptopWorkerError("heartbeat pairing mismatch")
    if lease is not None and record["lease_id"] != lease["lease_id"]:
        raise TrustedLaptopWorkerError("heartbeat lease mismatch")
    if job is not None and record["job_id"] != job["job_id"]:
        raise TrustedLaptopWorkerError("heartbeat job mismatch")
    if now is not None:
        current = now.astimezone(_UTC)
        if heartbeat_at > current:
            raise TrustedLaptopWorkerError("heartbeat is in the future")
        if expires <= current:
            raise TrustedLaptopWorkerError("heartbeat lease is expired")
    return record


def _validate_checkpoint(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _CHECKPOINT_KEYS, "checkpoint")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_checkpoint"
    ):
        raise TrustedLaptopWorkerError("invalid checkpoint contract")
    for field in (
        "checkpoint_id",
        "pairing_id",
        "lease_id",
        "job_id",
    ):
        _require_pattern(record[field], _ID, field)
    _require_pattern(
        record["job_contract_sha256"], _HASH, "job_contract_sha256"
    )
    if record["stage"] not in _STAGES:
        raise TrustedLaptopWorkerError("invalid checkpoint stage")
    _require_pattern(
        record["input_object_key"], _OBJECT_KEY, "input_object_key"
    )
    for field in ("output_object_key", "manifest_object_key"):
        if record[field] is not None:
            _require_pattern(record[field], _OBJECT_KEY, field)
    for field in ("output_receipt_id", "manifest_receipt_id"):
        if record[field] is not None:
            _require_pattern(record[field], _ID, field)
    if record["output_sha256"] is not None:
        _require_pattern(record["output_sha256"], _HASH, "output_sha256")
    if record["output_byte_size"] is not None:
        _positive_int(
            record["output_byte_size"],
            "output_byte_size",
            allow_zero=True,
        )
    for field in (
        "cpu_seconds",
        "peak_memory_bytes",
        "working_disk_bytes",
        "elapsed_seconds",
    ):
        if record[field] is not None:
            _finite_number(record[field], field)
    output_facts = (
        "output_object_key",
        "output_sha256",
        "output_byte_size",
        "cpu_seconds",
        "peak_memory_bytes",
        "working_disk_bytes",
        "elapsed_seconds",
    )
    stage = str(record["stage"])
    if stage in {"claimed", "input_verified", "transform_started"}:
        if any(record[field] is not None for field in output_facts):
            raise TrustedLaptopWorkerError(
                "pre-transform checkpoint cannot carry output facts"
            )
        if (
            record["output_receipt_id"] is not None
            or record["manifest_object_key"] is not None
            or record["manifest_receipt_id"] is not None
        ):
            raise TrustedLaptopWorkerError(
                "pre-transform checkpoint cannot carry receipts"
            )
    else:
        if any(record[field] is None for field in output_facts):
            raise TrustedLaptopWorkerError(
                "post-transform checkpoint requires exact output facts"
            )
        if stage == "transform_verified":
            if (
                record["output_receipt_id"] is not None
                or record["manifest_object_key"] is not None
                or record["manifest_receipt_id"] is not None
            ):
                raise TrustedLaptopWorkerError(
                    "transform checkpoint cannot claim durable receipts"
                )
        elif stage == "output_verified":
            if (
                record["output_receipt_id"] is None
                or record["manifest_object_key"] is not None
                or record["manifest_receipt_id"] is not None
            ):
                raise TrustedLaptopWorkerError(
                    "output checkpoint requires only its output receipt"
                )
        elif (
            record["output_receipt_id"] is None
            or record["manifest_object_key"] is None
            or record["manifest_receipt_id"] is None
        ):
            raise TrustedLaptopWorkerError(
                "manifest checkpoint requires both exact receipts"
            )
    _require_pattern(
        record["resume_state_sha256"], _HASH, "resume_state_sha256"
    )
    resume_state = {
        key: child
        for key, child in record.items()
        if key
        not in {
            "schema_version",
            "record_type",
            "checkpoint_id",
            "resume_state_sha256",
            "recorded_at",
        }
    }
    expected_resume_hash = _digest(resume_state)
    if record["resume_state_sha256"] != expected_resume_hash:
        raise TrustedLaptopWorkerError("checkpoint resume hash mismatch")
    if record["checkpoint_id"] != (
        "checkpoint_"
        + _digest({"resume_state_sha256": expected_resume_hash})
    ):
        raise TrustedLaptopWorkerError("checkpoint identifier mismatch")
    _time(record["recorded_at"], "recorded_at")
    return record


def _validate_result(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _RESULT_KEYS, "result")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_result"
    ):
        raise TrustedLaptopWorkerError("invalid result contract")
    for field in (
        "result_id",
        "job_id",
        "input_receipt_id",
        "output_receipt_id",
        "manifest_receipt_id",
    ):
        _require_pattern(record[field], _ID, field)
    _require_pattern(
        record["job_contract_sha256"], _HASH, "job_contract_sha256"
    )
    _require_pattern(record["source_id"], _SOURCE_ID, "source_id")
    _require_pattern(record["asset_id"], _ASSET_ID, "asset_id")
    _require_pattern(
        record["transformation_id"],
        _TRANSFORMATION_ID,
        "transformation_id",
    )
    for field in ("output_object_key", "manifest_object_key"):
        _require_pattern(record[field], _OBJECT_KEY, field)
    for field in (
        "output_sha256",
        "rights_snapshot_sha256",
        "privacy_snapshot_sha256",
    ):
        _require_pattern(record[field], _HASH, field)
    _positive_int(
        record["output_byte_size"], "output_byte_size", allow_zero=True
    )
    _safe_label(record["retention_class"], "retention_class")
    if record["retrieval_decision"] != "approved":
        raise TrustedLaptopWorkerError("invalid result retrieval decision")
    _safe_label(record["redaction_state"], "redaction_state")
    for field in (
        "cpu_seconds",
        "peak_memory_bytes",
        "working_disk_bytes",
        "elapsed_seconds",
    ):
        _finite_number(record[field], field)
    _require_pattern(record["evidence_ref"], _EVIDENCE_REF, "evidence_ref")
    _time(record["completed_at"], "completed_at")
    return record


def _validate_blocker(value: Mapping[str, object]) -> dict[str, object]:
    record = _exact(value, _BLOCKER_KEYS, "blocker")
    if (
        record["schema_version"] != 1
        or record["record_type"] != "trusted_laptop_blocker"
    ):
        raise TrustedLaptopWorkerError("invalid blocker contract")
    for field in (
        "blocker_id",
        "pairing_id",
        "lease_id",
        "job_id",
    ):
        _require_pattern(record[field], _ID, field)
    _safe_label(record["code"], "blocker code")
    _safe_label(record["gate"], "blocker gate")
    if record["required_authority_class"] not in {
        "none",
        "corpus_operator",
    }:
        raise TrustedLaptopWorkerError("invalid blocker authority class")
    _safe_text(record["next_safe_action"], "next safe action")
    _require_pattern(
        record["resume_state_sha256"], _HASH, "resume_state_sha256"
    )
    _time(record["recorded_at"], "recorded_at")
    return record


def validate_trusted_laptop_record(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Validate one strict public trusted-laptop transit record."""

    if not isinstance(value, Mapping):
        raise TrustedLaptopWorkerError("trusted-laptop record must be an object")
    record_type = value.get("record_type")
    validator = {
        "trusted_laptop_capability": _validate_capability,
        "trusted_laptop_pairing": _validate_pairing,
        "trusted_laptop_transformation_job": _validate_job,
        "trusted_laptop_lease": _validate_lease,
        "trusted_laptop_heartbeat": _validate_heartbeat,
        "trusted_laptop_checkpoint": _validate_checkpoint,
        "trusted_laptop_result": _validate_result,
        "trusted_laptop_blocker": _validate_blocker,
    }.get(str(record_type))
    if validator is None:
        raise TrustedLaptopWorkerError("unknown trusted-laptop record type")
    result = validator(value)
    if sanitize(result, environ={}) != result:
        raise TrustedLaptopWorkerError("unsafe trusted-laptop record")
    _canonical(result)
    return result


def _validate_authority(
    value: Mapping[str, object] | None,
    *,
    job: Mapping[str, object],
    now: datetime,
    stage: str,
) -> dict[str, object]:
    if value is None:
        raise TrustedLaptopExecutionError(
            "authority_missing",
            "authority",
            "Keep this job blocked until current derivation authority exists.",
            required_authority_class="corpus_operator",
        )
    try:
        record = _exact(value, _AUTHORITY_KEYS, "derivation authority")
        for field in (
            "input_rights_snapshot_sha256",
            "derivation_authority_sha256",
            "privacy_snapshot_sha256",
        ):
            _require_pattern(record[field], _HASH, field)
        _safe_label(record["retention_class"], "retention_class")
        checked_at = _time(record["checked_at"], "checked_at")
        expires_at = _time(record["expires_at"], "expires_at")
        if (
            record["job_id"] != job["job_id"]
            or record["input_rights_snapshot_sha256"]
            != job["input_rights_snapshot_sha256"]
            or record["derivation_authority_sha256"]
            != job["derivation_authority_sha256"]
            or record["privacy_snapshot_sha256"]
            != job["privacy_snapshot_sha256"]
            or record["retention_class"] != job["retention_class"]
        ):
            raise TrustedLaptopWorkerError("authority does not match the job")
        gates = record["gates"]
        if not isinstance(gates, Mapping) or set(gates) != set(_GATES):
            raise TrustedLaptopWorkerError("invalid authority gates")
        if any(not isinstance(gates[name], bool) for name in _GATES):
            raise TrustedLaptopWorkerError("authority gates must be boolean")
        if checked_at > now.astimezone(_UTC) or checked_at >= expires_at:
            raise TrustedLaptopWorkerError("invalid authority window")
    except TrustedLaptopWorkerError as exc:
        raise TrustedLaptopExecutionError(
            "authority_invalid",
            "authority",
            "Keep this job blocked and refresh its sanitized authority record.",
            required_authority_class="corpus_operator",
        ) from exc
    if expires_at <= now.astimezone(_UTC):
        raise TrustedLaptopExecutionError(
            f"authority_expired_{stage}",
            "authority",
            "Refresh current authority before resuming this exact job.",
            required_authority_class="corpus_operator",
        )
    for gate in _GATES:
        if not gates[gate]:
            raise TrustedLaptopExecutionError(
                f"{gate}_not_approved",
                gate,
                f"Keep this job blocked until the {gate} decision is approved.",
                required_authority_class="corpus_operator",
            )
    return record


def _file_digest(path: Path) -> tuple[int, str]:
    if not path.is_file() or path.is_symlink():
        raise TrustedLaptopExecutionError(
            "cache_file_invalid",
            "cache",
            "Discard the disposable cache and retry this exact job.",
        )
    size = 0
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_SIZE):
                size += len(chunk)
                hasher.update(chunk)
    except OSError as exc:
        raise TrustedLaptopExecutionError(
            "cache_file_unavailable",
            "cache",
            "Discard the disposable cache and retry this exact job.",
        ) from exc
    return size, hasher.hexdigest()


def _head_matches(
    value: Mapping[str, object] | None,
    *,
    byte_size: int,
    media_type: str,
    sha256: str,
) -> bool:
    if value is None:
        return False
    try:
        observed_size = int(value.get("byte_size", -1))
    except (TypeError, ValueError):
        return False
    return (
        observed_size == byte_size
        and str(value.get("media_type", "")).partition(";")[0].strip().lower()
        == media_type
        and value.get("sha256") == sha256
    )


def _cache_marker(
    cache_id: str,
    *,
    lease_expires_at: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "disposable_trusted_laptop_cache",
        "cache_id": cache_id,
        "lease_expires_at": lease_expires_at,
    }


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory(path: Path) -> int:
    return os.open(path, _directory_flags())


def _same_directory_entry(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
) -> bool:
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(observed.st_mode)
        and observed.st_dev == expected.st_dev
        and observed.st_ino == expected.st_ino
    )


def _read_json_at(directory_fd: int, name: str) -> object:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("marker is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(file_fd, _CHUNK_SIZE):
            chunks.append(chunk)
            if sum(map(len, chunks)) > _MANIFEST_MAX_BYTES:
                raise OSError("marker is unbounded")
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        os.close(file_fd)


def _write_exclusive_at(directory_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise OSError("short cache write")
            view = view[written:]
    finally:
        os.close(file_fd)


def _remove_directory_contents(directory_fd: int) -> None:
    """Remove entries relative to one already-open, non-symlink directory."""

    for name in os.listdir(directory_fd):
        child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(child_stat.st_mode):
            child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
            try:
                _remove_directory_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _directory_size(directory_fd: int) -> int:
    total = 0
    for name in os.listdir(directory_fd):
        child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(child_stat.st_mode):
            child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
            try:
                total += _directory_size(child_fd)
            finally:
                os.close(child_fd)
        else:
            total += child_stat.st_size
    return total


def _virtual_memory_bytes() -> int:
    if sys.platform.startswith("linux"):
        try:
            pages = int(
                Path("/proc/self/statm").read_text(encoding="ascii").split()[0]
            )
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
    try:
        value = subprocess.run(
            ["ps", "-o", "vsz=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return int(value) * 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _resident_memory_bytes(pid: int) -> int | None:
    if sys.platform.startswith("linux"):
        try:
            pages = int(
                Path(f"/proc/{pid}/statm")
                .read_text(encoding="ascii")
                .split()[1]
            )
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return None
    try:
        value = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return int(value) * 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _process_cpu_seconds(pid: int) -> float | None:
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            fields = raw[raw.rfind(")") + 2 :].split()
            ticks = int(fields[11]) + int(fields[12])
            return ticks / float(os.sysconf("SC_CLK_TCK"))
        except (OSError, ValueError, IndexError):
            return None
    try:
        value = subprocess.run(
            ["ps", "-o", "time=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return _parse_process_time(value)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _parse_process_time(value: str) -> float | None:
    try:
        day_parts = value.split("-", 1)
        days = int(day_parts[0]) if len(day_parts) == 2 else 0
        clock_parts = day_parts[-1].split(":")
        if len(clock_parts) == 3:
            hours, minutes, seconds = clock_parts
        elif len(clock_parts) == 2:
            hours = "0"
            minutes, seconds = clock_parts
        else:
            return None
        return (
            days * 86400
            + int(hours) * 3600
            + int(minutes) * 60
            + float(seconds)
        )
    except ValueError:
        return None


def _process_group_usage(process_group_id: int) -> tuple[float, int, int] | None:
    """Return aggregate CPU, resident bytes, and member count for one group."""

    if sys.platform.startswith("linux"):
        cpu_ticks = 0
        resident_pages = 0
        members = 0
        try:
            clock_ticks = float(os.sysconf("SC_CLK_TCK"))
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    raw = (entry / "stat").read_text(encoding="ascii")
                    fields = raw[raw.rfind(")") + 2 :].split()
                    if int(fields[2]) != process_group_id:
                        continue
                    cpu_ticks += int(fields[11]) + int(fields[12])
                    resident_pages += max(0, int(fields[21]))
                    members += 1
                except (OSError, ValueError, IndexError):
                    continue
            return cpu_ticks / clock_ticks, resident_pages * page_size, members
        except (OSError, ValueError):
            return None
    try:
        output = subprocess.run(
            ["ps", "-axo", "pgid=,time=,rss="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cpu_seconds = 0.0
    resident_bytes = 0
    members = 0
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            if int(fields[0]) != process_group_id:
                continue
            parsed_time = _parse_process_time(fields[1])
            if parsed_time is None:
                continue
            cpu_seconds += parsed_time
            resident_bytes += max(0, int(fields[2])) * 1024
            members += 1
        except ValueError:
            continue
    return cpu_seconds, resident_bytes, members


def _maximum_rss_bytes(usage: Any) -> int:
    observed = int(usage.ru_maxrss)
    return observed if sys.platform == "darwin" else observed * 1024


def _set_hard_resource_limit(resource_kind: int, requested: int) -> None:
    _, inherited_hard = resource.getrlimit(resource_kind)
    target = (
        requested
        if inherited_hard == resource.RLIM_INFINITY
        else min(requested, inherited_hard)
    )
    resource.setrlimit(resource_kind, (target, target))


def _set_cpu_resource_limit(requested: int) -> None:
    _set_hard_resource_limit(resource.RLIMIT_CPU, requested)


def _stop_transform_process(process: Any) -> None:
    pid = process.pid
    if pid is None:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        process.terminate()
    process.join(timeout=0.5)
    if not process.is_alive():
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        process.kill()
    process.join()


def _run_transform_child(
    transformer: TrustedLaptopTransformer,
    input_path: Path,
    output_path: Path,
    job: dict[str, object],
    connection: Any,
) -> None:
    """Run one reviewed transformer in a fresh forkserver child."""

    try:
        os.setsid()
        cpu_limit = int(job["maximum_cpu_seconds"])
        output_limit = int(job["maximum_output_bytes"])
        memory_limit = int(job["maximum_memory_bytes"])
        baseline_vms = _virtual_memory_bytes()
        baseline_rss = _maximum_rss_bytes(
            resource.getrusage(resource.RUSAGE_SELF)
        )
        _set_cpu_resource_limit(cpu_limit)
        _set_hard_resource_limit(resource.RLIMIT_FSIZE, output_limit)
        if baseline_vms > 0:
            address_limit = baseline_vms + memory_limit
            try:
                _set_hard_resource_limit(
                    resource.RLIMIT_AS, address_limit
                )
            except (OSError, ValueError):
                # Parent-side RSS supervision remains authoritative when the
                # host refuses to lower an address-space hard limit.
                pass
        try:
            outcome = transformer.transform(
                input_path=input_path,
                output_path=output_path,
                job=job,
            )
            usage = resource.getrusage(resource.RUSAGE_SELF)
            child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            payload: dict[str, object] = {
                "state": "ok",
                "outcome": dict(outcome),
                "observed_cpu_seconds": float(
                    usage.ru_utime
                    + usage.ru_stime
                    + child_usage.ru_utime
                    + child_usage.ru_stime
                ),
                "observed_memory_bytes": max(
                    0,
                    _maximum_rss_bytes(usage) - baseline_rss,
                    _maximum_rss_bytes(child_usage),
                ),
            }
        except MemoryError:
            payload = {"state": "memory_limit_exceeded"}
        except OSError as exc:
            payload = {
                "state": (
                    "output_limit_exceeded"
                    if exc.errno == errno.EFBIG
                    else "transformer_failed"
                )
            }
        except BaseException:
            payload = {"state": "transformer_failed"}
        try:
            encoded = _canonical(payload)
            if len(encoded) > _MANIFEST_MAX_BYTES:
                encoded = _canonical(
                    {"state": "transformer_metrics_invalid"}
                )
        except BaseException:
            encoded = _canonical(
                {"state": "transformer_metrics_invalid"}
            )
        connection.send_bytes(encoded)
    finally:
        connection.close()


def reap_stale_disposable_caches(
    cache_root: Path,
    *,
    now: datetime | None = None,
) -> int:
    """Remove only marker-bound cache directories owned by this worker.

    Unknown directories, symlinks, malformed markers, and non-worker paths are
    preserved.  The caller chooses the dedicated root.
    """

    if now is not None and now.tzinfo is None:
        raise TrustedLaptopWorkerError(
            "cache reaper time must be timezone-aware"
        )
    current_time = (
        datetime.now(_UTC) if now is None else now.astimezone(_UTC)
    )
    root = cache_root.expanduser()
    if root.is_symlink():
        raise TrustedLaptopWorkerError("cache root must not be a symlink")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TrustedLaptopWorkerError(
            "cache root is unavailable"
        ) from exc
    if not root.is_dir():
        raise TrustedLaptopWorkerError("cache root must be a directory")
    removed = 0
    root_fd = _open_directory(root)
    try:
        for name in tuple(os.listdir(root_fd)):
            if not _CACHE_DIR.fullmatch(name):
                continue
            try:
                candidate_fd = os.open(
                    name, _directory_flags(), dir_fd=root_fd
                )
            except OSError:
                continue
            try:
                candidate_stat = os.fstat(candidate_fd)
                marker = _read_json_at(candidate_fd, ".disposable-v1")
                if (
                    not isinstance(marker, Mapping)
                    or set(marker)
                    != {
                        "schema_version",
                        "record_type",
                        "cache_id",
                        "lease_expires_at",
                    }
                    or marker.get("schema_version") != 1
                    or marker.get("record_type")
                    != "disposable_trusted_laptop_cache"
                    or not isinstance(marker.get("cache_id"), str)
                    or not _CACHE_ID.fullmatch(str(marker["cache_id"]))
                    or name
                    != ".performing-fire-laptop-cache-"
                    + str(marker["cache_id"]).removeprefix("cache_")
                ):
                    continue
                lease_expires_at = _time(
                    marker["lease_expires_at"], "lease_expires_at"
                )
                if (
                    lease_expires_at > current_time
                    or not _same_directory_entry(
                        root_fd, name, candidate_stat
                    )
                ):
                    continue
                _remove_directory_contents(candidate_fd)
                if not _same_directory_entry(
                    root_fd, name, candidate_stat
                ):
                    continue
                os.rmdir(name, dir_fd=root_fd)
                removed += 1
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                TrustedLaptopWorkerError,
            ):
                continue
            finally:
                os.close(candidate_fd)
    finally:
        os.close(root_fd)
    return removed


class _DisposableCache:
    def __init__(
        self,
        root: Path,
        *,
        pairing_id: str,
        lease_id: str,
        job_id: str,
        lease_expires_at: str,
    ) -> None:
        cache_id = "cache_" + _digest(
            {
                "pairing_id": pairing_id,
                "lease_id": lease_id,
                "job_id": job_id,
            }
        )[:32]
        root.mkdir(parents=True, exist_ok=True)
        self._root_fd = _open_directory(root)
        self.path = root / (
            ".performing-fire-laptop-cache-"
            + cache_id.removeprefix("cache_")
        )
        if self.path.exists():
            os.close(self._root_fd)
            raise TrustedLaptopWorkerError(
                "existing cache must be reaped before reuse"
            )
        os.mkdir(self.path.name, mode=0o700, dir_fd=self._root_fd)
        self._directory_fd = os.open(
            self.path.name, _directory_flags(), dir_fd=self._root_fd
        )
        self._directory_stat = os.fstat(self._directory_fd)
        self._cache_id = cache_id
        _write_exclusive_at(
            self._directory_fd,
            ".disposable-v1",
            _canonical(
                _cache_marker(
                    cache_id,
                    lease_expires_at=lease_expires_at,
                )
            ),
        )
        self.input_path = self.path / "input.bin"
        self.output_path = self.path / "output.bin"
        self.manifest_path = self.path / "manifest.json"

    def _assert_identity(self) -> None:
        if not _same_directory_entry(
            self._root_fd, self.path.name, self._directory_stat
        ):
            raise TrustedLaptopWorkerError(
                "disposable cache directory identity changed"
            )

    def refresh_lease_expires_at(self, lease_expires_at: str) -> None:
        self._assert_identity()
        _time(lease_expires_at, "lease_expires_at")
        temporary_name = ".disposable-v1.next"
        try:
            os.unlink(temporary_name, dir_fd=self._directory_fd)
        except FileNotFoundError:
            pass
        _write_exclusive_at(
            self._directory_fd,
            temporary_name,
            _canonical(
                _cache_marker(
                    self._cache_id,
                    lease_expires_at=lease_expires_at,
                )
            ),
        )
        os.rename(
            temporary_name,
            ".disposable-v1",
            src_dir_fd=self._directory_fd,
            dst_dir_fd=self._directory_fd,
        )

    def write_manifest(self, payload: bytes) -> None:
        self._assert_identity()
        _write_exclusive_at(self._directory_fd, "manifest.json", payload)

    def close(self) -> None:
        try:
            self._assert_identity()
        except TrustedLaptopWorkerError:
            os.close(self._directory_fd)
            os.close(self._root_fd)
            raise
        try:
            marker = _read_json_at(self._directory_fd, ".disposable-v1")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            os.close(self._directory_fd)
            os.close(self._root_fd)
            raise TrustedLaptopWorkerError(
                "disposable cache ownership marker changed"
            ) from exc
        if (
            not isinstance(marker, Mapping)
            or marker.get("cache_id") != self._cache_id
        ):
            os.close(self._directory_fd)
            os.close(self._root_fd)
            raise TrustedLaptopWorkerError(
                "disposable cache ownership marker changed"
            )
        try:
            _remove_directory_contents(self._directory_fd)
            if not _same_directory_entry(
                self._root_fd, self.path.name, self._directory_stat
            ):
                raise TrustedLaptopWorkerError(
                    "disposable cache directory identity changed"
                )
            os.rmdir(self.path.name, dir_fd=self._root_fd)
        finally:
            os.close(self._directory_fd)
            os.close(self._root_fd)


def _result_id(value: Mapping[str, object]) -> str:
    return "result_" + _digest(
        {
            key: child
            for key, child in value.items()
            if key
            not in {
                "result_id",
                "completed_at",
            }
        }
    )


def _checkpoint(
    *,
    pairing: Mapping[str, object],
    lease: Mapping[str, object],
    job: Mapping[str, object],
    stage: str,
    now: datetime,
    progress: Mapping[str, object] | None = None,
) -> dict[str, object]:
    resume_state = {
        "pairing_id": pairing["pairing_id"],
        "lease_id": lease["lease_id"],
        "job_id": job["job_id"],
        "job_contract_sha256": _job_contract_sha256(job),
        "stage": stage,
        "input_object_key": job["input_object_key"],
        "output_object_key": None,
        "output_receipt_id": None,
        "manifest_object_key": None,
        "manifest_receipt_id": None,
        "output_sha256": None,
        "output_byte_size": None,
        "cpu_seconds": None,
        "peak_memory_bytes": None,
        "working_disk_bytes": None,
        "elapsed_seconds": None,
    }
    if progress is not None:
        allowed_progress = {
            "output_object_key",
            "output_receipt_id",
            "manifest_object_key",
            "manifest_receipt_id",
            "output_sha256",
            "output_byte_size",
            "cpu_seconds",
            "peak_memory_bytes",
            "working_disk_bytes",
            "elapsed_seconds",
        }
        if set(progress) - allowed_progress:
            raise TrustedLaptopWorkerError("invalid checkpoint progress")
        resume_state.update(progress)
    resume_hash = _digest(resume_state)
    value = {
        "schema_version": 1,
        "record_type": "trusted_laptop_checkpoint",
        "checkpoint_id": "checkpoint_" + _digest(
            {"resume_state_sha256": resume_hash}
        ),
        "pairing_id": pairing["pairing_id"],
        "lease_id": lease["lease_id"],
        "job_id": job["job_id"],
        "job_contract_sha256": resume_state["job_contract_sha256"],
        "stage": stage,
        "input_object_key": resume_state["input_object_key"],
        "output_object_key": resume_state["output_object_key"],
        "output_receipt_id": resume_state["output_receipt_id"],
        "manifest_object_key": resume_state["manifest_object_key"],
        "manifest_receipt_id": resume_state["manifest_receipt_id"],
        "output_sha256": resume_state["output_sha256"],
        "output_byte_size": resume_state["output_byte_size"],
        "cpu_seconds": resume_state["cpu_seconds"],
        "peak_memory_bytes": resume_state["peak_memory_bytes"],
        "working_disk_bytes": resume_state["working_disk_bytes"],
        "elapsed_seconds": resume_state["elapsed_seconds"],
        "resume_state_sha256": resume_hash,
        "recorded_at": _utc_text(now),
    }
    return _validate_checkpoint(value)


def _blocker(
    *,
    pairing: Mapping[str, object],
    lease: Mapping[str, object],
    job: Mapping[str, object],
    error: TrustedLaptopExecutionError,
    now: datetime,
) -> dict[str, object]:
    resume_hash = _digest(
        {
            "pairing_id": pairing["pairing_id"],
            "lease_id": lease["lease_id"],
            "job_id": job["job_id"],
            "code": error.code,
            "input_object_key": job["input_object_key"],
            "transformation_id": job["transformation_id"],
        }
    )
    value = {
        "schema_version": 1,
        "record_type": "trusted_laptop_blocker",
        "blocker_id": "blocker_" + _digest(
            {
                "job_id": job["job_id"],
                "code": error.code,
                "resume_state_sha256": resume_hash,
            }
        ),
        "pairing_id": pairing["pairing_id"],
        "lease_id": lease["lease_id"],
        "job_id": job["job_id"],
        "code": error.code,
        "gate": error.gate,
        "required_authority_class": error.required_authority_class,
        "next_safe_action": error.next_safe_action,
        "resume_state_sha256": resume_hash,
        "recorded_at": _utc_text(now),
    }
    return _validate_blocker(value)


class _GuardedCreateStorage:
    """Recheck lease/authority/tombstones at the actual create boundary."""

    def __init__(
        self,
        storage: TrustedLaptopObjectStore,
        *,
        before_create: Callable[[str], None],
    ) -> None:
        self.storage = storage
        self.before_create = before_create
        self.guard_error: TrustedLaptopExecutionError | None = None

    def head_object(self, key: str) -> Mapping[str, object] | None:
        return self.storage.head_object(key)

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        try:
            self.before_create(key)
        except TrustedLaptopExecutionError as exc:
            self.guard_error = exc
            raise CorpusObjectError(
                "trusted_laptop_create_guard",
                "Resume only after the exact laptop create guard passes.",
            ) from exc
        return self.storage.create_file_if_absent(
            key,
            path,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
        )


class BoundedTrustedLaptopWorker:
    """Run at most one rights-approved exact-key transformation at a time."""

    def __init__(
        self,
        *,
        control_plane: TrustedLaptopControlPlane,
        authority_resolver: TrustedLaptopAuthorityResolver,
        object_store: TrustedLaptopObjectStore,
        receipt_authority: TrustedLaptopReceiptAuthority,
        transformer: TrustedLaptopTransformer,
        cache_root: Path,
        clock: Callable[[], datetime],
    ) -> None:
        self.control_plane = control_plane
        self.authority_resolver = authority_resolver
        self.object_store = object_store
        self.receipt_authority = receipt_authority
        self.transformer = transformer
        self.cache_root = cache_root
        self.clock = clock
        self._active_cache: _DisposableCache | None = None
        self._run_lock = threading.Lock()

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise TrustedLaptopWorkerError(
                "worker clock must return a timezone-aware datetime"
            )
        return value.astimezone(_UTC)

    def _heartbeat_only(
        self,
        *,
        pairing: Mapping[str, object],
        lease: Mapping[str, object],
        job: Mapping[str, object],
    ) -> dict[str, object]:
        now = self._now()
        if _time(pairing["expires_at"], "expires_at") <= now:
            raise TrustedLaptopExecutionError(
                "pairing_expired",
                "pairing",
                "Establish a fresh outbound pairing before resuming this job.",
            )
        try:
            heartbeat = self.control_plane.heartbeat(
                str(lease["lease_id"]),
                str(pairing["pairing_id"]),
                now=now,
            )
            validated = _validate_heartbeat(
                heartbeat,
                pairing=pairing,
                lease=lease,
                job=job,
                now=now,
            )
            if self._active_cache is not None:
                self._active_cache.refresh_lease_expires_at(
                    str(validated["expires_at"])
                )
            return validated
        except TrustedLaptopExecutionError:
            raise
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "pairing_disconnected",
                "pairing",
                "Resume this exact lease after outbound pairing is restored.",
            ) from exc

    def _maintain(
        self,
        *,
        pairing: Mapping[str, object],
        lease: Mapping[str, object],
        job: Mapping[str, object],
        stage: str,
        progress: Mapping[str, object] | None = None,
    ) -> None:
        self._heartbeat_only(pairing=pairing, lease=lease, job=job)
        now = self._now()
        try:
            self.control_plane.checkpoint(
                _checkpoint(
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    stage=stage,
                    now=now,
                    progress=progress,
                )
            )
        except TrustedLaptopExecutionError:
            raise
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "pairing_disconnected",
                "pairing",
                "Resume this exact lease after outbound pairing is restored.",
            ) from exc

    def _current_authority(
        self,
        *,
        job: Mapping[str, object],
        stage: str,
    ) -> dict[str, object]:
        now = self._now()
        try:
            value = self.authority_resolver.resolve_current_derivation_authority(
                job=dict(job),
                now=now,
            )
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "authority_resolver_unavailable",
                "authority",
                "Retry current authority resolution without weakening any gate.",
            ) from exc
        return _validate_authority(value, job=job, now=now, stage=stage)

    def _assert_elapsed(
        self,
        *,
        job: Mapping[str, object],
        started_at: datetime,
    ) -> None:
        if (self._now() - started_at).total_seconds() > float(
            job["maximum_elapsed_seconds"]
        ):
            raise TrustedLaptopExecutionError(
                "elapsed_limit_exceeded",
                "resource",
                "Discard the cache and keep this job within reviewed bounds.",
            )

    def _assert_capability_current(
        self,
        capability: Mapping[str, object],
    ) -> None:
        if _time(capability["expires_at"], "expires_at") <= self._now():
            raise TrustedLaptopExecutionError(
                "capability_expired",
                "capability",
                "Advertise a fresh bounded capability before resuming this job.",
            )

    def _create_boundary_guard(
        self,
        *,
        capability: Mapping[str, object],
        pairing: Mapping[str, object],
        lease: Mapping[str, object],
        job: Mapping[str, object],
        started_at: datetime,
        authority_stage: str,
        dependent_keys: Sequence[str] = (),
    ) -> Callable[[str], None]:
        def guard(target_key: str) -> None:
            self._assert_capability_current(capability)
            self._heartbeat_only(
                pairing=pairing,
                lease=lease,
                job=job,
            )
            self._assert_elapsed(job=job, started_at=started_at)
            self._current_authority(job=job, stage=authority_stage)
            self._assert_not_tombstoned(
                str(job["input_object_key"]),
                code="input_object_tombstoned_before_create",
            )
            for dependent_key in dependent_keys:
                self._assert_not_tombstoned(
                    dependent_key,
                    code="output_object_tombstoned",
                )
            self._assert_not_tombstoned(
                target_key,
                code=(
                    "manifest_object_tombstoned"
                    if "/manifests/" in target_key
                    else "output_object_tombstoned"
                ),
            )

        return guard

    def _assert_capability_covers_job(
        self,
        capability: Mapping[str, object],
        job: Mapping[str, object],
    ) -> None:
        if job["required_capability"] not in capability["capabilities"]:
            raise TrustedLaptopExecutionError(
                "required_capability_unavailable",
                "capability",
                "Keep this job queued for a matching outbound worker.",
            )
        for field in (
            "maximum_input_bytes",
            "maximum_output_bytes",
            "maximum_cpu_seconds",
            "maximum_memory_bytes",
            "maximum_disk_bytes",
            "maximum_elapsed_seconds",
        ):
            if int(job[field]) > int(capability[field]):
                raise TrustedLaptopExecutionError(
                    "worker_capacity_insufficient",
                    "capability",
                    "Keep this job queued for a worker with sufficient bounds.",
                )
        if int(job["attempt"]) > int(job["maximum_attempts"]):
            raise TrustedLaptopExecutionError(
                "retry_budget_exhausted",
                "retry",
                "Review the sanitized failures before authorizing another attempt.",
                required_authority_class="corpus_operator",
            )
        required_disk = (
            int(job["input_byte_size"])
            + int(job["maximum_output_bytes"])
            + _MANIFEST_MAX_BYTES
        )
        if int(job["maximum_disk_bytes"]) < required_disk:
            raise TrustedLaptopExecutionError(
                "job_disk_bound_inconsistent",
                "resource",
                "Keep this job queued with enough disk for input, output, and manifest.",
            )

    def _input_receipt(
        self,
        job: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            value = self.receipt_authority.get_corpus_receipt(
                str(job["input_receipt_id"])
            )
            if value is None:
                raise TrustedLaptopExecutionError(
                    "input_receipt_missing",
                    "input",
                    "Keep this job blocked until its exact input receipt exists.",
                )
            receipt = validate_object_receipt(value)
        except TrustedLaptopExecutionError:
            raise
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "input_receipt_invalid",
                "input",
                "Keep this job blocked and reconcile its input receipt.",
                required_authority_class="corpus_operator",
            ) from exc
        expected = {
            "source_id": job["source_id"],
            "asset_id": job["asset_id"],
            "receipt_id": job["input_receipt_id"],
            "object_key": job["input_object_key"],
            "sha256": job["input_sha256"],
            "byte_size": job["input_byte_size"],
            "media_type": job["input_media_type"],
            "rights_snapshot_sha256": job["input_rights_snapshot_sha256"],
            "retention_class": job["retention_class"],
            "retrieval_decision": "approved",
        }
        if any(receipt.get(key) != child for key, child in expected.items()):
            raise TrustedLaptopExecutionError(
                "input_receipt_mismatch",
                "input",
                "Hold this exact job and reconcile its immutable input facts.",
                required_authority_class="corpus_operator",
            )
        return receipt

    def _assert_not_tombstoned(
        self,
        object_key: str,
        *,
        code: str,
    ) -> None:
        try:
            tombstone = self.receipt_authority.get_cleanup_tombstone_by_key(
                object_key
            )
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "tombstone_authority_unavailable",
                "deletion",
                "Retry exact-key deletion-authority lookup safely.",
            ) from exc
        if tombstone is not None:
            raise TrustedLaptopExecutionError(
                code,
                "deletion",
                "Keep the exact object absent and reconcile deletion authority.",
                required_authority_class="corpus_operator",
            )

    def _verify_input_head(
        self,
        *,
        job: Mapping[str, object],
    ) -> None:
        try:
            head = self.object_store.head_object(str(job["input_object_key"]))
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "input_exact_head_failed",
                "input",
                "Retry exact-key input verification without listing objects.",
            ) from exc
        if head is None:
            raise TrustedLaptopExecutionError(
                "input_object_missing",
                "input",
                "Keep this job queued until its exact approved input exists.",
            )
        if not _head_matches(
            head,
            byte_size=int(job["input_byte_size"]),
            media_type=str(job["input_media_type"]),
            sha256=str(job["input_sha256"]),
        ):
            raise TrustedLaptopExecutionError(
                "input_head_mismatch",
                "input",
                "Hold this exact input key for immutable-fact review.",
                required_authority_class="corpus_operator",
            )

    def _download_and_verify(
        self,
        *,
        job: Mapping[str, object],
        path: Path,
    ) -> None:
        try:
            self.object_store.download_exact_to_file(
                str(job["input_object_key"]),
                path,
                maximum_bytes=int(job["maximum_input_bytes"]),
            )
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "input_download_failed",
                "input",
                "Retry this exact bounded input after the storage lane recovers.",
            ) from exc
        size, sha256 = _file_digest(path)
        if size != int(job["input_byte_size"]):
            raise TrustedLaptopExecutionError(
                "input_size_mismatch",
                "input",
                "Discard the cache and reconcile the exact input receipt.",
                required_authority_class="corpus_operator",
            )
        if sha256 != job["input_sha256"]:
            raise TrustedLaptopExecutionError(
                "input_hash_mismatch",
                "input",
                "Discard the cache and reconcile the exact input receipt.",
                required_authority_class="corpus_operator",
            )

    def _transform(
        self,
        *,
        pairing: Mapping[str, object],
        lease: Mapping[str, object],
        job: Mapping[str, object],
        cache: _DisposableCache,
        started_at: datetime,
    ) -> tuple[int, str, dict[str, float]]:
        elapsed_before_transform = max(
            0.0, (self._now() - started_at).total_seconds()
        )
        remaining_elapsed_seconds = (
            float(job["maximum_elapsed_seconds"]) - elapsed_before_transform
        )
        if remaining_elapsed_seconds <= 0:
            raise TrustedLaptopExecutionError(
                "elapsed_limit_exceeded",
                "resource",
                "Discard the cache and keep this job within reviewed bounds.",
            )
        try:
            context = multiprocessing.get_context("forkserver")
        except ValueError as exc:
            raise TrustedLaptopExecutionError(
                "transformer_isolation_unavailable",
                "resource",
                "Use a trusted laptop with forkserver process isolation.",
            ) from exc
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_transform_child,
            args=(
                self.transformer,
                cache.input_path,
                cache.output_path,
                dict(job),
                send_connection,
            ),
        )
        try:
            process.start()
        except Exception as exc:
            receive_connection.close()
            send_connection.close()
            raise TrustedLaptopExecutionError(
                "transformer_isolation_unavailable",
                "resource",
                "Use a serializable reviewed transformer in the isolated lane.",
            ) from exc
        send_connection.close()
        process_started = time.monotonic()
        limit_code: str | None = None
        baseline_child_rss = (
            None
            if process.pid is None
            else _resident_memory_bytes(process.pid)
        )
        peak_group_cpu = 0.0
        peak_group_memory = 0
        lease_window_seconds = max(
            0.0,
            (
                _time(lease["expires_at"], "expires_at")
                - _time(lease["acquired_at"], "acquired_at")
            ).total_seconds(),
        )
        heartbeat_interval = max(
            0.1, min(30.0, lease_window_seconds / 3.0)
        )
        next_heartbeat = process_started + heartbeat_interval
        try:
            deadline = process_started + remaining_elapsed_seconds
            while process.is_alive():
                observed_cpu: float | None = None
                observed_rss: int | None = None
                if process.pid is not None:
                    group_usage = _process_group_usage(process.pid)
                    if group_usage is not None:
                        observed_cpu, observed_rss, _ = group_usage
                    else:
                        observed_cpu = _process_cpu_seconds(process.pid)
                        observed_rss = _resident_memory_bytes(process.pid)
                if observed_cpu is not None:
                    peak_group_cpu = max(peak_group_cpu, observed_cpu)
                if (
                    observed_cpu is not None
                    and observed_cpu
                    >= float(job["maximum_cpu_seconds"])
                ):
                    limit_code = "cpu_limit_exceeded"
                    _stop_transform_process(process)
                    break
                if baseline_child_rss is None and observed_rss is not None:
                    baseline_child_rss = observed_rss
                if (
                    baseline_child_rss is not None
                    and observed_rss is not None
                ):
                    peak_group_memory = max(
                        peak_group_memory,
                        max(0, observed_rss - baseline_child_rss),
                    )
                if (
                    baseline_child_rss is not None
                    and observed_rss is not None
                    and observed_rss - baseline_child_rss
                    > int(job["maximum_memory_bytes"])
                ):
                    limit_code = "memory_limit_exceeded"
                    _stop_transform_process(process)
                    break
                current_monotonic = time.monotonic()
                if current_monotonic >= next_heartbeat:
                    self._heartbeat_only(
                        pairing=pairing,
                        lease=lease,
                        job=job,
                    )
                    next_heartbeat = (
                        current_monotonic + heartbeat_interval
                    )
                if _directory_size(cache._directory_fd) > int(
                    job["maximum_disk_bytes"]
                ):
                    limit_code = "disk_limit_exceeded"
                    _stop_transform_process(process)
                    break
                if current_monotonic >= deadline:
                    limit_code = "elapsed_limit_exceeded"
                    _stop_transform_process(process)
                    break
                time.sleep(0.01 if sys.platform.startswith("linux") else 0.05)
            process.join()
            if process.pid is not None:
                final_group_usage = _process_group_usage(process.pid)
                if final_group_usage is not None:
                    group_cpu, group_rss, group_members = final_group_usage
                    peak_group_cpu = max(peak_group_cpu, group_cpu)
                    if baseline_child_rss is not None:
                        peak_group_memory = max(
                            peak_group_memory,
                            max(0, group_rss - baseline_child_rss),
                        )
                    if group_members:
                        limit_code = limit_code or "transformer_failed"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if limit_code is not None:
                raise TrustedLaptopExecutionError(
                    limit_code,
                    "resource",
                    "Discard the cache and keep this job within reviewed bounds.",
                )
            if process.exitcode is not None and process.exitcode < 0:
                child_signal = -process.exitcode
                elapsed_at_exit = time.monotonic() - process_started
                hard_cpu_stop = (
                    child_signal == signal.SIGKILL
                    and elapsed_at_exit
                    >= max(
                        0.5,
                        float(job["maximum_cpu_seconds"]) * 0.8,
                    )
                )
                code = (
                    "cpu_limit_exceeded"
                    if child_signal == signal.SIGXCPU or hard_cpu_stop
                    else (
                        "output_limit_exceeded"
                        if child_signal == signal.SIGXFSZ
                        else "transformer_failed"
                    )
                )
                raise TrustedLaptopExecutionError(
                    code,
                    "resource" if code != "transformer_failed" else "transform",
                    "Discard the cache and keep this job within reviewed bounds."
                    if code != "transformer_failed"
                    else "Retry the same bounded transformation without persisting cache.",
                )
            if not receive_connection.poll():
                raise TrustedLaptopExecutionError(
                    "transformer_failed",
                    "transform",
                    "Retry the same bounded transformation without persisting cache.",
                )
            encoded = receive_connection.recv_bytes(
                maxlength=_MANIFEST_MAX_BYTES
            )
            try:
                payload = json.loads(encoded.decode("ascii"))
            except (UnicodeError, ValueError, TypeError) as exc:
                raise TrustedLaptopExecutionError(
                    "transformer_metrics_invalid",
                    "resource",
                    "Use a transformer that reports the reviewed resource metrics.",
                ) from exc
        finally:
            if process.is_alive():
                _stop_transform_process(process)
            receive_connection.close()
        if (
            not isinstance(payload, Mapping)
            or set(payload) not in (
                {"state"},
                {
                    "state",
                    "outcome",
                    "observed_cpu_seconds",
                    "observed_memory_bytes",
                },
            )
            or payload.get("state") != "ok"
        ):
            state = (
                str(payload.get("state"))
                if isinstance(payload, Mapping)
                else "transformer_failed"
            )
            code = (
                state
                if state
                in {
                    "memory_limit_exceeded",
                    "output_limit_exceeded",
                    "transformer_metrics_invalid",
                }
                else "transformer_failed"
            )
            raise TrustedLaptopExecutionError(
                code,
                "resource" if code != "transformer_failed" else "transform",
                "Discard the cache and keep this job within reviewed bounds."
                if code != "transformer_failed"
                else "Retry the same bounded transformation without persisting cache.",
            )
        outcome = payload["outcome"]
        if not isinstance(outcome, Mapping) or set(outcome) != {
            "cpu_seconds",
            "peak_memory_bytes",
            "working_disk_bytes",
        }:
            raise TrustedLaptopExecutionError(
                "transformer_metrics_invalid",
                "resource",
                "Use a transformer that reports the reviewed resource metrics.",
            )
        metrics = {
            field: _finite_number(outcome[field], field)
            for field in (
                "cpu_seconds",
                "peak_memory_bytes",
                "working_disk_bytes",
            )
        }
        output_size, output_sha256 = _file_digest(cache.output_path)
        elapsed = max(
            (self._now() - started_at).total_seconds(),
            time.monotonic() - process_started,
        )
        metrics["cpu_seconds"] = max(
            metrics["cpu_seconds"],
            peak_group_cpu,
            _finite_number(
                payload["observed_cpu_seconds"],
                "observed_cpu_seconds",
            ),
        )
        metrics["peak_memory_bytes"] = max(
            metrics["peak_memory_bytes"],
            float(peak_group_memory),
            _finite_number(
                payload["observed_memory_bytes"],
                "observed_memory_bytes",
            ),
        )
        metrics["working_disk_bytes"] = max(
            metrics["working_disk_bytes"],
            float(_directory_size(cache._directory_fd)),
        )
        metrics["elapsed_seconds"] = elapsed
        checks = (
            (
                metrics["cpu_seconds"],
                float(job["maximum_cpu_seconds"]),
                "cpu_limit_exceeded",
            ),
            (
                metrics["peak_memory_bytes"],
                float(job["maximum_memory_bytes"]),
                "memory_limit_exceeded",
            ),
            (
                max(
                    metrics["working_disk_bytes"],
                    float(int(job["input_byte_size"]) + output_size),
                ),
                float(job["maximum_disk_bytes"]),
                "disk_limit_exceeded",
            ),
            (
                metrics["elapsed_seconds"],
                float(job["maximum_elapsed_seconds"]),
                "elapsed_limit_exceeded",
            ),
            (
                float(output_size),
                float(job["maximum_output_bytes"]),
                "output_limit_exceeded",
            ),
        )
        for observed, maximum, code in checks:
            if observed > maximum:
                raise TrustedLaptopExecutionError(
                    code,
                    "resource",
                    "Discard the cache and keep this job within reviewed bounds.",
                )
        return output_size, output_sha256, metrics

    def _persist_receipt(
        self,
        receipt: Mapping[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]:
        try:
            requested = validate_object_receipt(receipt)
            persisted = self.receipt_authority.upsert(
                requested,
                operation_id=operation_id,
            )
            observed = validate_object_receipt(persisted)
            if observed != requested:
                raise TrustedLaptopExecutionError(
                    "receipt_commit_conflict",
                    "receipt",
                    "Hold the exact object and reconcile its durable receipt.",
                    required_authority_class="corpus_operator",
                )
            return observed
        except TrustedLaptopExecutionError:
            raise
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "receipt_commit_failed",
                "receipt",
                "Resume from the exact object key and reconcile its receipt.",
            ) from exc

    def _create_output(
        self,
        *,
        job: Mapping[str, object],
        cache: _DisposableCache,
        output_size: int,
        output_sha256: str,
        output_key: str,
        storage: TrustedLaptopObjectStore,
        after_create: Callable[[str], None],
    ) -> tuple[dict[str, object], str]:
        key = output_key
        if key != derived_object_key(
            str(job["namespace_prefix"]),
            str(job["source_id"]),
            str(job["asset_id"]),
            str(job["transformation_id"]),
            output_sha256,
        ):
            raise TrustedLaptopExecutionError(
                "output_key_mismatch",
                "output",
                "Keep the exact transform checkpoint blocked for review.",
                required_authority_class="corpus_operator",
            )
        self._assert_not_tombstoned(
            key, code="output_object_tombstoned"
        )
        try:
            receipt = immutable_create_and_verify(
                storage,
                key=key,
                path=cache.output_path,
                object_kind="derived",
                source_id=str(job["source_id"]),
                asset_id=str(job["asset_id"]),
                transformation_id=str(job["transformation_id"]),
                byte_size=output_size,
                media_type=str(job["output_media_type"]),
                sha256=output_sha256,
                rights_snapshot_sha256=str(
                    job["input_rights_snapshot_sha256"]
                ),
                retention_class=str(job["retention_class"]),
                creation_run_id=str(job["creation_run_id"]),
                retrieval_decision="approved",
                evidence_ref=str(job["evidence_ref"]),
                receipt_authority=self.receipt_authority,
            )
        except CorpusObjectError as exc:
            if (
                isinstance(storage, _GuardedCreateStorage)
                and storage.guard_error is not None
            ):
                raise storage.guard_error from exc
            raise TrustedLaptopExecutionError(
                exc.code,
                "output",
                exc.next_action,
            ) from exc
        after_create(key)
        return (
            self._persist_receipt(
                receipt,
                operation_id="trusted-laptop-output-"
                + _digest({"job_id": job["job_id"], "object_key": key}),
            ),
            key,
        )

    def _create_manifest(
        self,
        *,
        job: Mapping[str, object],
        cache: _DisposableCache,
        input_receipt: Mapping[str, object],
        output_receipt: Mapping[str, object],
        boundary_guard_factory: Callable[[str], Callable[[str], None]],
    ) -> tuple[dict[str, object], str]:
        manifest_id = "manifest_" + _digest(
            {
                "job_id": job["job_id"],
                "input_receipt_id": input_receipt["receipt_id"],
                "output_receipt_id": output_receipt["receipt_id"],
                "transformation_id": job["transformation_id"],
            }
        )
        try:
            manifest = build_derivation_manifest(
                manifest_id=manifest_id,
                source_id=str(job["source_id"]),
                asset_id=str(job["asset_id"]),
                transformation_id=str(job["transformation_id"]),
                tool_id=str(job["tool_id"]),
                tool_version=str(job["tool_version"]),
                contract_version=int(job["contract_version"]),
                parameters=dict(job["parameters"]),  # type: ignore[arg-type]
                inputs=[input_receipt],
                outputs=[output_receipt],
                rights_inheritance="most_restrictive",
                redaction_state=str(job["redaction_state"]),
                evidence_ref=str(job["evidence_ref"]),
            )
            payload = _canonical(manifest)
            if (
                len(payload) > _MANIFEST_MAX_BYTES
                or int(job["input_byte_size"])
                + int(output_receipt["byte_size"])
                + len(payload)
                > int(job["maximum_disk_bytes"])
            ):
                raise TrustedLaptopExecutionError(
                    "manifest_disk_limit_exceeded",
                    "resource",
                    "Keep the sanitized manifest within the reviewed disk bound.",
                )
            cache.write_manifest(payload)
            manifest_sha256 = hashlib.sha256(payload).hexdigest()
            key = manifest_object_key(
                str(job["namespace_prefix"]),
                str(job["source_id"]),
                str(job["asset_id"]),
                manifest_id,
                manifest_sha256,
            )
        except TrustedLaptopExecutionError:
            raise
        except (CorpusObjectError, OSError, TrustedLaptopWorkerError) as exc:
            raise TrustedLaptopExecutionError(
                "manifest_build_failed",
                "manifest",
                "Rebuild the sanitized manifest from durable exact receipts.",
            ) from exc
        self._assert_not_tombstoned(
            key, code="manifest_object_tombstoned"
        )
        boundary_guard = boundary_guard_factory(key)
        guarded_storage = _GuardedCreateStorage(
            self.object_store,
            before_create=boundary_guard,
        )
        try:
            receipt = immutable_create_and_verify(
                guarded_storage,
                key=key,
                path=cache.manifest_path,
                object_kind="manifest",
                source_id=str(job["source_id"]),
                asset_id=str(job["asset_id"]),
                byte_size=len(payload),
                media_type="application/json",
                sha256=manifest_sha256,
                rights_snapshot_sha256=str(
                    job["input_rights_snapshot_sha256"]
                ),
                retention_class=str(job["retention_class"]),
                creation_run_id=str(job["creation_run_id"]),
                retrieval_decision="approved",
                evidence_ref=str(job["evidence_ref"]),
                receipt_authority=self.receipt_authority,
            )
        except CorpusObjectError as exc:
            if guarded_storage.guard_error is not None:
                raise guarded_storage.guard_error from exc
            raise TrustedLaptopExecutionError(
                exc.code,
                "manifest",
                exc.next_action,
            ) from exc
        boundary_guard(key)
        return (
            self._persist_receipt(
                receipt,
                operation_id="trusted-laptop-manifest-"
                + _digest({"job_id": job["job_id"], "object_key": key}),
            ),
            key,
        )

    def _result(
        self,
        *,
        job: Mapping[str, object],
        output_receipt: Mapping[str, object],
        manifest_receipt: Mapping[str, object],
        output_key: str,
        manifest_key: str,
        output_sha256: str,
        output_size: int,
        metrics: Mapping[str, float],
        completed_at: str | None = None,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": 1,
            "record_type": "trusted_laptop_result",
            "result_id": "result_placeholder",
            "job_id": job["job_id"],
            "job_contract_sha256": _job_contract_sha256(job),
            "source_id": job["source_id"],
            "asset_id": job["asset_id"],
            "transformation_id": job["transformation_id"],
            "input_receipt_id": job["input_receipt_id"],
            "output_receipt_id": output_receipt["receipt_id"],
            "manifest_receipt_id": manifest_receipt["receipt_id"],
            "output_object_key": output_key,
            "manifest_object_key": manifest_key,
            "output_sha256": output_sha256,
            "output_byte_size": output_size,
            "rights_snapshot_sha256": job[
                "input_rights_snapshot_sha256"
            ],
            "privacy_snapshot_sha256": job["privacy_snapshot_sha256"],
            "retention_class": job["retention_class"],
            "retrieval_decision": "approved",
            "redaction_state": job["redaction_state"],
            "cpu_seconds": metrics["cpu_seconds"],
            "peak_memory_bytes": metrics["peak_memory_bytes"],
            "working_disk_bytes": metrics["working_disk_bytes"],
            "elapsed_seconds": metrics["elapsed_seconds"],
            "evidence_ref": job["evidence_ref"],
            "completed_at": (
                _utc_text(self._now())
                if completed_at is None
                else completed_at
            ),
        }
        value["result_id"] = _result_id(value)
        return _validate_result(value)

    def _latest_checkpoint(
        self,
        *,
        job: Mapping[str, object],
    ) -> dict[str, object] | None:
        try:
            value = self.control_plane.get_latest_checkpoint(
                str(job["job_id"])
            )
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "pairing_disconnected",
                "pairing",
                "Resume this exact lease after outbound pairing is restored.",
            ) from exc
        if value is None:
            return None
        try:
            checkpoint = _validate_checkpoint(value)
        except TrustedLaptopWorkerError as exc:
            raise TrustedLaptopExecutionError(
                "resume_checkpoint_invalid",
                "checkpoint",
                "Hold this exact job and reconcile its durable checkpoint.",
                required_authority_class="corpus_operator",
            ) from exc
        if (
            checkpoint["job_id"] != job["job_id"]
            or checkpoint["input_object_key"] != job["input_object_key"]
            or checkpoint["job_contract_sha256"]
            != _job_contract_sha256(job)
        ):
            raise TrustedLaptopExecutionError(
                "resume_checkpoint_mismatch",
                "checkpoint",
                "Hold this exact job and reconcile its durable checkpoint.",
                required_authority_class="corpus_operator",
            )
        return checkpoint

    def _checkpoint_receipt(
        self,
        *,
        job: Mapping[str, object],
        receipt_id: object,
        object_key: object,
        object_kind: str,
    ) -> dict[str, object]:
        if not isinstance(receipt_id, str) or not isinstance(object_key, str):
            raise TrustedLaptopExecutionError(
                "resume_checkpoint_incomplete",
                "checkpoint",
                "Keep this job blocked until its exact receipt is durable.",
            )
        try:
            value = self.receipt_authority.get_corpus_receipt(receipt_id)
            if value is None:
                raise TrustedLaptopExecutionError(
                    "resume_receipt_missing",
                    "checkpoint",
                    "Keep this job blocked until its exact receipt is durable.",
                )
            receipt = validate_object_receipt(value)
        except TrustedLaptopExecutionError:
            raise
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "resume_receipt_invalid",
                "checkpoint",
                "Hold this exact job and reconcile its durable receipt.",
                required_authority_class="corpus_operator",
            ) from exc
        expected = {
            "receipt_id": receipt_id,
            "object_key": object_key,
            "object_kind": object_kind,
            "source_id": job["source_id"],
            "asset_id": job["asset_id"],
            "rights_snapshot_sha256": job[
                "input_rights_snapshot_sha256"
            ],
            "retention_class": job["retention_class"],
            "retrieval_decision": "approved",
        }
        if object_kind == "derived":
            expected["transformation_id"] = job["transformation_id"]
        if any(receipt.get(key) != child for key, child in expected.items()):
            raise TrustedLaptopExecutionError(
                "resume_receipt_mismatch",
                "checkpoint",
                "Hold this exact job and reconcile its immutable receipt.",
                required_authority_class="corpus_operator",
            )
        self._assert_not_tombstoned(
            object_key,
            code=(
                "manifest_object_tombstoned"
                if object_kind == "manifest"
                else "output_object_tombstoned"
            ),
        )
        try:
            head = self.object_store.head_object(object_key)
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "resume_exact_head_failed",
                "checkpoint",
                "Retry exact-key resume verification without listing objects.",
            ) from exc
        if not _head_matches(
            head,
            byte_size=int(receipt["byte_size"]),
            media_type=str(receipt["media_type"]),
            sha256=str(receipt["sha256"]),
        ):
            raise TrustedLaptopExecutionError(
                "resume_object_mismatch",
                "checkpoint",
                "Hold this exact object and reconcile its immutable receipt.",
                required_authority_class="corpus_operator",
            )
        return receipt

    def _run_claim(
        self,
        *,
        capability: Mapping[str, object],
        pairing: Mapping[str, object],
        lease: Mapping[str, object],
        job: Mapping[str, object],
    ) -> dict[str, object]:
        self._assert_capability_covers_job(capability, job)
        self._assert_capability_current(capability)
        try:
            completed = self.control_plane.get_completed_result(
                str(job["job_id"])
            )
        except Exception as exc:
            raise TrustedLaptopExecutionError(
                "pairing_disconnected",
                "pairing",
                "Resume this exact lease after outbound pairing is restored.",
            ) from exc
        if completed is not None:
            result = _validate_result(completed)
            if (
                result["job_id"] != job["job_id"]
                or result["job_contract_sha256"]
                != _job_contract_sha256(job)
                or result["transformation_id"] != job["transformation_id"]
                or result["input_receipt_id"] != job["input_receipt_id"]
                or result["source_id"] != job["source_id"]
                or result["asset_id"] != job["asset_id"]
                or result["rights_snapshot_sha256"]
                != job["input_rights_snapshot_sha256"]
                or result["privacy_snapshot_sha256"]
                != job["privacy_snapshot_sha256"]
                or result["retention_class"] != job["retention_class"]
                or result["redaction_state"] != job["redaction_state"]
                or result["evidence_ref"] != job["evidence_ref"]
            ):
                raise TrustedLaptopExecutionError(
                    "completed_result_conflict",
                    "result",
                    "Hold this exact job and reconcile its completed result.",
                    required_authority_class="corpus_operator",
                )
            self._heartbeat_only(
                pairing=pairing,
                lease=lease,
                job=job,
            )
            self._assert_capability_current(capability)
            self._current_authority(
                job=job, stage="before_completed_resume"
            )
            self._assert_not_tombstoned(
                str(job["input_object_key"]),
                code="input_object_tombstoned",
            )
            self._input_receipt(job)
            self._verify_input_head(job=job)
            output_receipt = self._checkpoint_receipt(
                job=job,
                receipt_id=result["output_receipt_id"],
                object_key=result["output_object_key"],
                object_kind="derived",
            )
            manifest_receipt = self._checkpoint_receipt(
                job=job,
                receipt_id=result["manifest_receipt_id"],
                object_key=result["manifest_object_key"],
                object_kind="manifest",
            )
            if (
                output_receipt["sha256"] != result["output_sha256"]
                or output_receipt["byte_size"]
                != result["output_byte_size"]
            ):
                raise TrustedLaptopExecutionError(
                    "completed_result_conflict",
                    "result",
                    "Hold this exact job and reconcile its completed result.",
                    required_authority_class="corpus_operator",
                )
            return self._result(
                job=job,
                output_receipt=output_receipt,
                manifest_receipt=manifest_receipt,
                output_key=str(result["output_object_key"]),
                manifest_key=str(result["manifest_object_key"]),
                output_sha256=str(result["output_sha256"]),
                output_size=int(result["output_byte_size"]),
                metrics={
                    field: float(result[field])
                    for field in (
                        "cpu_seconds",
                        "peak_memory_bytes",
                        "working_disk_bytes",
                        "elapsed_seconds",
                    )
                },
                completed_at=str(result["completed_at"]),
            )

        resume = self._latest_checkpoint(job=job)
        if resume is None:
            self._maintain(
                pairing=pairing, lease=lease, job=job, stage="claimed"
            )
        else:
            self._heartbeat_only(
                pairing=pairing,
                lease=lease,
                job=job,
            )
        self._current_authority(job=job, stage="after_claim")
        self._current_authority(job=job, stage="before_input")
        self._assert_not_tombstoned(
            str(job["input_object_key"]),
            code="input_object_tombstoned",
        )
        input_receipt = self._input_receipt(job)
        self._verify_input_head(job=job)
        started_at = self._now()
        metrics: dict[str, float] | None = None
        output_size: int | None = None
        output_sha256: str | None = None
        output_key: str | None = None
        output_receipt: dict[str, object] | None = None
        manifest_key: str | None = None
        manifest_receipt: dict[str, object] | None = None
        if resume is not None and resume["stage"] in {
            "transform_verified",
            "output_verified",
            "manifest_verified",
        }:
            output_size = int(resume["output_byte_size"])
            output_sha256 = str(resume["output_sha256"])
            output_key = str(resume["output_object_key"])
            metrics = {
                field: float(resume[field])
                for field in (
                    "cpu_seconds",
                    "peak_memory_bytes",
                    "working_disk_bytes",
                    "elapsed_seconds",
                )
            }
        if resume is not None and resume["stage"] in {
            "output_verified",
            "manifest_verified",
        }:
            output_receipt = self._checkpoint_receipt(
                job=job,
                receipt_id=resume["output_receipt_id"],
                object_key=resume["output_object_key"],
                object_kind="derived",
            )
            if (
                output_receipt["sha256"] != output_sha256
                or output_receipt["byte_size"] != output_size
            ):
                raise TrustedLaptopExecutionError(
                    "resume_receipt_mismatch",
                    "checkpoint",
                    "Hold this exact job and reconcile its immutable receipt.",
                    required_authority_class="corpus_operator",
                )
        if resume is not None and resume["stage"] == "manifest_verified":
            manifest_key = str(resume["manifest_object_key"])
            manifest_receipt = self._checkpoint_receipt(
                job=job,
                receipt_id=resume["manifest_receipt_id"],
                object_key=resume["manifest_object_key"],
                object_kind="manifest",
            )
            self._heartbeat_only(
                pairing=pairing,
                lease=lease,
                job=job,
            )
            self._assert_elapsed(job=job, started_at=started_at)
            self._current_authority(
                job=job, stage="before_resume_complete"
            )
            if (
                output_receipt is None
                or output_key is None
                or output_sha256 is None
                or output_size is None
                or metrics is None
                or manifest_receipt is None
                or manifest_key is None
            ):
                raise TrustedLaptopExecutionError(
                    "resume_checkpoint_incomplete",
                    "checkpoint",
                    "Keep this job blocked until its exact resume facts are complete.",
                )
            return self._result(
                job=job,
                output_receipt=output_receipt,
                manifest_receipt=manifest_receipt,
                output_key=output_key,
                manifest_key=manifest_key,
                output_sha256=output_sha256,
                output_size=output_size,
                metrics=metrics,
            )
        cache = _DisposableCache(
            self.cache_root,
            pairing_id=str(pairing["pairing_id"]),
            lease_id=str(lease["lease_id"]),
            job_id=str(job["job_id"]),
            lease_expires_at=str(lease["expires_at"]),
        )
        self._active_cache = cache
        try:
            if output_receipt is None:
                self._heartbeat_only(
                    pairing=pairing,
                    lease=lease,
                    job=job,
                )
                self._assert_capability_current(capability)
                self._assert_elapsed(job=job, started_at=started_at)
                self._current_authority(
                    job=job, stage="at_input_download"
                )
                self._assert_not_tombstoned(
                    str(job["input_object_key"]),
                    code="input_object_tombstoned",
                )
                self._download_and_verify(job=job, path=cache.input_path)
                if resume is not None and resume["stage"] == "transform_verified":
                    self._heartbeat_only(
                        pairing=pairing,
                        lease=lease,
                        job=job,
                    )
                else:
                    self._maintain(
                        pairing=pairing,
                        lease=lease,
                        job=job,
                        stage="input_verified",
                    )
                    self._maintain(
                        pairing=pairing,
                        lease=lease,
                        job=job,
                        stage="transform_started",
                    )
                self._current_authority(
                    job=job, stage="before_transform"
                )
                self._assert_capability_current(capability)
                self._assert_not_tombstoned(
                    str(job["input_object_key"]),
                    code="input_object_tombstoned",
                )
                (
                    observed_output_size,
                    observed_output_sha256,
                    observed_metrics,
                ) = self._transform(
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    cache=cache,
                    started_at=started_at,
                )
                observed_output_key = derived_object_key(
                    str(job["namespace_prefix"]),
                    str(job["source_id"]),
                    str(job["asset_id"]),
                    str(job["transformation_id"]),
                    observed_output_sha256,
                )
                if (
                    resume is not None
                    and resume["stage"] == "transform_verified"
                    and (
                        observed_output_size != output_size
                        or observed_output_sha256 != output_sha256
                        or observed_output_key != output_key
                    )
                ):
                    raise TrustedLaptopExecutionError(
                        "transformation_resume_mismatch",
                        "checkpoint",
                        "Hold this nondeterministic output and review the exact transform.",
                        required_authority_class="corpus_operator",
                    )
                output_size = observed_output_size
                output_sha256 = observed_output_sha256
                output_key = observed_output_key
                metrics = observed_metrics
                transform_progress: dict[str, object] = {
                    "output_object_key": output_key,
                    "output_sha256": output_sha256,
                    "output_byte_size": output_size,
                    "cpu_seconds": metrics["cpu_seconds"],
                    "peak_memory_bytes": metrics["peak_memory_bytes"],
                    "working_disk_bytes": metrics["working_disk_bytes"],
                    "elapsed_seconds": metrics["elapsed_seconds"],
                }
                self._maintain(
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    stage="transform_verified",
                    progress=transform_progress,
                )
                self._current_authority(
                    job=job, stage="before_create"
                )
                self._assert_not_tombstoned(
                    str(job["input_object_key"]),
                    code="input_object_tombstoned_before_create",
                )
                output_boundary = self._create_boundary_guard(
                    capability=capability,
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    started_at=started_at,
                    authority_stage="at_output_create",
                )
                output_receipt, output_key = self._create_output(
                    job=job,
                    cache=cache,
                    output_size=output_size,
                    output_sha256=output_sha256,
                    output_key=output_key,
                    storage=_GuardedCreateStorage(
                        self.object_store,
                        before_create=output_boundary,
                    ),
                    after_create=output_boundary,
                )
                output_progress = {
                    **transform_progress,
                    "output_receipt_id": output_receipt["receipt_id"],
                }
                self._maintain(
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    stage="output_verified",
                    progress=output_progress,
                )
            if (
                output_receipt is None
                or output_key is None
                or output_sha256 is None
                or output_size is None
                or metrics is None
            ):
                raise TrustedLaptopExecutionError(
                    "resume_checkpoint_incomplete",
                    "checkpoint",
                    "Keep this job blocked until its exact resume facts are complete.",
                )
            self._current_authority(job=job, stage="before_manifest")
            self._assert_not_tombstoned(
                str(job["input_object_key"]),
                code="input_object_tombstoned_before_create",
            )
            self._assert_not_tombstoned(
                output_key, code="output_object_tombstoned"
            )

            def manifest_guard_factory(
                _manifest_key: str,
            ) -> Callable[[str], None]:
                return self._create_boundary_guard(
                    capability=capability,
                    pairing=pairing,
                    lease=lease,
                    job=job,
                    started_at=started_at,
                    authority_stage="at_manifest_create",
                    dependent_keys=(output_key,),
                )

            manifest_receipt, manifest_key = self._create_manifest(
                job=job,
                cache=cache,
                input_receipt=input_receipt,
                output_receipt=output_receipt,
                boundary_guard_factory=manifest_guard_factory,
            )
            manifest_progress = {
                "output_object_key": output_key,
                "output_receipt_id": output_receipt["receipt_id"],
                "manifest_object_key": manifest_key,
                "manifest_receipt_id": manifest_receipt["receipt_id"],
                "output_sha256": output_sha256,
                "output_byte_size": output_size,
                "cpu_seconds": metrics["cpu_seconds"],
                "peak_memory_bytes": metrics["peak_memory_bytes"],
                "working_disk_bytes": metrics["working_disk_bytes"],
                "elapsed_seconds": metrics["elapsed_seconds"],
            }
            self._maintain(
                pairing=pairing,
                lease=lease,
                job=job,
                stage="manifest_verified",
                progress=manifest_progress,
            )
            return self._result(
                job=job,
                output_receipt=output_receipt,
                manifest_receipt=manifest_receipt,
                output_key=output_key,
                manifest_key=manifest_key,
                output_sha256=output_sha256,
                output_size=output_size,
                metrics=metrics,
            )
        finally:
            self._active_cache = None
            cache.close()

    def run_once(
        self,
        capability_value: Mapping[str, object],
    ) -> dict[str, object] | None:
        """Pair outbound, claim no more than one job, and fail closed."""

        if not self._run_lock.acquire(blocking=False):
            raise TrustedLaptopWorkerError(
                "worker already has an active run"
            )
        try:
            return self._run_once_serialized(capability_value)
        finally:
            self._run_lock.release()

    def _run_once_serialized(
        self,
        capability_value: Mapping[str, object],
    ) -> dict[str, object] | None:
        capability = _validate_capability(capability_value)
        now = self._now()
        if (
            _time(capability["issued_at"], "issued_at") > now
            or _time(capability["expires_at"], "expires_at") <= now
        ):
            raise TrustedLaptopWorkerError("worker capability is not current")
        # Clean only marker-bound remnants before any outbound work.  This is
        # what removes a previous process's disposable cache after restart.
        reap_stale_disposable_caches(self.cache_root, now=now)
        try:
            pairing = _validate_pairing(
                self.control_plane.pair_outbound(capability, now=now)
            )
            if (
                _time(pairing["paired_at"], "paired_at") > now
                or _time(pairing["expires_at"], "expires_at") <= now
            ):
                raise TrustedLaptopWorkerError("outbound pairing is not current")
            reservation = self.control_plane.claim_one(
                pairing, capability, now=self._now()
            )
        except TrustedLaptopWorkerError:
            raise
        except Exception as exc:
            raise TrustedLaptopWorkerError(
                "outbound control-plane pairing is unavailable"
            ) from exc
        if reservation is None:
            return None
        if (
            not isinstance(reservation, Mapping)
            or set(reservation) != {"job", "lease"}
            or not isinstance(reservation["job"], Mapping)
            or not isinstance(reservation["lease"], Mapping)
        ):
            raise TrustedLaptopWorkerError("invalid one-job reservation")
        try:
            job = _validate_job(reservation["job"])
            lease = _validate_lease(
                reservation["lease"],
                pairing=pairing,
                job=job,
                now=self._now(),
            )
        except TrustedLaptopWorkerError as exc:
            # A malformed job cannot safely contribute its unvalidated IDs to
            # a blocker.  Reject the reservation and let the control plane
            # quarantine its own strict source record.
            raise TrustedLaptopWorkerError(
                "unsafe claimed job or lease"
            ) from exc
        try:
            result = self._run_claim(
                capability=capability,
                pairing=pairing,
                lease=lease,
                job=job,
            )
            self.control_plane.complete(result)
            return result
        except TrustedLaptopExecutionError as error:
            blocker = _blocker(
                pairing=pairing,
                lease=lease,
                job=job,
                error=error,
                now=self._now(),
            )
            try:
                self.control_plane.block(blocker)
            except Exception:
                pass
            try:
                self.control_plane.release(
                    str(lease["lease_id"]),
                    str(pairing["pairing_id"]),
                    reason=error.code,
                )
            except Exception:
                pass
            return None
        except Exception:
            error = TrustedLaptopExecutionError(
                "worker_failed",
                "worker",
                "Resume this exact job after reviewing sanitized worker state.",
            )
            blocker = _blocker(
                pairing=pairing,
                lease=lease,
                job=job,
                error=error,
                now=self._now(),
            )
            try:
                self.control_plane.block(blocker)
            except Exception:
                pass
            try:
                self.control_plane.release(
                    str(lease["lease_id"]),
                    str(pairing["pairing_id"]),
                    reason=error.code,
                )
            except Exception:
                pass
            return None
