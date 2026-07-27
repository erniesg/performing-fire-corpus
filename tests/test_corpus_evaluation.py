from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.evaluation import (  # noqa: E402
    EVALUATION_POLICY_VERSION,
    NEXT_ACTION_CLASSES,
    QUALITY_CHECKS,
    EvaluationError,
    build_evaluation_run,
    detect_duplicate_findings,
    evaluate_corpus_quality,
    evaluate_retrieval_cases,
    evaluate_selection_coverage,
    evaluate_source_completeness,
    prioritize_recommendations,
    render_evaluation_report,
    validate_evaluation_run,
)
from performing_fire_corpus.search_index import (  # noqa: E402
    build_index_snapshot,
    index_format_checker,
    record_sha256,
)
from performing_fire_corpus.search_service import (  # noqa: E402
    BundleAuthority,
    build_corpus_index,
)
from performing_fire_corpus.selection import evaluate_selection  # noqa: E402

SCHEMA_DIR = ROOT / "schemas" / "v1"
EVALUATION_SCHEMAS = (
    "coverage-gap",
    "duplicate-finding",
    "evaluation-metric",
    "evaluation-recommendation",
    "evaluation-run",
    "quality-finding",
    "retrieval-case",
)

NOW = "2026-07-24T00:00:00.125000Z"
EXPIRES = "2026-08-24T00:00:00.125000Z"
STALE_EXPIRES = "2026-07-25T00:00:00Z"
EVALUATED = "2026-07-25T12:00:00Z"
EVENT_TIME = "2026-07-24T06:00:00Z"
EVENT_BUILT = "2026-07-24T12:00:00Z"

ORIGIN_SHA = "a" * 64
RIGHTS_TITLE = "b" * 64
RIGHTS_SUMMARY = "c" * 64
DERIVED_SHA = "d" * 64
RIGHTS_CREATOR = "e" * 64
RIGHTS_TRANSCRIPT = "f" * 64
INVENTORY_SHA = "1" * 64
OBSERVATION_SHA = "2" * 64
AUTHORITY_SHA = "3" * 64

TITLE_VALUE = "Synthetic catalogue title"
SUMMARY_VALUE = "Synthetic derived observation summary"
CREATOR_VALUE = "Synthetic shared creator"
OTHER_CREATOR_VALUE = "Synthetic other creator"
FOURTH_CREATOR_VALUE = "Synthetic fourth creator"
TRANSCRIPT_VALUE = "Synthetic derived transcript"

DERIVED_KEY_A = (
    "corpus-staging/v1/derived/njp-video-library/asset_001"
    f"/transform_ocr_v1/{DERIVED_SHA}"
)
DERIVED_KEY_C = (
    "corpus-staging/v1/derived/antiegg-fluxus/asset_001"
    f"/transform_ocr_v1/{DERIVED_SHA}"
)


def load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def value_hash(text: str) -> str:
    return hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()


def lineage_hash(edges: list[dict[str, object]]) -> str:
    return record_sha256(
        {
            "event_lineage_edges": sorted(
                copy.deepcopy(edges),
                key=lambda item: str(item["provenance_edge_id"]),
            )
        }
    )


# --- completeness reports -------------------------------------------------


def completeness_report(
    *,
    suffix: str,
    source_id: str,
    endpoint_id: str,
    state: str = "bounded_partial",
    stop_reason: str = "page_budget_exhausted",
    observed: int = 29,
    expected_total: int | None = None,
    unvisited_remainder: int | None = None,
    blocked_pages: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "completeness_report",
        "report_id": f"completeness_report_{suffix}",
        "run_id": f"discovery_run_{suffix}",
        "policy_snapshot_id": f"policy_snapshot_{suffix}",
        "source_id": source_id,
        "endpoint_id": endpoint_id,
        "adapter_version": "1.0.0",
        "generated_at": NOW,
        "state": state,
        "stop_reason": stop_reason,
        "expected_total": expected_total,
        "observed_unique_records": observed,
        "unvisited_remainder": unvisited_remainder,
        "pages_committed": 3,
        "blocked_pages": blocked_pages,
        "terminal_pages": 1,
        "duplicate_records": 1,
        "rejected_records": 0,
        "requests_attempted": 3,
    }


def njp_reports() -> list[dict[str, object]]:
    """One complete endpoint, one blocked source, and one bounded remainder."""

    return [
        completeness_report(
            suffix="njp_center_main_home",
            source_id="njp-center-main",
            endpoint_id="njp-center-main-home",
            state="complete_for_observed_endpoint",
            stop_reason="terminal_page",
            observed=29,
            expected_total=29,
            unvisited_remainder=0,
        ),
        completeness_report(
            suffix="njp_center_video_archive_page",
            source_id="njp-center-video-archive",
            endpoint_id="njp-center-video-archive-page",
            state="blocked",
            stop_reason="robots_expired",
            observed=0,
            blocked_pages=1,
        ),
        completeness_report(
            suffix="njp_video_library_home",
            source_id="njp-video-library",
            endpoint_id="njp-video-library-home",
            state="bounded_partial",
            stop_reason="page_budget_exhausted",
            observed=12,
        ),
    ]


# --- index fixtures -------------------------------------------------------


def field(
    *,
    field_id: str,
    name: str,
    value: str,
    origin_class: str,
    provenance_edge_id: str,
    rights_snapshot_sha256: str,
    retention_class: str = "inventory_metadata",
) -> dict[str, object]:
    return {
        "field_id": field_id,
        "name": name,
        "value": value,
        "origin_class": origin_class,
        "provenance_edge_id": provenance_edge_id,
        "rights_snapshot_sha256": rights_snapshot_sha256,
        "consent_snapshot_sha256": None,
        "retention_class": retention_class,
        "visibility_class": "reviewed_metadata",
        "review_trigger": "Re-review when source authority changes.",
    }


def edge(
    *,
    edge_id: str,
    index_document_id: str,
    field_id: str,
    field_name: str,
    value: str,
    source_id: str,
    asset_id: str,
    origin_class: str = "factual_source_metadata",
    origin_record_id: str = "observation_001",
    transformation_id: str | None = None,
    inputs: list[str] | None = None,
    evidence_expires_at: str = EXPIRES,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "provenance_edge",
        "provenance_edge_id": edge_id,
        "index_document_id": index_document_id,
        "field_id": field_id,
        "field_name": field_name,
        "field_value_sha256": value_hash(value),
        "source_id": source_id,
        "asset_id": asset_id,
        "origin_class": origin_class,
        "origin_record_id": origin_record_id,
        "origin_record_sha256": ORIGIN_SHA,
        "transformation_id": transformation_id,
        "input_provenance_edge_ids": sorted(inputs or []),
        "evidence_at": NOW,
        "evidence_expires_at": evidence_expires_at,
    }


