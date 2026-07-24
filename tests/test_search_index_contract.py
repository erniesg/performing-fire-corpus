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

from performing_fire_corpus.search_index import (
    SearchIndexError,
    build_index_snapshot,
    query_index,
    validate_deletion_event,
    validate_duplicate_cluster,
    validate_index_document,
    validate_provenance_edge,
    validate_visibility_policy,
)


NOW = "2026-07-24T00:00:00.125000Z"
EXPIRES = "2026-08-24T00:00:00.125000Z"
SHA = "a" * 64


def record_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def lineage_hash(edges: list[dict[str, object]]) -> str:
    return record_hash(
        {
            "event_lineage_edges": sorted(
                copy.deepcopy(edges),
                key=lambda item: str(item["provenance_edge_id"]),
            )
        }
    )


class SyntheticIndexAuthority:
    def __init__(
        self,
        policies: list[dict[str, object]],
        events: list[dict[str, object]] | None = None,
        edges: list[dict[str, object]] | None = None,
        documents: list[dict[str, object]] | None = None,
    ) -> None:
        self.policies = {
            (item["index_document_id"], item["field_id"]): copy.deepcopy(item)
            for item in policies
        }
        self.events = {
            item["deletion_event_id"]: copy.deepcopy(item)
            for item in (events or [])
        }
        self.edges = {
            item["provenance_edge_id"]: copy.deepcopy(item)
            for item in (edges or [])
        }
        self.documents = {
            item["index_document_id"]: copy.deepcopy(item)
            for item in (documents or [])
        }

    def resolve_index_document(
        self, *, index_document_id: str
    ) -> dict[str, object] | None:
        value = self.documents.get(index_document_id)
        return None if value is None else copy.deepcopy(value)

    def resolve_visibility_policy(
        self, *, index_document_id: str, field_id: str
    ) -> dict[str, object] | None:
        value = self.policies.get((index_document_id, field_id))
        return None if value is None else copy.deepcopy(value)

    def resolve_deletion_event(
        self, *, deletion_event_id: str
    ) -> dict[str, object] | None:
        value = self.events.get(deletion_event_id)
        return None if value is None else copy.deepcopy(value)

    def resolve_provenance_edge(
        self, *, provenance_edge_id: str
    ) -> dict[str, object] | None:
        value = self.edges.get(provenance_edge_id)
        return None if value is None else copy.deepcopy(value)


def edge(field_id: str = "field_title") -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "provenance_edge",
        "provenance_edge_id": f"provenance_edge_{field_id}",
        "index_document_id": "index_document_asset_001",
        "field_id": field_id,
        "field_name": "title" if field_id == "field_title" else "period",
        "field_value_sha256": hashlib.sha256(
            (
                "Synthetic catalogue title\n"
                if field_id == "field_title"
                else "1980s\n"
            ).encode("utf-8")
        ).hexdigest(),
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "origin_class": "factual_source_metadata",
        "origin_record_id": "observation_001",
        "origin_record_sha256": SHA,
        "transformation_id": None,
        "input_provenance_edge_ids": [],
        "evidence_at": NOW,
        "evidence_expires_at": EXPIRES,
    }


def field(field_id: str = "field_title") -> dict[str, object]:
    return {
        "field_id": field_id,
        "name": "title",
        "value": "Synthetic catalogue title",
        "origin_class": "factual_source_metadata",
        "provenance_edge_id": f"provenance_edge_{field_id}",
        "rights_snapshot_sha256": "b" * 64,
        "consent_snapshot_sha256": None,
        "retention_class": "inventory_metadata",
        "visibility_class": "reviewed_metadata",
        "review_trigger": "Re-review when source authority changes.",
    }


def document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "index_document",
        "index_document_id": "index_document_asset_001",
        "source_id": "njp-video-library",
        "asset_id": "asset_001",
        "selection_state": "inventory_only",
        "duplicate_cluster_id": "duplicate_cluster_001",
        "languages": ["ko"],
        "period": "1980s",
        "mediums": ["video"],
        "fields": [field()],
    }


