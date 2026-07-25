"""Rights-aware OCR, transcription, and video-understanding workflows.

Each operation has its own versioned transformation profile, its own admission
gate, and its own content-free result contract. Nothing here executes a tool,
uploads content, or retains a prompt, trace, frame, waveform, or source
excerpt: derived content stays in R2 under the most restrictive input rights.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.corpus_objects import (
    CorpusObjectError,
    build_retention_authority,
    validate_object_receipt,
    validate_object_tombstone,
)
from performing_fire_corpus.qualification import (
    QualificationError,
    validate_asset_qualification,
)
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
DERIVED_MEDIA_OPERATIONS = ("ocr", "transcription", "video_understanding")
CONSENT_STATES = ("granted", "not_applicable", "withdrawn")
DELETION_REASON_CODES = (
    "consent_withdrawn",
    "exact_key_deleted",
    "retention_expired",
    "rights_revoked",
    "source_corrected",
    "transformation_replaced",
)
# Rights, consent, and retention are decided for a whole asset, so they reach
# every derivative of that asset. The rest name one exact object.
_ASSET_SCOPED_REASON_CODES = frozenset(
    {"consent_withdrawn", "retention_expired", "rights_revoked"}
)
_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_RESULT_RECORD_TYPES = {
    "ocr": "ocr_result",
    "transcription": "transcription_result",
    "video_understanding": "video_understanding_result",
}
_RESULT_SCHEMA_NAMES = {
    "ocr_result": "ocr-result",
    "transcription_result": "transcription-result",
    "video_understanding_result": "video-understanding-result",
}
_RESULT_ID_PREFIXES = {
    "ocr_result": "ocrresult",
    "transcription_result": "transcriptionresult",
    "video_understanding_result": "videoresult",
}
_SCHEMA_ID = "https://performing-fire-corpus.invalid/schemas/v1/{name}.json"
_RETRIEVAL_ORDER = {"approved": 0, "metadata_only": 1, "blocked": 2}
_DERIVED_RETENTION_CLASSES = frozenset(
    {"project_native_expiring", "selected_derived"}
)
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][a-z0-9.-]+)?$")
_JOB_ID_FIELDS = ("job_id", "job_sha256")
_RESULT_ID_FIELDS = ("result_id", "result_sha256")


class DerivedMediaError(ValueError):
    """Raised when a derived-media contract is unsafe, stale, or inconsistent."""


class DerivedMediaDeletionAuthority(Protocol):
    """Trusted current exact-key deletion boundary for a transformation input."""

    def resolve_tombstone_by_key(
        self, *, object_key: str
    ) -> Mapping[str, Any] | None: ...


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate_schema(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DerivedMediaError(f"{name} record must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise DerivedMediaError(
            f"{name} record does not match the strict schema"
        ) from error
    if sanitize(record, environ={}) != record:
        raise DerivedMediaError(
            f"{name} record contains private or secret-like data"
        )
    return record


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _without(record: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in fields}


def _safe_identifier(value: object, field: str) -> str:
    """Reject free text before it can reach a durable or loggable plan."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise DerivedMediaError(f"{field} is not a bounded identifier")
    return value


