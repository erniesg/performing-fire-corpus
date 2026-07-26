"""Deterministic, evidence-scoped corpus evaluation contracts.

Every number this module reports is scoped to the exact snapshot it was read
from. A bounded or blocked observation is never widened into a whole-source
total, a duplicate is never merged or deleted, and no recommendation asks for
acquisition past a rights, robots, platform, access, privacy, or retention
blocker. The module reads sanitized snapshots only: it contacts no source, no
object store, and no index service.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.governance import CANONICAL_ENDPOINT_IDS
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.search_index import (
    index_format_checker,
    parse_index_timestamp,
    validate_visibility_policy,
)
from performing_fire_corpus.search_service import (
    EXPORT_AUDIENCES,
    export_score_features,
    search_corpus_index,
    validate_corpus_index,
)
from performing_fire_corpus.selection import candidate_matches_coverage_target


EVALUATION_POLICY_VERSION = "corpus_evaluation_v1"

NEXT_ACTION_CLASSES = (
    "bounded_adapter_run",
    "duplicate_review",
    "human_decision",
    "index_repair",
    "metadata_correction",
    "retention_review",
    "rights_review",
    "transformation_review",
)
QUALITY_CHECKS = (
    "deletion_propagation",
    "derived_confidence",
    "index_consistency",
    "metadata_normalization",
    "provenance_completeness",
    "retention_readiness",
    "rights_freshness",
)
REQUIRED_FIELD_NAMES = ("title",)
UNRESOLVED_LABEL = "unknown"

_RECORD_CONTRACTS = {
    "evaluation_metric": ("evaluation-metric", "metric_id"),
    "coverage_gap": ("coverage-gap", "coverage_gap_id"),
    "duplicate_finding": ("duplicate-finding", "duplicate_finding_id"),
    "quality_finding": ("quality-finding", "quality_finding_id"),
    "retrieval_case": ("retrieval-case", "retrieval_case_id"),
    "evaluation_recommendation": (
        "evaluation-recommendation",
        "recommendation_id",
    ),
}
_RUN_SECTIONS = (
    ("metrics", "evaluation_metric"),
    ("coverage_gaps", "coverage_gap"),
    ("duplicate_findings", "duplicate_finding"),
    ("quality_findings", "quality_finding"),
    ("retrieval_cases", "retrieval_case"),
    ("recommendations", "evaluation_recommendation"),
)

# A bounded, blocked, changed, or unknown observation can never become a total.
UNBOUNDED_COMPLETENESS_STATES = frozenset(
    {"blocked", "bounded_partial", "changed", "unknown"}
)
_STATE_PRECEDENCE = (
    "blocked",
    "unknown",
    "changed",
    "bounded_partial",
    "complete_for_observed_endpoint",
)
_REPORT_METRIC_FIELDS = (
    ("observed_unique_records", "observed_unique_records"),
    ("duplicate_records", "duplicate_records"),
    ("rejected_records", "rejected_records"),
    ("blocked_pages", "blocked_pages"),
    ("pages_committed", "pages_committed"),
    ("requests_attempted", "requests_attempted"),
    ("bounded_remainder", "unvisited_remainder"),
)

_BLOCKED_REASON_CODES = frozenset(
    {
        "inventory_blocked",
        "privacy_expired",
        "privacy_not_approved",
        "proof_requires_review",
        "retention_expired",
        "retention_not_approved",
        "retrieval_blocked",
        "rights_expired",
        "rights_not_approved",
        "source_governance_expired",
        "source_governance_not_approved",
        "transformation_expired",
        "transformation_not_approved",
    }
)
_UNAVAILABLE_REASON_CODES = frozenset(
    {
        "inventory_out_of_scope",
        "inventory_unavailable",
        "retrieval_unavailable",
        "retrieval_unknown",
    }
)
_GAP_ACTIONS = {
    "blocked": ("rights_review", "rights"),
    "unavailable": ("human_decision", "access"),
    "unresolved": ("metadata_correction", "none"),
    "underrepresented": ("bounded_adapter_run", "none"),
}
_DERIVED_ORIGIN_CLASSES = frozenset({"derived_observation", "generated_score"})
_CASE_REQUIRED_KEYS = frozenset(
    {
        "retrieval_case_id",
        "audience",
        "expected_visible_field_ids",
        "forbidden_field_ids",
        "rationale",
    }
)
_CASE_OPTIONAL_KEYS = frozenset(
    {"query_terms", "filters", "forbidden_facet_values", "checked_surfaces"}
)
_FILTER_KEYS = ("source_id", "language", "period", "medium", "selection_state")
_MAX_FINDING_REFS = 64


class EvaluationError(ValueError):
    """Raised when evaluation input or output is unsafe or inconsistent."""


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{name} record must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=index_format_checker()
        ).validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise EvaluationError(
            f"{name} record does not match the strict schema"
        ) from error
    if sanitize(record, environ={}) != record:
        raise EvaluationError(f"{name} record contains private data")
    return record


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _bound_id(prefix: str, value: Mapping[str, Any], id_field: str) -> str:
    payload = {key: child for key, child in value.items() if key != id_field}
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _bind(prefix: str, record: dict[str, Any], id_field: str) -> dict[str, Any]:
    record[id_field] = _bound_id(prefix, record, id_field)
    return record


def _worst_state(states: Iterable[str]) -> str:
    observed = set(states)
    for state in _STATE_PRECEDENCE:
        if state in observed:
            return state
    return "unknown"


def _validate_completeness_report(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate("completeness-report", value)


def validate_evaluation_metric(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one evidence-scoped metric and its bound identity."""

    record = _validate("evaluation-metric", value)
    if record["metric_id"] != _bound_id("metric", record, "metric_id"):
        raise EvaluationError("metric identity is not bound to its content")
    if record["completeness_state"] in UNBOUNDED_COMPLETENESS_STATES and (
        record["denominator"] is not None or record["is_whole_source_total"]
    ):
        raise EvaluationError(
            "a bounded or blocked observation cannot become a whole-source total"
        )
    # A complete endpoint may still have an unknown remainder, so only a
    # metric that states a denominator needs a numerator.
    if record["denominator"] is not None and record["observed_value"] is None:
        raise EvaluationError("a metric with a denominator requires a numerator")
    return record


