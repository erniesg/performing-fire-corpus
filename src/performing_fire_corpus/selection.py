"""Deterministic, rights-first rich-corpus selection contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
_POLICY_VERSION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_QUALITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
_DIMENSION_FIELDS = {
    "source": "source",
    "period": "period",
    "language": "languages",
    "medium": "mediums",
    "topic": "topics",
    "performance_context": "performance_contexts",
}


class SelectionPolicyError(ValueError):
    """Raised when selection data is unsafe, unbound, or inconsistent."""


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate_schema(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectionPolicyError(f"{name} record must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise SelectionPolicyError(
            f"{name} record does not match the strict schema"
        ) from error
    if sanitize(record, environ={}) != record:
        raise SelectionPolicyError(
            f"{name} record contains private or secret-like data"
        )
    return record


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _bound_id(prefix: str, value: Mapping[str, Any], id_field: str) -> str:
    payload = {key: child for key, child in value.items() if key != id_field}
    return f"{prefix}_{hashlib.sha256(_canonical_bytes(payload)).hexdigest()[:24]}"


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise SelectionPolicyError(f"{field} must be a valid timestamp") from error
    if parsed.tzinfo is None:
        raise SelectionPolicyError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_window(decided_at: str, expires_at: str) -> None:
    if _parse_time(decided_at, "decided_at") >= _parse_time(
        expires_at, "expires_at"
    ):
        raise SelectionPolicyError("selection authority must expire after its decision")


def _require_policy_version(value: str) -> str:
    if not isinstance(value, str) or _POLICY_VERSION.fullmatch(value) is None:
        raise SelectionPolicyError("selection policy version is invalid")
    return value


def validate_selection_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one source-universe candidate without changing its facts."""

    record = _validate_schema("selection-candidate", value)
    if record["dimensions"]["source"] != record["source_id"]:
        raise SelectionPolicyError("candidate source dimension is not bound")
    _parse_time(record["rights_expires_at"], "rights_expires_at")
    for field in ("languages", "mediums", "topics", "performance_contexts"):
        values = record["dimensions"][field]
        if values != sorted(set(values)):
            raise SelectionPolicyError(
                f"candidate dimension {field} must be unique and sorted"
            )
    return record