def policy(
    *,
    field_id: str = "field_title",
    rights_state: str = "approved",
    evidence_expires_at: str = EXPIRES,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "visibility_policy",
        "visibility_policy_id": f"visibility_policy_{field_id}",
        "index_document_id": "index_document_asset_001",
        "field_id": field_id,
        "rights_snapshot_sha256": "b" * 64,
        "rights_state": rights_state,
        "consent_snapshot_sha256": None,
        "consent_state": "not_required",
        "retention_state": "retain",
        "allowed_operations": ["search_visibility"],
        "allowed_audiences": ["researcher"],
        "decided_at": NOW,
        "expires_at": EXPIRES,
        "evidence_expires_at": evidence_expires_at,
        "review_trigger": "Re-review when source authority changes.",
    }


def cluster() -> dict[str, object]:
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
                "provenance_edge_ids": ["provenance_edge_field_title"],
            },
            {
                "index_document_id": "index_document_asset_002",
                "source_id": "njp-center-main",
                "asset_id": "asset_002",
                "provenance_edge_ids": ["provenance_edge_field_title_002"],
            },
        ],
    }


class SearchIndexContractTests(unittest.TestCase):
    def test_published_schemas_are_valid_and_strict(self) -> None:
        safe_text_patterns = set()
        for name in (
            "index-document",
            "provenance-edge",
            "duplicate-cluster",
            "visibility-policy",
            "deletion-event",
            "index-snapshot",
        ):
            schema = json.loads(
                (ROOT / "schemas" / "v1" / f"{name}.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(schema)
            if name in {
                "index-document",
                "duplicate-cluster",
                "visibility-policy",
                "index-snapshot",
            }:
                safe_text_patterns.add(schema["$defs"]["safeText"]["pattern"])
        self.assertEqual(1, len(safe_text_patterns))
        snapshot_schema = json.loads(
            (ROOT / "schemas" / "v1" / "index-snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        base = {
            "schema_version": 1,
            "record_type": "index_snapshot",
            "index_snapshot_id": "index_snapshot_schema",
            "snapshot_sha256": "0" * 64,
            "built_at": NOW,
            "documents": [],
            "provenance_edges": [],
            "visibility_policies": [],
            "duplicate_clusters": [],
            "deletion_events": [],
            "event_lineage_edges": [],
        }
        for field_name in (
            "documents",
            "provenance_edges",
            "visibility_policies",
            "duplicate_clusters",
            "deletion_events",
            "event_lineage_edges",
        ):
            malformed = copy.deepcopy(base)
            malformed[field_name] = [{}]
            with self.assertRaises(ValidationError):
                Draft202012Validator(snapshot_schema).validate(malformed)

    def test_strict_records_preserve_field_level_provenance(self) -> None:
        checked_edge = validate_provenance_edge(edge())
        checked_document = validate_index_document(document())
        checked_policy = validate_visibility_policy(policy())
        checked_cluster = validate_duplicate_cluster(cluster())
        self.assertEqual(
            checked_edge["provenance_edge_id"],
            checked_document["fields"][0]["provenance_edge_id"],
        )
        self.assertEqual(
            checked_document["fields"][0]["rights_snapshot_sha256"],
            checked_policy["rights_snapshot_sha256"],
        )
        self.assertEqual(2, len(checked_cluster["members"]))

        tampered = document()
        tampered["duplicate_cluster_id"] = None
        tampered["fields"][0]["value"] = "Tampered title"
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_tampered",
                documents=[tampered],
                provenance_edges=[edge()],
                visibility_policies=[policy()],
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [policy()],
                    edges=[edge()],
                    documents=[tampered],
                ),
            )

    def test_query_fails_closed_for_stale_or_blocked_field_authority(self) -> None:
        indexed = document()
        indexed["duplicate_cluster_id"] = None
        approved = policy()
        authority = SyntheticIndexAuthority(
            [approved], edges=[edge()], documents=[indexed]
        )
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_001",
            documents=[indexed],
            provenance_edges=[edge()],
            visibility_policies=[approved],
            duplicate_clusters=[],
            deletion_events=[],
            built_at=NOW,
            authority_resolver=authority,
        )
        visible = query_index(
            snapshot,
            operation="search_visibility",
            audience="researcher",
            current_time="2026-07-25T00:00:00Z",
            authority_resolver=authority,
        )
        self.assertEqual(["field_title"], visible[0]["visible_field_ids"])
        self.assertEqual(
            "factual_source_metadata",
            visible[0]["fields"][0]["origin_class"],
        )
        for query_filter in (
            {"source_id": "njp-center-main"},
            {"language": "en"},
            {"period": "1970s"},
            {"medium": "audio"},
            {"selection_state": "selected_rich_corpus"},
            {"duplicate_cluster_id": "duplicate_cluster_999"},
        ):
            self.assertEqual(
                [],
                query_index(
                    snapshot,
                    operation="search_visibility",
                    audience="researcher",
                    current_time="2026-07-25T00:00:00Z",
                    authority_resolver=authority,
                    **query_filter,
                ),
            )
        self.assertEqual(
            [],
            query_index(
                snapshot,
                operation="search_visibility",
                audience="public",
                current_time="2026-07-25T00:00:00Z",
                authority_resolver=authority,
            ),
        )

        stale_document = copy.deepcopy(indexed)
        stale_document["period"] = "1970s"
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_stale_document",
                documents=[indexed],
                provenance_edges=[edge()],
                visibility_policies=[approved],
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [approved],
                    edges=[edge()],
                    documents=[stale_document],
                ),
            )

        future_edge = edge()
        future_edge["evidence_at"] = "2026-07-30T00:00:00Z"
        future_snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_future_evidence",
            documents=[indexed],
            provenance_edges=[future_edge],
            visibility_policies=[approved],
            duplicate_clusters=[],
            deletion_events=[],
            built_at="2026-07-30T00:00:01Z",
            authority_resolver=SyntheticIndexAuthority(
                [approved],
                edges=[future_edge],
                documents=[indexed],
            ),
        )
        self.assertEqual(
            [],
            query_index(
                future_snapshot,
                operation="search_visibility",
                audience="researcher",
                current_time="2026-07-25T00:00:00Z",
                authority_resolver=SyntheticIndexAuthority(
                    [approved],
                    edges=[future_edge],
                    documents=[indexed],
                ),
            ),
        )

        for blocked_policy in (
            policy(rights_state="blocked"),
            policy(evidence_expires_at="2026-07-24T12:00:00Z"),
        ):
            blocked_authority = SyntheticIndexAuthority(
                [blocked_policy], edges=[edge()], documents=[indexed]
            )
            self.assertEqual(
                [],
                query_index(
                    snapshot,
                    operation="search_visibility",
                    audience="researcher",
                    current_time="2026-07-25T00:00:00Z",
                    authority_resolver=blocked_authority,
                ),
            )
        self.assertEqual(
            [],
            query_index(
                snapshot,
                operation="search_visibility",
                audience="researcher",
                current_time="2026-07-25T00:00:00Z",
                authority_resolver=SyntheticIndexAuthority([]),
            ),
        )
        corrected_edge = edge()
        corrected_edge["origin_record_sha256"] = "d" * 64
        self.assertEqual(
            [],
            query_index(
                snapshot,
                operation="search_visibility",
                audience="researcher",
                current_time="2026-07-25T00:00:00Z",
                authority_resolver=SyntheticIndexAuthority(
                    [approved],
                    edges=[corrected_edge],
                    documents=[indexed],
                ),
            ),
        )

    def test_project_native_private_fields_require_consent(self) -> None:
        private_document = document()
        private_document["source_id"] = "project-native-visitor-inputs"
        private_document["duplicate_cluster_id"] = None
        private_document["fields"][0]["origin_class"] = "project_native"
        private_document["fields"][0]["visibility_class"] = "project_private"
        private_edge = edge()
        private_edge["source_id"] = "project-native-visitor-inputs"
        private_edge["origin_class"] = "project_native"
        public_policy = policy()
        public_policy["allowed_audiences"] = ["public"]
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_private_without_consent",
                documents=[private_document],
                provenance_edges=[private_edge],
                visibility_policies=[public_policy],
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [public_policy],
                    edges=[private_edge],
                    documents=[private_document],
                ),
            )

        approved_private = copy.deepcopy(private_document)
        approved_private["fields"][0]["consent_snapshot_sha256"] = "f" * 64
        approved_policy = policy()
        approved_policy["consent_snapshot_sha256"] = "f" * 64
        approved_policy["consent_state"] = "approved"
        approved_policy["allowed_audiences"] = ["operator"]
        standalone_schema = json.loads(
            (ROOT / "schemas" / "v1" / "index-document.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaises(ValidationError):
            Draft202012Validator(standalone_schema).validate(approved_private)
        non_project_private = document()
        non_project_private["duplicate_cluster_id"] = None
        non_project_private["fields"][0]["origin_class"] = "project_native"
        non_project_private["fields"][0][
            "visibility_class"
        ] = "project_private"
        non_project_private["fields"][0][
            "consent_snapshot_sha256"
        ] = "f" * 64
        with self.assertRaises(ValidationError):
            Draft202012Validator(standalone_schema).validate(
                non_project_private
            )
        nested_schema = json.loads(
            (ROOT / "schemas" / "v1" / "index-snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaises(ValidationError):
            Draft202012Validator(nested_schema).validate(
                {
                    "schema_version": 1,
                    "record_type": "index_snapshot",
                    "index_snapshot_id": "index_snapshot_private_schema",
                    "snapshot_sha256": "0" * 64,
                    "built_at": NOW,
                    "documents": [approved_private],
                    "provenance_edges": [],
                    "visibility_policies": [],
                    "duplicate_clusters": [],
                    "deletion_events": [],
                    "event_lineage_edges": [],
                }
            )
        with self.assertRaises(ValidationError):
            Draft202012Validator(nested_schema).validate(
                {
                    "schema_version": 1,
                    "record_type": "index_snapshot",
                    "index_snapshot_id": "index_snapshot_private_field_schema",
                    "snapshot_sha256": "0" * 64,
                    "built_at": NOW,
                    "documents": [non_project_private],
                    "provenance_edges": [],
                    "visibility_policies": [],
                    "duplicate_clusters": [],
                    "deletion_events": [],
                    "event_lineage_edges": [],
                }
            )
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_private_wrong_retention",
                documents=[approved_private],
                provenance_edges=[private_edge],
                visibility_policies=[approved_policy],
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [approved_policy],
                    edges=[private_edge],
                    documents=[approved_private],
                ),
            )
        approved_private["fields"][0][
            "retention_class"
        ] = "project_native_expiring"
        approved = build_index_snapshot(
            snapshot_id="index_snapshot_private_expiring",
            documents=[approved_private],
            provenance_edges=[private_edge],
            visibility_policies=[approved_policy],
            duplicate_clusters=[],
            deletion_events=[],
            built_at=NOW,
            authority_resolver=SyntheticIndexAuthority(
                [approved_policy],
                edges=[private_edge],
                documents=[approved_private],
            ),
        )
        self.assertEqual(
            "project_native_expiring",
            approved["documents"][0]["fields"][0]["retention_class"],
        )

    def test_duplicate_cluster_never_collapses_source_records(self) -> None:
        bad = cluster()
        bad["members"][1]["index_document_id"] = bad["members"][0][
            "index_document_id"
        ]
        with self.assertRaises(SearchIndexError):
            validate_duplicate_cluster(bad)

        first_document = document()
        second_document = copy.deepcopy(first_document)
        second_document["index_document_id"] = "index_document_asset_002"
        second_document["source_id"] = "njp-center-main"
        second_document["asset_id"] = "asset_002"
        second_document["fields"][0][
            "provenance_edge_id"
        ] = "provenance_edge_field_title_002"
        first_edge = edge()
        second_edge = copy.deepcopy(first_edge)
        second_edge["provenance_edge_id"] = (
            "provenance_edge_field_title_002"
        )
        second_edge["index_document_id"] = "index_document_asset_002"
        second_edge["source_id"] = "njp-center-main"
        second_edge["asset_id"] = "asset_002"
        first_policy = policy()
        second_policy = copy.deepcopy(first_policy)
        second_policy["visibility_policy_id"] = (
            "visibility_policy_field_title_002"
        )
        second_policy["index_document_id"] = "index_document_asset_002"
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_duplicates",
            documents=[second_document, first_document],
            provenance_edges=[second_edge, first_edge],
            visibility_policies=[second_policy, first_policy],
            duplicate_clusters=[cluster()],
            deletion_events=[],
            built_at=NOW,
            authority_resolver=SyntheticIndexAuthority(
                [first_policy, second_policy],
                edges=[first_edge, second_edge],
                documents=[first_document, second_document],
            ),
        )
        self.assertEqual(
            ["index_document_asset_001", "index_document_asset_002"],
            [item["index_document_id"] for item in snapshot["documents"]],
        )
        third_document = copy.deepcopy(second_document)
        third_document["index_document_id"] = "index_document_asset_003"
        third_document["asset_id"] = "asset_003"
        third_document["fields"][0][
            "provenance_edge_id"
        ] = "provenance_edge_field_title_003"
        third_edge = copy.deepcopy(second_edge)
        third_edge["provenance_edge_id"] = (
            "provenance_edge_field_title_003"
        )
        third_edge["index_document_id"] = "index_document_asset_003"
        third_edge["asset_id"] = "asset_003"
        third_policy = copy.deepcopy(second_policy)
        third_policy["visibility_policy_id"] = (
            "visibility_policy_field_title_003"
        )
        third_policy["index_document_id"] = "index_document_asset_003"
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_incomplete_cluster",
                documents=[
                    first_document,
                    second_document,
                    third_document,
                ],
                provenance_edges=[first_edge, second_edge, third_edge],
                visibility_policies=[
                    first_policy,
                    second_policy,
                    third_policy,
                ],
                duplicate_clusters=[cluster()],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [first_policy, second_policy, third_policy],
                    edges=[first_edge, second_edge, third_edge],
                    documents=[
                        first_document,
                        second_document,
                        third_document,
                    ],
                ),
            )
        duplicate_source_asset = copy.deepcopy(second_document)
        duplicate_source_asset["duplicate_cluster_id"] = None
        duplicate_source_asset["source_id"] = first_document["source_id"]
        duplicate_source_asset["asset_id"] = first_document["asset_id"]
        first_without_cluster = copy.deepcopy(first_document)
        first_without_cluster["duplicate_cluster_id"] = None
        duplicate_edge = copy.deepcopy(second_edge)
        duplicate_edge["source_id"] = first_document["source_id"]
        duplicate_edge["asset_id"] = first_document["asset_id"]
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_duplicate_source_asset",
                documents=[first_without_cluster, duplicate_source_asset],
                provenance_edges=[first_edge, duplicate_edge],
                visibility_policies=[first_policy, second_policy],
                duplicate_clusters=[],
                deletion_events=[],
                built_at=NOW,
                authority_resolver=SyntheticIndexAuthority(
                    [first_policy, second_policy],
                    edges=[first_edge, duplicate_edge],
                    documents=[
                        first_without_cluster,
                        duplicate_source_asset,
                    ],
                ),
            )

    def test_exact_deletion_event_removes_only_named_field(self) -> None:
        second = field("field_period")
        second["name"] = "period"
        second["value"] = "1980s"
        value = document()
        value["duplicate_cluster_id"] = None
        value["fields"].append(second)
        value["fields"].sort(key=lambda item: item["field_id"])
        deletion = {
            "schema_version": 1,
            "record_type": "deletion_event",
            "deletion_event_id": "deletion_event_001",
            "index_document_id": "index_document_asset_001",
            "field_id": "field_title",
            "reason_code": "rights_revoked",
            "authority_snapshot_sha256": "c" * 64,
            "occurred_at": "2026-07-25T00:00:00Z",
            "reindex_action": "remove_exact_field",
            "replacement_document_sha256": None,
            "replacement_provenance_edge_sha256": None,
            "replacement_visibility_policy_sha256": None,
        }
        self.assertEqual(deletion, validate_deletion_event(deletion))
        final_document = copy.deepcopy(value)
        final_document["fields"] = [
            item
            for item in final_document["fields"]
            if item["field_id"] != "field_title"
        ]
        final_edge = edge("field_period")
        final_policy = policy(field_id="field_period")
        deletion["authority_snapshot_sha256"] = lineage_hash(
            [edge(), final_edge]
        )
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_002",
            documents=[value],
            provenance_edges=[final_edge],
            visibility_policies=[final_policy],
            duplicate_clusters=[],
            deletion_events=[deletion],
            event_lineage_edges=[edge(), final_edge],
            built_at="2026-07-25T00:00:01Z",
            authority_resolver=SyntheticIndexAuthority(
                [final_policy],
                [deletion],
                edges=[final_edge],
                documents=[final_document],
            ),
        )
        self.assertEqual(
            ["field_period"],
            [item["field_id"] for item in snapshot["documents"][0]["fields"]],
        )
        self.assertEqual(
            [
                "provenance_edge_field_period",
                "provenance_edge_field_title",
            ],
            [
                item["provenance_edge_id"]
                for item in snapshot["event_lineage_edges"]
            ],
        )
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_forged",
                documents=[value],
                provenance_edges=[final_edge],
                visibility_policies=[final_policy],
                duplicate_clusters=[],
                deletion_events=[deletion],
                event_lineage_edges=[edge(), final_edge],
                built_at="2026-07-25T00:00:01Z",
                authority_resolver=SyntheticIndexAuthority(
                    [final_policy],
                    edges=[final_edge],
                    documents=[final_document],
                ),
            )
        visible = query_index(
            snapshot,
            operation="search_visibility",
            audience="researcher",
            current_time="2026-07-25T00:00:02Z",
            authority_resolver=SyntheticIndexAuthority(
                [final_policy],
                [deletion],
                edges=[final_edge],
                documents=[final_document],
            ),
        )
        self.assertEqual(["field_period"], visible[0]["visible_field_ids"])

    def test_replacement_requires_complete_dependent_reindex_events(self) -> None:
        value = document()
        value["duplicate_cluster_id"] = None
        derived_field = field("field_period")
        derived_field["name"] = "period"
        derived_field["value"] = "1980s"
        derived_field["origin_class"] = "derived_observation"
        value["fields"].append(derived_field)
        value["fields"].sort(key=lambda item: item["field_id"])
        root_edge = edge()
        derived_edge = edge("field_period")
        derived_edge["origin_class"] = "derived_observation"
        derived_edge["transformation_id"] = "transformation_period_001"
        derived_edge["input_provenance_edge_ids"] = [
            "provenance_edge_field_title"
        ]
        old_root_edge = copy.deepcopy(root_edge)
        old_root_edge["origin_record_sha256"] = "d" * 64
        old_derived_edge = copy.deepcopy(derived_edge)
        old_derived_edge["origin_record_sha256"] = "e" * 64
        historical_lineage = [old_root_edge, old_derived_edge]
        policies = [policy(field_id="field_period"), policy()]
        root_replacement = {
            "schema_version": 1,
            "record_type": "deletion_event",
            "deletion_event_id": "deletion_event_replace_title",
            "index_document_id": "index_document_asset_001",
            "field_id": "field_title",
            "reason_code": "source_corrected",
            "authority_snapshot_sha256": lineage_hash(historical_lineage),
            "occurred_at": "2026-07-25T00:00:00Z",
            "reindex_action": "replace_exact_field",
            "replacement_document_sha256": record_hash(value),
            "replacement_provenance_edge_sha256": record_hash(root_edge),
            "replacement_visibility_policy_sha256": record_hash(policy()),
        }
        derived_replacement = copy.deepcopy(root_replacement)
        derived_replacement["deletion_event_id"] = (
            "deletion_event_replace_period"
        )
        derived_replacement["field_id"] = "field_period"
        derived_replacement["reason_code"] = "transformation_replaced"
        derived_replacement["authority_snapshot_sha256"] = lineage_hash(
            historical_lineage
        )
        derived_replacement["replacement_provenance_edge_sha256"] = (
            record_hash(derived_edge)
        )
        derived_replacement["replacement_visibility_policy_sha256"] = (
            record_hash(policy(field_id="field_period"))
        )
        base_arguments = {
            "snapshot_id": "index_snapshot_replacement",
            "documents": [value],
            "provenance_edges": [root_edge, derived_edge],
            "visibility_policies": policies,
            "duplicate_clusters": [],
            "built_at": "2026-07-25T00:00:01Z",
        }
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                **base_arguments,
                deletion_events=[root_replacement],
                event_lineage_edges=historical_lineage,
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    [root_replacement],
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            )
        equal_time_lineage = copy.deepcopy(historical_lineage)
        equal_time_lineage[0]["evidence_at"] = (
            root_replacement["occurred_at"]
        )
        equal_root_event = copy.deepcopy(root_replacement)
        equal_derived_event = copy.deepcopy(derived_replacement)
        equal_authority = lineage_hash(equal_time_lineage)
        equal_root_event["authority_snapshot_sha256"] = equal_authority
        equal_derived_event["authority_snapshot_sha256"] = equal_authority
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                **base_arguments,
                deletion_events=[
                    equal_root_event,
                    equal_derived_event,
                ],
                event_lineage_edges=equal_time_lineage,
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    [equal_root_event, equal_derived_event],
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            )
        replaced = build_index_snapshot(
            **base_arguments,
            deletion_events=[root_replacement, derived_replacement],
            event_lineage_edges=historical_lineage,
            authority_resolver=SyntheticIndexAuthority(
                policies,
                [root_replacement, derived_replacement],
                edges=[root_edge, derived_edge],
                documents=[value],
            ),
        )
        self.assertEqual(2, len(replaced["deletion_events"]))
        self.assertEqual(2, len(replaced["event_lineage_edges"]))
        self.assertEqual(2, len(replaced["documents"][0]["fields"]))
        self.assertNotEqual(
            replaced["provenance_edges"],
            replaced["event_lineage_edges"],
        )
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                **base_arguments,
                deletion_events=[
                    root_replacement,
                    derived_replacement,
                ],
                event_lineage_edges=[root_edge, derived_edge],
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    [root_replacement, derived_replacement],
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            )
        contradictory_lineage = copy.deepcopy(historical_lineage)
        contradictory_lineage[0]["origin_record_sha256"] = "f" * 64
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                **base_arguments,
                deletion_events=[
                    root_replacement,
                    derived_replacement,
                ],
                event_lineage_edges=contradictory_lineage,
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    [root_replacement, derived_replacement],
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            )
        late_lineage = copy.deepcopy(historical_lineage)
        late_lineage[0]["evidence_at"] = "2026-07-25T00:00:00.500000Z"
        late_root_event = copy.deepcopy(root_replacement)
        late_derived_event = copy.deepcopy(derived_replacement)
        late_authority = lineage_hash(late_lineage)
        late_root_event["authority_snapshot_sha256"] = late_authority
        late_derived_event["authority_snapshot_sha256"] = late_authority
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                **base_arguments,
                deletion_events=[late_root_event, late_derived_event],
                event_lineage_edges=late_lineage,
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    [late_root_event, late_derived_event],
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            )
        visible = query_index(
            replaced,
            operation="search_visibility",
            audience="researcher",
            current_time="2026-07-25T00:00:02Z",
            authority_resolver=SyntheticIndexAuthority(
                policies,
                [root_replacement, derived_replacement],
                edges=[root_edge, derived_edge],
                documents=[value],
            ),
        )
        self.assertEqual(
            ["field_period", "field_title"],
            visible[0]["visible_field_ids"],
        )
        self.assertEqual(
            [],
            query_index(
                replaced,
                operation="search_visibility",
                audience="researcher",
                current_time="2026-07-25T00:00:02Z",
                authority_resolver=SyntheticIndexAuthority(
                    policies,
                    edges=[root_edge, derived_edge],
                    documents=[value],
                ),
            ),
        )

    def test_raw_content_signed_urls_and_private_paths_are_rejected(self) -> None:
        for unsafe_value in (
            "https://example.invalid/object?X-Amz-Signature=secret",
            "file://private/video.mp4",
            "Full source prose that is intentionally not index metadata.",
            "/var/lib/private/catalogue.json",
            "See /var/lib/private/catalogue.json",
            "\\\\server\\private\\catalogue.json",
            "Copied from \\\\server\\private\\catalogue.json",
            "s3://private-bucket/object",
            "s3:private-bucket/object",
            "s3: private-bucket/object",
            "data:text/plain,private",
            "data: text/plain,private",
            "DATA: text/plain,private",
            "data:",
            "urn:performing-fire:private",
            "urn:",
            "javascript:alert(1)",
            "javascript: alert(1)",
            "JavaScript: alert(1)",
            "custom: payload",
            "vscode: file/private",
            "vscode: file%3A%2F%2FUsers%2Fprivate",
            "custom: s3%3A%2F%2Fprivate-bucket%2Fobject",
            "unknown: javascript%3Aalert(1)",
            "custom%3A payload",
            "CUSTOM%3a payload",
            "vscode%3Afile/private",
            "unknown%3A javascript%3Aalert(1)",
            "custom%253A payload",
            "custom&#58; payload",
            "custom&#x3a; payload",
            "custom&#58 payload",
            "custom&#x3a payload",
            "custom： payload",
            "custom∶ payload",
            "customː payload",
            "custom˸ payload",
            "custom։ payload",
            "custom᠄ payload",
            "custom⁚ payload",
            "custom⦂ payload",
            "custom፡ payload",
            "Title: Synthetic catalogue entry",
            "~/private/catalogue.json",
            "../private/catalogue.json",
            "Synthetic title\n",
            "Cafe\u0301",
            "가",
            "か\u3099",
            (
                "Authorization Bearer "
                "eyJhbGciOiJIUzI1NiJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.synthetic"
            ),
            "AWS Access Key ID AKIAIOSFODNN7EXAMPLE",
            "GitHub token " + "gh" + "p_" + ("synthetic" * 4),
            "Credential abcdefghijklmnopqrstuvwxyz",
        ):
            unsafe = document()
            unsafe["fields"][0]["value"] = unsafe_value
            with self.assertRaises(SearchIndexError):
                validate_index_document(unsafe)
            schema = json.loads(
                (ROOT / "schemas" / "v1" / "index-document.json").read_text(
                    encoding="utf-8"
                )
            )
            with self.assertRaises(ValidationError):
                Draft202012Validator(schema).validate(unsafe)

        for safe_value in (
            "Synthetic catalogue title",
            "백남준 비디오 아카이브",
            "Nam June Paik — 1980s",
            "ビデオ アーカイブ",
            "录像档案",
            "Tiếng Việt",
            "映像・アーカイブ「展示」",
            "映像、記録。",
            "录像档案（展览）",
            "Signature performance",
            "Credentialed artist",
            "Token performance",
        ):
            safe = document()
            safe["fields"][0]["value"] = safe_value
            self.assertEqual(safe, validate_index_document(safe))
            Draft202012Validator(schema).validate(safe)

        unsafe_policy = policy()
        unsafe_policy["review_trigger"] = "https://example.invalid/review"
        with self.assertRaises(SearchIndexError):
            validate_visibility_policy(unsafe_policy)
        newline_policy = policy()
        newline_policy["review_trigger"] = "Synthetic review trigger\n"
        with self.assertRaises(SearchIndexError):
            validate_visibility_policy(newline_policy)
        unsafe_cluster = cluster()
        unsafe_cluster["evidence_summary"] = "https://example.invalid/evidence"
        with self.assertRaises(SearchIndexError):
            validate_duplicate_cluster(unsafe_cluster)
        newline_cluster = cluster()
        newline_cluster["evidence_summary"] = "Synthetic evidence\n"
        with self.assertRaises(SearchIndexError):
            validate_duplicate_cluster(newline_cluster)


if __name__ == "__main__":
    unittest.main()
