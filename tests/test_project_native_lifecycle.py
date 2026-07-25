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

from performing_fire_corpus.project_native_lifecycle import (
    ProjectNativeLifecycleError,
    apply_project_native_withdrawal,
    build_project_native_contribution,
    build_project_native_deletion_work,
    build_subject_export_job,
    complete_project_native_deletion,
    derive_project_native_contribution,
    evaluate_project_native_graph_operation,
    evaluate_project_native_operation,
    validate_project_native_contributions,
)


NOW = datetime(2026, 7, 25, 1, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


def canonical_hash(value: object) -> str:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def rebind_deletion_work(value: dict[str, object]) -> dict[str, object]:
    record = copy.deepcopy(value)
    payload = {
        key: child
        for key, child in record.items()
        if key not in {"work_id", "work_sha256"}
    }
    record["work_id"] = (
        f"project_native_deletion_work_{canonical_hash(payload)[:24]}"
    )
    without_hash = {
        key: child
        for key, child in record.items()
        if key != "work_sha256"
    }
    record["work_sha256"] = canonical_hash(without_hash)
    return record


def consent(
    *,
    consent_id: str = "consent_synthetic_visitor_001",
    source_id: str = "project-native-visitor-inputs",
    subject_ref: str = "subject_synthetic_visitor_001",
    state: str = "active",
    confidentiality: str = "restricted",
    uses: list[str] | None = None,
    viewer_roles: list[str] | None = None,
) -> dict[str, object]:
    audit_events: list[dict[str, object]] = []
    allowed_uses = (
        [
            "derived_processing",
            "indexing",
            "metadata_inventory",
            "retention",
            "search_visibility",
            "subject_export",
        ]
        if uses is None
        else uses
    )
    if state in {"revoked", "expired"}:
        occurred_at = (
            "2026-07-25T00:30:00Z"
            if state == "revoked"
            else "2026-08-01T00:00:00Z"
        )
        audit_events = [
            {
                "schema_version": 1,
                "consent_id": consent_id,
                "source_id": source_id,
                "event_type": f"consent_{state}",
                "occurred_at": occurred_at,
            }
        ]
        allowed_uses = []
    return {
        "schema_version": 1,
        "record_type": "consent",
        "consent_id": consent_id,
        "source_id": source_id,
        "subject_ref": subject_ref,
        "purpose_code": "participatory_score_research",
        "notice_version": "notice_v1",
        "state": state,
        "authority_class": "consent_controller",
        "decided_at": "2026-07-24T00:00:00Z",
        "expires_at": "2026-08-01T00:00:00Z",
        "confidentiality_class": confidentiality,
        "allowed_viewer_roles": (
            ["researcher"] if viewer_roles is None else viewer_roles
        ),
        "allowed_uses": allowed_uses,
        "redaction_required": True,
        "withdrawal_supported": True,
        "export_policy": "subject_copy",
        "deletion_owner_role": "data_steward",
        "next_safe_action": "Honor withdrawal and exact deletion.",
        "audit_events": audit_events,
    }


def retention(
    *,
    consent_id: str = "consent_synthetic_visitor_001",
    source_id: str = "project-native-visitor-inputs",
    expires_at: str = "2026-08-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "retention",
        "retention_id": "retention_synthetic_visitor_001",
        "consent_id": consent_id,
        "source_id": source_id,
        "state": "retain_until",
        "expires_at": expires_at,
        "legal_hold_state": "none",
        "legal_hold_basis": None,
        "authority_class": "data_steward",
        "derived_data_treatment": "delete_on_withdrawal",
        "next_safe_action": "Delete all linked artifacts at expiry.",
    }


def deletion(
    *,
    consent_id: str = "consent_synthetic_visitor_001",
    source_id: str = "project-native-visitor-inputs",
    trigger_state: str = "none",
) -> dict[str, object]:
    requested_at = None
    due_at = None
    status = "not_requested"
    if trigger_state != "none":
        requested_at = "2026-07-25T00:30:00Z"
        due_at = "2026-07-28T00:30:00Z"
        status = "pending"
    return {
        "schema_version": 1,
        "record_type": "deletion",
        "deletion_id": "deletion_synthetic_visitor_001",
        "consent_id": consent_id,
        "source_id": source_id,
        "trigger_state": trigger_state,
        "requested_at": requested_at,
        "deletion_due_at": due_at,
        "deletion_sla_hours": 72,
        "deletion_owner_role": "data_steward",
        "content_action": "delete",
        "derived_action": "delete",
        "index_action": "remove",
        "status": status,
        "next_safe_action": "Delete exact linked artifacts only.",
    }


def contribution(
    *,
    contribution_id: str = "contribution_synthetic_visitor_001",
    consent_value: dict[str, object] | None = None,
    raw_hash: str = HASH_A,
) -> dict[str, object]:
    consent_record = consent() if consent_value is None else consent_value
    return build_project_native_contribution(
        contribution_id=contribution_id,
        subject_ref=str(consent_record["subject_ref"]),
        source_id=str(consent_record["source_id"]),
        data_class="visitor_input",
        purpose_code=str(consent_record["purpose_code"]),
        consent=consent_record,
        confidentiality_class=str(
            consent_record["confidentiality_class"]
        ),
        allowed_audiences=["researcher"],
        allowed_uses=[
            "derived_processing",
            "indexing",
            "metadata_inventory",
        ],
        provenance_id="provenance_synthetic_visitor_001",
        input_contribution_ids=[],
        raw_object_keys=[
            "performing-fire/v1/raw/project-native-visitor-inputs/"
            f"{contribution_id}/{raw_hash}"
        ],
        derived_object_keys=[
            "performing-fire/v1/derived/project-native-visitor-inputs/"
            f"{contribution_id}/{HASH_B}"
        ],
        index_document_ids=["index_document_synthetic_visitor_001"],
        cache_entry_ids=["cache_entry_synthetic_visitor_001"],
        score_export_ids=["score_export_synthetic_visitor_001"],
        retention_expires_at="2026-08-01T00:00:00Z",
        system_provenance_id=None,
        created_at="2026-07-24T01:00:00Z",
    )


def graph_args(
    records: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "authoritative_contribution_ids": sorted(
            str(record["contribution_id"]) for record in records
        ),
        "lineage_authority": {
            str(record["contribution_id"]): list(
                record["input_contribution_ids"]
            )
            for record in records
            if record["input_contribution_ids"]
        },
    }


def authority_bundle(
    consent_value: dict[str, object],
) -> dict[str, dict[str, object]]:
    consent_id = str(consent_value["consent_id"])
    source_id = str(consent_value["source_id"])
    return {
        consent_id: {
            "consent": consent_value,
            "retention": retention(
                consent_id=consent_id,
                source_id=source_id,
            ),
            "deletion": deletion(
                consent_id=consent_id,
                source_id=source_id,
            ),
        }
    }


class ProjectNativeLifecycleTests(unittest.TestCase):
    def test_published_schemas_are_strict_and_content_free(self) -> None:
        values = {
            "project-native-contribution": contribution(),
            "project-native-export-job": build_subject_export_job(
                [contribution()],
                [consent()],
                **graph_args([contribution()]),
                subject_ref="subject_synthetic_visitor_001",
                requested_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            ),
        }
        for name, value in values.items():
            with self.subTest(name=name):
                schema = json.loads(
                    (ROOT / "schemas" / "v1" / f"{name}.json").read_text()
                )
                Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                ).validate(value)
                invalid = {**value, "free_form_comment": "not allowed"}
                with self.assertRaises(ValidationError):
                    Draft202012Validator(schema).validate(invalid)
        for name in (
            "project-native-audit-tombstone",
            "project-native-contribution",
            "project-native-deletion-work",
            "project-native-export-job",
            "project-native-legal-hold",
        ):
            with self.subTest(schema_mirror=name):
                self.assertEqual(
                    (ROOT / "schemas" / "v1" / f"{name}.json").read_bytes(),
                    (
                        ROOT
                        / "src"
                        / "performing_fire_corpus"
                        / "schemas"
                        / "v1"
                        / f"{name}.json"
                    ).read_bytes(),
                )

        defaulted = build_project_native_contribution(
            contribution_id="contribution_synthetic_default_001",
            subject_ref="subject_synthetic_visitor_001",
            source_id="project-native-visitor-inputs",
            data_class="visitor_input",
            purpose_code="participatory_score_research",
            consent=consent(),
            confidentiality_class="restricted",
            allowed_audiences=["researcher"],
            allowed_uses=["metadata_inventory"],
            provenance_id="provenance_synthetic_default_001",
            input_contribution_ids=[],
            raw_object_keys=[],
            derived_object_keys=[],
            index_document_ids=[],
            cache_entry_ids=[],
            score_export_ids=[],
            retention_expires_at=None,
            system_provenance_id=None,
            created_at="2026-07-24T01:00:00Z",
        )
        self.assertEqual(
            "2026-08-01T00:00:00Z",
            defaulted["retention_expires_at"],
        )

    def test_current_specific_consent_and_audience_gate_every_use(self) -> None:
        contribution_value = contribution()
        result = evaluate_project_native_operation(
            contribution_value,
            consent(),
            retention(),
            deletion(),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=NOW,
        )
        self.assertTrue(result["eligible"])

        unauthorized = evaluate_project_native_operation(
            contribution_value,
            consent(),
            retention(),
            deletion(),
            operation="indexing",
            audience="public",
            redaction_applied=True,
            now=NOW,
        )
        self.assertFalse(unauthorized["eligible"])
        self.assertIn("audience:not_allowed", unauthorized["reasons"])

        revoked = consent(state="revoked")
        blocked = evaluate_project_native_operation(
            contribution_value,
            revoked,
            retention(),
            deletion(trigger_state="consent_revoked"),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=NOW,
        )
        self.assertFalse(blocked["eligible"])
        self.assertIn("consent:revoked", blocked["reasons"])

    def test_expiry_and_incompatible_purpose_fail_closed(self) -> None:
        value = contribution()
        expired = evaluate_project_native_operation(
            value,
            consent(),
            retention(expires_at="2026-07-25T00:59:59Z"),
            deletion(),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=NOW,
        )
        self.assertFalse(expired["eligible"])
        self.assertIn("retention:expired", expired["reasons"])
        self.assertIn(
            "retention:authority_shortened",
            expired["reasons"],
        )
        contribution_expired = evaluate_project_native_operation(
            value,
            consent(),
            retention(),
            deletion(),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.assertIn(
            "contribution_retention:expired",
            contribution_expired["reasons"],
        )

        changed = copy.deepcopy(consent())
        changed["purpose_code"] = "unrelated_future_use"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "purpose",
        ):
            evaluate_project_native_operation(
                value,
                changed,
                retention(),
                deletion(),
                operation="indexing",
                audience="researcher",
                redaction_applied=True,
                now=NOW,
            )

    def test_derived_records_inherit_most_restrictive_inputs(self) -> None:
        first = contribution()
        second_consent = consent(
            consent_id="consent_synthetic_artist_001",
            source_id="project-native-artist-submissions",
            subject_ref="subject_synthetic_artist_001",
            confidentiality="sensitive",
            uses=["derived_processing", "metadata_inventory"],
            viewer_roles=["curator", "researcher"],
        )
        second = build_project_native_contribution(
            contribution_id="contribution_synthetic_artist_001",
            subject_ref="subject_synthetic_artist_001",
            source_id="project-native-artist-submissions",
            data_class="artist_submission",
            purpose_code="participatory_score_research",
            consent=second_consent,
            confidentiality_class="sensitive",
            allowed_audiences=["curator", "researcher"],
            allowed_uses=["derived_processing", "metadata_inventory"],
            provenance_id="provenance_synthetic_artist_001",
            input_contribution_ids=[],
            raw_object_keys=[],
            derived_object_keys=[],
            index_document_ids=[],
            cache_entry_ids=[],
            score_export_ids=[],
            retention_expires_at="2026-07-28T00:00:00Z",
            system_provenance_id=None,
            created_at="2026-07-24T02:00:00Z",
        )
        derived = derive_project_native_contribution(
            [first, second],
            input_contribution_ids=[
                first["contribution_id"],
                second["contribution_id"],
            ],
            authorities={
                **authority_bundle(consent()),
                **authority_bundle(second_consent),
            },
            lineage_authority={},
            redaction_applied=True,
            contribution_id="contribution_synthetic_score_001",
            source_id="project-native-generated-scores",
            data_class="generated_score",
            provenance_id="provenance_synthetic_score_001",
            system_provenance_id="system_provenance_visual_rules_v1",
            derived_object_keys=[
                "performing-fire/v1/derived/project-native-generated-scores/"
                f"contribution_synthetic_score_001/{HASH_A}"
            ],
            created_at="2026-07-24T03:00:00Z",
        )
        self.assertEqual("sensitive", derived["confidentiality_class"])
        self.assertEqual(["researcher"], derived["allowed_audiences"])
        self.assertEqual(
            ["derived_processing", "metadata_inventory"],
            derived["allowed_uses"],
        )
        self.assertEqual(
            "2026-07-28T00:00:00Z",
            derived["retention_expires_at"],
        )
        self.assertEqual(
            [
                "contribution_synthetic_artist_001",
                "contribution_synthetic_visitor_001",
            ],
            derived["input_contribution_ids"],
        )

    def test_derived_use_rechecks_every_current_input_authority(self) -> None:
        first = contribution()
        artist_consent = consent(
            consent_id="consent_synthetic_artist_001",
            source_id="project-native-artist-submissions",
            subject_ref="subject_synthetic_artist_001",
        )
        second = build_project_native_contribution(
            contribution_id="contribution_synthetic_artist_001",
            subject_ref="subject_synthetic_artist_001",
            source_id="project-native-artist-submissions",
            data_class="artist_submission",
            purpose_code="participatory_score_research",
            consent=artist_consent,
            confidentiality_class="restricted",
            allowed_audiences=["researcher"],
            allowed_uses=[
                "derived_processing",
                "indexing",
                "metadata_inventory",
            ],
            provenance_id="provenance_synthetic_artist_001",
            input_contribution_ids=[],
            raw_object_keys=[],
            derived_object_keys=[],
            index_document_ids=[],
            cache_entry_ids=[],
            score_export_ids=[],
            retention_expires_at="2026-08-01T00:00:00Z",
            system_provenance_id=None,
            created_at="2026-07-24T02:00:00Z",
        )
        derived = derive_project_native_contribution(
            [first, second],
            input_contribution_ids=[
                first["contribution_id"],
                second["contribution_id"],
            ],
            authorities={
                **authority_bundle(consent()),
                **authority_bundle(artist_consent),
            },
            lineage_authority={},
            redaction_applied=True,
            contribution_id="contribution_synthetic_score_002",
            source_id="project-native-generated-scores",
            data_class="generated_score",
            provenance_id="provenance_synthetic_score_002",
            system_provenance_id="system_provenance_visual_rules_v1",
            derived_object_keys=[],
            created_at="2026-07-24T03:00:00Z",
        )
        authorities = {
            "consent_synthetic_visitor_001": {
                "consent": consent(),
                "retention": retention(),
                "deletion": deletion(),
            },
            "consent_synthetic_artist_001": {
                "consent": artist_consent,
                "retention": retention(
                    consent_id="consent_synthetic_artist_001",
                    source_id="project-native-artist-submissions",
                ),
                "deletion": deletion(
                    consent_id="consent_synthetic_artist_001",
                    source_id="project-native-artist-submissions",
                ),
            },
        }
        current = evaluate_project_native_graph_operation(
            derived,
            [first, second, derived],
            authorities,
            **graph_args([first, second, derived]),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=NOW,
        )
        self.assertTrue(current["eligible"])

        revoked = consent(state="revoked")
        authorities["consent_synthetic_visitor_001"] = {
            "consent": revoked,
            "retention": retention(),
            "deletion": deletion(trigger_state="consent_revoked"),
        }
        blocked = evaluate_project_native_graph_operation(
            derived,
            [first, second, derived],
            authorities,
            **graph_args([first, second, derived]),
            operation="indexing",
            audience="researcher",
            redaction_applied=True,
            now=NOW,
        )
        self.assertFalse(blocked["eligible"])
        self.assertIn(
            "input:contribution_synthetic_visitor_001:consent:revoked",
            blocked["reasons"],
        )

        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "completeness",
        ):
            evaluate_project_native_graph_operation(
                derived,
                [second, derived],
                authorities,
                authoritative_contribution_ids=[
                    first["contribution_id"],
                    second["contribution_id"],
                    derived["contribution_id"],
                ],
                lineage_authority={
                    derived["contribution_id"]: [
                        first["contribution_id"],
                        second["contribution_id"],
                    ]
                },
                operation="indexing",
                audience="researcher",
                redaction_applied=True,
                now=NOW,
            )

    def test_subject_export_contains_only_stable_ids_and_object_keys(self) -> None:
        value = build_subject_export_job(
            [contribution()],
            [consent()],
            **graph_args([contribution()]),
            subject_ref="subject_synthetic_visitor_001",
            requested_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
        self.assertEqual(
            ["contribution_synthetic_visitor_001"],
            value["contribution_ids"],
        )
        self.assertTrue(value["object_keys"])
        encoded = json.dumps(value, sort_keys=True)
        for forbidden in (
            "content",
            "comment",
            "name",
            "contact",
            "proposal",
        ):
            self.assertNotIn(forbidden, encoded)
        missing_export_authority = consent()
        missing_export_authority["allowed_uses"].remove("subject_export")
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "export",
        ):
            build_subject_export_job(
                [contribution()],
                [missing_export_authority],
                **graph_args([contribution()]),
                subject_ref="subject_synthetic_visitor_001",
                requested_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "duplicate consent",
        ):
            build_subject_export_job(
                [contribution()],
                [consent(), consent()],
                **graph_args([contribution()]),
                subject_ref="subject_synthetic_visitor_001",
                requested_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )

    def test_withdrawal_during_processing_plans_every_exact_removal(self) -> None:
        revoked = consent(state="revoked")
        updated, work = apply_project_native_withdrawal(
            [contribution()],
            revoked,
            deletion(trigger_state="consent_revoked"),
            **graph_args([contribution()]),
            legal_hold=None,
            now=NOW,
        )
        self.assertEqual("revoked", updated[0]["consent_state"])
        self.assertEqual("withdrawn", updated[0]["withdrawal_state"])
        self.assertEqual("pending", updated[0]["deletion_state"])
        self.assertEqual([], updated[0]["allowed_uses"])
        self.assertEqual("pending", work["state"])
        self.assertEqual(1, len(work["raw_object_keys"]))
        self.assertEqual(1, len(work["derived_object_keys"]))
        self.assertEqual(
            ["index_document_synthetic_visitor_001"],
            work["index_document_ids"],
        )
        self.assertEqual(
            ["cache_entry_synthetic_visitor_001"],
            work["cache_entry_ids"],
        )
        self.assertEqual(
            ["score_export_synthetic_visitor_001"],
            work["score_export_ids"],
        )
        slow = deletion(trigger_state="consent_revoked")
        slow["deletion_sla_hours"] = 73
        slow["deletion_due_at"] = "2026-07-28T01:30:00Z"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "data-class default",
        ):
            build_project_native_deletion_work(
                [contribution()],
                slow,
                **graph_args([contribution()]),
                legal_hold=None,
                now=NOW,
            )

    def test_scoped_current_legal_hold_prevents_deletion(self) -> None:
        hold = {
            "schema_version": 1,
            "record_type": "project_native_legal_hold",
            "legal_hold_id": "legal_hold_synthetic_001",
            "authority_class": "legal_reviewer",
            "basis_code": "reviewed_preservation_duty",
            "contribution_ids": ["contribution_synthetic_visitor_001"],
            "decided_at": "2026-07-24T00:00:00Z",
            "expires_at": "2026-07-26T00:00:00Z",
            "review_at": "2026-07-25T12:00:00Z",
            "state": "active",
        }
        work = build_project_native_deletion_work(
            [contribution()],
            {
                **deletion(trigger_state="consent_revoked"),
                "status": "under_legal_hold_review",
            },
            **graph_args([contribution()]),
            legal_hold=hold,
            now=NOW,
        )
        self.assertEqual("legal_hold_review", work["state"])
        self.assertEqual("legal_hold_synthetic_001", work["legal_hold_id"])

        expired = copy.deepcopy(hold)
        expired["expires_at"] = "2026-07-25T00:59:59Z"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "legal hold",
        ):
            build_project_native_deletion_work(
                [contribution()],
                {
                    **deletion(trigger_state="consent_revoked"),
                    "status": "under_legal_hold_review",
                },
                **graph_args([contribution()]),
                legal_hold=expired,
                now=NOW,
            )
        review_due = copy.deepcopy(hold)
        review_due["review_at"] = "2026-07-25T00:59:59Z"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "review",
        ):
            build_project_native_deletion_work(
                [contribution()],
                {
                    **deletion(trigger_state="consent_revoked"),
                    "status": "under_legal_hold_review",
                },
                **graph_args([contribution()]),
                legal_hold=review_due,
                now=NOW,
            )

    def test_exact_completion_emits_content_free_tombstones(self) -> None:
        work = build_project_native_deletion_work(
            [contribution()],
            deletion(trigger_state="consent_revoked"),
            **graph_args([contribution()]),
            legal_hold=None,
            now=NOW,
        )
        tombstones = complete_project_native_deletion(
            work,
            [contribution()],
            **graph_args([contribution()]),
            deleted_raw_object_keys=work["raw_object_keys"],
            deleted_derived_object_keys=work["derived_object_keys"],
            removed_index_document_ids=work["index_document_ids"],
            invalidated_cache_entry_ids=work["cache_entry_ids"],
            removed_score_export_ids=work["score_export_ids"],
            completed_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(1, len(tombstones))
        self.assertEqual(
            {
                "schema_version",
                "record_type",
                "tombstone_id",
                "contribution_id",
                "deletion_id",
                "completed_at",
                "removed_counts",
            },
            set(tombstones[0]),
        )
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "exact",
        ):
            complete_project_native_deletion(
                work,
                [contribution()],
                **graph_args([contribution()]),
                deleted_raw_object_keys=[],
                deleted_derived_object_keys=work["derived_object_keys"],
                removed_index_document_ids=work["index_document_ids"],
                invalidated_cache_entry_ids=work["cache_entry_ids"],
                removed_score_export_ids=work["score_export_ids"],
                completed_at=NOW + timedelta(minutes=5),
            )
        tampered = copy.deepcopy(work)
        tampered["deletion_due_at"] = "2026-07-29T00:30:00Z"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "identifier|hash",
        ):
            complete_project_native_deletion(
                tampered,
                [contribution()],
                **graph_args([contribution()]),
                deleted_raw_object_keys=work["raw_object_keys"],
                deleted_derived_object_keys=work["derived_object_keys"],
                removed_index_document_ids=work["index_document_ids"],
                invalidated_cache_entry_ids=work["cache_entry_ids"],
                removed_score_export_ids=work["score_export_ids"],
                completed_at=NOW + timedelta(minutes=5),
            )
        self_rehashed = copy.deepcopy(work)
        self_rehashed["targets"][0]["index_document_ids"] = []
        self_rehashed = rebind_deletion_work(self_rehashed)
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "aggregate targets",
        ):
            complete_project_native_deletion(
                self_rehashed,
                [contribution()],
                **graph_args([contribution()]),
                deleted_raw_object_keys=work["raw_object_keys"],
                deleted_derived_object_keys=work["derived_object_keys"],
                removed_index_document_ids=work["index_document_ids"],
                invalidated_cache_entry_ids=work["cache_entry_ids"],
                removed_score_export_ids=work["score_export_ids"],
                completed_at=NOW + timedelta(minutes=5),
            )

    def test_duplicate_contribution_ids_never_silently_merge(self) -> None:
        first = contribution()
        duplicate = contribution(raw_hash=HASH_B)
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "duplicate",
        ):
            validate_project_native_contributions([first, duplicate])
        same_content = contribution(
            contribution_id="contribution_synthetic_visitor_002",
        )
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "duplicate submission content",
        ):
            validate_project_native_contributions([first, same_content])

    def test_reviewer_fail_closed_regressions(self) -> None:
        first = contribution()
        second = contribution(
            contribution_id="contribution_synthetic_visitor_002",
            raw_hash=HASH_B,
        )
        work = build_project_native_deletion_work(
            [first],
            deletion(trigger_state="consent_revoked"),
            **graph_args([first]),
            legal_hold=None,
            now=NOW,
        )
        forged = copy.deepcopy(work)
        forged["targets"][0]["raw_object_keys"] = second["raw_object_keys"]
        forged["raw_object_keys"] = second["raw_object_keys"]
        forged = rebind_deletion_work(forged)
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "authoritative contribution targets",
        ):
            complete_project_native_deletion(
                forged,
                [first],
                **graph_args([first]),
                deleted_raw_object_keys=forged["raw_object_keys"],
                deleted_derived_object_keys=forged["derived_object_keys"],
                removed_index_document_ids=forged["index_document_ids"],
                invalidated_cache_entry_ids=forged["cache_entry_ids"],
                removed_score_export_ids=forged["score_export_ids"],
                completed_at=NOW + timedelta(minutes=5),
            )

        hold_status = deletion(trigger_state="consent_revoked")
        hold_status["status"] = "under_legal_hold_review"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "status and legal-hold",
        ):
            build_project_native_deletion_work(
                [first],
                hold_status,
                **graph_args([first]),
                legal_hold=None,
                now=NOW,
            )

        future_consent = consent()
        future_consent["decided_at"] = "2026-07-26T00:00:00Z"
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "currently effective consent",
        ):
            contribution(consent_value=future_consent)
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "currently effective consent",
        ):
            build_subject_export_job(
                [first],
                [future_consent],
                **graph_args([first]),
                subject_ref="subject_synthetic_visitor_001",
                requested_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )

    def test_authoritative_lineage_drives_export_and_withdrawal(self) -> None:
        direct = contribution()
        derived = derive_project_native_contribution(
            [direct],
            input_contribution_ids=[direct["contribution_id"]],
            authorities=authority_bundle(consent()),
            lineage_authority={},
            redaction_applied=True,
            contribution_id="contribution_synthetic_score_subject_001",
            source_id="project-native-generated-scores",
            data_class="generated_score",
            provenance_id="provenance_synthetic_score_subject_001",
            system_provenance_id="system_provenance_visual_rules_v1",
            derived_object_keys=[
                "performing-fire/v1/derived/project-native-generated-scores/"
                f"contribution_synthetic_score_subject_001/{HASH_A}"
            ],
            created_at="2026-07-24T03:00:00Z",
        )
        records = [direct, derived]
        authority = graph_args(records)
        export = build_subject_export_job(
            records,
            [consent()],
            **authority,
            subject_ref="subject_synthetic_visitor_001",
            requested_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
        self.assertEqual(
            sorted(
                [direct["contribution_id"], derived["contribution_id"]]
            ),
            export["contribution_ids"],
        )

        artist_consent = consent(
            consent_id="consent_synthetic_artist_lineage_001",
            source_id="project-native-artist-submissions",
            subject_ref="subject_synthetic_artist_lineage_001",
            confidentiality="sensitive",
        )
        artist = build_project_native_contribution(
            contribution_id="contribution_synthetic_artist_lineage_001",
            subject_ref=str(artist_consent["subject_ref"]),
            source_id=str(artist_consent["source_id"]),
            data_class="artist_submission",
            purpose_code=str(artist_consent["purpose_code"]),
            consent=artist_consent,
            confidentiality_class="sensitive",
            allowed_audiences=["researcher"],
            allowed_uses=[
                "derived_processing",
                "indexing",
                "metadata_inventory",
            ],
            provenance_id="provenance_synthetic_artist_lineage_001",
            input_contribution_ids=[],
            raw_object_keys=[],
            derived_object_keys=[],
            index_document_ids=[],
            cache_entry_ids=[],
            score_export_ids=[],
            retention_expires_at="2026-08-01T00:00:00Z",
            system_provenance_id=None,
            created_at="2026-07-24T02:00:00Z",
        )
        two_input = derive_project_native_contribution(
            [direct, artist],
            input_contribution_ids=[
                direct["contribution_id"],
                artist["contribution_id"],
            ],
            authorities={
                **authority_bundle(consent()),
                **authority_bundle(artist_consent),
            },
            lineage_authority={},
            redaction_applied=True,
            contribution_id="contribution_synthetic_score_lineage_001",
            source_id="project-native-generated-scores",
            data_class="generated_score",
            provenance_id="provenance_synthetic_score_lineage_001",
            system_provenance_id="system_provenance_visual_rules_v1",
            derived_object_keys=[],
            created_at="2026-07-24T03:00:00Z",
        )
        tampered = copy.deepcopy(two_input)
        tampered["input_contribution_ids"] = [direct["contribution_id"]]
        tampered["consent_ids"] = direct["consent_ids"]
        tampered["confidentiality_class"] = direct["confidentiality_class"]
        tampered["allowed_audiences"] = direct["allowed_audiences"]
        tampered["allowed_uses"] = direct["allowed_uses"]
        lineage_records = [direct, artist, tampered]
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "authoritative lineage",
        ):
            evaluate_project_native_graph_operation(
                tampered,
                lineage_records,
                {
                    **authority_bundle(consent()),
                    **authority_bundle(artist_consent),
                },
                authoritative_contribution_ids=sorted(
                    record["contribution_id"] for record in lineage_records
                ),
                lineage_authority={
                    tampered["contribution_id"]: sorted(
                        [
                            direct["contribution_id"],
                            artist["contribution_id"],
                        ]
                    )
                },
                operation="indexing",
                audience="researcher",
                redaction_applied=True,
                now=NOW,
            )

        revoked = consent(state="revoked")
        updated, work = apply_project_native_withdrawal(
            records,
            revoked,
            deletion(trigger_state="consent_revoked"),
            **authority,
            legal_hold=None,
            now=NOW,
        )
        self.assertEqual(
            sorted(
                [direct["contribution_id"], derived["contribution_id"]]
            ),
            work["contribution_ids"],
        )
        self.assertTrue(
            all(record["withdrawal_state"] == "withdrawn" for record in updated)
        )
        with self.assertRaisesRegex(
            ProjectNativeLifecycleError,
            "completeness",
        ):
            apply_project_native_withdrawal(
                [direct],
                revoked,
                deletion(trigger_state="consent_revoked"),
                authoritative_contribution_ids=authority[
                    "authoritative_contribution_ids"
                ],
                lineage_authority=authority["lineage_authority"],
                legal_hold=None,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
