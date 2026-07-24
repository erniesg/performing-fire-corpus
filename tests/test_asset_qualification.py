from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import raw_object_key
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
    return {
        "source_id": source_id,
        "endpoint_id": ENDPOINTS[source_id],
        "asset_id": asset_id,
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
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = {
            (str(value["source_id"]), str(value["asset_id"])): copy.deepcopy(value)
            for value in records
        }

    def resolve_asset_qualification(
        self, *, source_id: str, asset_id: str
    ) -> dict[str, object] | None:
        value = self.records.get((source_id, asset_id))
        return None if value is None else copy.deepcopy(value)


class AssetQualificationTests(unittest.TestCase):
    def qualify(
        self,
        asset_value: dict[str, object],
        decision_values: list[dict[str, object]] | None = None,
        governance: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return compile_asset_qualification(
            asset_value,
            source_governance(
                str(asset_value["source_id"]),
                asset_id=str(asset_value["asset_id"]),
            )
            if governance is None
            else governance,
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
        ):
            with self.subTest(public_url=public_url):
                asset_value = asset(public_url=public_url)
                asset_value["expected_host"] = expected_host
                with self.assertRaisesRegex(
                    QualificationError,
                    "host boundary|credential-bearing|private or secret-like",
                ):
                    self.qualify(asset_value)

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
        value = self.qualify(asset())
        authority = SyntheticQualificationAuthority([value])
        results = query_qualified_assets(
            [value],
            operation="transcription",
            authority_resolver=authority,
            now=NOW,
        )
        self.assertEqual([value], results)
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

        current = copy.deepcopy(value)
        current["access_state"] = "http_403"
        authority = SyntheticQualificationAuthority([current])
        with self.assertRaisesRegex(QualificationError, "current authority"):
            build_qualified_job(
                value,
                operation="transcription",
                authority_resolver=authority,
                now=NOW,
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
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
