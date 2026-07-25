"""Allowlisted, content-free observability records and exact-head evidence.

Every durable diagnostic record in this repository is built here. The
serializer is deliberately fail-closed: unknown fields, bytes, exception
objects, and nested provider payloads raise instead of being stringified,
so no logger, metric, trace, issue body, or evidence file can ever carry
response bodies, source prose, personal details, credentials, endpoints,
or machine-local paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.redaction import contains_secret_like_text, sanitize


UTC = timezone.utc

LANES = (
    "portable",
    "network-acquisition",
    "trusted-vm",
    "trusted-laptop",
    "object-storage",
    "deploy",
    "sandbox",
)
OUTCOME_CODES = (
    "succeeded",
    "checkpoint_committed",
    "blocked_on_human",
    "bound_exhausted",
    "rate_limited",
    "retry_scheduled",
    "lease_expired",
    "failed_closed",
    "held_by_billing",
    "skipped_not_run",
)
SEVERITIES = ("info", "warning", "blocked", "error")
LANE_STATUSES = ("passed", "failed", "held", "skipped")
HELD_REASONS = (
    "billing_limit",
    "spending_limit",
    "private_actions_disabled",
    "awaiting_human_approval",
)
ARTIFACT_KINDS = (
    "evidence_manifest",
    "sanitized_manifest",
    "selected_log",
    "schema_validation",
    "screenshot_digest",
)
BOUND_FIELDS = ("requests", "bytes", "pages", "retries", "elapsed_seconds")
METRIC_DEFINITIONS = {
    "request_total": ("counter", "count"),
    "byte_total": ("counter", "bytes"),
    "page_total": ("counter", "count"),
    "retry_total": ("counter", "count"),
    "rate_limit_wait_seconds": ("counter", "seconds"),
    "lease_active": ("gauge", "count"),
    "checkpoint_total": ("counter", "count"),
    "queue_age_seconds": ("gauge", "seconds"),
    "storage_object_total": ("gauge", "count"),
    "storage_byte_total": ("gauge", "bytes"),
    "transformation_total": ("counter", "count"),
    "deletion_total": ("counter", "count"),
    "blocker_open": ("gauge", "count"),
}
METRIC_DIMENSIONS = ("source_id", "worker_id", "lane", "operation")

MAX_TEXT_LENGTH = 512
_ALLOWED_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_OPERATION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_POLICY_VERSION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[a-z0-9][a-z0-9._-]{0,127}$")
_LANE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_WORKER_ID = re.compile(r"^worker_[a-z0-9][a-z0-9._-]{0,63}$")
_STABLE_ID = re.compile(
    r"^(?:(?:asset|contribution|discovery_run|job|lease|object|rights|run"
    r"|source|worker)_[a-z0-9][a-z0-9._-]{0,127}"
    r"|[a-z][a-z0-9]*(?:-[a-z0-9]+)*)$"
)
_EVIDENCE_REFERENCE_ID = re.compile(r"^evidence_reference_[0-9a-f]{24}$")
_EMBEDDED_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")


class ObservabilityError(ValueError):
    """Raised when diagnostic output could carry unsafe or unbounded data."""


def _carries_unsafe_url(value: str, *, environ: Mapping[str, str] | None) -> bool:
    """Detect signed or credential-bearing URLs embedded inside longer text."""

    return any(
        sanitize(match.group(0), environ=environ) != match.group(0)
        for match in _EMBEDDED_URL.finditer(value)
    )


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def validate_record(name: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an already-allowlisted record against its strict v1 schema."""

    if not isinstance(record, Mapping):
        raise ObservabilityError(f"{name} record must be an object")
    candidate = copy.deepcopy(dict(record))
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            candidate
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise ObservabilityError(
            f"{name} record does not match the strict schema"
        ) from error
    return candidate


def safe_text(value: Any, *, field: str) -> str:
    """Return `value` when it is bounded, content-free text, else fail closed."""

    if not isinstance(value, str) or isinstance(value, bool):
        raise ObservabilityError(f"{field} must be text")
    if not value or len(value) > MAX_TEXT_LENGTH:
        raise ObservabilityError(f"{field} is empty or exceeds the text bound")
    if _CONTROL_CHARACTER.search(value):
        raise ObservabilityError(f"{field} carries control characters")
    if sanitize(value, environ={}) != value or _carries_unsafe_url(value, environ={}):
        raise ObservabilityError(f"{field} carries private or machine-local data")
    if contains_secret_like_text(value):
        raise ObservabilityError(f"{field} resembles a credential")
    return value