def policy(
    *,
    policy_id: str,
    index_document_id: str,
    field_id: str,
    rights_snapshot_sha256: str,
    allowed_operations: list[str],
    allowed_audiences: list[str],
    expires_at: str = EXPIRES,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "visibility_policy",
        "visibility_policy_id": policy_id,
        "index_document_id": index_document_id,
        "field_id": field_id,
        "rights_snapshot_sha256": rights_snapshot_sha256,
        "rights_state": "approved",
        "consent_snapshot_sha256": None,
        "consent_state": "not_required",
        "retention_state": "retain",
        "allowed_operations": sorted(allowed_operations),
        "allowed_audiences": sorted(allowed_audiences),
        "decided_at": NOW,
        "expires_at": expires_at,
        "evidence_expires_at": expires_at,
        "review_trigger": "Re-review when source authority changes.",
    }


ALL_INDEX_OPERATIONS = [
    "indexing",
    "score_feature_value",
    "score_generation",
    "search_visibility",
    "snippet_render",
]


def document_a() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_asset_001",
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "selection_state": "selected_rich_corpus",
        "duplicate_cluster_id": "duplicate_cluster_001",
        "languages": ["ko"],
        "period": "1980s",
        "mediums": ["video"],
        "fields": [
            field(
                field_id="field_summary",
                name="summary",
                value=SUMMARY_VALUE,
                origin_class="derived_observation",
                provenance_edge_id="provenance_edge_summary",
                rights_snapshot_sha256=RIGHTS_SUMMARY,
                retention_class="selected_derived",
            ),
            field(
                field_id="field_title",
                name="title",
                value=TITLE_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_title",
                rights_snapshot_sha256=RIGHTS_TITLE,
            ),
        ],
    }


def document_b() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_asset_002",
        "source_id": "njp-center-main",
        "asset_id": "asset_002",
        "selection_state": "inventory_only",
        "duplicate_cluster_id": "duplicate_cluster_001",
        "languages": ["en"],
        "period": "1980s",
        "mediums": ["video"],
        "fields": [
            field(
                field_id="field_creator_002",
                name="creator",
                value=CREATOR_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_creator_002",
                rights_snapshot_sha256=RIGHTS_CREATOR,
                # Deliberate mismatch: an inventory-only record carries a
                # selected-corpus retention class.
                retention_class="selected_derived",
            ),
            field(
                field_id="field_title_002",
                name="title",
                value=TITLE_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_title_002",
                rights_snapshot_sha256=RIGHTS_TITLE,
            ),
        ],
    }


def document_c() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_asset_003",
        "source_id": "antiegg-fluxus",
        "asset_id": "asset_001",
        "selection_state": "inventory_only",
        "duplicate_cluster_id": None,
        # Deliberate gap: no resolved language.
        "languages": [],
        "period": "1990s",
        "mediums": ["article"],
        "fields": [
            field(
                field_id="field_creator_003",
                name="creator",
                value=OTHER_CREATOR_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_creator_003",
                rights_snapshot_sha256=RIGHTS_CREATOR,
            ),
            field(
                field_id="field_title_003",
                name="title",
                value=TITLE_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_title_003",
                rights_snapshot_sha256=RIGHTS_TITLE,
            ),
            field(
                field_id="field_transcript_003",
                name="transcript",
                value=TRANSCRIPT_VALUE,
                origin_class="derived_observation",
                provenance_edge_id="provenance_edge_transcript_003",
                rights_snapshot_sha256=RIGHTS_TRANSCRIPT,
            ),
        ],
    }


def document_d() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_asset_004",
        "source_id": "njp-youtube-official",
        "asset_id": "asset_004",
        "selection_state": "inventory_only",
        "duplicate_cluster_id": None,
        "languages": ["en"],
        "period": "2000s",
        "mediums": ["video"],
        # Deliberate gap: no title field at all.
        "fields": [
            field(
                field_id="field_creator_004",
                name="creator",
                value=FOURTH_CREATOR_VALUE,
                origin_class="factual_source_metadata",
                provenance_edge_id="provenance_edge_creator_004",
                rights_snapshot_sha256=RIGHTS_CREATOR,
            )
        ],
    }


def base_edges() -> list[dict[str, object]]:
    return [
        edge(
            edge_id="provenance_edge_creator_002",
            index_document_id="index_document_asset_002",
            field_id="field_creator_002",
            field_name="creator",
            value=CREATOR_VALUE,
            source_id="njp-center-main",
            asset_id="asset_002",
            origin_record_id="observation_002",
        ),
        edge(
            edge_id="provenance_edge_creator_003",
            index_document_id="index_document_asset_003",
            field_id="field_creator_003",
            field_name="creator",
            value=OTHER_CREATOR_VALUE,
            source_id="antiegg-fluxus",
            asset_id="asset_001",
            origin_record_id="observation_003",
        ),
        edge(
            edge_id="provenance_edge_creator_004",
            index_document_id="index_document_asset_004",
            field_id="field_creator_004",
            field_name="creator",
            value=FOURTH_CREATOR_VALUE,
            source_id="njp-youtube-official",
            asset_id="asset_004",
            origin_record_id="observation_004",
        ),
        edge(
            edge_id="provenance_edge_summary",
            index_document_id="index_document_asset_001",
            field_id="field_summary",
            field_name="summary",
            value=SUMMARY_VALUE,
            source_id="njp-video-library",
            asset_id="asset_001",
            origin_class="derived_observation",
            origin_record_id="derived_result_001",
            transformation_id="transform_ocr_v1",
            inputs=["provenance_edge_title"],
        ),
        edge(
            edge_id="provenance_edge_title",
            index_document_id="index_document_asset_001",
            field_id="field_title",
            field_name="title",
            value=TITLE_VALUE,
            source_id="njp-video-library",
            asset_id="asset_001",
        ),
        edge(
            edge_id="provenance_edge_title_002",
            index_document_id="index_document_asset_002",
            field_id="field_title_002",
            field_name="title",
            value=TITLE_VALUE,
            source_id="njp-center-main",
            asset_id="asset_002",
            origin_record_id="observation_002",
        ),
        edge(
            edge_id="provenance_edge_title_003",
            index_document_id="index_document_asset_003",
            field_id="field_title_003",
            field_name="title",
            value=TITLE_VALUE,
            source_id="antiegg-fluxus",
            asset_id="asset_001",
            origin_record_id="observation_003",
        ),
        edge(
            edge_id="provenance_edge_transcript_003",
            index_document_id="index_document_asset_003",
            field_id="field_transcript_003",
            field_name="transcript",
            value=TRANSCRIPT_VALUE,
            source_id="antiegg-fluxus",
            asset_id="asset_001",
            origin_class="derived_observation",
            origin_record_id="derived_result_003",
            transformation_id="transform_ocr_v1",
            # Deliberate cross-record fusion.
            inputs=["provenance_edge_title", "provenance_edge_title_003"],
        ),
    ]