def _parse_time(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise DerivedMediaError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise DerivedMediaError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise DerivedMediaError("derived-media clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _version_key(value: str) -> tuple[int, ...]:
    if _VERSION.fullmatch(value) is None:
        raise DerivedMediaError("tool version is not a bounded version string")
    core = re.split(r"[-+]", value, maxsplit=1)[0]
    parts = tuple(int(part) for part in core.split("."))
    return parts + (0,) * (4 - len(parts))


def most_restrictive_retrieval_decision(values: Sequence[str]) -> str:
    """Return the most restrictive retrieval decision in a bounded set."""

    decisions = [str(value) for value in values]
    if not decisions or any(value not in _RETRIEVAL_ORDER for value in decisions):
        raise DerivedMediaError("retrieval decisions must be known and non-empty")
    return max(decisions, key=_RETRIEVAL_ORDER.__getitem__)


def validate_transformation_profile(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one versioned, operation-specific transformation profile."""

    record = _validate_schema("transformation-profile", value)
    if record["output_record_type"] != _RESULT_RECORD_TYPES[str(record["operation"])]:
        raise DerivedMediaError("profile output record must match its operation")
    for name in ("allowed_tool_classes", "allowed_tool_ids", "allowed_languages"):
        values = list(record[name])
        if values != sorted(set(values)):
            raise DerivedMediaError(f"{name} must be unique and sorted")
    if list(record["allowed_input_media_types"]) != sorted(
        set(record["allowed_input_media_types"])
    ):
        raise DerivedMediaError(
            "allowed_input_media_types must be unique and sorted"
        )
    if _version_key(str(record["minimum_tool_version"])) > _version_key(
        str(record["maximum_tool_version"])
    ):
        raise DerivedMediaError("profile tool-version range is inverted")
    bounds = record["resource_bounds"]
    if bounds["maximum_output_bytes"] > bounds["maximum_disk_bytes"]:
        raise DerivedMediaError("profile output bound exceeds its disk bound")
    expected = _digest(_without(record, "profile_sha256"))
    if record["profile_sha256"] != expected:
        raise DerivedMediaError("profile hash does not bind its own facts")
    return record


def build_transformation_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an unhashed profile draft to its canonical content hash."""

    if not isinstance(value, Mapping):
        raise DerivedMediaError("transformation-profile draft must be an object")
    draft = _without(dict(value), "profile_sha256")
    draft["profile_sha256"] = _digest(draft)
    return validate_transformation_profile(draft)


def _current_qualification(
    value: Mapping[str, Any], *, now: datetime
) -> dict[str, Any]:
    try:
        return validate_asset_qualification(value, now=now)
    except QualificationError as error:
        raise DerivedMediaError(
            "asset qualification is not a current operation-specific record"
        ) from error


def _current_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate the input receipt through its own content-binding authority."""

    record = _validate_schema("object-receipt", value)
    try:
        return dict(validate_object_receipt(record))
    except CorpusObjectError as error:
        raise DerivedMediaError(
            "input receipt is not a verified content-bound corpus receipt"
        ) from error


def _current_retention_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """Rebind the retention authority so a tampered decision cannot pass."""

    record = _validate_schema("retention-authority", value)
    try:
        rebound = build_retention_authority(
            authority_id=str(record["authority_id"]),
            source_id=str(record["source_id"]),
            asset_id=str(record["asset_id"]),
            retention_class=str(record["retention_class"]),
            expires_at=str(record["expires_at"]),
            legal_hold_state=str(record["legal_hold_state"]),
            legal_hold_basis_sha256=record["legal_hold_basis_sha256"],
            decided_at=str(record["decided_at"]),
            valid_until=str(record["valid_until"]),
            evidence_ref=str(record["evidence_ref"]),
        )
    except CorpusObjectError as error:
        raise DerivedMediaError(
            "retention authority is not a current hash-bound decision"
        ) from error
    if dict(rebound) != record:
        raise DerivedMediaError(
            "retention authority hash does not bind its own decision"
        )
    return record


def _decision_for(
    qualification: Mapping[str, Any], operation: str
) -> Mapping[str, Any] | None:
    for decision in qualification["operation_decisions"]:
        if decision.get("operation") == operation:
            return decision
    return None


def _deletion_reasons(
    authority: DerivedMediaDeletionAuthority, receipt: Mapping[str, Any]
) -> list[str]:
    """Consult the trusted exact-key deletion boundary, failing closed."""

    object_key = str(receipt["object_key"])
    try:
        tombstone = authority.resolve_tombstone_by_key(object_key=object_key)
    except Exception:  # noqa: BLE001 - an unavailable authority is never permission
        return ["deletion:authority_unavailable"]
    if tombstone is None:
        return []
    record = _validate_schema("object-tombstone", tombstone)
    try:
        validate_object_tombstone(record)
    except CorpusObjectError:
        return ["deletion:authority_invalid"]
    if (
        record["deleted_object_key"] != object_key
        or record["deleted_object_sha256"] != receipt["sha256"]
        or record["source_id"] != receipt["source_id"]
        or record["asset_id"] != receipt["asset_id"]
    ):
        return ["deletion:authority_key_mismatch"]
    return ["deletion:input_tombstoned"]


def evaluate_derived_media_admission(
    *,
    profile: Mapping[str, Any],
    qualification: Mapping[str, Any],
    input_receipt: Mapping[str, Any],
    retention_authority: Mapping[str, Any],
    deletion_authority: DerivedMediaDeletionAuthority,
    tool: Mapping[str, Any],
    language_hint: str | None = None,
    consent_state: str = "not_applicable",
    now: datetime,
) -> dict[str, Any]:
    """Return the fail-closed admission decision for one exact transformation."""

    profile_record = validate_transformation_profile(profile)
    qualification_record = _current_qualification(qualification, now=now)
    receipt = _current_receipt(input_receipt)
    authority = _current_retention_authority(retention_authority)
    operation = str(profile_record["operation"])
    reasons: list[str] = []

    if not isinstance(tool, Mapping) or set(tool) != {
        "tool_id",
        "tool_class",
        "tool_version",
        "contract_version",
    }:
        raise DerivedMediaError("tool selection must use the strict field set")
    if tool["tool_id"] not in profile_record["allowed_tool_ids"]:
        reasons.append("profile:tool_not_allowed")
    if tool["tool_class"] not in profile_record["allowed_tool_classes"]:
        reasons.append("profile:tool_class_not_allowed")
    tool_version = _version_key(str(tool["tool_version"]))
    if tool_version < _version_key(str(profile_record["minimum_tool_version"])):
        reasons.append("profile:tool_version_below_minimum")
    if tool_version > _version_key(str(profile_record["maximum_tool_version"])):
        reasons.append("profile:tool_version_above_maximum")
    if receipt["media_type"] not in profile_record["allowed_input_media_types"]:
        reasons.append("profile:media_type_not_allowed")
    allowed_languages = list(profile_record["allowed_languages"])
    if language_hint is None:
        reasons.append("profile:language_hint_required")
    elif language_hint not in allowed_languages:
        reasons.append("profile:language_not_supported")

    if consent_state not in CONSENT_STATES:
        raise DerivedMediaError("consent state is not a known bounded label")
    if consent_state != "granted" and str(receipt["source_id"]).startswith(
        "project-native-"
    ):
        reasons.append("consent:required")
    if consent_state == "withdrawn":
        reasons.append("consent:withdrawn")

    decision = _decision_for(qualification_record, operation)
    if decision is None:
        reasons.append("rights:decision_missing")
    else:
        if not decision.get("eligible") or decision.get("state") != "approved":
            reasons.append("rights:not_eligible")
        expires_at = decision.get("expires_at")
        if expires_at is None or _parse_time(expires_at, "expires_at") <= now:
            reasons.append("rights:decision_expired")
    if qualification_record["derivative_policy"] != "operation_specific":
        reasons.append("rights:derivative_prohibited")
    if (
        qualification_record["source_id"] != receipt["source_id"]
        or qualification_record["asset_id"] != receipt["asset_id"]
    ):
        reasons.append("rights:asset_mismatch")
    if _parse_time(
        qualification_record["source_governance_expires_at"],
        "source_governance_expires_at",
    ) <= now:
        reasons.append("rights:governance_expired")

    if receipt["object_kind"] != "raw":
        reasons.append("input:not_raw_object")
    if receipt["verification_state"] != "verified":
        reasons.append("input:not_verified")
    if receipt["byte_size"] > profile_record["resource_bounds"]["maximum_input_bytes"]:
        reasons.append("bounds:input_too_large")
    if (
        most_restrictive_retrieval_decision(
            (
                str(receipt["retrieval_decision"]),
                str(profile_record["maximum_retrieval_decision"]),
            )
        )
        == "blocked"
    ):
        reasons.append("retrieval:blocked")

    if (
        authority["source_id"] != receipt["source_id"]
        or authority["asset_id"] != receipt["asset_id"]
    ):
        reasons.append("retention:authority_mismatch")
    if authority["legal_hold_state"] == "active":
        reasons.append("retention:legal_hold")
    if _parse_time(authority["expires_at"], "authority.expires_at") <= now:
        reasons.append("retention:expired")
    if _parse_time(authority["valid_until"], "authority.valid_until") <= now:
        reasons.append("retention:authority_stale")
    if profile_record["retention_class"] not in _DERIVED_RETENTION_CLASSES:
        reasons.append("retention:class_not_derived")

    reasons.extend(_deletion_reasons(deletion_authority, receipt))

    return {
        "operation": operation,
        "profile_id": profile_record["profile_id"],
        "profile_version": profile_record["profile_version"],
        "reasons": sorted(set(reasons)),
        "eligible": not reasons,
    }


def plan_derived_media_job(
    *,
    profile: Mapping[str, Any],
    qualification: Mapping[str, Any],
    input_receipt: Mapping[str, Any],
    retention_authority: Mapping[str, Any],
    deletion_authority: DerivedMediaDeletionAuthority,
    tool: Mapping[str, Any],
    evidence_ref: str,
    language_hint: str | None = None,
    medium_hint: str | None = None,
    consent_state: str = "not_applicable",
    now: datetime,
) -> dict[str, Any]:
    """Queue one exact transformation only when every gate is currently met."""

    admission = evaluate_derived_media_admission(
        profile=profile,
        qualification=qualification,
        input_receipt=input_receipt,
        retention_authority=retention_authority,
        deletion_authority=deletion_authority,
        tool=tool,
        language_hint=language_hint,
        consent_state=consent_state,
        now=now,
    )
    if not admission["eligible"]:
        raise DerivedMediaError(
            "derived-media job is not currently allowed: "
            + ",".join(admission["reasons"])
        )
    profile_record = validate_transformation_profile(profile)
    qualification_record = _current_qualification(qualification, now=now)
    receipt = _current_receipt(input_receipt)
    authority = _current_retention_authority(retention_authority)
    decision = _decision_for(qualification_record, str(profile_record["operation"]))
    if decision is None:
        raise DerivedMediaError("admitted job lost its operation decision")

    draft: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "derived_media_job",
        "operation": profile_record["operation"],
        "profile_id": profile_record["profile_id"],
        "profile_version": profile_record["profile_version"],
        "profile_sha256": profile_record["profile_sha256"],
        "source_id": receipt["source_id"],
        "asset_id": receipt["asset_id"],
        "input_object_key": receipt["object_key"],
        "input_sha256": receipt["sha256"],
        "input_byte_size": receipt["byte_size"],
        "input_media_type": receipt["media_type"],
        "input_receipt_id": receipt["receipt_id"],
        "qualification_id": qualification_record["qualification_id"],
        "qualification_sha256": qualification_record["qualification_sha256"],
        "rights_snapshot_sha256": receipt["rights_snapshot_sha256"],
        "rights_decision_expires_at": decision["expires_at"],
        "rights_state": "approved",
        "consent_state": consent_state,
        "tool_id": tool["tool_id"],
        "tool_class": tool["tool_class"],
        "tool_version": tool["tool_version"],
        "contract_version": tool["contract_version"],
        "language_hint": language_hint,
        "medium_hint": medium_hint,
        "resource_bounds": copy.deepcopy(dict(profile_record["resource_bounds"])),
        "output_record_type": profile_record["output_record_type"],
        "output_schema_id": profile_record["output_schema_id"],
        "output_media_type": profile_record["output_media_type"],
        "retention_class": profile_record["retention_class"],
        "retention_expires_at": authority["expires_at"],
        "redaction_state": profile_record["redaction_state"],
        "retrieval_decision": most_restrictive_retrieval_decision(
            (
                str(receipt["retrieval_decision"]),
                str(profile_record["maximum_retrieval_decision"]),
            )
        ),
        "external_service_policy": profile_record["external_service_policy"],
        "model_trace_retention": profile_record["model_trace_retention"],
        "evidence_ref": evidence_ref,
        "queued_at": _utc_text(now),
    }
    draft["job_id"] = "derivedjob_" + _digest(draft)[:24]
    draft["job_sha256"] = _digest(draft)
    return validate_derived_media_job(draft)


def validate_derived_media_job(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one admitted job and its self-binding identity."""

    record = _validate_schema("derived-media-job", value)
    operation = str(record["operation"])
    if record["output_record_type"] != _RESULT_RECORD_TYPES[operation]:
        raise DerivedMediaError("job output record must match its operation")
    if record["output_schema_id"] != _SCHEMA_ID.format(
        name=_RESULT_SCHEMA_NAMES[str(record["output_record_type"])]
    ):
        raise DerivedMediaError("job output schema must match its output record")
    if record["retrieval_decision"] == "blocked":
        raise DerivedMediaError("a blocked asset cannot queue a transformation")
    if _parse_time(
        record["rights_decision_expires_at"], "rights_decision_expires_at"
    ) <= _parse_time(record["queued_at"], "queued_at"):
        raise DerivedMediaError("job rights decision must outlast queueing")
    if record["input_byte_size"] > record["resource_bounds"]["maximum_input_bytes"]:
        raise DerivedMediaError("job input exceeds its own resource bound")
    identity = _digest(_without(record, *_JOB_ID_FIELDS))[:24]
    if record["job_id"] != "derivedjob_" + identity:
        raise DerivedMediaError("job identity does not bind its own facts")
    if record["job_sha256"] != _digest(_without(record, "job_sha256")):
        raise DerivedMediaError("job hash does not bind its own facts")
    return record


def _validate_ocr_facts(record: Mapping[str, Any]) -> list[int]:
    pages = list(record["pages"])
    if [page["page_index"] for page in pages] != list(range(len(pages))):
        raise DerivedMediaError("ocr pages must be contiguous and ordered")
    if sum(page["word_count"] for page in pages) != record["word_count"]:
        raise DerivedMediaError("ocr token counts must agree with their pages")
    if sum(page["line_count"] for page in pages) != record["line_count"]:
        raise DerivedMediaError("ocr line counts must agree with their pages")
    return [int(page["mean_confidence_milli"]) for page in pages]


def _validate_transcription_facts(record: Mapping[str, Any]) -> list[int]:
    segments = list(record["segments"])
    if [item["segment_index"] for item in segments] != list(range(len(segments))):
        raise DerivedMediaError("transcript segments must be contiguous and ordered")
    if len(segments) != record["segment_count"]:
        raise DerivedMediaError("segment count must agree with its segments")
    if sum(item["word_count"] for item in segments) != record["word_count"]:
        raise DerivedMediaError("transcript tokens must agree with their segments")
    previous_end = 0
    for item in segments:
        if item["start_ms"] >= item["end_ms"]:
            raise DerivedMediaError("transcript segments must advance in time")
        if item["start_ms"] < previous_end:
            raise DerivedMediaError("transcript segments must not overlap")
        if item["end_ms"] > record["media_duration_ms"]:
            raise DerivedMediaError("transcript segments must stay inside the media")
        previous_end = int(item["end_ms"])
    return [int(item["confidence_milli"]) for item in segments]


def _validate_video_facts(record: Mapping[str, Any]) -> list[int]:
    observations = list(record["observations"])
    if [item["observation_index"] for item in observations] != list(
        range(len(observations))
    ):
        raise DerivedMediaError("video observations must be contiguous and ordered")
    if len(observations) != record["observation_count"]:
        raise DerivedMediaError("observation count must agree with its observations")
    kinds = [str(item["observation_kind"]) for item in observations]
    if kinds.count("shot") != record["shot_count"]:
        raise DerivedMediaError("shot count must agree with its observations")
    if kinds.count("event") != record["event_count"]:
        raise DerivedMediaError("event count must agree with its observations")
    seen: set[tuple[str, str, int, int]] = set()
    for item in observations:
        if item["start_ms"] >= item["end_ms"]:
            raise DerivedMediaError("video observations must advance in time")
        if item["end_ms"] > record["media_duration_ms"]:
            raise DerivedMediaError("video observations must stay inside the media")
        # Shots and events may legitimately overlap; the same span twice is a
        # double count, not an observation.
        span = (
            str(item["observation_kind"]),
            str(item["observation_label"]),
            int(item["start_ms"]),
            int(item["end_ms"]),
        )
        if span in seen:
            raise DerivedMediaError("video observations must not repeat one span")
        seen.add(span)
    return [int(item["confidence_milli"]) for item in observations]


_FACT_VALIDATORS = {
    "ocr_result": _validate_ocr_facts,
    "transcription_result": _validate_transcription_facts,
    "video_understanding_result": _validate_video_facts,
}


def validate_derived_media_result(
    value: Mapping[str, Any], *, job: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate one content-free derived result and its separated outputs."""

    if not isinstance(value, Mapping):
        raise DerivedMediaError("derived-media result must be an object")
    record_type = value.get("record_type")
    if record_type not in _RESULT_SCHEMA_NAMES:
        raise DerivedMediaError("derived-media result type is unknown")
    record = _validate_schema(_RESULT_SCHEMA_NAMES[str(record_type)], value)
    if record["output_object_key"] == record["input_object_key"]:
        raise DerivedMediaError("derived output must not reuse its source key")
    if record["output_sha256"] == record["input_sha256"]:
        raise DerivedMediaError("derived output must differ from its source bytes")
    confidences = _FACT_VALIDATORS[str(record_type)](record)
    if record["minimum_observed_confidence_milli"] != min(confidences):
        raise DerivedMediaError("minimum confidence must agree with its facts")
    if record["mean_confidence_milli"] != sum(confidences) // len(confidences):
        raise DerivedMediaError("mean confidence must agree with its facts")
    if (
        record["quality_state"] == "unsupported_language"
        and record["detected_language"] is not None
    ):
        raise DerivedMediaError(
            "an unsupported-language result cannot report a detected language"
        )
    identity = _digest(_without(record, *_RESULT_ID_FIELDS))[:24]
    if record["result_id"] != _RESULT_ID_PREFIXES[str(record_type)] + "_" + identity:
        raise DerivedMediaError("result identity does not bind its own facts")
    if record["result_sha256"] != _digest(_without(record, "result_sha256")):
        raise DerivedMediaError("result hash does not bind its own facts")
    if job is not None:
        _assert_result_matches_job(record, validate_derived_media_job(job))
    return record


def _assert_result_matches_job(
    record: Mapping[str, Any], job: Mapping[str, Any]
) -> None:
    for result_field, job_field in (
        ("job_id", "job_id"),
        ("operation", "operation"),
        ("profile_id", "profile_id"),
        ("profile_version", "profile_version"),
        ("profile_sha256", "profile_sha256"),
        ("source_id", "source_id"),
        ("asset_id", "asset_id"),
        ("input_object_key", "input_object_key"),
        ("input_sha256", "input_sha256"),
        ("record_type", "output_record_type"),
        ("output_media_type", "output_media_type"),
        # tool_version is deliberately unbound so drift surfaces as a conflict.
        ("tool_id", "tool_id"),
        ("tool_class", "tool_class"),
        ("contract_version", "contract_version"),
        ("evidence_ref", "evidence_ref"),
        ("rights_snapshot_sha256", "rights_snapshot_sha256"),
        ("retention_class", "retention_class"),
        ("retrieval_decision", "retrieval_decision"),
        ("redaction_state", "redaction_state"),
    ):
        if record[result_field] != job[job_field]:
            raise DerivedMediaError(
                f"result {result_field} does not match its admitted job"
            )
    if record["output_byte_size"] > job["resource_bounds"]["maximum_output_bytes"]:
        raise DerivedMediaError("result output exceeds its admitted bound")
    observed_at = _parse_time(record["observed_at"], "observed_at")
    if observed_at < _parse_time(job["queued_at"], "queued_at"):
        raise DerivedMediaError("result cannot precede its admitted job")
    if observed_at > _parse_time(
        job["rights_decision_expires_at"], "rights_decision_expires_at"
    ):
        raise DerivedMediaError(
            "result cannot outlive the rights decision that admitted it"
        )


def build_derived_media_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an unhashed result draft to its canonical identity and hash."""

    if not isinstance(value, Mapping):
        raise DerivedMediaError("derived-media result draft must be an object")
    record_type = value.get("record_type")
    if record_type not in _RESULT_ID_PREFIXES:
        raise DerivedMediaError("derived-media result type is unknown")
    draft = _without(dict(value), *_RESULT_ID_FIELDS)
    draft["result_id"] = (
        _RESULT_ID_PREFIXES[str(record_type)] + "_" + _digest(draft)[:24]
    )
    draft["result_sha256"] = _digest(draft)
    return validate_derived_media_result(draft)


def _job_profile_conflicts(
    job: Mapping[str, Any], profile: Mapping[str, Any]
) -> list[str]:
    """Re-check an admitted job against the profile that is supposed to bound it.

    A job hash binds only itself, so a job that never passed admission can still
    look internally consistent. This is the one review that holds both records.
    """

    conflicts: list[str] = []
    if (
        job["tool_id"] not in profile["allowed_tool_ids"]
        or job["tool_class"] not in profile["allowed_tool_classes"]
    ):
        conflicts.append("tool_not_allowed")
    if not (
        _version_key(str(profile["minimum_tool_version"]))
        <= _version_key(str(job["tool_version"]))
        <= _version_key(str(profile["maximum_tool_version"]))
    ):
        conflicts.append("tool_version_drift")
    if job["input_media_type"] not in profile["allowed_input_media_types"]:
        conflicts.append("media_type_not_allowed")
    if job["operation"] != profile["operation"] or (
        job["output_record_type"] != profile["output_record_type"]
    ):
        conflicts.append("profile_operation_mismatch")
    if job["resource_bounds"] != profile["resource_bounds"]:
        conflicts.append("resource_bounds_drift")
    if _RETRIEVAL_ORDER[str(job["retrieval_decision"])] < _RETRIEVAL_ORDER[
        str(profile["maximum_retrieval_decision"])
    ]:
        conflicts.append("retrieval_decision_too_permissive")
    if job["language_hint"] is not None and (
        job["language_hint"] not in profile["allowed_languages"]
    ):
        conflicts.append("unsupported_language")
    return conflicts


def evaluate_derived_media_conflicts(
    job: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    prior_jobs: Sequence[Mapping[str, Any]] = (),
    results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Detect duplicate, conflicting, low-confidence, or drifted transformations."""

    job_record = validate_derived_media_job(job)
    profile_record = validate_transformation_profile(profile)
    if profile_record["profile_sha256"] != job_record["profile_sha256"]:
        raise DerivedMediaError("conflict review needs the job's exact profile")
    conflicts: list[str] = []
    conflicts.extend(_job_profile_conflicts(job_record, profile_record))

    signature = (
        job_record["operation"],
        job_record["profile_id"],
        job_record["profile_version"],
        job_record["input_sha256"],
    )
    duplicates = sorted(
        {
            str(prior["job_id"])
            for prior in (validate_derived_media_job(item) for item in prior_jobs)
            if prior["job_id"] != job_record["job_id"]
            and (
                prior["operation"],
                prior["profile_id"],
                prior["profile_version"],
                prior["input_sha256"],
            )
            == signature
        }
    )
    if duplicates:
        conflicts.append("duplicate_transformation")

    outputs: set[tuple[str, str]] = set()
    allowed_languages = list(profile_record["allowed_languages"])
    minimum_confidence = int(profile_record["minimum_confidence_milli"])
    for item in results:
        result = validate_derived_media_result(item, job=job_record)
        outputs.add(
            (str(result["output_object_key"]), str(result["output_sha256"]))
        )
        if result["minimum_observed_confidence_milli"] < minimum_confidence:
            conflicts.append("low_confidence")
        detected = result["detected_language"]
        if allowed_languages and (
            detected is None or detected not in allowed_languages
        ):
            conflicts.append("unsupported_language")
        if str(result["tool_version"]) != str(job_record["tool_version"]):
            conflicts.append("tool_version_drift")
        elif not (
            _version_key(str(profile_record["minimum_tool_version"]))
            <= _version_key(str(result["tool_version"]))
            <= _version_key(str(profile_record["maximum_tool_version"]))
        ):
            conflicts.append("tool_version_drift")
    if len(outputs) > 1:
        conflicts.append("conflicting_output_receipt")

    return {
        "job_id": job_record["job_id"],
        "duplicate_job_ids": duplicates,
        "conflicts": sorted(set(conflicts)),
        "clear": not conflicts,
    }


def propagate_derived_media_deletion(
    trigger: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    *,
    index_entries: Sequence[Mapping[str, Any]] = (),
    export_entries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Carry one raw-input obligation to every derived, index, and export target."""

    if not isinstance(trigger, Mapping) or set(trigger) != {
        "reason_code",
        "source_id",
        "asset_id",
        "input_object_key",
        "input_sha256",
        "derived_data_treatment",
    }:
        raise DerivedMediaError("deletion trigger must use the strict field set")
    if trigger["reason_code"] not in DELETION_REASON_CODES:
        raise DerivedMediaError("deletion reason code is unknown")
    if trigger["derived_data_treatment"] not in {
        "delete_on_withdrawal",
        "review_on_withdrawal",
    }:
        raise DerivedMediaError("derived data treatment is unknown")

    asset_scoped = trigger["reason_code"] in _ASSET_SCOPED_REASON_CODES
    known: set[str] = set()
    records: list[dict[str, Any]] = []
    affected: list[dict[str, Any]] = []
    for item in results:
        result = validate_derived_media_result(item)
        known.add(str(result["result_id"]))
        records.append(result)
        same_asset = (
            result["source_id"] == trigger["source_id"]
            and result["asset_id"] == trigger["asset_id"]
        )
        exact_input = (
            result["input_object_key"] == trigger["input_object_key"]
            and result["input_sha256"] == trigger["input_sha256"]
        )
        # Revocation, withdrawal, and expiry are decided per asset; an exact-key
        # obligation is decided per object. Neither may leak into the other.
        if same_asset and (asset_scoped or exact_input):
            affected.append(result)

    # A derivative of a swept derivative inherits the same obligation. The
    # admission gate only accepts raw inputs today, so this normally converges
    # immediately, but a chained record must never be silently left behind.
    affected_ids = {str(result["result_id"]) for result in affected}
    while True:
        swept_outputs = {
            (str(result["output_object_key"]), str(result["output_sha256"]))
            for result in affected
        }
        discovered = [
            result
            for result in records
            if str(result["result_id"]) not in affected_ids
            and (str(result["input_object_key"]), str(result["input_sha256"]))
            in swept_outputs
        ]
        if not discovered:
            break
        affected.extend(discovered)
        affected_ids.update(str(result["result_id"]) for result in discovered)
    index_targets: list[dict[str, Any]] = []
    export_targets: set[str] = set()
    unresolved: set[str] = set()
    for entry in index_entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "index_document_id",
            "field_id",
            "result_id",
        }:
            raise DerivedMediaError("index entry must use the strict field set")
        result_id = _safe_identifier(entry["result_id"], "result_id")
        document_id = _safe_identifier(
            entry["index_document_id"], "index_document_id"
        )
        field_id = _safe_identifier(entry["field_id"], "field_id")
        if result_id not in known:
            unresolved.add(result_id)
        elif result_id in affected_ids:
            index_targets.append(
                {
                    "index_document_id": document_id,
                    "field_id": field_id,
                    "reindex_action": "remove_exact_field",
                }
            )
    for entry in export_entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "export_id",
            "result_id",
        }:
            raise DerivedMediaError("export entry must use the strict field set")
        result_id = _safe_identifier(entry["result_id"], "result_id")
        export_id = _safe_identifier(entry["export_id"], "export_id")
        if result_id not in known:
            unresolved.add(result_id)
        elif result_id in affected_ids:
            export_targets.add(export_id)

    return {
        "reason_code": trigger["reason_code"],
        "source_id": trigger["source_id"],
        "asset_id": trigger["asset_id"],
        "derived_action": (
            "delete"
            if trigger["derived_data_treatment"] == "delete_on_withdrawal"
            else "review"
        ),
        "result_ids": sorted(affected_ids),
        "derived_object_keys": sorted(
            {str(result["output_object_key"]) for result in affected}
        ),
        "index_targets": sorted(
            index_targets,
            key=lambda item: (item["index_document_id"], item["field_id"]),
        ),
        "export_targets": sorted(export_targets),
        "unresolved_result_ids": sorted(unresolved),
        "complete": not unresolved,
    }
