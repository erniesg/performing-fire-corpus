"""Fail-closed source governance and project-native consent contracts."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
FACT_DIMENSIONS = (
    "access_control",
    "api_availability",
    "authentication",
    "copyright_lawful_basis",
    "platform_terms",
    "robots",
)
SOURCE_OPERATIONS = (
    "acquisition_eligibility",
    "caption_retention",
    "deletion",
    "derivative_eligibility",
    "derived_processing",
    "indexing",
    "media_acquisition",
    "metadata_inventory",
    "prose_retention",
    "public_retrieval",
    "retention",
    "search_visibility",
)
PASSING_FACT_STATES = {"allowed", "available", "not_required", "permitted"}


class GovernanceError(ValueError):
    """Raised when governance data violates a durable public contract."""


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate_schema(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(dict(value))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
        raise GovernanceError(f"{name} record does not match the strict schema") from error
    if sanitize(value, environ={}) != value:
        raise GovernanceError(f"{name} record contains private or secret-like data")
    return dict(value)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise GovernanceError("governance timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise GovernanceError("governance timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise GovernanceError("transition time must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_source_governance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one source-governance record and its internal bindings."""

    record = _validate_schema("source-governance", value)
    observations = record["observations"]
    if any(
        _parse_time(item["observed_at"]) >= _parse_time(item["expires_at"])
        for item in observations
    ):
        raise GovernanceError("observation expiry must follow its evidence time")
    observation_keys = [
        (item["dimension"], item["evidence_id"]) for item in observations
    ]
    if observation_keys != sorted(observation_keys) or len(observation_keys) != len(
        set(observation_keys)
    ):
        raise GovernanceError("observations must have unique sorted stable keys")

    decisions = record["decisions"]
    if any(
        _parse_time(item["decided_at"]) >= _parse_time(item["expires_at"])
        for item in decisions
    ):
        raise GovernanceError("decision expiry must follow its authority time")
    decision_operations = [item["affected_operation"] for item in decisions]
    if decision_operations != sorted(decision_operations) or len(
        decision_operations
    ) != len(set(decision_operations)):
        raise GovernanceError("decisions must be unique and sorted by operation")

    blocker_keys = [
        (item["code"], item["endpoint_id"] or "", item["observed_at"])
        for item in record["blockers"]
    ]
    if blocker_keys != sorted(blocker_keys) or len(blocker_keys) != len(
        set(blocker_keys)
    ):
        raise GovernanceError("blockers must have unique sorted stable keys")
    return record