def base_policies() -> list[dict[str, object]]:
    return [
        policy(
            policy_id="visibility_policy_creator_002",
            index_document_id="index_document_asset_002",
            field_id="field_creator_002",
            rights_snapshot_sha256=RIGHTS_CREATOR,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator"],
        ),
        policy(
            policy_id="visibility_policy_creator_003",
            index_document_id="index_document_asset_003",
            field_id="field_creator_003",
            rights_snapshot_sha256=RIGHTS_CREATOR,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator"],
        ),
        policy(
            policy_id="visibility_policy_creator_004",
            index_document_id="index_document_asset_004",
            field_id="field_creator_004",
            rights_snapshot_sha256=RIGHTS_CREATOR,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator", "researcher"],
            # Deliberately stale before the evaluation time.
            expires_at=STALE_EXPIRES,
        ),
        policy(
            policy_id="visibility_policy_summary",
            index_document_id="index_document_asset_001",
            field_id="field_summary",
            rights_snapshot_sha256=RIGHTS_SUMMARY,
            allowed_operations=ALL_INDEX_OPERATIONS,
            allowed_audiences=["operator", "researcher"],
        ),
        policy(
            policy_id="visibility_policy_title",
            index_document_id="index_document_asset_001",
            field_id="field_title",
            rights_snapshot_sha256=RIGHTS_TITLE,
            allowed_operations=["search_visibility"],
            allowed_audiences=["researcher"],
        ),
        policy(
            policy_id="visibility_policy_title_002",
            index_document_id="index_document_asset_002",
            field_id="field_title_002",
            rights_snapshot_sha256=RIGHTS_TITLE,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator"],
        ),
        policy(
            policy_id="visibility_policy_title_003",
            index_document_id="index_document_asset_003",
            field_id="field_title_003",
            rights_snapshot_sha256=RIGHTS_TITLE,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator"],
        ),
        policy(
            policy_id="visibility_policy_transcript_003",
            index_document_id="index_document_asset_003",
            field_id="field_transcript_003",
            rights_snapshot_sha256=RIGHTS_TRANSCRIPT,
            allowed_operations=["search_visibility"],
            allowed_audiences=["operator"],
        ),
    ]


def base_cluster() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "duplicate_cluster",
        "duplicate_cluster_id": "duplicate_cluster_001",
        "canonicalization_version": "canonicalization_v1",
        "evidence_summary": "Synthetic exact-metadata match.",
        "members": [
            {
                "index_document_id": "index_document_asset_001",
                "source_id": "njp-video-library",
                "asset_id": "asset_001",
                "provenance_edge_ids": [
                    "provenance_edge_summary",
                    "provenance_edge_title",
                ],
            },
            {
                "index_document_id": "index_document_asset_002",
                "source_id": "njp-center-main",
                "asset_id": "asset_002",
                "provenance_edge_ids": [
                    "provenance_edge_creator_002",
                    "provenance_edge_title_002",
                ],
            },
        ],
    }


def derived_object(*, source_id: str, object_key: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "derived_object",
        "source_id": source_id,
        "asset_id": "asset_001",
        "transformation_id": "transform_ocr_v1",
        "input_receipt_ids": ["receipt_raw_asset_001"],
        "object_key": object_key,
        "sha256": DERIVED_SHA,
        "byte_size": 1024,
        "media_type": "text/plain",
        "rights_snapshot_sha256": RIGHTS_SUMMARY,
        "retention_class": "selected_derived",
        "retrieval_decision": "approved",
        "redaction_state": "reviewed_redacted",
    }


def object_receipt(*, receipt_id: str, source_id: str, object_key: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "object_receipt",
        "receipt_id": receipt_id,
        "object_kind": "derived",
        "source_id": source_id,
        "asset_id": "asset_001",
        "transformation_id": "transform_ocr_v1",
        "object_key": object_key,
        "byte_size": 1024,
        "media_type": "text/plain",
        "sha256": DERIVED_SHA,
        "rights_snapshot_sha256": RIGHTS_SUMMARY,
        "retention_class": "selected_derived",
        "creation_run_id": "run_synthetic_001",
        "retrieval_decision": "approved",
        "evidence_ref": "evidence:synthetic-derived-001",
        "verification_state": "verified",
        "create_disposition": "created",
    }


def base_derived_objects() -> list[dict[str, object]]:
    return [
        derived_object(source_id="njp-video-library", object_key=DERIVED_KEY_A),
        derived_object(source_id="antiegg-fluxus", object_key=DERIVED_KEY_C),
    ]


def base_receipts() -> list[dict[str, object]]:
    return [
        object_receipt(
            receipt_id="receipt_derived_asset_001",
            source_id="njp-video-library",
            object_key=DERIVED_KEY_A,
        ),
        object_receipt(
            receipt_id="receipt_derived_asset_003",
            source_id="antiegg-fluxus",
            object_key=DERIVED_KEY_C,
        ),
    ]


def coverage_target(
    *,
    target_id: str,
    dimension: str,
    value: str,
    minimum_selected: int = 1,
    priority: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "coverage_target",
        "coverage_target_id": target_id,
        "dimension": dimension,
        "value": value,
        "minimum_selected": minimum_selected,
        "priority": priority,
        "rationale": "Synthetic stratum for the offline evaluation suite.",
    }


def base_authority() -> BundleAuthority:
    return BundleAuthority(
        documents=[document_a(), document_b(), document_c(), document_d()],
        visibility_policies=base_policies(),
        provenance_edges=base_edges(),
        deletion_events=[],
        object_receipts=base_receipts(),
    )


def base_snapshot(resolver: BundleAuthority) -> dict[str, object]:
    return build_index_snapshot(
        snapshot_id="index_snapshot_001",
        documents=[document_a(), document_b(), document_c(), document_d()],
        provenance_edges=base_edges(),
        visibility_policies=base_policies(),
        duplicate_clusters=[base_cluster()],
        deletion_events=[],
        built_at=NOW,
        authority_resolver=resolver,
    )


def base_index(
    resolver: BundleAuthority, *, derived_objects: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return build_corpus_index(
        index_id="corpus_index_001",
        snapshot=base_snapshot(resolver),
        built_at=NOW,
        authority_resolver=resolver,
        derived_objects=(
            base_derived_objects() if derived_objects is None else derived_objects
        ),
        object_authority=resolver,
        coverage_targets=[
            coverage_target(
                target_id="coverage_period_1980s", dimension="period", value="1980s"
            )
        ],
    )


# --- minimal single-record index used for generation drift and deletion ---


def minimal_document(*, with_summary: bool = True) -> dict[str, object]:
    fields = [
        field(
            field_id="field_min_summary",
            name="summary",
            value=SUMMARY_VALUE,
            origin_class="derived_observation",
            provenance_edge_id="provenance_edge_min_summary",
            rights_snapshot_sha256=RIGHTS_SUMMARY,
            retention_class="selected_derived",
        ),
        field(
            field_id="field_min_title",
            name="title",
            value=TITLE_VALUE,
            origin_class="factual_source_metadata",
            provenance_edge_id="provenance_edge_min_title",
            rights_snapshot_sha256=RIGHTS_TITLE,
        ),
    ]
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_min",
        "source_id": "njp-video-library",
        "asset_id": "asset_min",
        "selection_state": "selected_rich_corpus",
        "duplicate_cluster_id": None,
        "languages": ["ko"],
        "period": "1980s",
        "mediums": ["video"],
        "fields": fields if with_summary else fields[1:],
    }