def safe_serialize(value: Any, *, field: str = "record") -> Any:
    """Return a JSON-safe projection, refusing anything not on the allowlist.

    Mappings, sequences, booleans, finite numbers, `None`, and bounded
    content-free text are copied. Bytes, exception objects, sets, dates,
    and every other object raise `ObservabilityError` rather than being
    coerced to text.
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return safe_text(value, field=field)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ObservabilityError(f"{field} must be a finite number")
        return value
    if isinstance(value, Mapping):
        serialized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not _ALLOWED_KEY.match(key):
                raise ObservabilityError(f"{field} carries an unsupported field name")
            serialized[key] = safe_serialize(child, field=f"{field}.{key}")
        return serialized
    if isinstance(value, (list, tuple)):
        return [
            safe_serialize(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ObservabilityError(
        f"{field} is not an allowlisted observability value and is never stringified"
    )


def secret_presence(
    secret_names: Iterable[str], *, environ: Mapping[str, str] | None = None
) -> tuple[dict[str, str], ...]:
    """Report only the name and presence of separately authorized secrets."""

    source = os.environ if environ is None else environ
    states: list[dict[str, str]] = []
    for name in secret_names:
        if not isinstance(name, str) or not _SECRET_NAME.match(name):
            raise ObservabilityError("secret name is not an allowlisted identifier")
        present = bool(str(source.get(name, "")).strip())
        states.append(
            {"secret_name": name, "state": "present" if present else "missing"}
        )
    return tuple(states)


def assert_selected_log_is_safe(
    lines: Iterable[str], *, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Return log lines chosen for evidence, or fail closed on unsafe content."""

    safe: list[str] = []
    for index, line in enumerate(lines):
        if not isinstance(line, str) or isinstance(line, bool):
            raise ObservabilityError(f"selected log line {index} is not text")
        if len(line) > MAX_TEXT_LENGTH or _CONTROL_CHARACTER.search(line):
            raise ObservabilityError(f"selected log line {index} is unbounded")
        if sanitize(line, environ=environ) != line or _carries_unsafe_url(
            line, environ=environ
        ):
            raise ObservabilityError(
                f"selected log line {index} carries private or machine-local data"
            )
        if contains_secret_like_text(line):
            raise ObservabilityError(
                f"selected log line {index} resembles a credential"
            )
        safe.append(line)
    return tuple(safe)


def format_instant(value: Any, *, field: str) -> str:
    if not isinstance(value, datetime):
        raise ObservabilityError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservabilityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_identifier(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}_{_digest(payload)[:24]}"