def validate_coverage_gap(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one coverage gap and its bound identity."""

    record = _validate("coverage-gap", value)
    if record["coverage_gap_id"] != _bound_id(
        "coverage_gap", record, "coverage_gap_id"
    ):
        raise EvaluationError("coverage gap identity is not bound to its content")
    counted = (
        record["selected_candidates"]
        + record["excluded_candidates"]
        + record["unresolved_candidates"]
    )
    if counted > record["observed_candidates"]:
        raise EvaluationError("coverage gap counts exceed the observed stratum")
    if (
        record["blocked_candidates"] + record["unavailable_candidates"]
        > record["excluded_candidates"]
    ):
        raise EvaluationError("blocked and unavailable counts must be excluded ones")
    return record


def validate_duplicate_finding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one explainable duplicate finding that merges nothing."""

    record = _validate("duplicate-finding", value)
    if record["duplicate_finding_id"] != _bound_id(
        "duplicate_finding", record, "duplicate_finding_id"
    ):
        raise EvaluationError(
            "duplicate finding identity is not bound to its content"
        )
    if (
        record["review_state"] != "requires_human_review"
        or record["merge_action"] != "none"
    ):
        raise EvaluationError("duplicate findings never authorize a merge")
    members = [
        (item["index_document_id"], item["source_id"], item["asset_id"])
        for item in record["members"]
    ]
    if len(members) != len(set(members)) or members != sorted(members):
        raise EvaluationError("duplicate members must be distinct and canonical")
    return record


def validate_quality_finding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one metadata, rights, retention, or index quality finding."""

    record = _validate("quality-finding", value)
    if record["quality_finding_id"] != _bound_id(
        "quality_finding", record, "quality_finding_id"
    ):
        raise EvaluationError("quality finding identity is not bound to its content")
    if record["field_id"] is not None and record["index_document_id"] is None:
        raise EvaluationError("a field finding requires its exact document")
    return record


def validate_retrieval_case(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one rights-filtered retrieval case and its observed outcome."""

    record = _validate("retrieval-case", value)
    forbidden = set(record["forbidden_field_ids"])
    if forbidden & set(record["expected_visible_field_ids"]):
        raise EvaluationError(
            "a retrieval case cannot expect and forbid the same field"
        )
    for name in (
        "expected_visible_field_ids",
        "forbidden_field_ids",
        "forbidden_facet_values",
        "observed_field_ids",
        "observed_facet_values",
        "exported_field_ids",
        "failure_reasons",
        "checked_surfaces",
    ):
        if record[name] != sorted(record[name]):
            raise EvaluationError(f"{name} must be sorted")
    return record


def validate_evaluation_recommendation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one bounded recommendation and its bound identity."""

    record = _validate("evaluation-recommendation", value)
    if record["recommendation_id"] != _bound_id(
        "recommendation", record, "recommendation_id"
    ):
        raise EvaluationError("recommendation identity is not bound to its content")
    if (
        record["next_action_class"] == "bounded_adapter_run"
        and record["blocker_class"] != "none"
    ):
        raise EvaluationError(
            "a bounded adapter run cannot be recommended past a blocker"
        )
    if record["finding_refs"] != sorted(set(record["finding_refs"])):
        raise EvaluationError("finding references must be unique and sorted")
    return record


_RECORD_VALIDATORS = {
    "evaluation_metric": validate_evaluation_metric,
    "coverage_gap": validate_coverage_gap,
    "duplicate_finding": validate_duplicate_finding,
    "quality_finding": validate_quality_finding,
    "retrieval_case": validate_retrieval_case,
    "evaluation_recommendation": validate_evaluation_recommendation,
}


def _metric(
    *,
    metric_key: str,
    dimension: str,
    scope: str,
    observed_value: int | None,
    completeness_state: str,
    rationale: str,
    source_id: str | None = None,
    endpoint_id: str | None = None,
    denominator: int | None = None,
    is_whole_source_total: bool = False,
    evidence_at: str | None = None,
    evidence_expires_at: str | None = None,
    input_record_ids: Sequence[str] = (),
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "record_type": "evaluation_metric",
        "metric_id": "metric_" + "0" * 24,
        "metric_key": metric_key,
        "dimension": dimension,
        "scope": scope,
        "source_id": source_id,
        "endpoint_id": endpoint_id,
        "observed_value": observed_value,
        "denominator": denominator,
        "completeness_state": completeness_state,
        "is_whole_source_total": is_whole_source_total,
        "evidence_at": evidence_at,
        "evidence_expires_at": evidence_expires_at,
        "input_record_ids": sorted(set(input_record_ids)),
        "rationale": rationale,
    }
    return validate_evaluation_metric(_bind("metric", record, "metric_id"))


def evaluate_source_completeness(
    completeness_reports: Sequence[Mapping[str, Any]],
    *,
    exhaustive_endpoint_sources: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Report per-endpoint and per-source observations without inventing totals.

    An endpoint observation is never a whole-source total. A per-source
    aggregate claims one only when every canonical endpoint of that source
    reported `complete_for_observed_endpoint` *and* a human has declared that
    source's reviewed endpoint list exhaustive. For an open website that
    declaration is normally absent, so the honest answer stays "not a total".
    """

    reports = [_validate_completeness_report(item) for item in completeness_reports]
    exhaustive = frozenset(str(item) for item in exhaustive_endpoint_sources)
    unknown_sources = exhaustive - {str(item["source_id"]) for item in reports}
    if unknown_sources:
        raise EvaluationError(
            "an exhaustive endpoint declaration needs that source's reports"
        )
    identities = [
        (str(item["source_id"]), str(item["endpoint_id"])) for item in reports
    ]
    if len(identities) != len(set(identities)):
        raise EvaluationError("one endpoint cannot report completeness twice")
    metrics: list[dict[str, Any]] = []
    for report in reports:
        source_id = str(report["source_id"])
        endpoint_id = str(report["endpoint_id"])
        state = str(report["state"])
        refs = [
            str(report["report_id"]),
            str(report["run_id"]),
            str(report["policy_snapshot_id"]),
        ]
        for metric_key, field in _REPORT_METRIC_FIELDS:
            complete = (
                metric_key == "observed_unique_records"
                and state == "complete_for_observed_endpoint"
            )
            metrics.append(
                _metric(
                    metric_key=metric_key,
                    dimension="source_completeness",
                    scope="source_endpoint",
                    source_id=source_id,
                    endpoint_id=endpoint_id,
                    observed_value=report[field],
                    denominator=report["expected_total"] if complete else None,
                    completeness_state=state,
                    is_whole_source_total=False,
                    evidence_at=str(report["generated_at"]),
                    input_record_ids=refs,
                    rationale=(
                        f"Endpoint {endpoint_id} of {source_id} reported "
                        f"{metric_key} in state {state} after stop reason "
                        f"{report['stop_reason']}. The value is scoped to this "
                        "endpoint observation only."
                    ),
                )
            )
    for source_id in sorted({str(item["source_id"]) for item in reports}):
        metrics.extend(
            _source_scope_metrics(
                source_id, reports, exhaustive=source_id in exhaustive
            )
        )
    return sorted(metrics, key=lambda item: str(item["metric_id"]))


def _source_scope_metrics(
    source_id: str, reports: Sequence[Mapping[str, Any]], *, exhaustive: bool
) -> list[dict[str, Any]]:
    scoped = [item for item in reports if str(item["source_id"]) == source_id]
    observed_endpoints = {str(item["endpoint_id"]) for item in scoped}
    canonical = set(CANONICAL_ENDPOINT_IDS.get(source_id, frozenset()))
    states = [str(item["state"]) for item in scoped]
    state = _worst_state(states)
    every_endpoint_complete = (
        bool(canonical)
        and observed_endpoints == canonical
        and set(states) == {"complete_for_observed_endpoint"}
    )
    whole_source = every_endpoint_complete and exhaustive
    expected = [item["expected_total"] for item in scoped]
    remainders = [item["unvisited_remainder"] for item in scoped]
    refs = [str(item["report_id"]) for item in scoped]
    unobserved = sorted(canonical - observed_endpoints)
    if whole_source:
        scope_note = (
            "Every reviewed endpoint of this source completed, and its "
            "endpoint list is declared exhaustive, so this is a whole-source "
            "total."
        )
    elif every_endpoint_complete:
        scope_note = (
            "Every reviewed endpoint of this source completed, but its "
            "endpoint list is not declared exhaustive, so this is not a "
            "whole-source total."
        )
    else:
        scope_note = (
            "This is the sum of observed endpoint records, not a whole-source "
            "total. Unobserved endpoints remain "
            + (", ".join(unobserved) if unobserved else "unknown")
            + "."
        )
    return [
        _metric(
            metric_key="observed_unique_records",
            dimension="source_completeness",
            scope="source",
            source_id=source_id,
            observed_value=sum(int(item["observed_unique_records"]) for item in scoped),
            denominator=(
                sum(int(value) for value in expected)
                if whole_source and all(value is not None for value in expected)
                else None
            ),
            completeness_state=state,
            is_whole_source_total=whole_source,
            evidence_at=max(str(item["generated_at"]) for item in scoped),
            input_record_ids=refs,
            rationale=scope_note,
        ),
        _metric(
            metric_key="bounded_remainder",
            dimension="source_completeness",
            scope="source",
            source_id=source_id,
            observed_value=(
                sum(int(value) for value in remainders)
                if all(value is not None for value in remainders)
                else None
            ),
            completeness_state=state,
            evidence_at=max(str(item["generated_at"]) for item in scoped),
            input_record_ids=refs,
            rationale=(
                "The remainder is unknown for at least one endpoint, so no "
                "source remainder can be stated."
                if any(value is None for value in remainders)
                else "Bounded remainder summed across the observed endpoints only."
            ),
        ),
        _metric(
            metric_key="blocked_pages",
            dimension="source_completeness",
            scope="source",
            source_id=source_id,
            observed_value=sum(int(item["blocked_pages"]) for item in scoped),
            completeness_state=state,
            evidence_at=max(str(item["generated_at"]) for item in scoped),
            input_record_ids=refs,
            rationale=(
                "Blocked pages are an explicit result, never a reason to raise "
                "a bound."
            ),
        ),
    ]


def evaluate_selection_coverage(
    selection_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Report every declared stratum with its causes counted separately."""

    manifest = _validate("selection-manifest", selection_manifest)
    decisions = {
        str(item["candidate_id"]): item for item in manifest["decisions"]
    }
    gaps: list[dict[str, Any]] = []
    for target in manifest["coverage_targets"]:
        matching = [
            item
            for item in manifest["candidates"]
            if candidate_matches_coverage_target(item, target)
        ]
        counts = {
            "selected": 0,
            "excluded": 0,
            "unresolved": 0,
            "blocked": 0,
            "unavailable": 0,
        }
        for candidate in matching:
            decision = decisions.get(str(candidate["candidate_id"]))
            if decision is None:
                raise EvaluationError("every candidate requires a decision")
            reason = str(decision["reason_code"])
            if decision["decision"] == "include":
                counts["selected"] += 1
                continue
            if decision["decision"] == "unresolved":
                counts["unresolved"] += 1
                continue
            counts["excluded"] += 1
            if reason in _BLOCKED_REASON_CODES:
                counts["blocked"] += 1
            elif reason in _UNAVAILABLE_REASON_CODES:
                counts["unavailable"] += 1
        minimum = int(target["minimum_selected"])
        shortfall = max(0, minimum - counts["selected"])
        eligible = (
            len(matching)
            - counts["blocked"]
            - counts["unavailable"]
            - counts["unresolved"]
        )
        if shortfall == 0:
            state = "met"
        elif counts["blocked"]:
            state = "blocked"
        elif counts["unavailable"]:
            state = "unavailable"
        elif counts["unresolved"]:
            state = "unresolved"
        else:
            state = "underrepresented"
        action = None if state == "met" else _GAP_ACTIONS[state][0]
        record = {
            "schema_version": 1,
            "record_type": "coverage_gap",
            "coverage_gap_id": "coverage_gap_" + "0" * 24,
            "coverage_target_id": target["coverage_target_id"],
            "dimension": target["dimension"],
            "value": target["value"],
            "minimum_selected": minimum,
            "observed_candidates": len(matching),
            "eligible_candidates": eligible,
            "selected_candidates": counts["selected"],
            "excluded_candidates": counts["excluded"],
            "blocked_candidates": counts["blocked"],
            "unavailable_candidates": counts["unavailable"],
            "unresolved_candidates": counts["unresolved"],
            "shortfall": shortfall,
            "state": state,
            "next_action_class": action,
            "rationale": _gap_rationale(target, counts, state, shortfall),
        }
        gaps.append(
            validate_coverage_gap(_bind("coverage_gap", record, "coverage_gap_id"))
        )
    return sorted(gaps, key=lambda item: str(item["coverage_gap_id"]))


def _gap_rationale(
    target: Mapping[str, Any],
    counts: Mapping[str, int],
    state: str,
    shortfall: int,
) -> str:
    stratum = f"{target['dimension']} {target['value']}"
    if state == "met":
        return (
            f"Stratum {stratum} is met with {counts['selected']} selected "
            "contributions."
        )
    return (
        f"Stratum {stratum} is short by {shortfall}. Excluded "
        f"{counts['excluded']}, of which blocked {counts['blocked']} and "
        f"unavailable {counts['unavailable']}; unresolved metadata "
        f"{counts['unresolved']}. Blocked coverage is reported, not bypassed."
    )


def _index_view(index: Mapping[str, Any]) -> dict[str, Any]:
    record = validate_corpus_index(index)
    snapshot = record["snapshot"]
    return {
        "record": record,
        "snapshot": snapshot,
        "documents": {
            str(item["index_document_id"]): item for item in snapshot["documents"]
        },
        "entries": {
            str(item["index_document_id"]): item for item in record["entries"]
        },
        "edges": {
            str(item["provenance_edge_id"]): item
            for item in snapshot["provenance_edges"]
        },
        "policies": {
            (str(item["index_document_id"]), str(item["field_id"])): item
            for item in snapshot["visibility_policies"]
        },
        "events": list(snapshot["deletion_events"]),
    }


def _duplicate_member(
    document: Mapping[str, Any],
    *,
    field_ids: Sequence[str] = (),
    value_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "index_document_id": document["index_document_id"],
        "source_id": document["source_id"],
        "asset_id": document["asset_id"],
        "field_ids": sorted(set(str(item) for item in field_ids)),
        "value_sha256": value_sha256,
    }


def _duplicate_finding(
    *,
    finding_class: str,
    confidence: str,
    members: Sequence[Mapping[str, Any]],
    matched_field_names: Sequence[str],
    conflicting_field_names: Sequence[str],
    evidence_summary: str,
    duplicate_cluster_id: str | None,
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "record_type": "duplicate_finding",
        "duplicate_finding_id": "duplicate_finding_" + "0" * 24,
        "finding_class": finding_class,
        "confidence": confidence,
        "duplicate_cluster_id": duplicate_cluster_id,
        "members": sorted(
            (copy.deepcopy(dict(item)) for item in members),
            key=lambda item: (
                str(item["index_document_id"]),
                str(item["source_id"]),
                str(item["asset_id"]),
            ),
        ),
        "matched_field_names": sorted(set(matched_field_names)),
        "conflicting_field_names": sorted(set(conflicting_field_names)),
        "evidence_summary": evidence_summary,
        "review_state": "requires_human_review",
        "merge_action": "none",
    }
    return validate_duplicate_finding(
        _bind("duplicate_finding", record, "duplicate_finding_id")
    )


def _shared_cluster(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> str | None:
    cluster = first["duplicate_cluster_id"]
    return str(cluster) if cluster is not None and cluster == second[
        "duplicate_cluster_id"
    ] else None


def detect_duplicate_findings(index: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Detect duplicate evidence classes without merging or deleting anything.

    Metadata comparison is an exhaustive pairwise scan of the generation rather
    than a sampled or capped one, so a finding is never silently dropped.
    """

    view = _index_view(index)
    documents = view["documents"]
    edges = view["edges"]
    findings: list[dict[str, Any]] = []

    by_object_hash: dict[str, list[tuple[str, str]]] = {}
    for document_id, entry in view["entries"].items():
        for binding in entry["derived_objects"]:
            by_object_hash.setdefault(str(binding["sha256"]), []).append(
                (document_id, str(binding["object_key"]))
            )
    for sha256, bound in sorted(by_object_hash.items()):
        holders = sorted({document_id for document_id, _ in bound})
        if len(holders) < 2:
            continue
        findings.append(
            _duplicate_finding(
                finding_class="exact_hash_duplicate",
                confidence="exact",
                members=[
                    _duplicate_member(documents[item], value_sha256=sha256)
                    for item in holders
                ],
                matched_field_names=[],
                conflicting_field_names=[],
                evidence_summary=(
                    "Two indexed records bind derived objects with the same "
                    "exact content hash. Provenance is preserved and no merge "
                    "is performed."
                ),
                duplicate_cluster_id=_shared_cluster(
                    documents[holders[0]], documents[holders[1]]
                )
                if len(holders) == 2
                else None,
            )
        )

    by_asset_id: dict[str, list[str]] = {}
    for document_id, document in documents.items():
        by_asset_id.setdefault(str(document["asset_id"]), []).append(document_id)
    for asset_id, holders in sorted(by_asset_id.items()):
        sources = {str(documents[item]["source_id"]) for item in holders}
        if len(holders) < 2 or len(sources) < 2:
            continue
        ordered = sorted(holders)
        findings.append(
            _duplicate_finding(
                finding_class="stable_id_alias",
                confidence="high",
                members=[_duplicate_member(documents[item]) for item in ordered],
                matched_field_names=[],
                conflicting_field_names=[],
                evidence_summary=(
                    f"The stable item identifier {asset_id} appears under more "
                    "than one source. Review whether these are aliases of one "
                    "record before any correction."
                ),
                duplicate_cluster_id=_shared_cluster(
                    documents[ordered[0]], documents[ordered[1]]
                )
                if len(ordered) == 2
                else None,
            )
        )

    hashes = {
        document_id: {
            str(field["name"]): (
                str(field["field_id"]),
                str(edges[str(field["provenance_edge_id"])]["field_value_sha256"]),
            )
            for field in document["fields"]
        }
        for document_id, document in documents.items()
    }
    ordered_ids = sorted(documents)
    for position, first_id in enumerate(ordered_ids):
        for second_id in ordered_ids[position + 1 :]:
            first, second = hashes[first_id], hashes[second_id]
            shared = sorted(set(first) & set(second))
            if not shared:
                continue
            matched = [name for name in shared if first[name][1] == second[name][1]]
            if not matched:
                continue
            conflicting = [name for name in shared if name not in matched]
            members = [
                _duplicate_member(
                    documents[document_id],
                    field_ids=[field_hashes[name][0] for name in matched],
                )
                for document_id, field_hashes in (
                    (first_id, first),
                    (second_id, second),
                )
            ]
            cluster = _shared_cluster(documents[first_id], documents[second_id])
            if conflicting:
                findings.append(
                    _duplicate_finding(
                        finding_class="conflicting_duplicate",
                        confidence="medium",
                        members=members,
                        matched_field_names=matched,
                        conflicting_field_names=conflicting,
                        evidence_summary=(
                            "Two records agree on "
                            f"{len(matched)} shared metadata field hashes and "
                            f"disagree on {len(conflicting)}. A human decides "
                            "which record is authoritative."
                        ),
                        duplicate_cluster_id=cluster,
                    )
                )
                continue
            findings.append(
                _duplicate_finding(
                    finding_class="likely_metadata_duplicate",
                    confidence="high" if len(matched) > 1 else "medium",
                    members=members,
                    matched_field_names=matched,
                    conflicting_field_names=[],
                    evidence_summary=(
                        "Every shared metadata field hash matches across both "
                        f"records ({len(matched)} of {len(shared)}). Review "
                        "before selecting one representative."
                    ),
                    duplicate_cluster_id=cluster,
                )
            )
    return sorted(findings, key=lambda item: str(item["duplicate_finding_id"]))


def _quality_finding(
    *,
    check: str,
    state: str,
    severity: str,
    detail: str,
    next_action_class: str,
    source_id: str | None = None,
    index_document_id: str | None = None,
    field_id: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "record_type": "quality_finding",
        "quality_finding_id": "quality_finding_" + "0" * 24,
        "check": check,
        "state": state,
        "severity": "info" if state == "pass" else severity,
        "source_id": source_id,
        "index_document_id": index_document_id,
        "field_id": field_id,
        "next_action_class": next_action_class,
        "detail": detail,
    }
    return validate_quality_finding(
        _bind("quality_finding", record, "quality_finding_id")
    )


def _lineage_documents(edge_id: str, edges: Mapping[str, Any]) -> set[str]:
    """Every index document one field's complete lineage reaches."""

    edge = edges[edge_id]
    reached = {str(edge["index_document_id"])}
    for item in edge["input_provenance_edge_ids"]:
        reached |= _lineage_documents(str(item), edges)
    return reached


def _normalization_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for document_id, document in sorted(view["documents"].items()):
        names = {str(field["name"]) for field in document["fields"]}
        missing = sorted(set(REQUIRED_FIELD_NAMES) - names)
        unresolved = (
            not document["languages"]
            or not document["mediums"]
            or str(document["period"]) == UNRESOLVED_LABEL
            or UNRESOLVED_LABEL in document["languages"]
            or UNRESOLVED_LABEL in document["mediums"]
        )
        if missing:
            state, severity = "fail", "high"
            detail = (
                "Required metadata fields are missing from this record "
                f"({', '.join(missing)}). The gap is reported, not imputed."
            )
        elif unresolved:
            state, severity = "unknown", "medium"
            detail = (
                "One or more declared dimensions are unresolved, so this record "
                "cannot be counted toward a stratum."
            )
        else:
            state, severity = "pass", "info"
            detail = "Required metadata fields and declared dimensions resolve."
        findings.append(
            _quality_finding(
                check="metadata_normalization",
                state=state,
                severity=severity,
                detail=detail,
                next_action_class="metadata_correction",
                source_id=str(document["source_id"]),
                index_document_id=document_id,
            )
        )
    return findings


def _provenance_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    edges = view["edges"]
    for document_id, document in sorted(view["documents"].items()):
        for field in document["fields"]:
            if field["origin_class"] not in _DERIVED_ORIGIN_CLASSES:
                continue
            reached = _lineage_documents(str(field["provenance_edge_id"]), edges)
            contained = reached == {document_id}
            findings.append(
                _quality_finding(
                    check="provenance_completeness",
                    state="pass" if contained else "unknown",
                    severity="medium",
                    detail=(
                        "Derived lineage stays inside this record and reaches "
                        "its own factual root."
                        if contained
                        else "Derived lineage reaches "
                        f"{len(reached) - 1} other indexed record(s), so the "
                        "fusion must be confirmed before this field is trusted."
                    ),
                    next_action_class="transformation_review",
                    source_id=str(document["source_id"]),
                    index_document_id=document_id,
                    field_id=str(field["field_id"]),
                )
            )
        if not any(
            field["origin_class"] in _DERIVED_ORIGIN_CLASSES
            for field in document["fields"]
        ):
            findings.append(
                _quality_finding(
                    check="provenance_completeness",
                    state="pass",
                    severity="info",
                    detail=(
                        "This record carries no derived field, so no derived "
                        "lineage was required."
                    ),
                    next_action_class="transformation_review",
                    source_id=str(document["source_id"]),
                    index_document_id=document_id,
                )
            )
    return findings


def _current_policy(
    authority_resolver: Any, index_document_id: str, field_id: str
) -> dict[str, Any] | None:
    try:
        resolved = authority_resolver.resolve_visibility_policy(
            index_document_id=index_document_id, field_id=field_id
        )
        return None if resolved is None else validate_visibility_policy(resolved)
    except Exception:
        return None


def _rights_findings(
    view: Mapping[str, Any],
    evaluated: datetime,
    authority_resolver: Any | None = None,
) -> list[dict[str, Any]]:
    """Report rights and provenance freshness against current authority.

    Without an authority boundary this reports staleness only: a revoked or
    withdrawn grant is invisible to a snapshot that still carries it.
    """

    findings = []
    edges = view["edges"]
    for document_id, document in sorted(view["documents"].items()):
        for field in document["fields"]:
            key = (document_id, str(field["field_id"]))
            policy = view["policies"][key]
            edge = edges[str(field["provenance_edge_id"])]
            current = (
                policy
                if authority_resolver is None
                else _current_policy(authority_resolver, key[0], key[1])
            )
            if current != policy:
                state, severity = "blocked", "blocker"
                detail = (
                    "Current field visibility authority is missing, revoked, or "
                    "no longer matches the indexed policy, so the field must "
                    "leave every surface."
                )
            elif parse_index_timestamp(
                policy["decided_at"], "decided_at"
            ) > evaluated:
                state, severity = "unknown", "medium"
                detail = "Field visibility authority is not yet effective."
            elif (
                evaluated
                >= parse_index_timestamp(policy["expires_at"], "expires_at")
                or evaluated
                >= parse_index_timestamp(
                    policy["evidence_expires_at"], "evidence_expires_at"
                )
                or evaluated
                >= parse_index_timestamp(
                    edge["evidence_expires_at"], "evidence_expires_at"
                )
            ):
                state, severity = "fail", "high"
                detail = (
                    "Rights or provenance evidence for this field has expired, "
                    "so the indexed answer is stale."
                )
            else:
                state, severity = "pass", "info"
                detail = "Rights and provenance evidence are current."
            findings.append(
                _quality_finding(
                    check="rights_freshness",
                    state=state,
                    severity=severity,
                    detail=detail,
                    next_action_class="rights_review",
                    source_id=str(document["source_id"]),
                    index_document_id=document_id,
                    field_id=str(field["field_id"]),
                )
            )
    return findings


_RETENTION_CLASSES_BY_SELECTION = {
    "selected_rich_corpus": frozenset(
        {"inventory_metadata", "selected_derived", "selected_raw"}
    ),
    "inventory_only": frozenset({"inventory_metadata"}),
    "excluded": frozenset({"inventory_metadata"}),
    "unresolved": frozenset({"inventory_metadata"}),
}


def _retention_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for document_id, document in sorted(view["documents"].items()):
        for field in document["fields"]:
            expiring = (
                str(document["source_id"]).startswith("project-native-")
                or field["origin_class"] == "project_native"
                or field["visibility_class"] == "project_private"
            )
            allowed = (
                frozenset({"project_native_expiring"})
                if expiring
                else _RETENTION_CLASSES_BY_SELECTION[
                    str(document["selection_state"])
                ]
            )
            ready = str(field["retention_class"]) in allowed
            findings.append(
                _quality_finding(
                    check="retention_readiness",
                    state="pass" if ready else "fail",
                    severity="medium",
                    detail=(
                        "Retention class matches the selection state of this "
                        "record."
                        if ready
                        else "Retention class does not match the selection "
                        "state of this record, so deletion timing is unproven."
                    ),
                    next_action_class="retention_review",
                    source_id=str(document["source_id"]),
                    index_document_id=document_id,
                    field_id=str(field["field_id"]),
                )
            )
    return findings


def _derived_confidence_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    edges = view["edges"]
    for document_id, document in sorted(view["documents"].items()):
        entry = view["entries"][document_id]
        bound = {
            str(item["transformation_id"]) for item in entry["derived_objects"]
        }
        for field in document["fields"]:
            if field["origin_class"] not in _DERIVED_ORIGIN_CLASSES:
                continue
            transformation = edges[str(field["provenance_edge_id"])][
                "transformation_id"
            ]
            confirmed = str(transformation) in bound
            findings.append(
                _quality_finding(
                    check="derived_confidence",
                    state="pass" if confirmed else "unknown",
                    severity="medium",
                    detail=(
                        "A verified derived object backs this generated field, "
                        "and model output is still not ground truth."
                        if confirmed
                        else "No verified derived object backs this generated "
                        "field, so its confidence stays unknown; model output "
                        "is not ground truth."
                    ),
                    next_action_class="transformation_review",
                    source_id=str(document["source_id"]),
                    index_document_id=document_id,
                    field_id=str(field["field_id"]),
                )
            )
    return findings


def _deletion_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = view["events"]
    if not events:
        return [
            _quality_finding(
                check="deletion_propagation",
                state="pass",
                severity="info",
                detail=(
                    "This snapshot carries no deletion event, so nothing "
                    "required propagation."
                ),
                next_action_class="index_repair",
            )
        ]
    findings = []
    for event in sorted(events, key=lambda item: str(item["deletion_event_id"])):
        document_id = str(event["index_document_id"])
        entry = view["entries"].get(document_id)
        propagated = entry is not None and bool(entry["field_ids"])
        findings.append(
            _quality_finding(
                check="deletion_propagation",
                state="pass" if propagated else "unknown",
                severity="medium",
                detail=(
                    "The deletion outcome is observable in this index "
                    f"generation for reason {event['reason_code']}."
                    if propagated
                    else "The affected record retains no indexed field or is "
                    "absent, so propagation cannot be proven from this "
                    "snapshot alone."
                ),
                next_action_class="index_repair",
                index_document_id=document_id,
                field_id=str(event["field_id"]),
            )
        )
    return findings


def _consistency_findings(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    record = view["record"]
    covered_fields = {
        (str(item["index_document_id"]), str(item["field_id"]))
        for item in view["events"]
    }
    covered_documents = {document_id for document_id, _ in covered_fields}
    findings = []
    for item in record["superseded_fields"]:
        key = (str(item["index_document_id"]), str(item["field_id"]))
        if key in covered_fields:
            continue
        findings.append(
            _quality_finding(
                check="index_consistency",
                state="fail",
                severity="high",
                detail=(
                    "A field left the index without a deletion event that "
                    "authorizes its removal."
                ),
                next_action_class="index_repair",
                index_document_id=key[0],
                field_id=key[1],
            )
        )
    for document_id in record["removed_document_ids"]:
        if document_id in covered_documents:
            continue
        findings.append(
            _quality_finding(
                check="index_consistency",
                state="fail",
                severity="high",
                detail=(
                    "A record left the index without a deletion event that "
                    "authorizes its removal."
                ),
                next_action_class="index_repair",
                index_document_id=str(document_id),
            )
        )
    if not findings:
        findings.append(
            _quality_finding(
                check="index_consistency",
                state="pass",
                severity="info",
                detail=(
                    "Every removal and supersession in this generation is "
                    "covered by a deletion event."
                ),
                next_action_class="index_repair",
            )
        )
    return findings


def evaluate_corpus_quality(
    index: Mapping[str, Any],
    *,
    evaluated_at: str,
    authority_resolver: Any | None = None,
) -> list[dict[str, Any]]:
    """Evaluate normalization, provenance, rights, retention, and consistency."""

    view = _index_view(index)
    evaluated = parse_index_timestamp(evaluated_at, "evaluated_at")
    findings = (
        _normalization_findings(view)
        + _provenance_findings(view)
        + _rights_findings(view, evaluated, authority_resolver)
        + _retention_findings(view)
        + _derived_confidence_findings(view)
        + _deletion_findings(view)
        + _consistency_findings(view)
    )
    return sorted(findings, key=lambda item: str(item["quality_finding_id"]))


def _case_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError("retrieval case definition must be an object")
    keys = set(value)
    if not _CASE_REQUIRED_KEYS <= keys or keys - (
        _CASE_REQUIRED_KEYS | _CASE_OPTIONAL_KEYS
    ):
        raise EvaluationError("retrieval case definition shape is not canonical")
    filters = dict(value.get("filters") or {})
    if set(filters) - set(_FILTER_KEYS):
        raise EvaluationError("retrieval case filters are not canonical")
    return {
        "retrieval_case_id": str(value["retrieval_case_id"]),
        "audience": str(value["audience"]),
        "query_terms": sorted({str(item) for item in value.get("query_terms", ())}),
        "filters": {name: filters.get(name) for name in _FILTER_KEYS},
        "expected_visible_field_ids": sorted(
            {str(item) for item in value["expected_visible_field_ids"]}
        ),
        "forbidden_field_ids": sorted(
            {str(item) for item in value["forbidden_field_ids"]}
        ),
        "forbidden_facet_values": sorted(
            {str(item) for item in value.get("forbidden_facet_values", ())}
        ),
        "checked_surfaces": sorted(
            {str(item) for item in value.get("checked_surfaces", ("facets", "results"))}
        ),
        "rationale": str(value["rationale"]),
    }


def evaluate_retrieval_cases(
    index: Mapping[str, Any],
    case_definitions: Sequence[Mapping[str, Any]],
    *,
    current_time: str,
    authority_resolver: Any,
    object_authority: Any | None = None,
) -> list[dict[str, Any]]:
    """Prove allowed fields appear and ineligible fields appear nowhere."""

    record = validate_corpus_index(index)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for definition in case_definitions:
        case = _case_definition(definition)
        if case["retrieval_case_id"] in seen:
            raise EvaluationError("retrieval case identifiers must be unique")
        seen.add(case["retrieval_case_id"])
        answer = search_corpus_index(
            record,
            audience=case["audience"],
            current_time=current_time,
            authority_resolver=authority_resolver,
            query_terms=case["query_terms"],
            **case["filters"],
        )
        observed_fields = sorted(
            {
                str(field["field_id"])
                for result in answer["results"]
                for field in result["fields"]
            }
        )
        observed_facets = sorted(
            {
                str(bucket["value"])
                for buckets in answer["facets"].values()
                for bucket in buckets
            }
        )
        exported_fields: list[str] = []
        if (
            "score_export" in case["checked_surfaces"]
            and case["audience"] in EXPORT_AUDIENCES
        ):
            export = export_score_features(
                record,
                audience=case["audience"],
                current_time=current_time,
                authority_resolver=authority_resolver,
                object_authority=object_authority,
            )
            exported_fields = sorted(
                {
                    str(feature["field_id"])
                    for document in export["documents"]
                    for feature in document["features"]
                }
            )
        forbidden = set(case["forbidden_field_ids"])
        reasons = set()
        if not set(case["expected_visible_field_ids"]) <= set(observed_fields):
            reasons.add("expected_field_missing")
        if "results" in case["checked_surfaces"] and forbidden & set(observed_fields):
            reasons.add("forbidden_field_in_results")
        if "facets" in case["checked_surfaces"] and set(
            case["forbidden_facet_values"]
        ) & set(observed_facets):
            reasons.add("forbidden_facet_value")
        if forbidden & set(exported_fields):
            reasons.add("forbidden_field_in_export")
        cases.append(
            validate_retrieval_case(
                dict(
                    case,
                    schema_version=1,
                    record_type="retrieval_case",
                    observed_field_ids=observed_fields,
                    observed_facet_values=observed_facets,
                    exported_field_ids=exported_fields,
                    outcome="fail" if reasons else "pass",
                    failure_reasons=sorted(reasons),
                )
            )
        )
    return sorted(cases, key=lambda item: str(item["retrieval_case_id"]))


# Current source-universe gaps and policy freshness come before corpus volume,
# so a bounded adapter run is the lowest priority action there is.
_RULE_PRIORITIES = {
    "rights_review": 1,
    "index_repair": 2,
    "human_decision": 3,
    "retention_review": 3,
    "metadata_correction": 4,
    "transformation_review": 5,
    "duplicate_review": 6,
    "bounded_adapter_run": 7,
}
_RULE_RATIONALES = {
    "rights_review": (
        "Current rights, consent, or provenance authority is stale or not "
        "approving. Policy freshness comes before corpus volume."
    ),
    "index_repair": (
        "A rights-filtered retrieval case or an index removal is not provable "
        "from the current generation."
    ),
    "human_decision": (
        "A durable blocker or an unavailable stratum needs a human decision "
        "before any further acquisition."
    ),
    "retention_review": (
        "Retention class and selection state disagree, so deletion timing is "
        "unproven."
    ),
    "metadata_correction": (
        "Required metadata is missing or unresolved, so records cannot be "
        "counted toward a declared stratum."
    ),
    "transformation_review": (
        "Derived lineage or derived confidence is incomplete. Uncertainty is "
        "reported rather than assumed."
    ),
    "duplicate_review": "Duplicate evidence needs an explainable human review.",
    "bounded_adapter_run": (
        "One bounded adapter run is the next safe step for this endpoint. "
        "Bulk acquisition is never recommended to move a metric."
    ),
}


def prioritize_recommendations(
    *,
    metrics: Sequence[Mapping[str, Any]] = (),
    coverage_gaps: Sequence[Mapping[str, Any]] = (),
    duplicate_findings: Sequence[Mapping[str, Any]] = (),
    quality_findings: Sequence[Mapping[str, Any]] = (),
    retrieval_cases: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return one prioritized bounded next action per grouped cause."""

    groups: dict[tuple[str, str, str | None], set[str]] = {}

    def add(action: str, blocker: str, source_id: str | None, ref: str) -> None:
        groups.setdefault((action, blocker, source_id), set()).add(ref)

    for item in quality_findings:
        finding = validate_quality_finding(item)
        if finding["state"] == "pass":
            continue
        check = str(finding["check"])
        source_id = finding["source_id"]
        ref = str(finding["quality_finding_id"])
        if check == "rights_freshness":
            add(
                "rights_review",
                "rights" if finding["state"] == "blocked" else "none",
                source_id,
                ref,
            )
        elif check == "retention_readiness":
            add("retention_review", "retention", source_id, ref)
        elif check == "metadata_normalization":
            add("metadata_correction", "none", source_id, ref)
        elif check in {"provenance_completeness", "derived_confidence"}:
            add("transformation_review", "none", source_id, ref)
        else:
            add("index_repair", "none", source_id, ref)
    for item in retrieval_cases:
        case = validate_retrieval_case(item)
        if case["outcome"] == "fail":
            add(
                "index_repair",
                "none",
                case["filters"]["source_id"],
                str(case["retrieval_case_id"]),
            )
    for item in coverage_gaps:
        gap = validate_coverage_gap(item)
        if gap["state"] == "met":
            continue
        action, blocker = _GAP_ACTIONS[str(gap["state"])]
        source_id = (
            str(gap["value"]) if gap["dimension"] == "source" else None
        )
        add(action, blocker, source_id, str(gap["coverage_gap_id"]))
    for item in duplicate_findings:
        finding = validate_duplicate_finding(item)
        add("duplicate_review", "none", None, str(finding["duplicate_finding_id"]))
    for item in metrics:
        metric = validate_evaluation_metric(item)
        if metric["scope"] != "source_endpoint":
            continue
        state = str(metric["completeness_state"])
        if state == "bounded_partial":
            add(
                "bounded_adapter_run",
                "none",
                metric["source_id"],
                str(metric["metric_id"]),
            )
        elif state in {"blocked", "changed"}:
            add("human_decision", "access", metric["source_id"], str(metric["metric_id"]))

    recommendations = []
    for (action, blocker, source_id), refs in groups.items():
        ordered = sorted(refs)
        truncated = len(ordered) > _MAX_FINDING_REFS
        rationale = _RULE_RATIONALES[action]
        if truncated:
            rationale = (
                f"{rationale} References are capped at {_MAX_FINDING_REFS} of "
                f"{len(ordered)} matching findings."
            )
        record = {
            "schema_version": 1,
            "record_type": "evaluation_recommendation",
            "recommendation_id": "recommendation_" + "0" * 24,
            "priority": _RULE_PRIORITIES[action],
            "next_action_class": action,
            "blocker_class": blocker,
            "source_id": source_id,
            "finding_refs": ordered[:_MAX_FINDING_REFS],
            "rationale": rationale,
        }
        recommendations.append(
            validate_evaluation_recommendation(
                _bind("recommendation", record, "recommendation_id")
            )
        )
    return sorted(
        recommendations,
        key=lambda item: (
            int(item["priority"]),
            str(item["next_action_class"]),
            str(item["source_id"] or ""),
            str(item["recommendation_id"]),
        ),
    )


def _input_snapshot(
    reports: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    index: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "completeness_report_ids": sorted(
            {str(item["report_id"]) for item in reports}
        ),
        "discovery_run_ids": sorted({str(item["run_id"]) for item in reports}),
        "policy_snapshot_ids": sorted(
            {str(item["policy_snapshot_id"]) for item in reports}
        ),
        "selection_manifest_id": (
            None if manifest is None else str(manifest["selection_manifest_id"])
        ),
        "selection_policy_version": (
            None if manifest is None else str(manifest["selection_policy_version"])
        ),
        "inventory_snapshot_sha256": (
            None if manifest is None else str(manifest["inventory_snapshot_sha256"])
        ),
        "corpus_index_id": (
            None if index is None else str(index["corpus_index_id"])
        ),
        "index_snapshot_id": (
            None if index is None else str(index["index_snapshot_id"])
        ),
        "index_sha256": None if index is None else str(index["index_sha256"]),
        "snapshot_sha256": None if index is None else str(index["snapshot_sha256"]),
        "policy_snapshot_sha256": (
            None if index is None else str(index["policy_snapshot_sha256"])
        ),
    }


def build_evaluation_run(
    *,
    evaluated_at: str,
    completeness_reports: Sequence[Mapping[str, Any]] = (),
    selection_manifest: Mapping[str, Any] | None = None,
    index: Mapping[str, Any] | None = None,
    retrieval_case_definitions: Sequence[Mapping[str, Any]] = (),
    authority_resolver: Any | None = None,
    object_authority: Any | None = None,
    exhaustive_endpoint_sources: Sequence[str] = (),
    caveats: Sequence[str] = (),
) -> dict[str, Any]:
    """Assemble one reproducible evaluation run from exact snapshots."""

    parse_index_timestamp(evaluated_at, "evaluated_at")
    reports = [_validate_completeness_report(item) for item in completeness_reports]
    manifest = (
        None
        if selection_manifest is None
        else _validate("selection-manifest", selection_manifest)
    )
    corpus_index = None if index is None else validate_corpus_index(index)
    if retrieval_case_definitions and (
        corpus_index is None or authority_resolver is None
    ):
        raise EvaluationError(
            "retrieval cases require an index and a current authority boundary"
        )

    metrics = evaluate_source_completeness(
        reports, exhaustive_endpoint_sources=exhaustive_endpoint_sources
    )
    gaps = [] if manifest is None else evaluate_selection_coverage(manifest)
    duplicates = [] if corpus_index is None else detect_duplicate_findings(corpus_index)
    quality = (
        []
        if corpus_index is None
        else evaluate_corpus_quality(
            corpus_index,
            evaluated_at=evaluated_at,
            authority_resolver=authority_resolver,
        )
    )
    cases = (
        []
        if corpus_index is None or not retrieval_case_definitions
        else evaluate_retrieval_cases(
            corpus_index,
            retrieval_case_definitions,
            current_time=evaluated_at,
            authority_resolver=authority_resolver,
            object_authority=object_authority,
        )
    )
    if corpus_index is not None:
        metrics = sorted(
            metrics + _index_metrics(corpus_index, duplicates, quality, cases),
            key=lambda item: str(item["metric_id"]),
        )
    if manifest is not None:
        metrics = sorted(
            metrics + _selection_metrics(manifest, gaps),
            key=lambda item: str(item["metric_id"]),
        )
    recommendations = prioritize_recommendations(
        metrics=metrics,
        coverage_gaps=gaps,
        duplicate_findings=duplicates,
        quality_findings=quality,
        retrieval_cases=cases,
    )
    run = {
        "schema_version": 1,
        "record_type": "evaluation_run",
        "evaluation_run_id": "evaluation_run_" + "0" * 24,
        "evaluated_at": evaluated_at,
        "evaluation_policy_version": EVALUATION_POLICY_VERSION,
        "input_snapshot": _input_snapshot(reports, manifest, corpus_index),
        "metrics": metrics,
        "coverage_gaps": gaps,
        "duplicate_findings": duplicates,
        "quality_findings": quality,
        "retrieval_cases": cases,
        "recommendations": recommendations,
        "caveats": sorted({str(item) for item in caveats}),
    }
    return validate_evaluation_run(
        _bind("evaluation_run", run, "evaluation_run_id")
    )


def _index_metrics(
    index: Mapping[str, Any],
    duplicates: Sequence[Mapping[str, Any]],
    quality: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries = index["entries"]
    return [
        _metric(
            metric_key="indexed_documents",
            dimension="metadata_quality",
            scope="corpus_index",
            observed_value=len(entries),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale=(
                "Indexed records in this exact generation. It is not a source "
                "universe count."
            ),
        ),
        _metric(
            metric_key="indexed_fields",
            dimension="metadata_quality",
            scope="corpus_index",
            observed_value=sum(len(item["field_ids"]) for item in entries),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale="Indexed fields in this exact generation.",
        ),
        _metric(
            metric_key="selected_documents",
            dimension="selection_coverage",
            scope="corpus_index",
            observed_value=sum(
                1
                for item in entries
                if item["selection_state"] == "selected_rich_corpus"
            ),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale="Records the selection policy included, in this generation.",
        ),
        _metric(
            metric_key="duplicate_findings",
            dimension="duplicates",
            scope="corpus_index",
            observed_value=len(duplicates),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale=(
                "Duplicate findings awaiting human review. Nothing was merged "
                "or deleted."
            ),
        ),
        _metric(
            metric_key="failing_quality_checks",
            dimension="metadata_quality",
            scope="corpus_index",
            observed_value=sum(
                1 for item in quality if item["state"] != "pass"
            ),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale="Quality checks that did not pass in this generation.",
        ),
        _metric(
            metric_key="failing_retrieval_cases",
            dimension="retrieval_behavior",
            scope="retrieval_suite",
            observed_value=sum(1 for item in cases if item["outcome"] == "fail"),
            completeness_state=(
                "complete_for_observed_endpoint" if cases else "not_applicable"
            ),
            evidence_at=str(index["built_at"]),
            input_record_ids=[str(index["corpus_index_id"])],
            rationale=(
                "Rights-filtered retrieval cases that did not hold."
                if cases
                else "No retrieval case was declared for this run."
            ),
        ),
    ]


def _selection_metrics(
    manifest: Mapping[str, Any], gaps: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [
        _metric(
            metric_key="declared_coverage_targets",
            dimension="selection_coverage",
            scope="selection_manifest",
            observed_value=len(manifest["coverage_targets"]),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(manifest["decided_at"]),
            input_record_ids=[str(manifest["selection_manifest_id"])],
            rationale="Strata the selection policy declares.",
        ),
        _metric(
            metric_key="met_coverage_targets",
            dimension="selection_coverage",
            scope="selection_manifest",
            observed_value=sum(1 for item in gaps if item["state"] == "met"),
            denominator=len(gaps),
            completeness_state="complete_for_observed_endpoint",
            evidence_at=str(manifest["decided_at"]),
            input_record_ids=[str(manifest["selection_manifest_id"])],
            rationale=(
                "Declared strata met by this manifest. Blocked strata are "
                "reported separately and are not a shortfall to acquire past."
            ),
        ),
    ]


def validate_evaluation_run(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one evaluation run, every nested record, and every reference."""

    record = _validate("evaluation-run", value)
    if record["evaluation_run_id"] != _bound_id(
        "evaluation_run", record, "evaluation_run_id"
    ):
        raise EvaluationError("evaluation run identity is not bound to its content")
    if record["evaluation_policy_version"] != EVALUATION_POLICY_VERSION:
        raise EvaluationError("evaluation policy version is unsupported")
    parse_index_timestamp(record["evaluated_at"], "evaluated_at")

    identities: dict[str, dict[str, Any]] = {}
    for section, record_type in _RUN_SECTIONS:
        schema_name, id_field = _RECORD_CONTRACTS[record_type]
        validated = [_RECORD_VALIDATORS[record_type](item) for item in record[section]]
        if validated != record[section]:
            raise EvaluationError(f"{schema_name} records are not canonical")
        ids = [str(item[id_field]) for item in validated]
        if len(ids) != len(set(ids)):
            raise EvaluationError(f"{section} identities must be unique")
        # Recommendations are ordered by priority; every other section by identity.
        if section != "recommendations" and ids != sorted(ids):
            raise EvaluationError(f"{section} must be sorted by identity")
        for item in validated:
            identities[str(item[id_field])] = item

    snapshot = record["input_snapshot"]
    if snapshot["corpus_index_id"] is None and (
        record["duplicate_findings"]
        or record["quality_findings"]
        or record["retrieval_cases"]
    ):
        raise EvaluationError("index findings require an exact index snapshot")
    if snapshot["selection_manifest_id"] is None and record["coverage_gaps"]:
        raise EvaluationError("coverage gaps require an exact selection manifest")
    if not snapshot["completeness_report_ids"] and any(
        item["dimension"] == "source_completeness" for item in record["metrics"]
    ):
        raise EvaluationError(
            "source completeness metrics require an exact completeness report"
        )

    priorities = [int(item["priority"]) for item in record["recommendations"]]
    if priorities != sorted(priorities):
        raise EvaluationError("recommendations must be ordered by priority")
    for recommendation in record["recommendations"]:
        for ref in recommendation["finding_refs"]:
            referenced = identities.get(str(ref))
            if referenced is None:
                raise EvaluationError("recommendation references an absent finding")
            if recommendation["next_action_class"] != "bounded_adapter_run":
                continue
            if (
                referenced.get("state") in {"blocked", "unavailable"}
                or referenced.get("completeness_state") == "blocked"
            ):
                raise EvaluationError(
                    "a bounded adapter run cannot answer a blocked observation"
                )
    return record


def render_evaluation_report(run: Mapping[str, Any]) -> str:
    """Render one sanitized aggregate markdown report for a validated run."""

    record = validate_evaluation_run(run)
    snapshot = record["input_snapshot"]

    def scoped(value: Any) -> str:
        return "not in scope" if value is None else f"`{value}`"

    lines = [
        "# Corpus evaluation run",
        "",
        f"- Run: `{record['evaluation_run_id']}`",
        f"- Evaluated at: `{record['evaluated_at']}`",
        f"- Evaluation policy: `{record['evaluation_policy_version']}`",
        f"- Selection policy: {scoped(snapshot['selection_policy_version'])}",
        f"- Corpus index: {scoped(snapshot['corpus_index_id'])}",
        f"- Completeness reports: {len(snapshot['completeness_report_ids'])}",
        "",
        "## Evidence-scoped metrics",
        "",
        "| Metric | Scope | Source | Endpoint | Observed | Denominator | State | Whole-source total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for metric in sorted(
        record["metrics"],
        key=lambda item: (
            str(item["scope"]),
            str(item["source_id"] or ""),
            str(item["endpoint_id"] or ""),
            str(item["metric_key"]),
        ),
    ):
        lines.append(
            "| {key} | {scope} | {source} | {endpoint} | {observed} | "
            "{denominator} | `{state}` | {total} |".format(
                key=metric["metric_key"],
                scope=metric["scope"],
                source=metric["source_id"] or "—",
                endpoint=metric["endpoint_id"] or "—",
                observed="unknown"
                if metric["observed_value"] is None
                else metric["observed_value"],
                denominator="unknown"
                if metric["denominator"] is None
                else metric["denominator"],
                state=metric["completeness_state"],
                total="yes" if metric["is_whole_source_total"] else "no",
            )
        )
    lines += ["", "## Coverage gaps", ""]
    if not record["coverage_gaps"]:
        lines.append("No selection manifest was in scope for this run.")
    else:
        lines += [
            "| Stratum | Minimum | Selected | Excluded | Blocked | Unavailable | Unresolved | State |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for gap in record["coverage_gaps"]:
            lines.append(
                "| {dimension} {value} | {minimum} | {selected} | {excluded} | "
                "{blocked} | {unavailable} | {unresolved} | `{state}` |".format(
                    dimension=gap["dimension"],
                    value=gap["value"],
                    minimum=gap["minimum_selected"],
                    selected=gap["selected_candidates"],
                    excluded=gap["excluded_candidates"],
                    blocked=gap["blocked_candidates"],
                    unavailable=gap["unavailable_candidates"],
                    unresolved=gap["unresolved_candidates"],
                    state=gap["state"],
                )
            )
    lines += ["", "## Duplicate findings", ""]
    lines.append(
        "No index snapshot was in scope, so no duplicate evidence was read."
        if snapshot["corpus_index_id"] is None
        else f"{len(record['duplicate_findings'])} finding(s), every one "
        "`requires_human_review` with `merge_action: none`."
    )
    lines += ["", "## Quality findings", ""]
    counts: dict[str, int] = {}
    for finding in record["quality_findings"]:
        key = f"{finding['check']}:{finding['state']}"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        lines.append("No index snapshot was in scope, so no check could run.")
    else:
        lines += ["| Check and state | Count |", "|---|---|"]
        for key, count in sorted(counts.items()):
            lines.append(f"| `{key}` | {count} |")
    lines += ["", "## Rights-filtered retrieval cases", ""]
    if not record["retrieval_cases"]:
        lines.append("No retrieval case was declared for this run.")
    else:
        lines += ["| Case | Audience | Outcome |", "|---|---|---|"]
        for case in record["retrieval_cases"]:
            lines.append(
                f"| `{case['retrieval_case_id']}` | {case['audience']} | "
                f"`{case['outcome']}` |"
            )
    lines += ["", "## Prioritized next actions", ""]
    if not record["recommendations"]:
        lines.append("No action is outstanding for this snapshot.")
    else:
        lines += [
            "| Priority | Next action | Blocker | Source | References |",
            "|---|---|---|---|---|",
        ]
        for item in record["recommendations"]:
            lines.append(
                f"| {item['priority']} | `{item['next_action_class']}` | "
                f"`{item['blocker_class']}` | {item['source_id'] or '—'} | "
                f"{len(item['finding_refs'])} |"
            )
    if record["caveats"]:
        lines += ["", "## Caveats", ""]
        lines += [f"- {item}" for item in record["caveats"]]
    text = "\n".join(lines) + "\n"
    if sanitize(text, environ={}) != text:
        raise EvaluationError("evaluation report contains private data")
    return text
