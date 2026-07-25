from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.observability import validate_record
from performing_fire_corpus.operator_gates import (
    BLOCKER_CATALOG,
    OperatorGateError,
    open_blocker,
    partition_work,
    record_human_decision,
    resume_checkpoint,
)


UTC = timezone.utc
OPENED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
EXPIRES_AT = OPENED_AT + timedelta(days=7)
BOUNDS = {
    "requests": 3,
    "bytes": 2048,
    "pages": 2,
    "retries": 1,
    "elapsed_seconds": 4.0,
}
CHECKPOINT = {
    "cursor": "page-2",
    "next_ordinal": 40,
    "processed_count": 40,
    "last_stable_id": "asset_synthetic_video_001",
    "attempt": 2,
}


def gate(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "missing_authority_class": "actions_spending_authority",
        "blocked_subject_id": "job_evidence_001",
        "isolation_scope": "single_job",
        "checkpoint": CHECKPOINT,
        "opened_at": OPENED_AT,
        "expires_at": EXPIRES_AT,
        "operation": "run_evidence",
        "subject_ids": ("job_evidence_001", "antiegg"),
        "lane": "portable",
        "policy_version": "operator_gates_v1",
        "attempt": 2,
        "bound_consumption": BOUNDS,
    }
    arguments.update(overrides)
    return open_blocker(**arguments)  # type: ignore[arg-type]


def blocker_schema() -> dict[str, object]:
    return json.loads(
        (ROOT / "schemas" / "v1" / "operator-blocker.json").read_text(encoding="utf-8")
    )


class BlockerContractTests(unittest.TestCase):
    def test_the_catalog_and_the_schema_declare_the_same_vocabulary(self) -> None:
        schema = blocker_schema()
        self.assertEqual(
            sorted(BLOCKER_CATALOG),
            sorted(schema["$defs"]["authorityClass"]["enum"]),
        )
        self.assertEqual(
            sorted(
                {gate["unblocking_command_class"] for gate in BLOCKER_CATALOG.values()}
            ),
            sorted(schema["properties"]["unblocking_command_class"]["enum"]),
        )

    def test_every_authority_class_produces_an_actionable_gate(self) -> None:
        for authority_class in BLOCKER_CATALOG:
            with self.subTest(authority_class=authority_class):
                opened = gate(missing_authority_class=authority_class)
                blocker = opened["blocker"]
                validate_record("operator-blocker", blocker)
                self.assertEqual(authority_class, blocker["missing_authority_class"])
                self.assertEqual("blocked_on_human", blocker["outcome_code"])
                for field in (
                    "question",
                    "recommended_response",
                    "next_safe_action",
                    "unblocking_command_class",
                    "review_trigger",
                    "expires_at",
                    "resume_token_id",
                ):
                    self.assertTrue(str(blocker[field]).strip(), field)
                self.assertEqual(
                    opened["resume_token"]["resume_token_id"],
                    blocker["resume_token_id"],
                )

    def test_blocker_carries_a_durable_resumable_checkpoint(self) -> None:
        opened = gate()
        token = opened["resume_token"]
        validate_record("resume-token", token)
        self.assertEqual(CHECKPOINT, token["checkpoint"])
        self.assertEqual("checkpoint_committed", token["outcome_code"])
        self.assertEqual("2026-03-04T05:06:07Z", token["issued_at"])

    def test_unactionable_or_unexpiring_gates_fail_closed(self) -> None:
        cases = (
            {"missing_authority_class": "vibes_approval"},
            {"isolation_scope": "everything"},
            {"expires_at": OPENED_AT},
            {"expires_at": OPENED_AT - timedelta(days=1)},
            {"blocked_subject_id": "job_unrelated_001"},
            {"checkpoint": {**CHECKPOINT, "extra": 1}},
            {"checkpoint": {**CHECKPOINT, "processed_count": -1}},
        )
        for override in cases:
            with self.subTest(override=sorted(override)):
                with self.assertRaises(OperatorGateError):
                    gate(**override)


class HumanDecisionTests(unittest.TestCase):
    def decide(self, **overrides: object) -> dict[str, object]:
        opened = gate()
        arguments: dict[str, object] = {
            "blocker": opened["blocker"],
            "resume_token": opened["resume_token"],
            "decision": "granted",
            "decided_at": OPENED_AT + timedelta(days=1),
            "granted_authority_class": "actions_spending_authority",
            "expires_at": OPENED_AT + timedelta(days=3),
        }
        arguments.update(overrides)
        return record_human_decision(**arguments)  # type: ignore[arg-type]

    def test_a_grant_names_exactly_the_missing_authority_and_expires(self) -> None:
        decision = self.decide()
        validate_record("human-decision", decision)
        self.assertEqual("granted", decision["decision"])
        self.assertEqual(
            "actions_spending_authority", decision["granted_authority_class"]
        )
        self.assertEqual("authority_recorded", decision["decision_reason_code"])
        self.assertEqual("2026-03-07T05:06:07Z", decision["expires_at"])
        self.assertIsNotNone(decision["resume_token_id"])

    def test_a_denial_is_the_least_permissive_outcome(self) -> None:
        decision = self.decide(
            decision="denied", granted_authority_class=None, expires_at=None
        )
        self.assertIsNone(decision["granted_authority_class"])
        self.assertIsNone(decision["resume_token_id"])
        self.assertEqual("failed_closed", decision["outcome_code"])

    def test_a_deferral_keeps_the_resume_token(self) -> None:
        decision = self.decide(
            decision="deferred", granted_authority_class=None, expires_at=None
        )
        self.assertEqual("awaiting_further_review", decision["decision_reason_code"])
        self.assertEqual("blocked_on_human", decision["outcome_code"])
        self.assertIsNotNone(decision["resume_token_id"])

    def test_a_grant_cannot_widen_or_outlive_the_gate(self) -> None:
        cases = (
            {"granted_authority_class": "deploy_approval"},
            {"granted_authority_class": None},
            {"expires_at": None},
            {"expires_at": OPENED_AT},
            {"decision": "denied", "granted_authority_class": "deploy_approval"},
        )
        for override in cases:
            with self.subTest(override=sorted(override)):
                with self.assertRaises(OperatorGateError):
                    self.decide(**override)

    def test_an_expired_blocker_must_be_reopened_before_it_is_granted(self) -> None:
        late = EXPIRES_AT + timedelta(days=1)
        with self.assertRaises(OperatorGateError):
            self.decide(decided_at=late, expires_at=late + timedelta(days=1))
        deferred = self.decide(
            decision="deferred",
            decided_at=late,
            granted_authority_class=None,
            expires_at=None,
        )
        self.assertEqual("blocker_expired", deferred["decision_reason_code"])

    def test_a_token_from_another_blocker_is_refused(self) -> None:
        other = gate(checkpoint={**CHECKPOINT, "processed_count": 7})
        with self.assertRaises(OperatorGateError):
            self.decide(resume_token=other["resume_token"])


