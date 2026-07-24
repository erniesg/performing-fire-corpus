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
        }
        for field_name in (
            "documents",
            "provenance_edges",
            "visibility_policies",
            "duplicate_clusters",
            "deletion_events",
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
                authority_resolver=SyntheticIndexAuthority([]),
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
            authority_resolver=SyntheticIndexAuthority([]),
        )
        self.assertEqual(
            ["index_document_asset_001", "index_document_asset_002"],
            [item["index_document_id"] for item in snapshot["documents"]],
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
        }
        self.assertEqual(deletion, validate_deletion_event(deletion))
        snapshot = build_index_snapshot(
            snapshot_id="index_snapshot_002",
            documents=[value],
            provenance_edges=[edge(), edge("field_period")],
            visibility_policies=[policy(field_id="field_period"), policy()],
            duplicate_clusters=[],
            deletion_events=[deletion],
            built_at="2026-07-25T00:00:01Z",
            authority_resolver=SyntheticIndexAuthority(
                [policy(field_id="field_period"), policy()],
                [deletion],
            ),
        )
        self.assertEqual(
            ["field_period"],
            [item["field_id"] for item in snapshot["documents"][0]["fields"]],
        )
        with self.assertRaises(SearchIndexError):
            build_index_snapshot(
                snapshot_id="index_snapshot_forged",
                documents=[value],
                provenance_edges=[edge(), edge("field_period")],
                visibility_policies=[
                    policy(field_id="field_period"),
                    policy(),
                ],
                duplicate_clusters=[],
                deletion_events=[deletion],
                built_at="2026-07-25T00:00:01Z",
                authority_resolver=SyntheticIndexAuthority(
                    [policy(field_id="field_period"), policy()]
                ),
            )

    def test_raw_content_signed_urls_and_private_paths_are_rejected(self) -> None:
        for unsafe_value in (
            "https://example.invalid/object?X-Amz-Signature=secret",
            "file://private/video.mp4",
            "Full source prose that is intentionally not index metadata.",
        ):
            unsafe = document()
            unsafe["fields"][0]["value"] = unsafe_value
            with self.assertRaises(SearchIndexError):
                validate_index_document(unsafe)


if __name__ == "__main__":
    unittest.main()