def minimal_summary_edge() -> dict[str, object]:
    return edge(
        edge_id="provenance_edge_min_summary",
        index_document_id="index_document_min",
        field_id="field_min_summary",
        field_name="summary",
        value=SUMMARY_VALUE,
        source_id="njp-video-library",
        asset_id="asset_min",
        origin_class="derived_observation",
        origin_record_id="derived_result_min",
        transformation_id="transform_ocr_v1",
        inputs=["provenance_edge_min_title"],
    )


def minimal_title_edge() -> dict[str, object]:
    return edge(
        edge_id="provenance_edge_min_title",
        index_document_id="index_document_min",
        field_id="field_min_title",
        field_name="title",
        value=TITLE_VALUE,
        source_id="njp-video-library",
        asset_id="asset_min",
        origin_record_id="observation_min",
    )


def minimal_policies(*, with_summary: bool = True) -> list[dict[str, object]]:
    policies = [
        policy(
            policy_id="visibility_policy_min_summary",
            index_document_id="index_document_min",
            field_id="field_min_summary",
            rights_snapshot_sha256=RIGHTS_SUMMARY,
            allowed_operations=ALL_INDEX_OPERATIONS,
            allowed_audiences=["operator", "researcher"],
        ),
        policy(
            policy_id="visibility_policy_min_title",
            index_document_id="index_document_min",
            field_id="field_min_title",
            rights_snapshot_sha256=RIGHTS_TITLE,
            allowed_operations=["search_visibility"],
            allowed_audiences=["researcher"],
        ),
    ]
    return policies if with_summary else policies[1:]


def minimal_authority(*, with_summary: bool = True) -> BundleAuthority:
    edges = (
        [minimal_summary_edge(), minimal_title_edge()]
        if with_summary
        else [minimal_title_edge()]
    )
    return BundleAuthority(
        documents=[minimal_document(with_summary=with_summary)],
        visibility_policies=minimal_policies(with_summary=with_summary),
        provenance_edges=edges,
        deletion_events=[],
        object_receipts=[],
    )


def minimal_index(*, with_summary: bool = True, index_id: str) -> dict[str, object]:
    resolver = minimal_authority(with_summary=with_summary)
    edges = (
        [minimal_summary_edge(), minimal_title_edge()]
        if with_summary
        else [minimal_title_edge()]
    )
    snapshot = build_index_snapshot(
        snapshot_id="index_snapshot_min" if with_summary else "index_snapshot_min_red",
        documents=[minimal_document(with_summary=with_summary)],
        provenance_edges=edges,
        visibility_policies=minimal_policies(with_summary=with_summary),
        duplicate_clusters=[],
        deletion_events=[],
        built_at=NOW,
        authority_resolver=resolver,
    )
    return build_corpus_index(
        index_id=index_id,
        snapshot=snapshot,
        built_at=NOW,
        authority_resolver=resolver,
    )


def deletion_event(
    *,
    event_id: str,
    field_id: str,
    lineage: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "deletion_event",
        "deletion_event_id": event_id,
        "index_document_id": "index_document_min",
        "field_id": field_id,
        "reason_code": "rights_revoked",
        "authority_snapshot_sha256": lineage_hash(lineage),
        "occurred_at": EVENT_TIME,
        "reindex_action": "remove_exact_field",
        "replacement_document_sha256": None,
        "replacement_provenance_edge_sha256": None,
        "replacement_visibility_policy_sha256": None,
    }


def index_with_emptied_document() -> dict[str, object]:
    """One index generation where a removal left an empty record shell."""

    lineage = [minimal_title_edge()]
    event = deletion_event(
        event_id="deletion_event_min_title",
        field_id="field_min_title",
        lineage=lineage,
    )
    shell = dict(minimal_document(with_summary=False), fields=[])
    resolver = BundleAuthority(
        documents=[shell],
        visibility_policies=[],
        provenance_edges=[],
        deletion_events=[event],
        object_receipts=[],
    )
    snapshot = build_index_snapshot(
        snapshot_id="index_snapshot_min_shell",
        documents=[minimal_document(with_summary=False)],
        provenance_edges=[],
        visibility_policies=[],
        duplicate_clusters=[],
        deletion_events=[event],
        built_at=EVENT_BUILT,
        authority_resolver=resolver,
        event_lineage_edges=lineage,
    )
    return build_corpus_index(
        index_id="corpus_index_min_shell",
        snapshot=snapshot,
        built_at=EVENT_BUILT,
        authority_resolver=resolver,
    )


# --- selection manifest fixture ------------------------------------------


class SyntheticCandidateRegistry:
    def __init__(self, candidates: list[dict[str, object]]) -> None:
        self.resolved = {
            (value["source_id"], value["asset_id"]): {
                key: copy.deepcopy(child)
                for key, child in value.items()
                if key
                not in {
                    "schema_version",
                    "record_type",
                    "candidate_id",
                    "candidate_sha256",
                    "source_id",
                    "asset_id",
                }
            }
            for value in candidates
        }

    def resolve_selection_candidate(
        self, *, source_id: str, asset_id: str
    ) -> dict[str, object]:
        return copy.deepcopy(self.resolved[(source_id, asset_id)])

    def resolve_selection_review_override(
        self, *, candidate_id: str, candidate_sha256: str
    ) -> dict[str, object] | None:
        return None


def selection_candidate(
    suffix: str,
    *,
    source_id: str = "njp-video-library",
    period: str = "1980s",
    rights_state: str = "approved",
    inventory_state: str = "observed",
    retrieval_state: str = "available",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "selection_candidate",
        "candidate_id": f"candidate_{suffix}",
        "candidate_sha256": "0" * 64,
        "source_id": source_id,
        "asset_id": f"asset_{suffix}",
        "inventory_observation_id": f"observation_{suffix}",
        "inventory_observation_sha256": OBSERVATION_SHA,
        "inventory_snapshot_sha256": INVENTORY_SHA,
        "inventory_state": inventory_state,
        "retrieval_state": retrieval_state,
        "dimensions": {
            "source": source_id,
            "period": period,
            "languages": ["ko"],
            "mediums": ["video"],
            "topics": ["performance"],
            "performance_contexts": ["broadcast"],
        },
        "technical_quality": "medium",
        "duplicate_cluster_id": None,
        "source_governance_state": "approved",
        "source_governance_snapshot_sha256": AUTHORITY_SHA,
        "source_governance_expires_at": EXPIRES,
        "rights_state": rights_state,
        "rights_snapshot_sha256": AUTHORITY_SHA,
        "rights_expires_at": EXPIRES,
        "retention_state": "approved",
        "retention_snapshot_sha256": AUTHORITY_SHA,
        "retention_expires_at": EXPIRES,
        "privacy_state": "approved",
        "privacy_snapshot_sha256": AUTHORITY_SHA,
        "privacy_expires_at": EXPIRES,
        "transformation_state": "approved",
        "transformation_snapshot_sha256": AUTHORITY_SHA,
        "transformation_expires_at": EXPIRES,
        "pipeline_proof": False,
        "evidence_scope": "Synthetic offline evaluation fixture.",
    }