def _bound_consumption(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(BOUND_FIELDS):
        raise ObservabilityError(
            "bound_consumption must report exactly the bounded consumption fields"
        )
    bounds: dict[str, Any] = {}
    for field in BOUND_FIELDS:
        amount = value[field]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ObservabilityError(f"bound_consumption.{field} must be a number")
        if isinstance(amount, float) and not math.isfinite(amount):
            raise ObservabilityError(f"bound_consumption.{field} must be finite")
        if amount < 0:
            raise ObservabilityError(f"bound_consumption.{field} must not be negative")
        bounds[field] = amount
    return bounds


def build_envelope(
    *,
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
    outcome_code: str,
    evidence_time: datetime,
) -> dict[str, Any]:
    if not isinstance(operation, str) or not _OPERATION.match(operation):
        raise ObservabilityError("operation must be a bounded snake_case code")
    if isinstance(subject_ids, (str, bytes)) or not isinstance(subject_ids, Sequence):
        raise ObservabilityError("subject_ids must be a sequence of stable IDs")
    identifiers: list[str] = []
    for subject_id in subject_ids:
        if not isinstance(subject_id, str) or not _STABLE_ID.match(subject_id):
            raise ObservabilityError("subject_ids must contain stable IDs only")
        if subject_id not in identifiers:
            identifiers.append(subject_id)
    if not identifiers:
        raise ObservabilityError("subject_ids must identify at least one subject")
    if lane not in LANES:
        raise ObservabilityError("lane is not a declared lane")
    if not isinstance(policy_version, str) or not _POLICY_VERSION.match(policy_version):
        raise ObservabilityError("policy_version must be a bounded snake_case code")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ObservabilityError("attempt must be a positive integer")
    if outcome_code not in OUTCOME_CODES:
        raise ObservabilityError("outcome_code is not a declared outcome")
    return {
        "operation": operation,
        "subject_ids": identifiers,
        "lane": lane,
        "policy_version": policy_version,
        "attempt": attempt,
        "bound_consumption": _bound_consumption(bound_consumption),
        "outcome_code": outcome_code,
        "evidence_time": format_instant(evidence_time, field="evidence_time"),
    }


def build_event(
    *,
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
    outcome_code: str,
    severity: str,
    evidence_time: datetime,
    secret_names: Iterable[str] = (),
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build one sanitized observability event."""

    if severity not in SEVERITIES:
        raise ObservabilityError("severity is not a declared severity")
    payload = build_envelope(
        operation=operation,
        subject_ids=subject_ids,
        lane=lane,
        policy_version=policy_version,
        attempt=attempt,
        bound_consumption=bound_consumption,
        outcome_code=outcome_code,
        evidence_time=evidence_time,
    )
    payload["severity"] = severity
    payload["secret_states"] = [
        dict(state) for state in secret_presence(secret_names, environ=environ)
    ]
    record = safe_serialize(
        {
            "schema_version": 1,
            "record_type": "observability_event",
            **payload,
        },
        field="observability_event",
    )
    record["event_id"] = record_identifier("observability_event", record)
    return validate_record("observability-event", record)


def build_metric(
    *,
    metric_name: str,
    value: float,
    dimensions: Mapping[str, Any],
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
    outcome_code: str,
    evidence_time: datetime,
) -> dict[str, Any]:
    """Build one content-free, low-cardinality metric sample."""

    if metric_name not in METRIC_DEFINITIONS:
        raise ObservabilityError("metric_name is not a declared metric")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservabilityError("metric value must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ObservabilityError("metric value must be finite")
    if value < 0:
        raise ObservabilityError("metric value must not be negative")
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(
        METRIC_DIMENSIONS
    ):
        raise ObservabilityError(
            "metric dimensions must be exactly the allowlisted dimensions"
        )
    source_id = dimensions["source_id"]
    if source_id is not None and (
        not isinstance(source_id, str)
        or len(source_id) > 64
        or not _SOURCE_ID.match(source_id)
    ):
        raise ObservabilityError("metric source_id dimension is high-cardinality")
    worker_id = dimensions["worker_id"]
    if worker_id is not None and (
        not isinstance(worker_id, str) or not _WORKER_ID.match(worker_id)
    ):
        raise ObservabilityError("metric worker_id dimension is high-cardinality")
    if dimensions["lane"] != lane:
        raise ObservabilityError("metric lane dimension must match the record lane")
    if dimensions["operation"] != operation:
        raise ObservabilityError(
            "metric operation dimension must match the record operation"
        )

    metric_kind, unit = METRIC_DEFINITIONS[metric_name]
    payload = build_envelope(
        operation=operation,
        subject_ids=subject_ids,
        lane=lane,
        policy_version=policy_version,
        attempt=attempt,
        bound_consumption=bound_consumption,
        outcome_code=outcome_code,
        evidence_time=evidence_time,
    )
    payload.update(
        {
            "metric_name": metric_name,
            "metric_kind": metric_kind,
            "value": value,
            "unit": unit,
            "dimensions": {
                "source_id": source_id,
                "worker_id": worker_id,
                "lane": lane,
                "operation": operation,
            },
        }
    )
    record = safe_serialize(
        {
            "schema_version": 1,
            "record_type": "observability_metric",
            **payload,
        },
        field="observability_metric",
    )
    record["metric_id"] = record_identifier("observability_metric", record)
    return validate_record("observability-metric", record)


def build_evidence_reference(
    *,
    commit: str,
    observed_head: str,
    lane_status: str,
    artifact_kind: str,
    artifact_sha256: str,
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
    outcome_code: str,
    evidence_time: datetime,
) -> dict[str, Any]:
    """Bind one artifact digest to the exact commit whose head produced it."""

    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise ObservabilityError("commit must be a full lowercase 40-hex SHA")
    if not isinstance(observed_head, str) or not _COMMIT.match(observed_head):
        raise ObservabilityError("exact-head state could not be established")
    if observed_head != commit:
        raise ObservabilityError(
            "evidence is not exact-head: the observed head differs from the commit"
        )
    if lane_status not in ("passed", "failed"):
        raise ObservabilityError(
            "only a lane that actually ran can carry an evidence reference"
        )
    if artifact_kind not in ARTIFACT_KINDS:
        raise ObservabilityError("artifact_kind is not a declared artifact kind")
    if not isinstance(artifact_sha256, str) or not _SHA256.match(artifact_sha256):
        raise ObservabilityError("artifact_sha256 must be a lowercase 64-hex digest")

    payload = build_envelope(
        operation=operation,
        subject_ids=subject_ids,
        lane=lane,
        policy_version=policy_version,
        attempt=attempt,
        bound_consumption=bound_consumption,
        outcome_code=outcome_code,
        evidence_time=evidence_time,
    )
    payload.update(
        {
            "commit": commit,
            "head_state": "exact_head",
            "lane_status": lane_status,
            "artifact_kind": artifact_kind,
            "artifact_sha256": artifact_sha256,
        }
    )
    record = safe_serialize(
        {
            "schema_version": 1,
            "record_type": "evidence_reference",
            **payload,
        },
        field="evidence_reference",
    )
    record["evidence_reference_id"] = record_identifier("evidence_reference", record)
    return validate_record("evidence-reference", record)


def _lane_result(
    result: Any, references: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise ObservabilityError("lane result must be an object")
    unexpected = set(result) - {
        "lane_id",
        "lane",
        "status",
        "held_reason",
        "evidence_reference_id",
    }
    if unexpected:
        raise ObservabilityError("lane result carries an unsupported field")
    lane_id = result.get("lane_id")
    if not isinstance(lane_id, str) or not _LANE_ID.match(lane_id):
        raise ObservabilityError("lane_id must be a bounded kebab-case code")
    lane = result.get("lane")
    if lane not in LANES:
        raise ObservabilityError("lane result names an undeclared lane")
    status = result.get("status")
    if status not in LANE_STATUSES:
        raise ObservabilityError("lane status is not a declared lane status")
    held_reason = result.get("held_reason")
    reference_id = result.get("evidence_reference_id")
    if held_reason is not None and held_reason not in HELD_REASONS:
        raise ObservabilityError("held_reason is not a declared hold reason")
    if held_reason is not None and status != "held":
        raise ObservabilityError(
            "a lane withheld by billing or approval is held, never passed or failed"
        )
    if status == "held":
        if held_reason is None:
            raise ObservabilityError("a held lane must record why it is held")
        if reference_id is not None:
            raise ObservabilityError("held CI is never run evidence")
    if status == "skipped" and reference_id is not None:
        raise ObservabilityError("a lane that did not run carries no evidence")
    if status in ("passed", "failed"):
        if not isinstance(reference_id, str) or not _EVIDENCE_REFERENCE_ID.match(
            reference_id
        ):
            raise ObservabilityError(
                "a passed or failed lane must reference the evidence it produced"
            )
        reference = references.get(reference_id)
        if reference is None:
            raise ObservabilityError("lane result references unknown evidence")
        if reference["lane"] != lane:
            raise ObservabilityError(
                "evidence may satisfy only the lane it actually ran"
            )
        if reference["lane_status"] != status:
            raise ObservabilityError("evidence contradicts the recorded lane status")
    return {
        "lane_id": lane_id,
        "lane": lane,
        "status": status,
        "held_reason": held_reason,
        "evidence_reference_id": reference_id,
    }


def build_run_manifest(
    *,
    run_id: str,
    commit: str,
    observed_head: str,
    lane_results: Sequence[Mapping[str, Any]],
    evidence_references: Sequence[Mapping[str, Any]] = (),
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
    outcome_code: str,
    evidence_time: datetime,
) -> dict[str, Any]:
    """Build a run manifest that never presents held CI as run evidence."""

    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ObservabilityError("run_id must be a bounded run identifier")
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise ObservabilityError("commit must be a full lowercase 40-hex SHA")
    if not isinstance(observed_head, str) or not _COMMIT.match(observed_head):
        raise ObservabilityError("exact-head state could not be established")
    if observed_head != commit:
        raise ObservabilityError(
            "run manifest is not exact-head: the observed head differs from the commit"
        )
    references: dict[str, Mapping[str, Any]] = {}
    for reference in evidence_references:
        validated = validate_record("evidence-reference", reference)
        if validated["commit"] != commit:
            raise ObservabilityError("evidence reference belongs to another commit")
        references[validated["evidence_reference_id"]] = validated
    if isinstance(lane_results, (str, bytes)) or not isinstance(
        lane_results, Sequence
    ):
        raise ObservabilityError("lane_results must be a sequence")
    results = [_lane_result(result, references) for result in lane_results]
    if not results:
        raise ObservabilityError("a run manifest must record at least one lane")
    statuses = {result["status"] for result in results}
    if statuses == {"passed"}:
        if outcome_code != "succeeded":
            raise ObservabilityError(
                "an all-passed run manifest must record the succeeded outcome"
            )
    elif "failed" in statuses:
        if outcome_code != "failed_closed":
            raise ObservabilityError(
                "a run manifest with a failed lane must fail closed"
            )
    elif outcome_code not in ("held_by_billing", "skipped_not_run", "blocked_on_human"):
        raise ObservabilityError(
            "a run manifest with held or skipped lanes must not claim success"
        )

    payload = build_envelope(
        operation=operation,
        subject_ids=subject_ids,
        lane=lane,
        policy_version=policy_version,
        attempt=attempt,
        bound_consumption=bound_consumption,
        outcome_code=outcome_code,
        evidence_time=evidence_time,
    )
    payload.update(
        {
            "run_id": run_id,
            "commit": commit,
            "head_state": "exact_head",
            "lane_results": results,
        }
    )
    record = safe_serialize(
        {
            "schema_version": 1,
            "record_type": "run_manifest",
            **payload,
        },
        field="run_manifest",
    )
    record["run_manifest_id"] = record_identifier("run_manifest", record)
    return validate_record("run-manifest", record)
