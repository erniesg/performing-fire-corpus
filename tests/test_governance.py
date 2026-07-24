from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.governance import (
    GovernanceError,
    canonical_governance_registry_bytes,
    evaluate_project_native_use,
    evaluate_source_operation,
    load_source_governance_registry,
    transition_consent,
    validate_project_native_contract,
)
from performing_fire_corpus.registry import load_registry


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "v1"
SOURCE_REGISTRY_PATH = ROOT / "config" / "source-registry.v1.json"
GOVERNANCE_REGISTRY_PATH = ROOT / "config" / "source-governance.v1.json"
NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)


def load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def validate_schema(name: str, value: dict[str, object]) -> None:
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def synthetic_governance() -> dict[str, object]:
    dimensions = (
        "access_control",
        "api_availability",
        "authentication",
        "copyright_lawful_basis",
        "platform_terms",
        "robots",
    )
    record = {
        "schema_version": 1,
        "record_type": "source_governance",
        "source_governance_id": "source_governance_synthetic",
        "source_id": "antiegg-fluxus",
        "endpoint_id": "antiegg-posts-api",
        "fact_states": {dimension: "allowed" for dimension in dimensions},
        "observations": [
            {
                "dimension": dimension,
                "state": "allowed",
                "observed_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-25T00:00:00Z",
                "evidence_id": f"evidence_synthetic_{dimension}",
                "next_safe_action": "Revalidate this synthetic fact after expiry.",
            }
            for dimension in dimensions
        ],
        "operation_states": {
            "acquisition_eligibility": "pending",
            "caption_retention": "pending",
            "deletion": "pending",
            "derivative_eligibility": "pending",
            "derived_processing": "pending",
            "indexing": "pending",
            "media_acquisition": "pending",
            "metadata_inventory": "approved",
            "prose_retention": "pending",
            "public_retrieval": "pending",
            "retention": "pending",
            "search_visibility": "pending",
        },
        "decisions": [
            {
                "affected_operation": "metadata_inventory",
                "state": "approved",
                "authority_class": "source_policy_reviewer",
                "basis_code": "synthetic_public_metadata",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-25T00:00:00Z",
                "review_trigger": "Recheck when the source policy changes.",
                "next_safe_action": "Run only the bounded metadata inventory.",
            }
        ],
        "blockers": [],
        "evaluated_at": "2026-07-23T00:00:00Z",
    }
    return record


def project_native_contract() -> tuple[dict[str, object], ...]:
    consent = {
        "schema_version": 1,
        "record_type": "consent",
        "consent_id": "consent_synthetic_001",
        "source_id": "project-native-visitor-inputs",
        "subject_ref": "subject_synthetic_001",
        "purpose_code": "score_generation_research",
        "notice_version": "notice_v1",
        "state": "active",
        "authority_class": "consent_controller",
        "decided_at": "2026-07-23T00:00:00Z",
        "expires_at": "2026-08-23T00:00:00Z",
        "confidentiality_class": "restricted",
        "allowed_viewer_roles": ["data_steward", "researcher"],
        "allowed_uses": ["derived_processing", "indexing"],
        "redaction_required": True,
        "withdrawal_supported": True,
        "export_policy": "subject_copy",
        "deletion_owner_role": "data_steward",
        "next_safe_action": "Honor withdrawal and propagate deletion.",
        "audit_events": [],
    }
    retention = {
        "schema_version": 1,
        "record_type": "retention",
        "retention_id": "retention_synthetic_001",
        "consent_id": "consent_synthetic_001",
        "source_id": "project-native-visitor-inputs",
        "state": "retain_until",
        "expires_at": "2026-08-23T00:00:00Z",
        "legal_hold_state": "none",
        "legal_hold_basis": None,
        "authority_class": "data_steward",
        "derived_data_treatment": "delete_on_withdrawal",
        "next_safe_action": "Delete content and derivatives on withdrawal or expiry.",
    }
    deletion = {
        "schema_version": 1,
        "record_type": "deletion",
        "deletion_id": "deletion_synthetic_001",
        "consent_id": "consent_synthetic_001",
        "source_id": "project-native-visitor-inputs",
        "trigger_state": "none",
        "requested_at": None,
        "deletion_due_at": None,
        "deletion_sla_hours": 72,
        "deletion_owner_role": "data_steward",
        "status": "not_requested",
        "content_action": "delete",
        "derived_action": "delete",
        "index_action": "remove",
        "next_safe_action": "Queue deletion when consent is withdrawn or expires.",
    }
    return consent, retention, deletion