def selection_manifest() -> dict[str, object]:
    candidates = [
        selection_candidate("001"),
        selection_candidate("002", rights_state="blocked"),
        selection_candidate("003", period="1990s", retrieval_state="unavailable"),
    ]
    registry = SyntheticCandidateRegistry(candidates)
    return evaluate_selection(
        candidates,
        [
            coverage_target(
                target_id="coverage_period_1980s",
                dimension="period",
                value="1980s",
                minimum_selected=2,
                priority=1,
            ),
            coverage_target(
                target_id="coverage_period_1990s",
                dimension="period",
                value="1990s",
                minimum_selected=1,
                priority=2,
            ),
        ],
        inventory_snapshot_sha256=INVENTORY_SHA,
        policy_version="selection_v1",
        decision_authority="offline_reference",
        decided_at=NOW,
        expires_at=EXPIRES,
        review_trigger="Re-review when source authority changes.",
        authority_resolver=registry,
    )


# --- retrieval case definitions ------------------------------------------


def researcher_case() -> dict[str, object]:
    return {
        "retrieval_case_id": "retrieval_case_researcher_allowed",
        "audience": "researcher",
        "expected_visible_field_ids": ["field_summary", "field_title"],
        "forbidden_field_ids": [
            "field_creator_002",
            "field_creator_003",
            "field_creator_004",
            "field_title_002",
            "field_title_003",
            "field_transcript_003",
        ],
        "forbidden_facet_values": ["1990s", "2000s"],
        "checked_surfaces": ["facets", "results", "score_export"],
        "rationale": (
            "A researcher sees only the fields their audience grant covers, and "
            "no operator-only or expired field appears anywhere."
        ),
    }


class EvaluationSchemaContractTests(unittest.TestCase):
    def test_every_evaluation_schema_is_versioned_and_strict(self) -> None:
        for name in EVALUATION_SCHEMAS:
            with self.subTest(schema=name):
                schema = load_schema(name)
                Draft202012Validator.check_schema(schema)
                self.assertEqual(
                    f"https://performing-fire-corpus.invalid/schemas/v1/{name}.json",
                    schema["$id"],
                )
                self.assertEqual(False, schema["additionalProperties"])
                self.assertEqual({"const": 1}, schema["properties"]["schema_version"])

    def test_public_vocabularies_match_their_schemas(self) -> None:
        actions = load_schema("evaluation-recommendation")["properties"][
            "next_action_class"
        ]["enum"]
        self.assertEqual(list(NEXT_ACTION_CLASSES), actions)
        self.assertNotIn("bulk_acquisition", actions)
        checks = load_schema("quality-finding")["properties"]["check"]["enum"]
        self.assertEqual(list(QUALITY_CHECKS), checks)

    def test_duplicate_findings_can_never_declare_a_merge(self) -> None:
        schema = load_schema("duplicate-finding")["properties"]
        self.assertEqual({"const": "requires_human_review"}, schema["review_state"])
        self.assertEqual({"const": "none"}, schema["merge_action"])

    def test_unknown_field_is_rejected_by_every_evaluation_schema(self) -> None:
        run = build_evaluation_run(
            evaluated_at=EVALUATED,
            completeness_reports=njp_reports(),
        )
        validator = Draft202012Validator(
            load_schema("evaluation-run"), format_checker=index_format_checker()
        )
        validator.validate(run)
        with self.assertRaises(ValidationError):
            validator.validate(dict(run, unexpected="not in the contract"))

        metric_validator = Draft202012Validator(
            load_schema("evaluation-metric"), format_checker=index_format_checker()
        )
        with self.assertRaises(ValidationError):
            metric_validator.validate(
                dict(run["metrics"][0], unexpected="not in the contract")
            )


class SourceCompletenessMetricTests(unittest.TestCase):
    def test_bounded_partial_never_becomes_a_whole_source_total(self) -> None:
        report = completeness_report(
            suffix="njp_center_main_home",
            source_id="njp-center-main",
            endpoint_id="njp-center-main-home",
            state="bounded_partial",
        )
        metrics = evaluate_source_completeness([report])
        for metric in metrics:
            self.assertFalse(metric["is_whole_source_total"])
            self.assertIsNone(metric["denominator"])
        source_scoped = [
            item
            for item in metrics
            if item["scope"] == "source"
            and item["metric_key"] == "observed_unique_records"
        ]
        self.assertEqual(1, len(source_scoped))
        self.assertEqual(29, source_scoped[0]["observed_value"])
        self.assertEqual("bounded_partial", source_scoped[0]["completeness_state"])
        self.assertIn("not a whole-source total", source_scoped[0]["rationale"])

    def test_unknown_remainder_is_reported_as_unknown_not_zero(self) -> None:
        metrics = evaluate_source_completeness(
            [
                completeness_report(
                    suffix="njp_center_main_home",
                    source_id="njp-center-main",
                    endpoint_id="njp-center-main-home",
                    unvisited_remainder=None,
                )
            ]
        )
        remainder = next(
            item
            for item in metrics
            if item["scope"] == "source" and item["metric_key"] == "bounded_remainder"
        )
        self.assertIsNone(remainder["observed_value"])
        self.assertIn("unknown", remainder["rationale"])

    def test_a_whole_source_total_needs_an_exhaustive_endpoint_declaration(
        self,
    ) -> None:
        complete = completeness_report(
            suffix="njp_center_main_home",
            source_id="njp-center-main",
            endpoint_id="njp-center-main-home",
            state="complete_for_observed_endpoint",
            stop_reason="terminal_page",
            expected_total=29,
            unvisited_remainder=0,
        )
        undeclared = next(
            item
            for item in evaluate_source_completeness([complete])
            if item["scope"] == "source"
            and item["metric_key"] == "observed_unique_records"
        )
        self.assertFalse(undeclared["is_whole_source_total"])
        self.assertIsNone(undeclared["denominator"])
        self.assertIn("not declared exhaustive", undeclared["rationale"])

        metrics = evaluate_source_completeness(
            [complete], exhaustive_endpoint_sources=["njp-center-main"]
        )
        source_scoped = next(
            item
            for item in metrics
            if item["scope"] == "source"
            and item["metric_key"] == "observed_unique_records"
        )
        self.assertTrue(source_scoped["is_whole_source_total"])
        self.assertEqual(29, source_scoped["denominator"])

        endpoint_scoped = next(
            item for item in metrics if item["scope"] == "source_endpoint"
        )
        self.assertFalse(endpoint_scoped["is_whole_source_total"])

        with self.assertRaises(EvaluationError):
            evaluate_source_completeness(
                [complete], exhaustive_endpoint_sources=["antiegg-fluxus"]
            )

        # ANTIEGG declares four canonical endpoints; one complete endpoint is
        # never the whole source even when declared exhaustive.
        partial_source = evaluate_source_completeness(
            [
                completeness_report(
                    suffix="antiegg_posts_api",
                    source_id="antiegg-fluxus",
                    endpoint_id="antiegg-posts-api",
                    state="complete_for_observed_endpoint",
                    stop_reason="terminal_page",
                    expected_total=2,
                    observed=2,
                    unvisited_remainder=0,
                )
            ],
            exhaustive_endpoint_sources=["antiegg-fluxus"],
        )
        aggregate = next(
            item
            for item in partial_source
            if item["scope"] == "source"
            and item["metric_key"] == "observed_unique_records"
        )
        self.assertFalse(aggregate["is_whole_source_total"])
        self.assertIsNone(aggregate["denominator"])
        self.assertIn("antiegg-media-api", aggregate["rationale"])

    def test_one_endpoint_cannot_report_completeness_twice(self) -> None:
        report = completeness_report(
            suffix="njp_center_main_home",
            source_id="njp-center-main",
            endpoint_id="njp-center-main-home",
        )
        with self.assertRaises(EvaluationError):
            evaluate_source_completeness([report, copy.deepcopy(report)])


class SelectionCoverageGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gaps = evaluate_selection_coverage(selection_manifest())
        self.by_target = {
            str(item["coverage_target_id"]): item for item in self.gaps
        }

    def test_blocked_unavailable_and_shortfall_are_counted_separately(self) -> None:
        eighties = self.by_target["coverage_period_1980s"]
        self.assertEqual(2, eighties["observed_candidates"])
        self.assertEqual(1, eighties["selected_candidates"])
        self.assertEqual(1, eighties["excluded_candidates"])
        self.assertEqual(1, eighties["blocked_candidates"])
        self.assertEqual(0, eighties["unavailable_candidates"])
        self.assertEqual(1, eighties["shortfall"])
        self.assertEqual("blocked", eighties["state"])
        self.assertEqual("rights_review", eighties["next_action_class"])

        nineties = self.by_target["coverage_period_1990s"]
        self.assertEqual(1, nineties["unavailable_candidates"])
        self.assertEqual(0, nineties["blocked_candidates"])
        self.assertEqual("unavailable", nineties["state"])
        self.assertEqual("human_decision", nineties["next_action_class"])

    def test_a_met_stratum_carries_no_next_action(self) -> None:
        candidates = [selection_candidate("001")]
        registry = SyntheticCandidateRegistry(candidates)
        manifest = evaluate_selection(
            candidates,
            [
                coverage_target(
                    target_id="coverage_period_1980s",
                    dimension="period",
                    value="1980s",
                )
            ],
            inventory_snapshot_sha256=INVENTORY_SHA,
            policy_version="selection_v1",
            decision_authority="offline_reference",
            decided_at=NOW,
            expires_at=EXPIRES,
            review_trigger="Re-review when source authority changes.",
            authority_resolver=registry,
        )
        gap = evaluate_selection_coverage(manifest)[0]
        self.assertEqual("met", gap["state"])
        self.assertEqual(0, gap["shortfall"])
        self.assertIsNone(gap["next_action_class"])

    def test_blocked_coverage_never_recommends_a_bounded_adapter_run(self) -> None:
        recommendations = prioritize_recommendations(coverage_gaps=self.gaps)
        actions = {str(item["next_action_class"]) for item in recommendations}
        self.assertNotIn("bounded_adapter_run", actions)
        self.assertEqual({"rights_review", "human_decision"}, actions)


class DuplicateFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = base_index(base_authority())
        self.findings = detect_duplicate_findings(self.index)
        self.by_class: dict[str, list[dict[str, object]]] = {}
        for finding in self.findings:
            self.by_class.setdefault(str(finding["finding_class"]), []).append(finding)

    def test_every_duplicate_class_is_detected_with_explainable_evidence(self) -> None:
        self.assertEqual(
            {
                "conflicting_duplicate",
                "exact_hash_duplicate",
                "likely_metadata_duplicate",
                "stable_id_alias",
            },
            set(self.by_class),
        )
        exact = self.by_class["exact_hash_duplicate"][0]
        self.assertEqual("exact", exact["confidence"])
        self.assertEqual(DERIVED_SHA, exact["members"][0]["value_sha256"])
        self.assertEqual(
            ["index_document_asset_001", "index_document_asset_003"],
            [item["index_document_id"] for item in exact["members"]],
        )

        alias = self.by_class["stable_id_alias"][0]
        self.assertEqual(
            {"antiegg-fluxus", "njp-video-library"},
            {str(item["source_id"]) for item in alias["members"]},
        )
        self.assertIn("asset_001", alias["evidence_summary"])

        conflicting = self.by_class["conflicting_duplicate"][0]
        self.assertEqual(["title"], conflicting["matched_field_names"])
        self.assertEqual(["creator"], conflicting["conflicting_field_names"])

        likely = self.by_class["likely_metadata_duplicate"]
        self.assertTrue(likely)
        for finding in likely:
            self.assertEqual([], finding["conflicting_field_names"])
            self.assertEqual(["title"], finding["matched_field_names"])

    def test_no_finding_authorizes_a_merge_or_deletion(self) -> None:
        for finding in self.findings:
            self.assertEqual("requires_human_review", finding["review_state"])
            self.assertEqual("none", finding["merge_action"])

    def test_findings_are_deterministic_and_identity_bound(self) -> None:
        repeated = detect_duplicate_findings(base_index(base_authority()))
        self.assertEqual(self.findings, repeated)
        tampered = copy.deepcopy(self.findings[0])
        tampered["confidence"] = "low"
        with self.assertRaises(EvaluationError):
            prioritize_recommendations(duplicate_findings=[tampered])


class CorpusQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = base_authority()
        self.index = base_index(self.resolver)
        self.findings = evaluate_corpus_quality(
            self.index, evaluated_at=EVALUATED, authority_resolver=self.resolver
        )

    def states(self, check: str) -> dict[str, str]:
        return {
            f"{item['index_document_id']}:{item['field_id']}": str(item["state"])
            for item in self.findings
            if item["check"] == check
        }

    def test_every_declared_check_runs(self) -> None:
        self.assertEqual(
            set(QUALITY_CHECKS),
            {str(item["check"]) for item in self.findings},
        )

    def test_missing_and_unresolved_metadata_are_reported_separately(self) -> None:
        states = self.states("metadata_normalization")
        self.assertEqual("pass", states["index_document_asset_001:None"])
        self.assertEqual("unknown", states["index_document_asset_003:None"])
        self.assertEqual("fail", states["index_document_asset_004:None"])
        missing = next(
            item
            for item in self.findings
            if item["check"] == "metadata_normalization"
            and item["index_document_id"] == "index_document_asset_004"
        )
        self.assertIn("title", missing["detail"])
        self.assertEqual("metadata_correction", missing["next_action_class"])

    def test_cross_record_derived_lineage_is_unknown_not_asserted(self) -> None:
        states = self.states("provenance_completeness")
        self.assertEqual("pass", states["index_document_asset_001:field_summary"])
        self.assertEqual(
            "unknown", states["index_document_asset_003:field_transcript_003"]
        )

    def test_expired_rights_evidence_fails_and_revoked_authority_blocks(self) -> None:
        states = self.states("rights_freshness")
        self.assertEqual("pass", states["index_document_asset_001:field_title"])
        self.assertEqual("fail", states["index_document_asset_004:field_creator_004"])

        revoked = BundleAuthority(
            documents=[document_a(), document_b(), document_c(), document_d()],
            visibility_policies=[
                item
                for item in base_policies()
                if item["visibility_policy_id"] != "visibility_policy_title"
            ],
            provenance_edges=base_edges(),
            deletion_events=[],
            object_receipts=base_receipts(),
        )
        blocked = evaluate_corpus_quality(
            self.index, evaluated_at=EVALUATED, authority_resolver=revoked
        )
        state = next(
            str(item["state"])
            for item in blocked
            if item["check"] == "rights_freshness"
            and item["field_id"] == "field_title"
        )
        self.assertEqual("blocked", state)

    def test_authority_not_yet_effective_is_unknown(self) -> None:
        early = evaluate_corpus_quality(
            self.index,
            evaluated_at="2026-07-23T00:00:00Z",
            authority_resolver=self.resolver,
        )
        states = {
            str(item["state"])
            for item in early
            if item["check"] == "rights_freshness"
        }
        self.assertEqual({"unknown"}, states)

    def test_retention_class_must_match_the_selection_state(self) -> None:
        states = self.states("retention_readiness")
        self.assertEqual("fail", states["index_document_asset_002:field_creator_002"])
        self.assertEqual("pass", states["index_document_asset_001:field_summary"])

    def test_unbacked_derived_fields_stay_unknown(self) -> None:
        states = self.states("derived_confidence")
        self.assertEqual("pass", states["index_document_asset_001:field_summary"])

        unbacked = evaluate_corpus_quality(
            base_index(base_authority(), derived_objects=[]),
            evaluated_at=EVALUATED,
            authority_resolver=self.resolver,
        )
        for item in unbacked:
            if item["check"] != "derived_confidence":
                continue
            self.assertEqual("unknown", item["state"])
            self.assertIn("not ground truth", item["detail"])

    def test_an_uncovered_removal_fails_index_consistency(self) -> None:
        previous = minimal_index(with_summary=True, index_id="corpus_index_min")
        resolver = minimal_authority(with_summary=False)
        reduced = build_corpus_index(
            index_id="corpus_index_min_reduced",
            snapshot=build_index_snapshot(
                snapshot_id="index_snapshot_min_red",
                documents=[minimal_document(with_summary=False)],
                provenance_edges=[minimal_title_edge()],
                visibility_policies=minimal_policies(with_summary=False),
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=resolver,
            ),
            built_at=NOW,
            authority_resolver=resolver,
            previous_index=previous,
        )
        self.assertEqual(
            [{"index_document_id": "index_document_min", "field_id": "field_min_summary"}],
            reduced["superseded_fields"],
        )
        findings = evaluate_corpus_quality(
            reduced, evaluated_at=EVALUATED, authority_resolver=resolver
        )
        consistency = [
            item for item in findings if item["check"] == "index_consistency"
        ]
        self.assertEqual(1, len(consistency))
        self.assertEqual("fail", consistency[0]["state"])
        self.assertEqual("field_min_summary", consistency[0]["field_id"])
        self.assertEqual("index_repair", consistency[0]["next_action_class"])

    def test_an_empty_record_shell_cannot_prove_deletion_propagation(self) -> None:
        findings = evaluate_corpus_quality(
            index_with_emptied_document(), evaluated_at=EVALUATED
        )
        propagation = [
            item for item in findings if item["check"] == "deletion_propagation"
        ]
        self.assertEqual(1, len(propagation))
        self.assertEqual("unknown", propagation[0]["state"])
        self.assertEqual("field_min_title", propagation[0]["field_id"])
        self.assertIn("cannot be proven", propagation[0]["detail"])

    def test_no_deletion_event_is_reported_rather_than_assumed_clean(self) -> None:
        propagation = [
            item for item in self.findings if item["check"] == "deletion_propagation"
        ]
        self.assertEqual(1, len(propagation))
        self.assertEqual("pass", propagation[0]["state"])
        self.assertIn("no deletion event", propagation[0]["detail"])


class RightsFilteredRetrievalCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = base_authority()
        self.index = base_index(self.resolver)

    def run_cases(self, definitions: list[dict[str, object]]) -> list[dict[str, object]]:
        return evaluate_retrieval_cases(
            self.index,
            definitions,
            current_time=EVALUATED,
            authority_resolver=self.resolver,
            object_authority=self.resolver,
        )

    def test_allowed_fields_appear_and_ineligible_fields_appear_nowhere(self) -> None:
        case = self.run_cases([researcher_case()])[0]
        self.assertEqual("pass", case["outcome"])
        self.assertEqual([], case["failure_reasons"])
        self.assertEqual(["field_summary", "field_title"], case["observed_field_ids"])
        self.assertEqual(["field_summary"], case["exported_field_ids"])
        self.assertNotIn("1990s", case["observed_facet_values"])
        self.assertNotIn("2000s", case["observed_facet_values"])

    def test_a_missing_expected_field_fails_the_case(self) -> None:
        definition = dict(
            researcher_case(),
            retrieval_case_id="retrieval_case_operator_expects_researcher_field",
            audience="operator",
            expected_visible_field_ids=["field_title"],
            forbidden_field_ids=[],
            forbidden_facet_values=[],
            checked_surfaces=["results"],
        )
        case = self.run_cases([definition])[0]
        self.assertEqual("fail", case["outcome"])
        self.assertEqual(["expected_field_missing"], case["failure_reasons"])

    def test_a_visible_forbidden_field_or_facet_fails_the_case(self) -> None:
        definition = dict(
            researcher_case(),
            retrieval_case_id="retrieval_case_operator_leak",
            audience="operator",
            expected_visible_field_ids=[],
            forbidden_field_ids=["field_title_003"],
            forbidden_facet_values=["1990s"],
            checked_surfaces=["facets", "results"],
        )
        case = self.run_cases([definition])[0]
        self.assertEqual("fail", case["outcome"])
        self.assertEqual(
            ["forbidden_facet_value", "forbidden_field_in_results"],
            case["failure_reasons"],
        )

    def test_an_exported_forbidden_field_fails_the_case(self) -> None:
        definition = dict(
            researcher_case(),
            retrieval_case_id="retrieval_case_export_leak",
            expected_visible_field_ids=[],
            forbidden_field_ids=["field_summary"],
            forbidden_facet_values=[],
            checked_surfaces=["results", "score_export"],
        )
        case = self.run_cases([definition])[0]
        self.assertEqual("fail", case["outcome"])
        self.assertEqual(
            ["forbidden_field_in_export", "forbidden_field_in_results"],
            case["failure_reasons"],
        )

    def test_a_case_cannot_expect_and_forbid_the_same_field(self) -> None:
        definition = dict(
            researcher_case(),
            retrieval_case_id="retrieval_case_contradiction",
            forbidden_field_ids=["field_title"],
        )
        with self.assertRaises(EvaluationError):
            self.run_cases([definition])

    def test_unknown_case_keys_and_duplicate_identifiers_are_refused(self) -> None:
        with self.assertRaises(EvaluationError):
            self.run_cases([dict(researcher_case(), unexpected="no")])
        with self.assertRaises(EvaluationError):
            self.run_cases([researcher_case(), researcher_case()])


class EvaluationRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = base_authority()
        self.index = base_index(self.resolver)
        self.run = build_evaluation_run(
            evaluated_at=EVALUATED,
            completeness_reports=njp_reports(),
            selection_manifest=selection_manifest(),
            index=self.index,
            retrieval_case_definitions=[researcher_case()],
            authority_resolver=self.resolver,
            object_authority=self.resolver,
            caveats=[
                "No live source was contacted for this run.",
                "Every count is scoped to its exact input snapshot.",
            ],
        )

    def test_the_run_pins_its_exact_inputs_and_policy_versions(self) -> None:
        snapshot = self.run["input_snapshot"]
        self.assertEqual(EVALUATION_POLICY_VERSION, self.run["evaluation_policy_version"])
        self.assertEqual("selection_v1", snapshot["selection_policy_version"])
        self.assertEqual("corpus_index_001", snapshot["corpus_index_id"])
        self.assertEqual("index_snapshot_001", snapshot["index_snapshot_id"])
        self.assertEqual(self.index["index_sha256"], snapshot["index_sha256"])
        self.assertEqual(
            [
                "completeness_report_njp_center_main_home",
                "completeness_report_njp_center_video_archive_page",
                "completeness_report_njp_video_library_home",
            ],
            snapshot["completeness_report_ids"],
        )
        self.assertEqual(
            [
                "policy_snapshot_njp_center_main_home",
                "policy_snapshot_njp_center_video_archive_page",
                "policy_snapshot_njp_video_library_home",
            ],
            snapshot["policy_snapshot_ids"],
        )

    def test_the_run_is_deterministic_and_identity_bound(self) -> None:
        repeated = build_evaluation_run(
            evaluated_at=EVALUATED,
            completeness_reports=njp_reports(),
            selection_manifest=selection_manifest(),
            index=base_index(base_authority()),
            retrieval_case_definitions=[researcher_case()],
            authority_resolver=base_authority(),
            object_authority=base_authority(),
            caveats=[
                "No live source was contacted for this run.",
                "Every count is scoped to its exact input snapshot.",
            ],
        )
        self.assertEqual(self.run, repeated)
        self.assertEqual(self.run, validate_evaluation_run(self.run))

        tampered = copy.deepcopy(self.run)
        tampered["metrics"][0]["observed_value"] = 999999
        with self.assertRaises(EvaluationError):
            validate_evaluation_run(tampered)

    def test_recommendations_are_prioritized_bounded_and_resolvable(self) -> None:
        recommendations = self.run["recommendations"]
        self.assertTrue(recommendations)
        priorities = [int(item["priority"]) for item in recommendations]
        self.assertEqual(sorted(priorities), priorities)
        self.assertEqual("rights_review", recommendations[0]["next_action_class"])

        known = {
            str(item[key])
            for section, key in (
                ("metrics", "metric_id"),
                ("coverage_gaps", "coverage_gap_id"),
                ("duplicate_findings", "duplicate_finding_id"),
                ("quality_findings", "quality_finding_id"),
                ("retrieval_cases", "retrieval_case_id"),
            )
            for item in self.run[section]
        }
        for item in recommendations:
            self.assertTrue(set(item["finding_refs"]) <= known)
            if item["next_action_class"] == "bounded_adapter_run":
                self.assertEqual("none", item["blocker_class"])

    def test_a_blocked_source_never_yields_a_bounded_adapter_run(self) -> None:
        blocked = [
            item
            for item in self.run["metrics"]
            if item["completeness_state"] == "blocked"
        ]
        self.assertTrue(blocked)
        blocked_ids = {str(item["metric_id"]) for item in blocked}
        for item in self.run["recommendations"]:
            if item["next_action_class"] != "bounded_adapter_run":
                continue
            self.assertEqual(set(), blocked_ids & set(item["finding_refs"]))
        self.assertIn(
            "human_decision",
            {str(item["next_action_class"]) for item in self.run["recommendations"]},
        )

    def test_a_bounded_adapter_run_cannot_answer_a_blocked_observation(self) -> None:
        tampered = copy.deepcopy(self.run)
        blocked_metric = next(
            item
            for item in tampered["metrics"]
            if item["completeness_state"] == "blocked"
        )
        run_recommendation = next(
            item
            for item in tampered["recommendations"]
            if item["next_action_class"] == "bounded_adapter_run"
        )
        run_recommendation["finding_refs"] = sorted(
            set(run_recommendation["finding_refs"]) | {str(blocked_metric["metric_id"])}
        )
        run_recommendation["recommendation_id"] = "recommendation_" + hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in run_recommendation.items()
                    if key != "recommendation_id"
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        ).hexdigest()[:24]
        with self.assertRaises(EvaluationError):
            validate_evaluation_run(tampered)

    def test_findings_require_the_snapshot_they_were_read_from(self) -> None:
        tampered = copy.deepcopy(self.run)
        tampered["input_snapshot"]["corpus_index_id"] = None
        with self.assertRaises(EvaluationError):
            validate_evaluation_run(tampered)

    def test_a_metrics_only_run_needs_no_index_or_manifest(self) -> None:
        run = build_evaluation_run(
            evaluated_at=EVALUATED, completeness_reports=njp_reports()
        )
        self.assertEqual([], run["coverage_gaps"])
        self.assertEqual([], run["duplicate_findings"])
        self.assertEqual([], run["quality_findings"])
        self.assertEqual([], run["retrieval_cases"])
        self.assertIsNone(run["input_snapshot"]["corpus_index_id"])
        self.assertEqual(run, validate_evaluation_run(run))

    def test_retrieval_cases_require_an_index_and_authority_boundary(self) -> None:
        with self.assertRaises(EvaluationError):
            build_evaluation_run(
                evaluated_at=EVALUATED,
                retrieval_case_definitions=[researcher_case()],
            )

    def test_the_aggregate_report_is_sanitized_and_content_free(self) -> None:
        report = render_evaluation_report(self.run)
        self.assertIn("# Corpus evaluation run", report)
        self.assertIn("Whole-source total", report)
        self.assertIn("requires_human_review", report)
        self.assertIn("`bounded_partial`", report)
        for forbidden in (
            TITLE_VALUE,
            SUMMARY_VALUE,
            TRANSCRIPT_VALUE,
            DERIVED_KEY_A,
            "https://",
            "/home/",
            "/tmp/",
        ):
            self.assertNotIn(forbidden, report)
        self.assertEqual(report, render_evaluation_report(self.run))


if __name__ == "__main__":
    unittest.main()