class ResumeTests(unittest.TestCase):
    def test_a_grant_resumes_from_the_exact_checkpoint(self) -> None:
        opened = gate()
        decision = record_human_decision(
            blocker=opened["blocker"],
            resume_token=opened["resume_token"],
            decision="granted",
            decided_at=OPENED_AT + timedelta(days=1),
            granted_authority_class="actions_spending_authority",
            expires_at=OPENED_AT + timedelta(days=3),
        )
        resumed = resume_checkpoint(
            resume_token=opened["resume_token"],
            human_decision=decision,
            resumed_at=OPENED_AT + timedelta(days=2),
        )
        self.assertEqual(CHECKPOINT, resumed)

    def test_denied_expired_or_mismatched_state_never_resumes(self) -> None:
        opened = gate()
        denied = record_human_decision(
            blocker=opened["blocker"],
            resume_token=opened["resume_token"],
            decision="denied",
            decided_at=OPENED_AT + timedelta(days=1),
        )
        with self.assertRaises(OperatorGateError):
            resume_checkpoint(
                resume_token=opened["resume_token"],
                human_decision=denied,
                resumed_at=OPENED_AT + timedelta(days=2),
            )

        granted = record_human_decision(
            blocker=opened["blocker"],
            resume_token=opened["resume_token"],
            decision="granted",
            decided_at=OPENED_AT + timedelta(days=1),
            granted_authority_class="actions_spending_authority",
            expires_at=OPENED_AT + timedelta(days=3),
        )
        with self.assertRaises(OperatorGateError):
            resume_checkpoint(
                resume_token=opened["resume_token"],
                human_decision=granted,
                resumed_at=OPENED_AT + timedelta(days=4),
            )
        with self.assertRaises(OperatorGateError):
            resume_checkpoint(
                resume_token=opened["resume_token"],
                human_decision=granted,
                resumed_at=EXPIRES_AT + timedelta(days=1),
            )

        tampered = {
            **opened["resume_token"],
            "checkpoint": {**CHECKPOINT, "processed_count": 0},
        }
        with self.assertRaises(OperatorGateError):
            resume_checkpoint(
                resume_token=tampered,
                human_decision=granted,
                resumed_at=OPENED_AT + timedelta(days=2),
            )


class WorkIsolationTests(unittest.TestCase):
    WORK = (
        {
            "job_id": "job_evidence_001",
            "endpoint_id": "articles",
            "source_id": "antiegg",
            "worker_id": "worker_vm_01",
        },
        {
            "job_id": "job_evidence_002",
            "endpoint_id": "articles",
            "source_id": "antiegg",
            "worker_id": "worker_vm_01",
        },
        {
            "job_id": "job_inventory_003",
            "endpoint_id": "video-library",
            "source_id": "njp-video",
            "worker_id": "worker_vm_02",
        },
    )

    def test_one_blocked_job_does_not_hold_unrelated_work(self) -> None:
        blocker = gate()["blocker"]
        split = partition_work(self.WORK, (blocker,))
        self.assertEqual(
            ["job_evidence_001"], [item["job_id"] for item in split["blocked"]]
        )
        self.assertEqual(
            ["job_evidence_002", "job_inventory_003"],
            [item["job_id"] for item in split["ready"]],
        )

    def test_a_source_scoped_blocker_holds_only_that_source(self) -> None:
        blocker = gate(
            missing_authority_class="source_governance_approval",
            blocked_subject_id="antiegg",
            isolation_scope="single_source",
        )["blocker"]
        split = partition_work(self.WORK, (blocker,))
        self.assertEqual(
            ["job_inventory_003"], [item["job_id"] for item in split["ready"]]
        )

    def test_an_endpoint_scoped_blocker_holds_only_that_endpoint(self) -> None:
        blocker = gate(
            missing_authority_class="network_acquisition_approval",
            blocked_subject_id="video-library",
            isolation_scope="single_endpoint",
            subject_ids=("video-library", "njp-video"),
        )["blocker"]
        split = partition_work(self.WORK, (blocker,))
        self.assertEqual(
            ["job_evidence_001", "job_evidence_002"],
            [item["job_id"] for item in split["ready"]],
        )

    def test_work_items_must_report_exactly_the_isolation_fields(self) -> None:
        blocker = gate()["blocker"]
        with self.assertRaises(OperatorGateError):
            partition_work(({"job_id": "job_evidence_001"},), (blocker,))


if __name__ == "__main__":
    unittest.main()