class GovernanceTests(unittest.TestCase):
    def test_governance_registry_covers_every_source_and_is_fail_closed(self) -> None:
        source_registry = load_registry(SOURCE_REGISTRY_PATH)
        governance = load_source_governance_registry(
            GOVERNANCE_REGISTRY_PATH, source_registry=source_registry
        )
        self.assertEqual(
            {item["source_id"] for item in source_registry["sources"]},
            {item["source_id"] for item in governance["records"]},
        )
        self.assertEqual(
            GOVERNANCE_REGISTRY_PATH.read_bytes(),
            canonical_governance_registry_bytes(governance),
        )
        for record in governance["records"]:
            self.assertEqual(
                {"unknown"}, set(record["fact_states"].values())
            )
            self.assertEqual(
                {"pending"}, set(record["operation_states"].values())
            )
            self.assertEqual([], record["observations"])
            self.assertEqual([], record["decisions"])
            self.assertEqual([], record["blockers"])

    def test_registry_supports_multiple_endpoint_specific_records_per_source(
        self,
    ) -> None:
        source_registry = load_registry(SOURCE_REGISTRY_PATH)
        governance = json.loads(
            GOVERNANCE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        endpoint_record = copy.deepcopy(governance["records"][0])
        endpoint_record["source_governance_id"] = (
            "source_governance_antiegg_fluxus_posts"
        )
        endpoint_record["endpoint_id"] = "antiegg-posts-api"
        governance["records"].append(endpoint_record)
        governance["records"].sort(
            key=lambda item: (
                item["source_id"],
                item["endpoint_id"] or "",
                item.get("asset_id") or "",
                item["source_governance_id"],
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "governance.json"
            path.write_text(
                json.dumps(governance, sort_keys=True),
                encoding="utf-8",
            )
            loaded = load_source_governance_registry(
                path, source_registry=source_registry
            )
        self.assertEqual(
            2,
            sum(
                record["source_id"] == "antiegg-fluxus"
                for record in loaded["records"]
            ),
        )

    def test_strict_schemas_accept_only_content_free_synthetic_contracts(self) -> None:
        governance = synthetic_governance()
        consent, retention, deletion = project_native_contract()
        for name, value in (
            ("source-governance", governance),
            ("consent", consent),
            ("retention", retention),
            ("deletion", deletion),
        ):
            with self.subTest(name=name):
                validate_schema(name, value)
                invalid = copy.deepcopy(value)
                invalid["private_comment"] = "not allowed"
                with self.assertRaises(ValidationError):
                    validate_schema(name, invalid)
        validate_project_native_contract(consent, retention, deletion)

    def test_metadata_permission_never_implies_content_permission(self) -> None:
        governance = synthetic_governance()
        metadata = evaluate_source_operation(
            governance, "metadata_inventory", now=NOW
        )
        media = evaluate_source_operation(
            governance, "media_acquisition", now=NOW
        )
        self.assertTrue(metadata["eligible"])
        self.assertFalse(media["eligible"])
        self.assertEqual("pending", media["state"])

    def test_unknown_stale_conflicting_and_durable_blockers_fail_closed(self) -> None:
        for state in ("unknown", "stale", "conflicting"):
            governance = synthetic_governance()
            governance["fact_states"]["robots"] = state
            governance["observations"] = [
                item
                for item in governance["observations"]
                if item["dimension"] != "robots"
            ]
            with self.subTest(state=state):
                result = evaluate_source_operation(
                    governance, "metadata_inventory", now=NOW
                )
                self.assertFalse(result["eligible"])

        blocker_codes = (
            "conflicting_evidence",
            "evidence_expired",
            "http_401",
            "http_403",
            "login_required",
            "platform_prohibited",
            "rate_exhausted",
            "robots_denied",
            "subscription_required",
            "unclear_rights",
        )
        for code in blocker_codes:
            governance = synthetic_governance()
            governance["blockers"] = [
                {
                    "code": code,
                    "observed_at": "2026-07-23T00:00:00Z",
                    "endpoint_id": "antiegg-posts-api",
                    "next_safe_action": "Keep the source blocked and review current policy.",
                }
            ]
            with self.subTest(code=code):
                result = evaluate_source_operation(
                    governance, "metadata_inventory", now=NOW
                )
                self.assertFalse(result["eligible"])
                self.assertEqual(code, result["blockers"][0]["code"])

        governance = synthetic_governance()
        conflicting = copy.deepcopy(governance["observations"][0])
        conflicting["evidence_id"] = "evidence_synthetic_conflict"
        conflicting["state"] = "denied"
        governance["observations"].append(conflicting)
        governance["observations"].sort(
            key=lambda item: (item["dimension"], item["evidence_id"])
        )
        self.assertFalse(
            evaluate_source_operation(
                governance, "metadata_inventory", now=NOW
            )["eligible"]
        )

    def test_expired_decisions_and_observations_fail_closed(self) -> None:
        governance = synthetic_governance()
        governance["decisions"][0]["expires_at"] = "2026-07-24T00:00:00Z"
        self.assertFalse(
            evaluate_source_operation(
                governance, "metadata_inventory", now=NOW
            )["eligible"]
        )

    def test_future_evidence_and_revoked_authority_fail_closed(self) -> None:
        governance = synthetic_governance()
        governance["observations"][0]["observed_at"] = "2026-07-24T00:00:01Z"
        self.assertFalse(
            evaluate_source_operation(
                governance, "metadata_inventory", now=NOW
            )["eligible"]
        )

        governance = synthetic_governance()
        governance["decisions"][0]["decided_at"] = "2026-07-24T00:00:01Z"
        self.assertFalse(
            evaluate_source_operation(
                governance, "metadata_inventory", now=NOW
            )["eligible"]
        )

        governance = synthetic_governance()
        governance["operation_states"]["metadata_inventory"] = "revoked"
        governance["decisions"][0]["state"] = "revoked"
        result = evaluate_source_operation(
            governance, "metadata_inventory", now=NOW
        )
        self.assertFalse(result["eligible"])
        self.assertEqual("revoked", result["state"])

        governance = synthetic_governance()
        governance["observations"][0]["expires_at"] = "2026-07-24T00:00:00Z"
        self.assertFalse(
            evaluate_source_operation(
                governance, "metadata_inventory", now=NOW
            )["eligible"]
        )

    def test_consent_revocation_blocks_use_and_creates_minimum_work(self) -> None:
        consent, retention, deletion = project_native_contract()
        self.assertTrue(
            evaluate_project_native_use(
                consent,
                retention,
                deletion,
                "derived_processing",
                now=NOW,
            )["eligible"]
        )

        revoked, outcome = transition_consent(
            consent,
            retention,
            deletion,
            new_state="revoked",
            at=NOW,
        )
        self.assertEqual("revoked", revoked["state"])
        self.assertEqual([], revoked["allowed_uses"])
        self.assertEqual(
            ["delete_content", "delete_derivatives", "reindex"],
            outcome["required_work"],
        )
        self.assertEqual(
            {
                "consent_id",
                "event_type",
                "occurred_at",
                "schema_version",
                "source_id",
            },
            set(outcome["audit_event"]),
        )
        self.assertFalse(
            evaluate_project_native_use(
                revoked,
                retention,
                deletion,
                "derived_processing",
                now=NOW,
            )["eligible"]
        )

    def test_legal_hold_blocks_use_and_requires_review_without_silent_retention(
        self,
    ) -> None:
        consent, retention, deletion = project_native_contract()
        retention["legal_hold_state"] = "active"
        retention["legal_hold_basis"] = "synthetic_reviewed_legal_hold"
        revoked, outcome = transition_consent(
            consent,
            retention,
            deletion,
            new_state="revoked",
            at=NOW,
        )
        self.assertEqual(["review_legal_hold", "reindex"], outcome["required_work"])
        result = evaluate_project_native_use(
            revoked,
            retention,
            deletion,
            "indexing",
            now=NOW,
        )
        self.assertFalse(result["eligible"])

    def test_project_native_intake_fails_without_notice_authority_or_owner(self) -> None:
        consent, retention, deletion = project_native_contract()
        for field in ("notice_version", "authority_class", "deletion_owner_role"):
            invalid = copy.deepcopy(consent)
            invalid[field] = ""
            with self.subTest(field=field), self.assertRaises(GovernanceError):
                evaluate_project_native_use(
                    invalid,
                    retention,
                    deletion,
                    "derived_processing",
                    now=NOW,
                )


if __name__ == "__main__":
    unittest.main()
