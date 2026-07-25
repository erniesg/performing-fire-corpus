from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.cli import main
from performing_fire_corpus.search_index import (
    SearchIndexError,
    build_index_snapshot,
    canonical_json_bytes,
    record_sha256,
)
from performing_fire_corpus.search_service import (
    BundleAuthority,
    SearchServiceError,
    build_corpus_index,
    export_score_features,
    search_corpus_index,
    validate_corpus_index,
    validate_derived_object,
)


NOW = "2026-07-24T00:00:00.125000Z"
EXPIRES = "2026-08-24T00:00:00.125000Z"
QUERY_TIME = "2026-07-25T00:00:00Z"
EVENT_TIME = "2026-07-24T06:00:00Z"
EVENT_BUILT = "2026-07-24T12:00:00Z"
RIGHTS_TITLE = "b" * 64
RIGHTS_SUMMARY = "c" * 64
DERIVED_SHA = "d" * 64
ORIGIN_SHA = "a" * 64
TITLE_VALUE = "Synthetic catalogue title"
SUMMARY_VALUE = "Synthetic derived observation summary"
DERIVED_KEY = (
    "corpus-staging/v1/derived/njp-video-library/asset_001"
    f"/transform_ocr_v1/{DERIVED_SHA}"
)


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


def title_edge() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "provenance_edge",
        "provenance_edge_id": "provenance_edge_title",
        "index_document_id": "index_document_asset_001",
        "field_id": "field_title",
        "field_name": "title",
        "field_value_sha256": value_hash(TITLE_VALUE),
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "origin_class": "factual_source_metadata",
        "origin_record_id": "observation_001",
        "origin_record_sha256": ORIGIN_SHA,
        "transformation_id": None,
        "input_provenance_edge_ids": [],
        "evidence_at": NOW,
        "evidence_expires_at": EXPIRES,
    }


def summary_edge() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "provenance_edge",
        "provenance_edge_id": "provenance_edge_summary",
        "index_document_id": "index_document_asset_001",
        "field_id": "field_summary",
        "field_name": "summary",
        "field_value_sha256": value_hash(SUMMARY_VALUE),
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "origin_class": "derived_observation",
        "origin_record_id": "derived_result_001",
        "origin_record_sha256": ORIGIN_SHA,
        "transformation_id": "transform_ocr_v1",
        "input_provenance_edge_ids": ["provenance_edge_title"],
        "evidence_at": NOW,
        "evidence_expires_at": EXPIRES,
    }


def second_title_edge() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "provenance_edge",
        "provenance_edge_id": "provenance_edge_title_002",
        "index_document_id": "index_document_asset_002",
        "field_id": "field_title_002",
        "field_name": "title",
        "field_value_sha256": value_hash(TITLE_VALUE),
        "source_id": "njp-center-main",
        "asset_id": "asset_002",
        "origin_class": "factual_source_metadata",
        "origin_record_id": "observation_002",
        "origin_record_sha256": ORIGIN_SHA,
        "transformation_id": None,
        "input_provenance_edge_ids": [],
        "evidence_at": NOW,
        "evidence_expires_at": EXPIRES,
    }


def first_document(*, with_summary: bool = True) -> dict[str, object]:
    fields = [
        {
            "field_id": "field_summary",
            "name": "summary",
            "value": SUMMARY_VALUE,
            "origin_class": "derived_observation",
            "provenance_edge_id": "provenance_edge_summary",
            "rights_snapshot_sha256": RIGHTS_SUMMARY,
            "consent_snapshot_sha256": None,
            "retention_class": "selected_derived",
            "visibility_class": "reviewed_metadata",
            "review_trigger": "Re-review when the transformation profile changes.",
        },
        {
            "field_id": "field_title",
            "name": "title",
            "value": TITLE_VALUE,
            "origin_class": "factual_source_metadata",
            "provenance_edge_id": "provenance_edge_title",
            "rights_snapshot_sha256": RIGHTS_TITLE,
            "consent_snapshot_sha256": None,
            "retention_class": "inventory_metadata",
            "visibility_class": "reviewed_metadata",
            "review_trigger": "Re-review when source authority changes.",
        },
    ]
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
        "fields": fields if with_summary else fields[1:],
    }