def load_source_governance_registry(
    path: str | Path, *, source_registry: Mapping[str, Any]
) -> dict[str, Any]:
    """Load governance and require exact coverage of the canonical registry."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceError("source governance registry could not be loaded") from error
    records = _validate_governance_registry(value)

    sources = source_registry.get("sources")
    if not isinstance(sources, list):
        raise GovernanceError("canonical source registry is invalid")
    canonical_sources = {
        item["source_id"]: item
        for item in sources
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    source_ids = {item["source_id"] for item in records}
    if source_ids != set(canonical_sources):
        raise GovernanceError("governance must cover the canonical source registry exactly")
    for record in records:
        endpoint_id = record["endpoint_id"]
        if endpoint_id is None:
            continue
        endpoint_ids = {
            item["endpoint_id"]
            for item in canonical_sources[record["source_id"]].get("endpoints", [])
        }
        if endpoint_id not in endpoint_ids:
            raise GovernanceError("governance endpoint is absent from its canonical source")
    return value


def _validate_governance_registry(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "registry_id",
        "records",
    }:
        raise GovernanceError("source governance registry root is not strict")
    if value["schema_version"] != 1 or value["registry_id"] != (
        "performing-fire-source-governance"
    ):
        raise GovernanceError("source governance registry identity is invalid")
    if not isinstance(value["records"], list):
        raise GovernanceError("source governance records must be an array")

    records = [validate_source_governance(item) for item in value["records"]]
    governance_ids = [item["source_governance_id"] for item in records]
    if len(governance_ids) != len(set(governance_ids)):
        raise GovernanceError("source governance identifiers must be unique")
    target_keys = [
        (
            item["source_id"],
            item["endpoint_id"] or "",
            item.get("asset_id") or "",
        )
        for item in records
    ]
    sort_keys = [
        (*target, item["source_governance_id"])
        for target, item in zip(target_keys, records, strict=True)
    ]
    if sort_keys != sorted(sort_keys):
        raise GovernanceError("governance records must use canonical target ordering")
    if len(target_keys) != len(set(target_keys)):
        raise GovernanceError("a governance target may have only one current record")
    return records


def canonical_governance_registry_bytes(value: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8 bytes for an already loaded registry."""

    _validate_governance_registry(value)
    return (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def evaluate_source_operation(
    value: Mapping[str, Any], operation: str, *, now: datetime
) -> dict[str, Any]:
    """Evaluate one operation without allowing any permission inference."""

    record = validate_source_governance(value)
    if operation not in SOURCE_OPERATIONS:
        raise GovernanceError("unknown source operation")
    if now.tzinfo is None:
        raise GovernanceError("evaluation time must be timezone-aware")
    current = now.astimezone(UTC)
    state = record["operation_states"][operation]
    reasons: list[str] = []

    if record["blockers"]:
        reasons.append("durable_blocker")
    for dimension in FACT_DIMENSIONS:
        fact_state = record["fact_states"][dimension]
        if fact_state not in PASSING_FACT_STATES:
            reasons.append(f"{dimension}:{fact_state}")
            continue
        relevant = [
            item
            for item in record["observations"]
            if item["dimension"] == dimension
        ]
        if len(relevant) != 1 or relevant[0]["state"] != fact_state:
            reasons.append(f"{dimension}:evidence_missing_or_conflicting")
        elif _parse_time(relevant[0]["observed_at"]) > current:
            reasons.append(f"{dimension}:evidence_not_yet_effective")
        elif _parse_time(relevant[0]["expires_at"]) <= current:
            reasons.append(f"{dimension}:evidence_expired")

    decisions = [
        item
        for item in record["decisions"]
        if item["affected_operation"] == operation and item["state"] == state
    ]
    if state != "approved":
        reasons.append(f"operation:{state}")
    elif len(decisions) != 1:
        reasons.append("operation:decision_missing_or_conflicting")
    elif _parse_time(decisions[0]["decided_at"]) > current:
        reasons.append("operation:decision_not_yet_effective")
    elif _parse_time(decisions[0]["expires_at"]) <= current:
        reasons.append("operation:decision_expired")

    return {
        "source_id": record["source_id"],
        "endpoint_id": record["endpoint_id"],
        "asset_id": record.get("asset_id"),
        "operation": operation,
        "state": state,
        "eligible": not reasons,
        "reasons": reasons,
        "blockers": copy.deepcopy(record["blockers"]),
    }


def validate_project_native_contract(
    consent: Mapping[str, Any],
    retention: Mapping[str, Any],
    deletion: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate the linked consent, retention, and deletion records."""

    consent_value = _validate_schema("consent", consent)
    retention_value = _validate_schema("retention", retention)
    deletion_value = _validate_schema("deletion", deletion)
    source_id = consent_value["source_id"]
    if not source_id.startswith("project-native-"):
        raise GovernanceError("consent must target a project-native source family")
    if (
        retention_value["source_id"] != source_id
        or deletion_value["source_id"] != source_id
        or retention_value["consent_id"] != consent_value["consent_id"]
        or deletion_value["consent_id"] != consent_value["consent_id"]
    ):
        raise GovernanceError("project-native governance records are not linked")
    if consent_value["state"] == "active" and (
        not consent_value["notice_version"]
        or not consent_value["authority_class"]
        or not consent_value["deletion_owner_role"]
    ):
        raise GovernanceError(
            "active consent requires an approved notice, authority, and deletion owner"
        )
    if retention_value["legal_hold_state"] == "active":
        if not retention_value["legal_hold_basis"]:
            raise GovernanceError("an active legal hold requires a reviewed basis")
    elif retention_value["legal_hold_basis"] is not None:
        raise GovernanceError("a legal-hold basis is forbidden without an active hold")
    if (
        consent_value["deletion_owner_role"]
        != deletion_value["deletion_owner_role"]
    ):
        raise GovernanceError("consent and deletion owner roles must match")
    return consent_value, retention_value, deletion_value


def evaluate_project_native_use(
    consent: Mapping[str, Any],
    retention: Mapping[str, Any],
    deletion: Mapping[str, Any],
    operation: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Evaluate future project-native use under consent and lifecycle controls."""

    consent_value, retention_value, deletion_value = validate_project_native_contract(
        consent, retention, deletion
    )
    if operation not in SOURCE_OPERATIONS:
        raise GovernanceError("unknown project-native operation")
    if now.tzinfo is None:
        raise GovernanceError("evaluation time must be timezone-aware")
    current = now.astimezone(UTC)
    reasons: list[str] = []
    if consent_value["state"] != "active":
        reasons.append(f"consent:{consent_value['state']}")
    if _parse_time(consent_value["decided_at"]) > current:
        reasons.append("consent:not_yet_effective")
    if operation not in consent_value["allowed_uses"]:
        reasons.append("consent:use_not_allowed")
    if _parse_time(consent_value["expires_at"]) <= current:
        reasons.append("consent:expired")
    if retention_value["state"] != "retain_until":
        reasons.append(f"retention:{retention_value['state']}")
    if _parse_time(retention_value["expires_at"]) <= current:
        reasons.append("retention:expired")
    if deletion_value["trigger_state"] != "none":
        reasons.append(f"deletion:{deletion_value['trigger_state']}")
    if deletion_value["status"] != "not_requested":
        reasons.append(f"deletion_status:{deletion_value['status']}")
    return {
        "consent_id": consent_value["consent_id"],
        "source_id": consent_value["source_id"],
        "operation": operation,
        "eligible": not reasons,
        "reasons": reasons,
    }


def transition_consent(
    consent: Mapping[str, Any],
    retention: Mapping[str, Any],
    deletion: Mapping[str, Any],
    *,
    new_state: str,
    at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Revoke or expire consent and emit only a minimal sanitized audit fact."""

    consent_value, retention_value, _ = validate_project_native_contract(
        consent, retention, deletion
    )
    if new_state not in {"revoked", "expired"}:
        raise GovernanceError("consent may transition only to revoked or expired")
    if consent_value["state"] != "active":
        raise GovernanceError("only active consent may be revoked or expired")
    occurred_at = _utc_text(at)
    event = {
        "schema_version": 1,
        "consent_id": consent_value["consent_id"],
        "source_id": consent_value["source_id"],
        "event_type": f"consent_{new_state}",
        "occurred_at": occurred_at,
    }
    updated = copy.deepcopy(consent_value)
    updated["state"] = new_state
    updated["decided_at"] = occurred_at
    updated["allowed_uses"] = []
    updated["audit_events"] = [*updated["audit_events"], event]
    _validate_schema("consent", updated)

    if retention_value["legal_hold_state"] == "active":
        required_work = ["review_legal_hold", "reindex"]
    else:
        required_work = ["delete_content", "delete_derivatives", "reindex"]
    return updated, {
        "audit_event": event,
        "required_work": required_work,
    }