def validate_coverage_target(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a declared selection stratum."""

    return _validate_schema("coverage-target", value)


def validate_selection_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a hash-bound include, exclude, or unresolved decision."""

    record = _validate_schema("selection-decision", value)
    _require_policy_version(record["selection_policy_version"])
    _validate_window(record["decided_at"], record["expires_at"])
    expected = _bound_id(
        "selection_decision", record, "selection_decision_id"
    )
    if record["selection_decision_id"] != expected:
        raise SelectionPolicyError("selection decision binding is invalid")
    return record


def validate_selection_exclusion(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a hash-bound, non-destructive exclusion record."""

    record = _validate_schema("selection-exclusion", value)
    expected = _bound_id(
        "selection_exclusion", record, "selection_exclusion_id"
    )
    if record["selection_exclusion_id"] != expected:
        raise SelectionPolicyError("selection exclusion binding is invalid")
    return record


def _matches(candidate: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    field = _DIMENSION_FIELDS[target["dimension"]]
    observed = candidate["dimensions"][field]
    if isinstance(observed, list):
        return target["value"] in observed
    return target["value"] == observed


def _reason_for(
    candidate: Mapping[str, Any], *, evaluated_at: str
) -> str | None:
    if candidate["inventory_state"] != "observed":
        return f"inventory_{candidate['inventory_state']}"
    if candidate["retrieval_state"] != "available":
        return f"retrieval_{candidate['retrieval_state']}"
    for field, reason in (
        ("rights_state", "rights_not_approved"),
        ("retention_state", "retention_not_approved"),
        ("privacy_state", "privacy_not_approved"),
        ("transformation_state", "transformation_not_approved"),
    ):
        if candidate[field] != "approved":
            return reason
    if _parse_time(
        str(candidate["rights_expires_at"]), "rights_expires_at"
    ) <= _parse_time(evaluated_at, "decided_at"):
        return "rights_expired"
    if candidate["pipeline_proof"]:
        return "proof_requires_review"
    return None


_RATIONALES = {
    "coverage_not_needed": "Excluded because declared strata are already satisfied.",
    "duplicate_not_representative": (
        "Excluded as a preserved duplicate-cluster member; provenance remains countable."
    ),
    "inventory_blocked": "Excluded because the inventory record is blocked.",
    "inventory_out_of_scope": (
        "Excluded from rich selection while remaining in universe accounting."
    ),
    "inventory_unavailable": "Excluded because the inventory record is unavailable.",
    "privacy_not_approved": "Excluded because current privacy authority is not approved.",
    "proof_requires_review": (
        "Excluded from automatic selection; proof assets require ordinary reviewed selection."
    ),
    "retention_not_approved": (
        "Excluded because current retention authority is not approved."
    ),
    "retrieval_blocked": "Excluded because retrieval is blocked.",
    "retrieval_unavailable": "Excluded because retrievable content is unavailable.",
    "retrieval_unknown": "Excluded because retrieval availability is unknown.",
    "rights_not_approved": (
        "Excluded because current operation-specific rights are not approved."
    ),
    "rights_expired": (
        "Excluded because operation-specific rights are no longer current."
    ),
    "selected_for_coverage": (
        "Included to satisfy declared coverage after all authority gates passed."
    ),
    "transformation_not_approved": (
        "Excluded because downstream transformation is not approved."
    ),
}


def _build_decision(
    candidate: Mapping[str, Any],
    *,
    decision: str,
    reason_code: str,
    policy_version: str,
    decision_authority: str,
    decided_at: str,
    expires_at: str,
    review_trigger: str,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "selection_decision",
        "selection_decision_id": "selection_decision_pending",
        "candidate_id": candidate["candidate_id"],
        "source_id": candidate["source_id"],
        "asset_id": candidate["asset_id"],
        "decision": decision,
        "reason_code": reason_code,
        "rationale": _RATIONALES[reason_code],
        "authority_class": decision_authority,
        "decided_at": decided_at,
        "expires_at": expires_at,
        "review_trigger": review_trigger,
        "selection_policy_version": policy_version,
        "rights_snapshot_sha256": candidate["rights_snapshot_sha256"],
        "evidence_scope": candidate["evidence_scope"],
    }
    record["selection_decision_id"] = _bound_id(
        "selection_decision", record, "selection_decision_id"
    )
    return validate_selection_decision(record)


def _build_exclusion(
    decision: Mapping[str, Any], *, review_trigger: str
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "selection_exclusion",
        "selection_exclusion_id": "selection_exclusion_pending",
        "selection_decision_id": decision["selection_decision_id"],
        "candidate_id": decision["candidate_id"],
        "source_id": decision["source_id"],
        "asset_id": decision["asset_id"],
        "reason_code": decision["reason_code"],
        "rationale": decision["rationale"],
        "review_trigger": review_trigger,
    }
    record["selection_exclusion_id"] = _bound_id(
        "selection_exclusion", record, "selection_exclusion_id"
    )
    return validate_selection_exclusion(record)


def _unknown_dimensions(candidate: Mapping[str, Any]) -> bool:
    dimensions = candidate["dimensions"]
    return any(
        value == "unknown"
        for field, value in dimensions.items()
        if not isinstance(value, list)
    ) or any(
        not value or "unknown" in value
        for value in dimensions.values()
        if isinstance(value, list)
    )


def validate_selection_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one complete, reproducible selection snapshot."""

    record = _validate_schema("selection-manifest", value)
    _require_policy_version(record["selection_policy_version"])
    _validate_window(record["decided_at"], record["expires_at"])
    decisions = [
        validate_selection_decision(item) for item in record["decisions"]
    ]
    exclusions = [
        validate_selection_exclusion(item) for item in record["exclusions"]
    ]
    candidate_ids = [item["candidate_id"] for item in decisions]
    if candidate_ids != sorted(set(candidate_ids)):
        raise SelectionPolicyError(
            "manifest decisions must cover unique, sorted candidates"
        )
    excluded_ids = [
        item["candidate_id"]
        for item in decisions
        if item["decision"] == "exclude"
    ]
    if [item["candidate_id"] for item in exclusions] != excluded_ids:
        raise SelectionPolicyError(
            "manifest exclusions must exactly match excluded decisions"
        )
    counts = record["universe_counts"]
    if counts != {
        "known_candidates": len(decisions),
        "included": sum(item["decision"] == "include" for item in decisions),
        "excluded": sum(item["decision"] == "exclude" for item in decisions),
        "unresolved": sum(item["decision"] == "unresolved" for item in decisions),
    }:
        raise SelectionPolicyError("manifest universe accounting is inconsistent")
    expected = _bound_id(
        "selection_manifest", record, "selection_manifest_id"
    )
    if record["selection_manifest_id"] != expected:
        raise SelectionPolicyError("selection manifest binding is invalid")
    return record


def evaluate_selection(
    candidates: Sequence[Mapping[str, Any]],
    coverage_targets: Sequence[Mapping[str, Any]],
    *,
    inventory_snapshot_sha256: str,
    policy_version: str,
    decision_authority: str,
    decided_at: str,
    expires_at: str,
    review_trigger: str,
) -> dict[str, Any]:
    """Select a deterministic, authority-eligible set for declared strata."""

    _require_policy_version(policy_version)
    _validate_window(decided_at, expires_at)
    checked_candidates = sorted(
        (validate_selection_candidate(item) for item in candidates),
        key=lambda item: item["candidate_id"],
    )
    checked_targets = sorted(
        (validate_coverage_target(item) for item in coverage_targets),
        key=lambda item: (item["priority"], item["coverage_target_id"]),
    )
    candidate_ids = [item["candidate_id"] for item in checked_candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise SelectionPolicyError("candidate IDs must be unique")
    asset_keys = [
        (item["source_id"], item["asset_id"]) for item in checked_candidates
    ]
    if len(asset_keys) != len(set(asset_keys)):
        raise SelectionPolicyError("source and asset candidate keys must be unique")
    target_ids = [item["coverage_target_id"] for item in checked_targets]
    if len(target_ids) != len(set(target_ids)):
        raise SelectionPolicyError("coverage target IDs must be unique")

    reasons: dict[str, str] = {}
    eligible: list[dict[str, Any]] = []
    for item in checked_candidates:
        reason = _reason_for(item, evaluated_at=decided_at)
        if reason is None:
            eligible.append(item)
        else:
            reasons[item["candidate_id"]] = reason

    target_match_count = {
        item["candidate_id"]: sum(
            _matches(item, target) for target in checked_targets
        )
        for item in eligible
    }
    clusters: dict[str, list[dict[str, Any]]] = {}
    unclustered: list[dict[str, Any]] = []
    for item in eligible:
        cluster_id = item["duplicate_cluster_id"]
        if cluster_id is None:
            unclustered.append(item)
        else:
            clusters.setdefault(cluster_id, []).append(item)
    representatives = list(unclustered)
    for members in clusters.values():
        ordered = sorted(
            members,
            key=lambda item: (
                -target_match_count[item["candidate_id"]],
                -_QUALITY_RANK[item["technical_quality"]],
                item["candidate_id"],
            ),
        )
        representatives.append(ordered[0])
        for duplicate in ordered[1:]:
            reasons[duplicate["candidate_id"]] = "duplicate_not_representative"

    remaining = {
        item["candidate_id"]: item for item in representatives
    }
    selected: set[str] = set()
    selected_by_target = {
        target["coverage_target_id"]: 0 for target in checked_targets
    }
    while True:
        unmet = [
            target
            for target in checked_targets
            if selected_by_target[target["coverage_target_id"]]
            < target["minimum_selected"]
        ]
        if not unmet:
            break
        ranked: list[tuple[tuple[Any, ...], dict[str, Any], list[dict[str, Any]]]] = []
        for item in remaining.values():
            contributions = [target for target in unmet if _matches(item, target)]
            if not contributions:
                continue
            ranked.append(
                (
                    (
                        -sum(
                            max(1, 100 - int(target["priority"]))
                            for target in contributions
                        ),
                        -len(contributions),
                        -_QUALITY_RANK[item["technical_quality"]],
                        item["candidate_id"],
                    ),
                    item,
                    contributions,
                )
            )
        if not ranked:
            break
        _, chosen, contributions = min(ranked, key=lambda value: value[0])
        selected.add(chosen["candidate_id"])
        remaining.pop(chosen["candidate_id"])
        for target in contributions:
            selected_by_target[target["coverage_target_id"]] += 1
    for candidate_id in remaining:
        reasons[candidate_id] = "coverage_not_needed"

    decisions = []
    for item in checked_candidates:
        included = item["candidate_id"] in selected
        reason = "selected_for_coverage" if included else reasons[item["candidate_id"]]
        decisions.append(
            _build_decision(
                item,
                decision="include" if included else "exclude",
                reason_code=reason,
                policy_version=policy_version,
                decision_authority=decision_authority,
                decided_at=decided_at,
                expires_at=expires_at,
                review_trigger=review_trigger,
            )
        )
    exclusions = [
        _build_exclusion(item, review_trigger=review_trigger)
        for item in decisions
        if item["decision"] == "exclude"
    ]
    by_id = {item["candidate_id"]: item for item in checked_candidates}
    coverage = []
    for target in sorted(
        checked_targets, key=lambda item: item["coverage_target_id"]
    ):
        observed_count = sum(
            _matches(item, target) for item in checked_candidates
        )
        eligible_count = sum(
            _matches(item, target)
            for item in checked_candidates
            if _reason_for(item, evaluated_at=decided_at) is None
        )
        selected_count = sum(
            _matches(by_id[candidate_id], target)
            for candidate_id in selected
        )
        shortfall = max(0, target["minimum_selected"] - selected_count)
        coverage.append(
            {
                "coverage_target_id": target["coverage_target_id"],
                "dimension": target["dimension"],
                "value": target["value"],
                "minimum_selected": target["minimum_selected"],
                "observed_candidates": observed_count,
                "eligible_candidates": eligible_count,
                "selected_candidates": selected_count,
                "shortfall": shortfall,
                "state": "met" if shortfall == 0 else "underrepresented",
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "selection_manifest",
        "selection_manifest_id": "selection_manifest_pending",
        "inventory_snapshot_sha256": inventory_snapshot_sha256,
        "selection_policy_version": policy_version,
        "decision_authority": decision_authority,
        "decided_at": decided_at,
        "expires_at": expires_at,
        "review_trigger": review_trigger,
        "coverage_targets": checked_targets,
        "decisions": decisions,
        "exclusions": exclusions,
        "coverage": coverage,
        "universe_counts": {
            "known_candidates": len(decisions),
            "included": sum(item["decision"] == "include" for item in decisions),
            "excluded": sum(item["decision"] == "exclude" for item in decisions),
            "unresolved": sum(
                item["decision"] == "unresolved" for item in decisions
            ),
        },
        "unresolved_metadata_candidate_ids": sorted(
            item["candidate_id"]
            for item in checked_candidates
            if _unknown_dimensions(item)
        ),
    }
    manifest["selection_manifest_id"] = _bound_id(
        "selection_manifest", manifest, "selection_manifest_id"
    )
    return validate_selection_manifest(manifest)