def second_document() -> dict[str, object]:
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
            {
                "field_id": "field_title_002",
                "name": "title",
                "value": TITLE_VALUE,
                "origin_class": "factual_source_metadata",
                "provenance_edge_id": "provenance_edge_title_002",
                "rights_snapshot_sha256": RIGHTS_TITLE,
                "consent_snapshot_sha256": None,
                "retention_class": "inventory_metadata",
                "visibility_class": "reviewed_metadata",
                "review_trigger": "Re-review when source authority changes.",
            }
        ],
    }


def visibility_policy(
    *,
    policy_id: str,
    index_document_id: str,
    field_id: str,
    rights_snapshot_sha256: str,
    allowed_operations: list[str],
    allowed_audiences: list[str],
    rights_state: str = "approved",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "visibility_policy",
        "visibility_policy_id": policy_id,
        "index_document_id": index_document_id,
        "field_id": field_id,
        "rights_snapshot_sha256": rights_snapshot_sha256,
        "rights_state": rights_state,
        "consent_snapshot_sha256": None,
        "consent_state": "not_required",
        "retention_state": "retain",
        "allowed_operations": allowed_operations,
        "allowed_audiences": allowed_audiences,
        "decided_at": NOW,
        "expires_at": EXPIRES,
        "evidence_expires_at": EXPIRES,
        "review_trigger": "Re-review when source authority changes.",
    }


def title_policy() -> dict[str, object]:
    return visibility_policy(
        policy_id="visibility_policy_title",
        index_document_id="index_document_asset_001",
        field_id="field_title",
        rights_snapshot_sha256=RIGHTS_TITLE,
        allowed_operations=["search_visibility"],
        allowed_audiences=["researcher"],
    )


def summary_policy() -> dict[str, object]:
    return visibility_policy(
        policy_id="visibility_policy_summary",
        index_document_id="index_document_asset_001",
        field_id="field_summary",
        rights_snapshot_sha256=RIGHTS_SUMMARY,
        allowed_operations=[
            "indexing",
            "score_feature_value",
            "score_generation",
            "search_visibility",
            "snippet_render",
        ],
        allowed_audiences=["operator", "researcher"],
    )


def second_title_policy() -> dict[str, object]:
    return visibility_policy(
        policy_id="visibility_policy_title_002",
        index_document_id="index_document_asset_002",
        field_id="field_title_002",
        rights_snapshot_sha256=RIGHTS_TITLE,
        allowed_operations=["search_visibility"],
        allowed_audiences=["operator"],
    )


def duplicate_cluster(*, with_summary: bool = True) -> dict[str, object]:
    first_edges = (
        ["provenance_edge_summary", "provenance_edge_title"]
        if with_summary
        else ["provenance_edge_title"]
    )
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
                "provenance_edge_ids": first_edges,
            },
            {
                "index_document_id": "index_document_asset_002",
                "source_id": "njp-center-main",
                "asset_id": "asset_002",
                "provenance_edge_ids": ["provenance_edge_title_002"],
            },
        ],
    }


def derived_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "derived_object",
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "transformation_id": "transform_ocr_v1",
        "input_receipt_ids": ["receipt_raw_asset_001"],
        "object_key": DERIVED_KEY,
        "sha256": DERIVED_SHA,
        "byte_size": 1024,
        "media_type": "text/plain",
        "rights_snapshot_sha256": RIGHTS_SUMMARY,
        "retention_class": "selected_derived",
        "retrieval_decision": "approved",
        "redaction_state": "reviewed_redacted",
    }


def object_receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "object_receipt",
        "receipt_id": "receipt_derived_asset_001",
        "object_kind": "derived",
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "transformation_id": "transform_ocr_v1",
        "object_key": DERIVED_KEY,
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


def coverage_target() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "coverage_target",
        "coverage_target_id": "coverage_period_1980s",
        "dimension": "period",
        "value": "1980s",
        "minimum_selected": 1,
        "priority": 1,
        "rationale": "Synthetic period coverage for the offline reference surface.",
    }


