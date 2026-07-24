from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import raw_object_key
from performing_fire_corpus.governance import CANONICAL_ENDPOINT_IDS
from performing_fire_corpus.qualification import (
    QUALIFICATION_OPERATIONS,
    QualificationError,
    asset_facts_sha256,
    build_qualified_job,
    compile_asset_qualification,
    query_qualified_assets,
    validate_asset_qualification,
)


SCHEMA = ROOT / "schemas" / "v1" / "asset-qualification.json"
NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
HASH = "a" * 64
GOVERNANCE_OPERATIONS = (
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
ENDPOINTS = {
    "antiegg-fluxus": "antiegg-media-api",
    "njp-center-main": "njp-center-main-home",
    "njp-center-video-archive": "njp-center-video-archive-page",
    "njp-video-library": "njp-video-library-home",
    "njp-youtube-official": "njp-youtube-videos-api",
}
HOSTS = {
    "antiegg-fluxus": "antiegg.kr",
    "njp-center-main": "njp.ggcf.kr",
    "njp-center-video-archive": "njp.ggcf.kr",
    "njp-video-library": "njpvideo.ggcf.kr",
    "njp-youtube-official": "www.youtube.com",
}


def source_governance(
    source_id: str,
    *,
    asset_id: str = "asset_synthetic_001",
    operation_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    operation_states = {
        operation: "approved" for operation in GOVERNANCE_OPERATIONS
    }
    operation_states.update(operation_overrides or {})
    fact_states = {
        "access_control": "allowed",
        "api_availability": "available",
        "authentication": "not_required",
        "copyright_lawful_basis": "permitted",
        "platform_terms": "permitted",
        "robots": "allowed",
    }
    return {
        "schema_version": 1,
        "record_type": "source_governance",
        "source_governance_id": f"source_governance_{source_id.replace('-', '_')}",
        "source_id": source_id,
        "endpoint_id": ENDPOINTS[source_id],
        "asset_id": asset_id,
        "fact_states": fact_states,
        "observations": [
            {
                "dimension": dimension,
                "state": state,
                "observed_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-30T00:00:00Z",
                "evidence_id": f"evidence_synthetic_{dimension}",
                "next_safe_action": "Revalidate the synthetic authority.",
            }
            for dimension, state in sorted(fact_states.items())
        ],
        "operation_states": operation_states,
        "decisions": [
            {
                "affected_operation": operation,
                "state": state,
                "authority_class": "source_policy_reviewer",
                "basis_code": "synthetic_reviewed_policy",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-30T00:00:00Z",
                "review_trigger": "Recheck when source policy changes.",
                "next_safe_action": "Use only the reviewed operation.",
            }
            for operation, state in sorted(operation_states.items())
        ],
        "blockers": [],
        "evaluated_at": "2026-07-23T00:00:00Z",
    }


def governance_registry(
    *records: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "registry_id": "performing-fire-source-governance",
        "records": sorted(
            (copy.deepcopy(record) for record in records),
            key=lambda record: (
                str(record["source_id"]),
                str(record["endpoint_id"] or ""),
                str(record.get("asset_id") or ""),
                str(record["source_governance_id"]),
            ),
        ),
    }


def complete_governance_registry(
    record: dict[str, object],
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for source_id, endpoint_ids in CANONICAL_ENDPOINT_IDS.items():
        source_wide = copy.deepcopy(record)
        source_wide["source_governance_id"] = (
            f"source_governance_{source_id.replace('-', '_')}_source"
        )
        source_wide["source_id"] = source_id
        source_wide["endpoint_id"] = None
        source_wide["asset_id"] = None
        records.append(source_wide)
        for endpoint_id in endpoint_ids:
            endpoint = copy.deepcopy(record)
            endpoint["source_governance_id"] = (
                f"source_governance_{endpoint_id.replace('-', '_')}_endpoint"
            )
            endpoint["source_id"] = source_id
            endpoint["endpoint_id"] = endpoint_id
            endpoint["asset_id"] = None
            records.append(endpoint)
    asset_scoped = copy.deepcopy(record)
    asset_scoped["source_governance_id"] = (
        f"source_governance_{str(record['source_id']).replace('-', '_')}_asset"
    )
    records.append(asset_scoped)
    return governance_registry(*records)


def inventory_authority(
    asset_value: dict[str, object],
) -> dict[str, object]:
    source_id = str(asset_value["source_id"])
    source_scope_id = (
        "source_scope_njp_youtube_official_channel"
        if source_id == "njp-youtube-official"
        else f"source_scope_{source_id.replace('-', '_')}"
    )
    youtube_fields: dict[str, object]
    if source_id == "njp-youtube-official":
        session_binding_sha256 = "c" * 64
        youtube_handle = "@NamJunePaikArtCenter"
        youtube_channel_id = "UCsyntheticChannel001"
        youtube_uploads_playlist_id = "UUsyntheticUploads001"
        channel_payload = {
            "channel_id": youtube_channel_id,
            "handle": youtube_handle,
            "session_binding_sha256": session_binding_sha256,
            "uploads_playlist_id": youtube_uploads_playlist_id,
        }
        youtube_channel_lineage_sha256 = hashlib.sha256(
            json.dumps(
                channel_payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        youtube_uploads_manifest_sha256 = "d" * 64
        youtube_video_ids = [str(asset_value["source_item_id"])]
        uploads_payload = {
            "channel_lineage_sha256": (
                youtube_channel_lineage_sha256
            ),
            "session_binding_sha256": session_binding_sha256,
            "uploads_manifest_sha256": (
                youtube_uploads_manifest_sha256
            ),
            "video_ids": youtube_video_ids,
        }
        youtube_uploads_lineage_sha256 = hashlib.sha256(
            json.dumps(
                uploads_payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        youtube_fields = {
            "youtube_channel_id": youtube_channel_id,
            "youtube_channel_lineage_sha256": (
                youtube_channel_lineage_sha256
            ),
            "youtube_handle": youtube_handle,
            "youtube_session_binding_sha256": (
                session_binding_sha256
            ),
            "youtube_uploads_lineage_sha256": (
                youtube_uploads_lineage_sha256
            ),
            "youtube_uploads_manifest_sha256": (
                youtube_uploads_manifest_sha256
            ),
            "youtube_uploads_playlist_id": (
                youtube_uploads_playlist_id
            ),
            "youtube_video_ids": youtube_video_ids,
        }
    else:
        youtube_fields = {
            "youtube_channel_id": None,
            "youtube_channel_lineage_sha256": None,
            "youtube_handle": None,
            "youtube_session_binding_sha256": None,
            "youtube_uploads_lineage_sha256": None,
            "youtube_uploads_manifest_sha256": None,
            "youtube_uploads_playlist_id": None,
            "youtube_video_ids": [],
        }
    payload = {
        "schema_version": 1,
        "record_type": "inventory_authority",
        "inventory_record_id": asset_value["inventory_record_id"],
        "source_id": source_id,
        "endpoint_id": asset_value["endpoint_id"],
        "source_item_id": asset_value["source_item_id"],
        "source_scope_id": source_scope_id,
        **youtube_fields,
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return {
        **payload,
        "inventory_record_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def rehash_inventory_authority(
    value: dict[str, object],
) -> dict[str, object]:
    rebound = copy.deepcopy(value)
    payload = {
        key: child
        for key, child in rebound.items()
        if key != "inventory_record_sha256"
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    rebound["inventory_record_sha256"] = hashlib.sha256(encoded).hexdigest()
    return rebound


def asset(
    source_id: str = "njp-video-library",
    *,
    asset_kind: str = "media",
    access_state: str = "available",
    public_url: str | None = None,
    retention_class: str = "selected_raw",
    planned_object_key: str | None = None,
) -> dict[str, object]:
    asset_id = "asset_synthetic_001"
    host = HOSTS[source_id]
    if public_url is None:
        public_url = f"https://{host}/synthetic-object"
    if planned_object_key is None:
        planned_object_key = raw_object_key(
            "performing-fire/",
            source_id,
            asset_id,
            HASH,
        )
    value = {
        "source_id": source_id,
        "endpoint_id": ENDPOINTS[source_id],
        "asset_id": asset_id,
        "inventory_record_id": "inventory_synthetic_001",
        "inventory_record_sha256": "0" * 64,
        "source_item_id": "synthetic001",
        "asset_kind": asset_kind,
        "public_url": public_url,
        "expected_host": host,
        "media_type": "video/mp4",
        "max_bytes": 4096,
        "access_state": access_state,
        "retention_class": retention_class,
        "deletion_policy": "review_on_revocation",
        "derivative_policy": "operation_specific",
        "retrieval_policy": "public",
        "planned_object_key": planned_object_key,
    }
    value["inventory_record_sha256"] = inventory_authority(value)[
        "inventory_record_sha256"
    ]
    return value


def decisions(
    asset_value: dict[str, object],
    *,
    basis_code: str = "asset_specific_permission",
    scope: str = "asset_specific",
) -> list[dict[str, object]]:
    snapshot = asset_facts_sha256(asset_value)
    values = []
    for operation in QUALIFICATION_OPERATIONS:
        operation_basis = (
            "reviewed_metadata_basis"
            if operation == "metadata_retention"
            else basis_code
        )
        operation_scope = (
            "source_policy"
            if operation == "metadata_retention"
            else scope
        )
        values.append(
            {
                "operation": operation,
                "state": "approved",
                "decision_scope": operation_scope,
                "basis_code": operation_basis,
                "authority_class": "rights_reviewer",
                "evidence_ref": f"evidence_{operation}",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-30T00:00:00Z",
                "review_trigger": "Recheck when rights or asset facts change.",
                "asset_facts_sha256": snapshot,
                "retention_class": asset_value["retention_class"],
            }
        )
    return values


def rebind_qualification(value: dict[str, object]) -> dict[str, object]:
    rebound = copy.deepcopy(value)
    payload = {
        key: child
        for key, child in rebound.items()
        if key not in {"qualification_id", "qualification_sha256"}
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    rebound["qualification_id"] = (
        "qualification_" + hashlib.sha256(encoded).hexdigest()[:24]
    )
    without_hash = {
        key: child
        for key, child in rebound.items()
        if key != "qualification_sha256"
    }
    rebound["qualification_sha256"] = hashlib.sha256(
        (
            json.dumps(
                without_hash,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    return rebound


class SyntheticQualificationAuthority:
    def __init__(
        self,
        bundles: list[dict[str, object]],
        *,
        issued_inventory_records: list[dict[str, object]] | None = None,
    ) -> None:
        self.records = {
            (
                str(bundle["asset"]["source_id"]),
                str(bundle["asset"]["asset_id"]),
            ): copy.deepcopy(bundle)
            for bundle in bundles
        }
        issued = (
            [
                copy.deepcopy(bundle["inventory_record"])
                for bundle in bundles
            ]
            if issued_inventory_records is None
            else copy.deepcopy(issued_inventory_records)
        )
        self.issued_inventory_records = {
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for record in issued
        }

    def resolve_asset_authority(
        self, *, source_id: str, asset_id: str
    ) -> dict[str, object] | None:
        value = self.records.get((source_id, asset_id))
        return None if value is None else copy.deepcopy(value)

    def inventory_authority_was_issued(
        self, *, inventory_record: dict[str, object]
    ) -> bool:
        return (
            json.dumps(
                inventory_record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            in self.issued_inventory_records
        )


class AssetQualificationTests(unittest.TestCase):
    def qualify(
        self,
        asset_value: dict[str, object],
        decision_values: list[dict[str, object]] | None = None,
        governance: dict[str, object] | None = None,
        inventory: dict[str, object] | None = None,
    ) -> dict[str, object]:
        governance_value = (
            source_governance(
                str(asset_value["source_id"]),
                asset_id=str(asset_value["asset_id"]),
            )
            if governance is None
            else governance
        )
        if "records" not in governance_value:
            governance_value = complete_governance_registry(
                governance_value
            )
        return compile_asset_qualification(
            asset_value,
            inventory_authority(asset_value)
            if inventory is None
            else inventory,
            governance_value,
            decisions(asset_value)
            if decision_values is None
            else decision_values,
            now=NOW,
        )

    def test_schema_and_runtime_preserve_nine_independent_operations(self) -> None:
        value = self.qualify(asset())
        self.assertEqual(
            list(QUALIFICATION_OPERATIONS),
            [item["operation"] for item in value["operation_decisions"]],
        )
        self.assertTrue(
            all(item["eligible"] for item in value["operation_decisions"])
        )
        self.assertEqual(value, validate_asset_qualification(value, now=NOW))

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(value)
        unknown = copy.deepcopy(value)
        unknown["unexpected"] = True
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(unknown)

        forged = copy.deepcopy(value)
        forged["operation_decisions"][1]["asset_facts_sha256"] = "b" * 64
        forged = rebind_qualification(forged)
        with self.assertRaisesRegex(QualificationError, "asset facts"):
            validate_asset_qualification(forged, now=NOW)

    def test_missing_or_public_visibility_basis_never_approves_other_operations(
        self,
    ) -> None:
        asset_value = asset()
        values = decisions(asset_value)
        values = [
            value
            for value in values
            if value["operation"] != "transcription"
        ]
        for value in values:
            if value["operation"] == "ocr":
                value["basis_code"] = "public_visibility"
            if value["operation"] == "video_understanding":
                value["evidence_ref"] = None
        qualified = self.qualify(asset_value, values)
        by_operation = {
            value["operation"]: value
            for value in qualified["operation_decisions"]
        }
        self.assertEqual("pending", by_operation["transcription"]["state"])
        self.assertEqual("blocked", by_operation["ocr"]["state"])
        self.assertEqual("pending", by_operation["video_understanding"]["state"])
        self.assertTrue(by_operation["metadata_retention"]["eligible"])
        self.assertTrue(by_operation["download"]["eligible"])

    def test_youtube_metadata_does_not_authorize_caption_or_media_use(self) -> None:
        asset_value = asset(
            "njp-youtube-official",
            asset_kind="caption",
            public_url="https://www.youtube.com/watch?v=synthetic001",
        )
        generic = decisions(
            asset_value,
            basis_code="official_channel_metadata",
            scope="source_policy",
        )
        qualified = self.qualify(asset_value, generic)
        by_operation = {
            value["operation"]: value
            for value in qualified["operation_decisions"]
        }
        self.assertTrue(by_operation["metadata_retention"]["eligible"])
        for operation in QUALIFICATION_OPERATIONS:
            if operation != "metadata_retention":
                self.assertFalse(
                    by_operation[operation]["eligible"],
                    operation,
                )

        explicit = self.qualify(asset_value)
        self.assertTrue(
            all(value["eligible"] for value in explicit["operation_decisions"])
        )

    def test_current_platform_prohibition_blocks_every_operation(self) -> None:
        asset_value = asset(
            "njp-youtube-official",
            asset_kind="caption",
            public_url="https://www.youtube.com/watch?v=synthetic001",
        )
        governance = source_governance(
            "njp-youtube-official",
            asset_id=str(asset_value["asset_id"]),
        )
        governance["fact_states"]["platform_terms"] = "prohibited"
        for observation in governance["observations"]:
            if observation["dimension"] == "platform_terms":
                observation["state"] = "prohibited"
        qualified = self.qualify(
            asset_value,
            decisions(asset_value),
            governance,
        )
        for decision in qualified["operation_decisions"]:
            self.assertFalse(decision["eligible"], decision["operation"])
            self.assertTrue(
                any(
                    reason.endswith(":platform_terms:prohibited")
                    for reason in decision["reasons"]
                ),
                decision,
            )

    def test_each_source_lifecycle_gate_holds_its_affected_operations(self) -> None:
        cases = (
            (
                "caption",
                "caption_retention",
                {"download", "raw_storage", "indexing", "public_retrieval"},
            ),
            (
                "prose",
                "prose_retention",
                {"download", "raw_storage", "indexing", "public_retrieval"},
            ),
            (
                "media",
                "deletion",
                set(QUALIFICATION_OPERATIONS),
            ),
            (
                "media",
                "search_visibility",
                {"indexing", "public_retrieval"},
            ),
        )
        for asset_kind, source_operation, held_operations in cases:
            with self.subTest(source_operation=source_operation):
                asset_value = asset(asset_kind=asset_kind)
                governance = source_governance(
                    str(asset_value["source_id"]),
                    asset_id=str(asset_value["asset_id"]),
                    operation_overrides={source_operation: "blocked"},
                )
                qualified = self.qualify(
                    asset_value,
                    decisions(asset_value),
                    governance,
                )
                by_operation = {
                    value["operation"]: value
                    for value in qualified["operation_decisions"]
                }
                for operation in held_operations:
                    self.assertFalse(
                        by_operation[operation]["eligible"],
                        (source_operation, operation),
                    )
                    self.assertTrue(
                        any(
                            reason.startswith(f"source:{source_operation}:")
                            for reason in by_operation[operation]["reasons"]
                        ),
                        (source_operation, operation),
                    )
                if source_operation == "search_visibility":
                    self.assertTrue(by_operation["download"]["eligible"])

    def test_asset_locators_are_public_unsigned_and_source_scoped(self) -> None:
        for public_url, expected_host in (
            (
                "https://unreviewed.example/synthetic-object",
                "unreviewed.example",
            ),
            (
                "https://user@njpvideo.ggcf.kr/synthetic-object",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?token=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object",
                "antiegg.kr",
            ),
            (
                "https://antiegg.kr/synthetic-object",
                "antiegg.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?session=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?auth=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?accessToken=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?JSESSIONID=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?xSessionId=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object?myAccessTokenValue=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object;jsessionid=synthetic",
                "njpvideo.ggcf.kr",
            ),
            (
                "https://njpvideo.ggcf.kr/synthetic-object%3Bjsessionid=synthetic",
                "njpvideo.ggcf.kr",
            ),
        ):
            with self.subTest(public_url=public_url):
                asset_value = asset(public_url=public_url)
                asset_value["expected_host"] = expected_host
                with self.assertRaisesRegex(
                    QualificationError,
                    "host boundary|credential-bearing|private or secret-like",
                ):
                    self.qualify(asset_value)

        youtube = asset(
            "njp-youtube-official",
            asset_kind="media",
            public_url="https://www.youtube.com/watch?v=synthetic001",
        )
        for public_url in (
            "https://www.youtube.com/watch",
            "https://www.youtube.com/synthetic-object",
            "https://www.youtube.com/@anotherchannel/videos",
            "https://www.youtube.com/watch?v=another001",
        ):
            with self.subTest(public_url=public_url):
                invalid = copy.deepcopy(youtube)
                invalid["public_url"] = public_url
                with self.assertRaisesRegex(
                    QualificationError,
                    "YouTube|query",
                ):
                    self.qualify(invalid)

    def test_inventory_authority_is_hash_bound_and_officially_scoped(self) -> None:
        asset_value = asset(
            "njp-youtube-official",
            asset_kind="media",
            public_url="https://www.youtube.com/watch?v=synthetic001",
        )
        inventory = inventory_authority(asset_value)
        wrong_hash = copy.deepcopy(inventory)
        wrong_hash["inventory_record_sha256"] = "f" * 64
        with self.assertRaisesRegex(QualificationError, "hash"):
            self.qualify(asset_value, inventory=wrong_hash)

        wrong_scope = copy.deepcopy(inventory)
        wrong_scope["source_scope_id"] = "source_scope_foreign_channel"
        wrong_scope = rehash_inventory_authority(wrong_scope)
        with self.assertRaisesRegex(QualificationError, "official channel"):
            self.qualify(asset_value, inventory=wrong_scope)

        value = self.qualify(asset_value, inventory=inventory)
        drifted_inventory = copy.deepcopy(inventory)
        drifted_inventory["inventory_record_id"] = "inventory_foreign_001"
        drifted_inventory = rehash_inventory_authority(drifted_inventory)
        authority = SyntheticQualificationAuthority(
            [
                {
                    "asset": asset_value,
                    "inventory_record": drifted_inventory,
                    "source_governance_registry": complete_governance_registry(
                        source_governance(
                            str(asset_value["source_id"]),
                            asset_id=str(asset_value["asset_id"]),
                        )
                    ),
                    "operation_decisions": decisions(asset_value),
                }
            ]
        )
        self.assertEqual(
            [],
            query_qualified_assets(
                [value],
                operation="download",
                authority_resolver=authority,
                now=NOW,
            ),
        )

    def test_structurally_valid_foreign_youtube_lineage_is_not_issued(
        self,
    ) -> None:
        asset_value = asset(
            "njp-youtube-official",
            asset_kind="media",
            public_url="https://www.youtube.com/watch?v=synthetic001",
        )
        issued = inventory_authority(asset_value)
        foreign = copy.deepcopy(issued)
        foreign["youtube_channel_id"] = "UCforeignChannel001"
        foreign["youtube_uploads_playlist_id"] = "UUforeignUploads001"
        foreign["youtube_session_binding_sha256"] = "e" * 64
        channel_payload = {
            "channel_id": foreign["youtube_channel_id"],
            "handle": foreign["youtube_handle"],
            "session_binding_sha256": (
                foreign["youtube_session_binding_sha256"]
            ),
            "uploads_playlist_id": (
                foreign["youtube_uploads_playlist_id"]
            ),
        }
        foreign["youtube_channel_lineage_sha256"] = hashlib.sha256(
            json.dumps(
                channel_payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        foreign["youtube_uploads_manifest_sha256"] = "f" * 64
        uploads_payload = {
            "channel_lineage_sha256": (
                foreign["youtube_channel_lineage_sha256"]
            ),
            "session_binding_sha256": (
                foreign["youtube_session_binding_sha256"]
            ),
            "uploads_manifest_sha256": (
                foreign["youtube_uploads_manifest_sha256"]
            ),
            "video_ids": foreign["youtube_video_ids"],
        }
        foreign["youtube_uploads_lineage_sha256"] = hashlib.sha256(
            json.dumps(
                uploads_payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        foreign = rehash_inventory_authority(foreign)
        asset_value["inventory_record_sha256"] = foreign[
            "inventory_record_sha256"
        ]
        governance = complete_governance_registry(
            source_governance(
                str(asset_value["source_id"]),
                asset_id=str(asset_value["asset_id"]),
            )
        )
        decision_values = decisions(asset_value)
        candidate = self.qualify(
            asset_value,
            decision_values,
            governance,
            foreign,
        )
        authority = SyntheticQualificationAuthority(
            [
                {
                    "asset": asset_value,
                    "inventory_record": foreign,
                    "source_governance_registry": governance,
                    "operation_decisions": decision_values,
                }
            ],
            issued_inventory_records=[issued],
        )
        self.assertEqual(
            [],
            query_qualified_assets(
                [candidate],
                operation="download",
                authority_resolver=authority,
                now=NOW,
            ),
        )

    def test_all_content_requires_an_affirmative_basis_and_authority(self) -> None:
        for source_id in (
            "njp-center-main",
            "njp-center-video-archive",
            "njp-video-library",
        ):
            for basis_code in (
                "unclear_permission",
                "official_site",
                "no_permission",
            ):
                with self.subTest(
                    source_id=source_id,
                    basis_code=basis_code,
                ):
                    asset_value = asset(source_id, asset_kind="attachment")
                    qualified = self.qualify(
                        asset_value,
                        decisions(asset_value, basis_code=basis_code),
                    )
                    by_operation = {
                        value["operation"]: value
                        for value in qualified["operation_decisions"]
                    }
                    self.assertTrue(
                        by_operation["metadata_retention"]["eligible"]
                    )
                    for operation in QUALIFICATION_OPERATIONS:
                        if operation != "metadata_retention":
                            self.assertFalse(
                                by_operation[operation]["eligible"],
                                (source_id, basis_code, operation),
                            )
                            self.assertIn(
                                "rights:affirmative_basis_required",
                                by_operation[operation]["reasons"],
                            )

        asset_value = asset(asset_kind="attachment")
        untrusted_authority = decisions(asset_value)
        for decision in untrusted_authority:
            if decision["operation"] != "metadata_retention":
                decision["authority_class"] = "catalogue_editor"
        qualified = self.qualify(asset_value, untrusted_authority)
        for decision in qualified["operation_decisions"]:
            if decision["operation"] != "metadata_retention":
                self.assertFalse(decision["eligible"], decision["operation"])
                self.assertIn(
                    "rights:reviewed_authority_required",
                    decision["reasons"],
                )

        forged = self.qualify(asset_value)
        forged["operation_decisions"][1]["basis_code"] = "official_site"
        forged["operation_decisions"][1]["authority_class"] = "catalogue_editor"
        forged = rebind_qualification(forged)
        with self.assertRaisesRegex(
            QualificationError,
            "affirmative reviewed rights",
        ):
            validate_asset_qualification(forged, now=NOW)

    def test_checked_in_source_and_endpoint_governance_are_consumable(self) -> None:
        registry = json.loads(
            (ROOT / "config" / "source-governance.v1.json").read_text()
        )
        asset_value = asset("njp-video-library")
        records = [
            record
            for record in registry["records"]
            if record["source_id"] == "njp-video-library"
        ]
        self.assertEqual(2, len(records))
        registry = copy.deepcopy(registry)
        asset_layer = copy.deepcopy(
            next(
                record
                for record in records
                if record["endpoint_id"] == asset_value["endpoint_id"]
            )
        )
        asset_layer["source_governance_id"] = (
            "source_governance_njp_video_library_asset"
        )
        asset_layer["asset_id"] = asset_value["asset_id"]
        registry["records"].append(asset_layer)
        registry["records"].sort(
            key=lambda record: (
                str(record["source_id"]),
                str(record["endpoint_id"] or ""),
                str(record.get("asset_id") or ""),
                str(record["source_governance_id"]),
            )
        )
        qualified = self.qualify(
            asset_value,
            decisions(asset_value),
            registry,
        )
        self.assertEqual(asset_value["asset_id"], qualified["asset_id"])
        self.assertFalse(
            any(
                item["eligible"]
                for item in qualified["operation_decisions"]
            )
        )

    def test_all_applicable_governance_layers_are_reconciled(self) -> None:
        asset_value = asset()
        source_wide = source_governance(
            str(asset_value["source_id"]),
            asset_id=str(asset_value["asset_id"]),
        )
        source_wide["source_governance_id"] = (
            "source_governance_njp_video_library_source"
        )
        source_wide["endpoint_id"] = None
        source_wide["asset_id"] = None
        endpoint = source_governance(
            str(asset_value["source_id"]),
            asset_id=str(asset_value["asset_id"]),
            operation_overrides={"media_acquisition": "blocked"},
        )
        endpoint["source_governance_id"] = (
            "source_governance_njp_video_library_endpoint"
        )
        full_registry = complete_governance_registry(endpoint)
        for index, record in enumerate(full_registry["records"]):
            if (
                record["source_id"] == asset_value["source_id"]
                and record["endpoint_id"] is None
                and record.get("asset_id") is None
            ):
                full_registry["records"][index] = source_wide
                break
        full_registry["records"].sort(
            key=lambda record: (
                str(record["source_id"]),
                str(record["endpoint_id"] or ""),
                str(record.get("asset_id") or ""),
                str(record["source_governance_id"]),
            )
        )
        qualified = self.qualify(
            asset_value,
            decisions(asset_value),
            full_registry,
        )
        download = next(
            item
            for item in qualified["operation_decisions"]
            if item["operation"] == "download"
        )
        self.assertFalse(download["eligible"])
        self.assertIn(
            "source:media_acquisition:operation:blocked",
            download["reasons"],
        )

        with self.assertRaisesRegex(
            QualificationError,
            "omits a required",
        ):
            incomplete = copy.deepcopy(full_registry)
            incomplete["records"] = [
                record
                for record in incomplete["records"]
                if not (
                    record["source_id"] == asset_value["source_id"]
                    and record["endpoint_id"] == asset_value["endpoint_id"]
                    and record.get("asset_id") == asset_value["asset_id"]
                )
            ]
            self.qualify(
                asset_value,
                decisions(asset_value),
                incomplete,
            )

    def test_njp_access_blockers_hold_content_but_not_reviewed_metadata(
        self,
    ) -> None:
        for source_id in (
            "njp-center-main",
            "njp-center-video-archive",
            "njp-video-library",
        ):
            for access_state in (
                "http_403",
                "login_required",
                "signed_url",
                "expired_url",
            ):
                with self.subTest(source_id=source_id, access_state=access_state):
                    asset_value = asset(
                        source_id,
                        asset_kind="attachment",
                        access_state=access_state,
                    )
                    qualified = self.qualify(asset_value)
                    by_operation = {
                        value["operation"]: value
                        for value in qualified["operation_decisions"]
                    }
                    self.assertTrue(
                        by_operation["metadata_retention"]["eligible"]
                    )
                    self.assertFalse(by_operation["download"]["eligible"])
                    self.assertFalse(by_operation["raw_storage"]["eligible"])
                    self.assertIn(
                        f"access:{access_state}",
                        by_operation["download"]["reasons"],
                    )

    def test_antiegg_prose_and_media_require_permission_or_lawful_basis(
        self,
    ) -> None:
        for asset_kind in ("prose", "media"):
            with self.subTest(asset_kind=asset_kind):
                asset_value = asset(
                    "antiegg-fluxus",
                    asset_kind=asset_kind,
                )
                editorial = decisions(
                    asset_value,
                    basis_code="editorial_metadata",
                    scope="source_policy",
                )
                qualified = self.qualify(asset_value, editorial)
                by_operation = {
                    value["operation"]: value
                    for value in qualified["operation_decisions"]
                }
                self.assertTrue(
                    by_operation["metadata_retention"]["eligible"]
                )
                self.assertFalse(by_operation["download"]["eligible"])

                reviewed = self.qualify(asset_value)
                self.assertTrue(
                    all(
                        value["eligible"]
                        for value in reviewed["operation_decisions"]
                    )
                )

    def test_conflict_expiry_revocation_url_and_retention_drift_fail_closed(
        self,
    ) -> None:
        asset_value = asset()
        base = decisions(asset_value)

        conflicting = copy.deepcopy(base)
        other = copy.deepcopy(conflicting[1])
        other["state"] = "blocked"
        conflicting.append(other)
        with self.assertRaisesRegex(QualificationError, "conflicting"):
            self.qualify(asset_value, conflicting)

        expired = copy.deepcopy(base)
        expired[1]["expires_at"] = "2026-07-24T11:59:59Z"
        expired_value = self.qualify(asset_value, expired)
        self.assertFalse(expired_value["operation_decisions"][1]["eligible"])
        self.assertIn(
            "decision:expired",
            expired_value["operation_decisions"][1]["reasons"],
        )

        revoked = copy.deepcopy(base)
        revoked[1]["state"] = "revoked"
        revoked_value = self.qualify(asset_value, revoked)
        self.assertEqual(
            "revoked", revoked_value["operation_decisions"][1]["state"]
        )

        changed_url = copy.deepcopy(asset_value)
        changed_url["public_url"] = (
            "https://njpvideo.ggcf.kr/changed-synthetic-object"
        )
        changed = self.qualify(changed_url, base)
        self.assertFalse(changed["operation_decisions"][1]["eligible"])
        self.assertIn(
            "asset_facts:changed",
            changed["operation_decisions"][1]["reasons"],
        )

        retention_drift = copy.deepcopy(base)
        retention_drift[1]["retention_class"] = "inventory_metadata"
        drifted = self.qualify(asset_value, retention_drift)
        self.assertFalse(drifted["operation_decisions"][1]["eligible"])
        self.assertIn(
            "retention:mismatch",
            drifted["operation_decisions"][1]["reasons"],
        )

    def test_current_authority_and_duplicate_candidates_gate_minimal_jobs(
        self,
    ) -> None:
        asset_value = asset()
        governance = source_governance(
            str(asset_value["source_id"]),
            asset_id=str(asset_value["asset_id"]),
        )
        governance_value = complete_governance_registry(governance)
        decision_values = decisions(asset_value)
        value = self.qualify(
            asset_value,
            decision_values,
            governance_value,
        )
        authority_bundle = {
            "asset": asset_value,
            "inventory_record": inventory_authority(asset_value),
            "source_governance_registry": governance_value,
            "operation_decisions": decision_values,
        }
        authority = SyntheticQualificationAuthority([authority_bundle])
        results = query_qualified_assets(
            [value],
            operation="transcription",
            authority_resolver=authority,
            now=NOW,
        )
        self.assertEqual([value], results)
        later_results = query_qualified_assets(
            [value],
            operation="transcription",
            authority_resolver=authority,
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual([value], later_results)
        job = build_qualified_job(
            value,
            operation="transcription",
            authority_resolver=authority,
            now=NOW,
        )
        self.assertEqual(
            {
                "qualification_id",
                "source_id",
                "asset_id",
                "operation",
                "input_object_key",
            },
            set(job),
        )
        self.assertEqual(value["planned_object_key"], job["input_object_key"])
        self.assertNotIn("public_url", job)

        with self.assertRaisesRegex(QualificationError, "duplicate"):
            query_qualified_assets(
                [value, copy.deepcopy(value)],
                operation="transcription",
                authority_resolver=authority,
                now=NOW,
            )

        current_asset = copy.deepcopy(asset_value)
        current_asset["access_state"] = "http_403"
        authority = SyntheticQualificationAuthority(
            [
                {
                    **authority_bundle,
                    "asset": current_asset,
                    "inventory_record": inventory_authority(
                        current_asset
                    ),
                }
            ]
        )
        with self.assertRaisesRegex(QualificationError, "current authority"):
            build_qualified_job(
                value,
                operation="transcription",
                authority_resolver=authority,
                now=NOW,
            )

    def test_execution_recompiles_from_raw_current_authority(self) -> None:
        asset_value = asset()
        blocked_governance = source_governance(
            str(asset_value["source_id"]),
            asset_id=str(asset_value["asset_id"]),
            operation_overrides={"media_acquisition": "blocked"},
        )
        governance_value = complete_governance_registry(
            blocked_governance
        )
        decision_values = decisions(asset_value)
        blocked = self.qualify(
            asset_value,
            decision_values,
            governance_value,
        )
        forged = copy.deepcopy(blocked)
        download = next(
            item
            for item in forged["operation_decisions"]
            if item["operation"] == "download"
        )
        download["eligible"] = True
        download["reasons"] = []
        forged = rebind_qualification(forged)
        validate_asset_qualification(forged, now=NOW)

        authority = SyntheticQualificationAuthority(
            [
                {
                    "asset": asset_value,
                    "inventory_record": inventory_authority(asset_value),
                    "source_governance_registry": governance_value,
                    "operation_decisions": decision_values,
                }
            ]
        )
        self.assertEqual(
            [],
            query_qualified_assets(
                [forged],
                operation="download",
                authority_resolver=authority,
                now=NOW,
            ),
        )

    def test_project_native_assets_require_the_consent_lifecycle_path(self) -> None:
        value = {
            **asset(),
            "source_id": "project-native-visitor-inputs",
            "endpoint_id": None,
        }
        with self.assertRaisesRegex(QualificationError, "consent"):
            compile_asset_qualification(
                value,
                {},
                [],
                [],
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