class CountingAuthority(BundleAuthority):
    """Authority boundary that records how often it is consulted."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    def resolve_index_document(self, *, index_document_id: str):
        self.calls.append("document")
        return super().resolve_index_document(index_document_id=index_document_id)

    def resolve_visibility_policy(self, *, index_document_id: str, field_id: str):
        self.calls.append("policy")
        return super().resolve_visibility_policy(
            index_document_id=index_document_id, field_id=field_id
        )

    def resolve_provenance_edge(self, *, provenance_edge_id: str):
        self.calls.append("edge")
        return super().resolve_provenance_edge(
            provenance_edge_id=provenance_edge_id
        )

    def resolve_deletion_event(self, *, deletion_event_id: str):
        self.calls.append("event")
        return super().resolve_deletion_event(deletion_event_id=deletion_event_id)


def authority(
    *,
    documents: list[dict[str, object]] | None = None,
    policies: list[dict[str, object]] | None = None,
    edges: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
    receipts: list[dict[str, object]] | None = None,
    counting: bool = False,
) -> BundleAuthority:
    kwargs = {
        "documents": documents
        if documents is not None
        else [first_document(), second_document()],
        "visibility_policies": policies
        if policies is not None
        else [summary_policy(), title_policy(), second_title_policy()],
        "provenance_edges": edges
        if edges is not None
        else [summary_edge(), title_edge(), second_title_edge()],
        "deletion_events": events or [],
        "object_receipts": receipts if receipts is not None else [object_receipt()],
    }
    return CountingAuthority(**kwargs) if counting else BundleAuthority(**kwargs)


def base_snapshot(resolver: BundleAuthority) -> dict[str, object]:
    return build_index_snapshot(
        snapshot_id="index_snapshot_001",
        documents=[first_document(), second_document()],
        provenance_edges=[summary_edge(), title_edge(), second_title_edge()],
        visibility_policies=[
            summary_policy(),
            title_policy(),
            second_title_policy(),
        ],
        duplicate_clusters=[duplicate_cluster()],
        deletion_events=[],
        built_at=NOW,
        authority_resolver=resolver,
    )


def base_index(
    resolver: BundleAuthority, **kwargs: object
) -> dict[str, object]:
    return build_corpus_index(
        index_id="corpus_index_001",
        snapshot=base_snapshot(resolver),
        built_at=NOW,
        authority_resolver=resolver,
        derived_objects=[derived_object()],
        object_authority=resolver,
        coverage_targets=[coverage_target()],
        **kwargs,
    )


class IndexerAdmissionTests(unittest.TestCase):
    def test_index_binds_snapshot_coverage_and_exact_derived_objects(self) -> None:
        resolver = authority()
        index = base_index(resolver)
        validated = validate_corpus_index(index)
        self.assertEqual(index, validated)
        entries = {
            item["index_document_id"]: item for item in index["entries"]
        }
        first = entries["index_document_asset_001"]
        self.assertEqual("selected_contribution", first["coverage_state"])
        self.assertEqual(["coverage_period_1980s"], first["coverage_target_ids"])
        self.assertEqual(
            "unselected_candidate",
            entries["index_document_asset_002"]["coverage_state"],
        )
        self.assertEqual(
            [DERIVED_KEY],
            [item["object_key"] for item in first["derived_objects"]],
        )
        self.assertEqual([], entries["index_document_asset_002"]["derived_objects"])
        self.assertEqual([], index["superseded_fields"])
        self.assertEqual(
            ["index_document_asset_001", "index_document_asset_002"],
            index["upserted_document_ids"],
        )

    def test_unverified_or_unprovenanced_derived_objects_are_rejected(self) -> None:
        resolver = authority()
        snapshot = base_snapshot(resolver)
        missing_receipt = authority(receipts=[])
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_unverified",
                snapshot=snapshot,
                built_at=NOW,
                authority_resolver=missing_receipt,
                derived_objects=[derived_object()],
                object_authority=missing_receipt,
            )
        mismatched = object_receipt()
        mismatched["byte_size"] = 2048
        mismatched_resolver = authority(receipts=[mismatched])
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_mismatched",
                snapshot=snapshot,
                built_at=NOW,
                authority_resolver=mismatched_resolver,
                derived_objects=[derived_object()],
                object_authority=mismatched_resolver,
            )
        unprovenanced = derived_object()
        unprovenanced["transformation_id"] = "transform_unknown_v1"
        unprovenanced["object_key"] = DERIVED_KEY.replace(
            "transform_ocr_v1", "transform_unknown_v1"
        )
        receipt = object_receipt()
        receipt["transformation_id"] = "transform_unknown_v1"
        receipt["object_key"] = unprovenanced["object_key"]
        unprovenanced_resolver = authority(receipts=[receipt])
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_unprovenanced",
                snapshot=snapshot,
                built_at=NOW,
                authority_resolver=unprovenanced_resolver,
                derived_objects=[unprovenanced],
                object_authority=unprovenanced_resolver,
            )

    def test_local_media_paths_and_blocked_objects_never_index(self) -> None:
        local = derived_object()
        local["object_key"] = "tmp/v1/derived/njp-video-library/asset_001/transform_ocr_v1/" + DERIVED_SHA
        local["object_key"] = "../" + local["object_key"]
        with self.assertRaises(SearchIndexError):
            validate_derived_object(local)
        blocked = derived_object()
        blocked["retrieval_decision"] = "blocked"
        with self.assertRaises(SearchServiceError):
            validate_derived_object(blocked)
        mismatched_key = derived_object()
        mismatched_key["object_key"] = DERIVED_KEY.replace(
            "asset_001", "asset_009"
        )
        with self.assertRaises(SearchIndexError):
            validate_derived_object(mismatched_key)

    def test_stale_or_revoked_policy_stops_index_construction(self) -> None:
        resolver = authority()
        snapshot = base_snapshot(resolver)
        revoked = title_policy()
        revoked["rights_state"] = "revoked"
        stale = authority(
            policies=[summary_policy(), revoked, second_title_policy()]
        )
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_revoked",
                snapshot=snapshot,
                built_at=NOW,
                authority_resolver=stale,
                object_authority=stale,
            )
        corrected = first_document()
        corrected["period"] = "1970s"
        corrected_resolver = authority(documents=[corrected, second_document()])
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_corrected",
                snapshot=snapshot,
                built_at=NOW,
                authority_resolver=corrected_resolver,
                object_authority=corrected_resolver,
            )
        with self.assertRaises(SearchServiceError):
            build_corpus_index(
                index_id="corpus_index_expired",
                snapshot=snapshot,
                built_at="2026-09-24T00:00:00Z",
                authority_resolver=resolver,
                object_authority=resolver,
            )

    def test_repeated_and_restarted_indexing_is_identical(self) -> None:
        resolver = authority()
        first = base_index(resolver)
        second = base_index(authority())
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        restarted = json.loads(canonical_json_bytes(first).decode("utf-8"))
        self.assertEqual(first, validate_corpus_index(restarted))
        upserted = base_index(authority(), previous_index=restarted)
        self.assertEqual([], upserted["upserted_document_ids"])
        self.assertEqual([], upserted["removed_document_ids"])
        self.assertEqual([], upserted["superseded_fields"])
        repeated = base_index(authority(), previous_index=restarted)
        self.assertEqual(
            canonical_json_bytes(upserted), canonical_json_bytes(repeated)
        )


class RightsFilteredSearchTests(unittest.TestCase):
    def test_audience_and_operation_gate_every_field(self) -> None:
        resolver = authority()
        index = base_index(resolver)
        researcher = search_corpus_index(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        self.assertEqual(1, researcher["result_count"])
        result = researcher["results"][0]
        self.assertEqual("index_document_asset_001", result["index_document_id"])
        self.assertEqual(1, result["rank"])
        self.assertEqual(
            ["field_summary", "field_title"], result["visible_field_ids"]
        )
        self.assertEqual("selected_contribution", result["coverage_state"])
        self.assertEqual("selected_rich_corpus", result["selection_state"])
        self.assertEqual(
            {"evidence_at": NOW, "evidence_expires_at": EXPIRES},
            result["evidence_scope"],
        )
        operator = search_corpus_index(
            index,
            audience="operator",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        self.assertEqual(2, operator["result_count"])
        self.assertEqual(
            ["field_summary"],
            operator["results"][0]["visible_field_ids"]
            if operator["results"][0]["index_document_id"]
            == "index_document_asset_001"
            else operator["results"][1]["visible_field_ids"],
        )

    def test_public_callers_learn_nothing_from_results_or_facets(self) -> None:
        index = base_index(authority())
        public = search_corpus_index(
            index,
            audience="public",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        self.assertEqual(0, public["result_count"])
        self.assertEqual([], public["results"])
        self.assertEqual(
            {name: [] for name in public["facets"]}, public["facets"]
        )
        serialized = canonical_json_bytes(public).decode("utf-8")
        for secret in (
            TITLE_VALUE,
            SUMMARY_VALUE,
            DERIVED_KEY,
            "index_document_asset_001",
            "duplicate_cluster_001",
        ):
            self.assertNotIn(secret, serialized)

    def test_authority_traffic_does_not_depend_on_the_answer(self) -> None:
        index = base_index(authority())
        counted = []
        for audience in ("researcher", "public"):
            resolver = authority(counting=True)
            search_corpus_index(
                index,
                audience=audience,
                current_time=QUERY_TIME,
                authority_resolver=resolver,
            )
            counted.append(list(resolver.calls))
        self.assertEqual(counted[0], counted[1])

    def test_facets_and_duplicate_clusters_only_expose_visible_records(
        self,
    ) -> None:
        index = base_index(authority())
        researcher = search_corpus_index(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        self.assertEqual(
            [{"value": "njp-video-library", "count": 1}],
            researcher["facets"]["source_id"],
        )
        self.assertEqual(
            [{"value": "duplicate_cluster_001", "count": 1}],
            researcher["facets"]["duplicate_cluster_id"],
        )
        self.assertEqual(
            ["index_document_asset_001"],
            researcher["results"][0]["duplicate_member_document_ids"],
        )
        operator = search_corpus_index(
            index,
            audience="operator",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        self.assertEqual(
            [{"value": "duplicate_cluster_001", "count": 2}],
            operator["facets"]["duplicate_cluster_id"],
        )
        self.assertEqual(
            ["index_document_asset_001", "index_document_asset_002"],
            operator["results"][0]["duplicate_member_document_ids"],
        )

    def test_ranking_and_filters_are_deterministic(self) -> None:
        index = base_index(authority())
        for _ in range(3):
            matched = search_corpus_index(
                index,
                audience="operator",
                current_time=QUERY_TIME,
                authority_resolver=authority(),
                query_terms=["synthetic"],
            )
            self.assertEqual(
                ["index_document_asset_001", "index_document_asset_002"],
                [item["index_document_id"] for item in matched["results"]],
            )
            self.assertEqual([1, 2], [item["rank"] for item in matched["results"]])
        self.assertEqual(
            0,
            search_corpus_index(
                index,
                audience="operator",
                current_time=QUERY_TIME,
                authority_resolver=authority(),
                query_terms=["absent"],
            )["result_count"],
        )
        limited = search_corpus_index(
            index,
            audience="operator",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
            limit=1,
        )
        self.assertEqual(2, limited["result_count"])
        self.assertEqual(1, len(limited["results"]))
        self.assertEqual(
            0,
            search_corpus_index(
                index,
                audience="researcher",
                current_time=QUERY_TIME,
                authority_resolver=authority(),
                language="en",
            )["result_count"],
        )
        self.assertEqual(
            0,
            search_corpus_index(
                index,
                audience="researcher",
                current_time="2026-07-23T00:00:00Z",
                authority_resolver=authority(),
            )["result_count"],
        )

    def test_snippets_require_an_explicit_grant(self) -> None:
        index = base_index(authority())
        researcher = search_corpus_index(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
        )
        fields = {
            item["field_id"]: item for item in researcher["results"][0]["fields"]
        }
        self.assertEqual(SUMMARY_VALUE, fields["field_summary"]["snippet"])
        self.assertIsNone(fields["field_title"]["snippet"])
        self.assertEqual(
            value_hash(TITLE_VALUE), fields["field_title"]["value_sha256"]
        )
        self.assertNotIn(
            TITLE_VALUE, canonical_json_bytes(researcher).decode("utf-8")
        )


class ScoreExportTests(unittest.TestCase):
    def test_export_carries_only_authorized_features_and_exact_keys(self) -> None:
        index = base_index(authority())
        export = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
            object_authority=authority(),
        )
        self.assertEqual(1, len(export["documents"]))
        document = export["documents"][0]
        self.assertEqual("index_document_asset_001", document["index_document_id"])
        self.assertEqual(
            ["field_summary"], [item["field_id"] for item in document["features"]]
        )
        feature = document["features"][0]
        self.assertEqual(SUMMARY_VALUE, feature["value"])
        self.assertEqual(value_hash(SUMMARY_VALUE), feature["value_sha256"])
        self.assertEqual(
            [DERIVED_KEY],
            [item["object_key"] for item in document["derived_object_keys"]],
        )
        serialized = canonical_json_bytes(export).decode("utf-8")
        for forbidden in ("://", "?", "&", "/home/", "/tmp/", "X-Amz-"):
            self.assertNotIn(forbidden, serialized)
        repeated = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
            object_authority=authority(),
        )
        self.assertEqual(export, repeated)

    def test_export_withholds_values_keys_and_public_audiences(self) -> None:
        index = base_index(authority())
        with self.assertRaises(SearchServiceError):
            export_score_features(
                index,
                audience="public",
                current_time=QUERY_TIME,
                authority_resolver=authority(),
                object_authority=authority(),
            )
        withheld = summary_policy()
        withheld["allowed_operations"] = [
            "indexing",
            "score_generation",
            "search_visibility",
        ]
        no_value = authority(
            policies=[withheld, title_policy(), second_title_policy()]
        )
        export = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=no_value,
            object_authority=no_value,
        )
        feature = export["documents"][0]["features"][0]
        self.assertIsNone(feature["value"])
        self.assertEqual(value_hash(SUMMARY_VALUE), feature["value_sha256"])
        self.assertNotIn(
            SUMMARY_VALUE, canonical_json_bytes(export).decode("utf-8")
        )
        unverified = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=authority(),
            object_authority=authority(receipts=[]),
        )
        self.assertEqual([], unverified["documents"][0]["derived_object_keys"])
        self.assertNotIn(
            DERIVED_KEY, canonical_json_bytes(unverified).decode("utf-8")
        )


    def test_export_schema_is_strict_and_refuses_long_form_text(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "v1" / "score-feature-export.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        self.assertFalse(schema["additionalProperties"])
        for name in ("document", "feature", "derivedObjectKey"):
            self.assertFalse(schema["$defs"][name]["additionalProperties"])
        self.assertEqual(["operator", "researcher"], schema["properties"]["audience"]["enum"])
        self.assertEqual(128, schema["$defs"]["featureValue"]["maxLength"])

        prose = "Synthetic derived observation summary. " * 5
        self.assertGreater(len(prose.strip()), 128)
        long_document = first_document()
        long_document["fields"][0]["value"] = prose.strip()
        long_edge = summary_edge()
        long_edge["field_value_sha256"] = value_hash(prose.strip())
        resolver = authority(
            documents=[long_document, second_document()],
            edges=[long_edge, title_edge(), second_title_edge()],
        )
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_prose",
            documents=[long_document, second_document()],
            provenance_edges=[long_edge, title_edge(), second_title_edge()],
            visibility_policies=[
                summary_policy(),
                title_policy(),
                second_title_policy(),
            ],
            duplicate_clusters=[duplicate_cluster()],
            deletion_events=[],
            built_at=NOW,
            authority_resolver=resolver,
        )
        index = build_corpus_index(
            index_id="corpus_index_prose",
            snapshot=snapshot,
            built_at=NOW,
            authority_resolver=resolver,
            object_authority=resolver,
        )
        with self.assertRaises(SearchServiceError):
            export_score_features(
                index,
                audience="researcher",
                current_time=QUERY_TIME,
                authority_resolver=resolver,
                object_authority=resolver,
            )


class RevocationAndDeletionTests(unittest.TestCase):
    def _deleted_index(self, previous: dict[str, object]) -> dict[str, object]:
        lineage = [summary_edge(), title_edge(), second_title_edge()]
        event = {
            "schema_version": 1,
            "record_type": "deletion_event",
            "deletion_event_id": "deletion_event_summary",
            "index_document_id": "index_document_asset_001",
            "field_id": "field_summary",
            "reason_code": "consent_withdrawn",
            "authority_snapshot_sha256": lineage_hash(lineage),
            "occurred_at": EVENT_TIME,
            "reindex_action": "remove_exact_field",
            "replacement_document_sha256": None,
            "replacement_provenance_edge_sha256": None,
            "replacement_visibility_policy_sha256": None,
        }
        resolver = authority(
            documents=[first_document(with_summary=False), second_document()],
            policies=[title_policy(), second_title_policy()],
            edges=[title_edge(), second_title_edge()],
            events=[event],
        )
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_002",
            documents=[first_document(), second_document()],
            provenance_edges=[title_edge(), second_title_edge()],
            visibility_policies=[title_policy(), second_title_policy()],
            duplicate_clusters=[duplicate_cluster(with_summary=False)],
            deletion_events=[event],
            built_at=EVENT_BUILT,
            authority_resolver=resolver,
            event_lineage_edges=lineage,
        )
        return build_corpus_index(
            index_id="corpus_index_002",
            snapshot=snapshot,
            built_at=EVENT_BUILT,
            authority_resolver=resolver,
            coverage_targets=[coverage_target()],
            previous_index=previous,
        )

    def test_exact_deletion_removes_the_field_from_every_surface(self) -> None:
        previous = base_index(authority())
        index = self._deleted_index(previous)
        self.assertEqual(
            [
                {
                    "index_document_id": "index_document_asset_001",
                    "field_id": "field_summary",
                }
            ],
            index["superseded_fields"],
        )
        entry = next(
            item
            for item in index["entries"]
            if item["index_document_id"] == "index_document_asset_001"
        )
        self.assertEqual(["field_title"], entry["field_ids"])
        self.assertEqual([], entry["derived_objects"])
        resolver = authority(
            documents=[first_document(with_summary=False), second_document()],
            policies=[title_policy(), second_title_policy()],
            edges=[title_edge(), second_title_edge()],
        )
        found = search_corpus_index(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=resolver,
        )
        serialized = canonical_json_bytes(found).decode("utf-8")
        self.assertNotIn("field_summary", serialized)
        self.assertNotIn(SUMMARY_VALUE, serialized)
        export = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=resolver,
            object_authority=resolver,
        )
        self.assertEqual([], export["documents"])
        self.assertNotIn(
            DERIVED_KEY, canonical_json_bytes(export).decode("utf-8")
        )
        repeated = self._deleted_index(previous)
        self.assertEqual(
            canonical_json_bytes(index), canonical_json_bytes(repeated)
        )

    def test_revocation_after_indexing_hides_the_cached_field(self) -> None:
        index = base_index(authority())
        revoked = summary_policy()
        revoked["rights_state"] = "revoked"
        resolver = authority(
            policies=[revoked, title_policy(), second_title_policy()]
        )
        found = search_corpus_index(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=resolver,
        )
        self.assertEqual(
            ["field_title"], found["results"][0]["visible_field_ids"]
        )
        self.assertNotIn(
            SUMMARY_VALUE, canonical_json_bytes(found).decode("utf-8")
        )
        export = export_score_features(
            index,
            audience="researcher",
            current_time=QUERY_TIME,
            authority_resolver=resolver,
            object_authority=resolver,
        )
        self.assertEqual([], export["documents"])
        expired = search_corpus_index(
            index,
            audience="researcher",
            current_time="2026-09-24T00:00:00Z",
            authority_resolver=authority(),
        )
        self.assertEqual(0, expired["result_count"])
        self.assertEqual({name: [] for name in expired["facets"]}, expired["facets"])


class LocalSearchCommandTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(argv)
        return code, stream.getvalue()

    def test_cli_builds_queries_and_exports_offline(self) -> None:
        resolver = authority()
        snapshot = base_snapshot(resolver)
        bundle = {
            "schema_version": 1,
            "record_type": "index_authority_bundle",
            "documents": [first_document(), second_document()],
            "visibility_policies": [
                summary_policy(),
                title_policy(),
                second_title_policy(),
            ],
            "provenance_edges": [
                summary_edge(),
                title_edge(),
                second_title_edge(),
            ],
            "deletion_events": [],
            "object_receipts": [object_receipt()],
        }
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            paths = {
                "snapshot": directory / "snapshot.json",
                "bundle": directory / "authority.json",
                "derived": directory / "derived.json",
                "coverage": directory / "coverage.json",
                "index": directory / "index.json",
                "results": directory / "results.json",
                "export": directory / "export.json",
            }
            paths["snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
            paths["bundle"].write_text(json.dumps(bundle), encoding="utf-8")
            paths["derived"].write_text(
                json.dumps([derived_object()]), encoding="utf-8"
            )
            paths["coverage"].write_text(
                json.dumps([coverage_target()]), encoding="utf-8"
            )
            code, _ = self.run_cli(
                [
                    "search",
                    "build",
                    "--index-id",
                    "corpus_index_cli",
                    "--snapshot",
                    str(paths["snapshot"]),
                    "--authority",
                    str(paths["bundle"]),
                    "--built-at",
                    NOW,
                    "--derived-objects",
                    str(paths["derived"]),
                    "--coverage-targets",
                    str(paths["coverage"]),
                    "--output",
                    str(paths["index"]),
                ]
            )
            self.assertEqual(0, code)
            index = json.loads(paths["index"].read_text(encoding="utf-8"))
            self.assertEqual("corpus_index_cli", index["corpus_index_id"])
            code, _ = self.run_cli(
                [
                    "search",
                    "query",
                    "--index",
                    str(paths["index"]),
                    "--authority",
                    str(paths["bundle"]),
                    "--audience",
                    "researcher",
                    "--current-time",
                    QUERY_TIME,
                    "--term",
                    "synthetic",
                    "--output",
                    str(paths["results"]),
                ]
            )
            self.assertEqual(0, code)
            results = json.loads(paths["results"].read_text(encoding="utf-8"))
            self.assertEqual(1, results["result_count"])
            code, _ = self.run_cli(
                [
                    "search",
                    "export-scores",
                    "--index",
                    str(paths["index"]),
                    "--authority",
                    str(paths["bundle"]),
                    "--audience",
                    "researcher",
                    "--current-time",
                    QUERY_TIME,
                    "--output",
                    str(paths["export"]),
                ]
            )
            self.assertEqual(0, code)
            export = json.loads(paths["export"].read_text(encoding="utf-8"))
            self.assertEqual("score_feature_export", export["record_type"])
            public_output = directory / "public.json"
            code, printed = self.run_cli(
                [
                    "search",
                    "query",
                    "--index",
                    str(paths["index"]),
                    "--authority",
                    str(paths["bundle"]),
                    "--audience",
                    "public",
                    "--current-time",
                    QUERY_TIME,
                    "--output",
                    str(public_output),
                ]
            )
            self.assertEqual(0, code)
            self.assertEqual({"result_count": 0, "status": "complete"}, json.loads(printed))
            self.assertNotIn(
                "index_document_asset_001",
                public_output.read_text(encoding="utf-8"),
            )
            code, printed = self.run_cli(
                [
                    "search",
                    "query",
                    "--index",
                    str(paths["snapshot"]),
                    "--authority",
                    str(paths["bundle"]),
                    "--audience",
                    "researcher",
                    "--current-time",
                    QUERY_TIME,
                    "--output",
                    str(directory / "unused.json"),
                ]
            )
            self.assertEqual(4, code)
            self.assertEqual(
                "search_authority_unavailable", json.loads(printed)["code"]
            )
            self.assertFalse((directory / "unused.json").exists())


if __name__ == "__main__":
    unittest.main()
