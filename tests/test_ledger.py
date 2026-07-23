from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.ledger import (
    ASSET_STATES,
    InvalidTransition,
    LeaseError,
    Ledger,
    LedgerError,
    RECORD_TYPES,
)


FIXTURES = ROOT / "tests" / "fixtures" / "records" / "v1"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.database)
        self.asset = fixture("asset")
        self.rights = fixture("rights")
        self.job = fixture("job")
        self.raw_object = fixture("object")

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def seed_asset(self, *, approved: bool = True) -> None:
        self.ledger.upsert(fixture("source"))
        self.ledger.upsert(self.asset)
        rights = copy.deepcopy(self.rights)
        if not approved:
            rights["state"] = "pending"
            rights.pop("decision_reason")
            rights.pop("decision_at")
        self.ledger.upsert(rights)

    def advance_to(self, state: str) -> None:
        current_index = ASSET_STATES.index(self.ledger.asset_state(self.asset["asset_id"]))
        target_index = ASSET_STATES.index(state)
        for next_state in ASSET_STATES[current_index + 1 : target_index + 1]:
            if next_state == "raw_in_object_store":
                self.ledger.upsert(self.raw_object)
            elif next_state == "derived_in_object_store":
                derived = copy.deepcopy(self.raw_object)
                derived["object_id"] = "object_synthetic_derived_001"
                derived["object_key"] = "derived/synthetic/video-001.json"
                derived["media_type"] = "application/json"
                self.ledger.upsert(derived)
            self.ledger.transition_asset(self.asset["asset_id"], next_state)

    def test_every_forward_transition_and_failure_branch_is_enforced(self) -> None:
        self.seed_asset()
        for index, state in enumerate(ASSET_STATES):
            self.assertEqual(state, self.ledger.asset_state(self.asset["asset_id"]))
            if index + 1 < len(ASSET_STATES):
                if index + 2 < len(ASSET_STATES):
                    with self.assertRaises(InvalidTransition):
                        self.ledger.transition_asset(
                            self.asset["asset_id"], ASSET_STATES[index + 2]
                        )
                self.advance_to(ASSET_STATES[index + 1])

        for failure in ("blocked", "failed_retryable", "failed_final"):
            with self.subTest(failure=failure):
                database = Path(self.temporary.name) / f"{failure}.sqlite3"
                with Ledger(database) as ledger:
                    ledger.upsert(fixture("source"))
                    ledger.upsert(self.asset)
                    kwargs = {"blocker": "Synthetic blocker."} if failure == "blocked" else {}
                    ledger.transition_asset(self.asset["asset_id"], failure, **kwargs)
                    self.assertEqual(failure, ledger.asset_state(self.asset["asset_id"]))

    def test_rights_and_object_verification_gates_fail_closed(self) -> None:
        self.seed_asset(approved=False)
        self.ledger.transition_asset(self.asset["asset_id"], "metadata_verified")
        with self.assertRaises(InvalidTransition):
            self.ledger.transition_asset(self.asset["asset_id"], "approved_for_ingest")

        other = Path(self.temporary.name) / "object-gate.sqlite3"
        with Ledger(other) as ledger:
            ledger.upsert(fixture("source"))
            ledger.upsert(self.asset)
            ledger.upsert(self.rights)
            for state in ("metadata_verified", "approved_for_ingest", "transfer_pending"):
                ledger.transition_asset(self.asset["asset_id"], state)
            with self.assertRaises(InvalidTransition):
                ledger.transition_asset(self.asset["asset_id"], "raw_in_object_store")

    def test_records_jobs_checkpoints_and_completion_are_idempotent(self) -> None:
        self.seed_asset()
        self.assertEqual(
            self.asset,
            self.ledger.upsert(self.asset, operation_id="op_upsert_asset"),
        )
        first = self.ledger.create_job(self.job, operation_id="op_create_job")
        second = self.ledger.create_job(self.job, operation_id="op_create_job")
        self.assertEqual(first, second)

        lease = self.ledger.claim_job(
            "worker_fixture_01", {"metadata-discovery"}, now=T0
        )
        checkpoint = {"sequence": 1, "summary": "Synthetic resume point."}
        self.assertEqual(
            checkpoint,
            self.ledger.write_checkpoint(
                lease["lease_id"],
                lease["holder_id"],
                checkpoint,
                operation_id="op_checkpoint",
                now=T0 + timedelta(seconds=1),
            ),
        )
        self.assertEqual(
            checkpoint,
            self.ledger.write_checkpoint(
                lease["lease_id"],
                lease["holder_id"],
                checkpoint,
                operation_id="op_checkpoint",
                now=T0 + timedelta(seconds=2),
            ),
        )
        completed = self.ledger.complete_job(
            lease["lease_id"],
            lease["holder_id"],
            operation_id="op_complete",
            now=T0 + timedelta(seconds=3),
        )
        self.assertEqual(
            completed,
            self.ledger.complete_job(
                lease["lease_id"],
                lease["holder_id"],
                operation_id="op_complete",
                now=T0 + timedelta(seconds=4),
            ),
        )

    def test_operation_ids_fail_closed_when_reused_for_a_different_request(self) -> None:
        source = fixture("source")
        self.ledger.upsert(source, operation_id="op_upsert_collision")
        with self.assertRaises(LedgerError):
            self.ledger.upsert(self.asset, operation_id="op_upsert_collision")

        self.seed_asset()
        self.ledger.create_job(self.job, operation_id="op_job_collision")
        changed_job = copy.deepcopy(self.job)
        changed_job["max_attempts"] = 4
        with self.assertRaises(LedgerError):
            self.ledger.create_job(changed_job, operation_id="op_job_collision")

        self.ledger.transition_asset(
            self.asset["asset_id"],
            "metadata_verified",
            operation_id="op_transition_collision",
        )
        with self.assertRaises(LedgerError):
            self.ledger.transition_asset(
                self.asset["asset_id"],
                "approved_for_ingest",
                operation_id="op_transition_collision",
            )

        lease = self.ledger.claim_job(
            "worker_fixture_01", {"metadata-discovery"}, now=T0
        )
        first_checkpoint = {"sequence": 1, "summary": "First request."}
        self.ledger.write_checkpoint(
            lease["lease_id"],
            lease["holder_id"],
            first_checkpoint,
            operation_id="op_checkpoint_collision",
            now=T0 + timedelta(seconds=1),
        )
        with self.assertRaises(LedgerError):
            self.ledger.write_checkpoint(
                lease["lease_id"],
                lease["holder_id"],
                {"sequence": 2, "summary": "Different request."},
                operation_id="op_checkpoint_collision",
                now=T0 + timedelta(seconds=2),
            )

        self.ledger.complete_job(
            lease["lease_id"],
            lease["holder_id"],
            operation_id="op_completion_collision",
            now=T0 + timedelta(seconds=3),
        )
        with self.assertRaises(LedgerError):
            self.ledger.complete_job(
                lease["lease_id"],
                "worker_fixture_wrong",
                operation_id="op_completion_collision",
                now=T0 + timedelta(seconds=4),
            )

        another_job = copy.deepcopy(self.job)
        another_job["job_id"] = "job_synthetic_metadata_002"
        another_job["operation"] = "metadata_discovery_second"
        self.ledger.create_job(another_job)
        failed_lease = self.ledger.claim_job(
            "worker_fixture_02",
            {"metadata-discovery"},
            now=T0 + timedelta(seconds=5),
        )
        self.ledger.fail_job(
            failed_lease["lease_id"],
            failed_lease["holder_id"],
            reason="First failure.",
            operation_id="op_failure_collision",
            now=T0 + timedelta(seconds=6),
        )
        with self.assertRaises(LedgerError):
            self.ledger.fail_job(
                failed_lease["lease_id"],
                failed_lease["holder_id"],
                reason="Different failure.",
                operation_id="op_failure_collision",
                now=T0 + timedelta(seconds=7),
            )

    def test_noop_transition_binds_its_operation_id(self) -> None:
        self.seed_asset()
        self.assertEqual(
            "discovered",
            self.ledger.transition_asset(
                self.asset["asset_id"],
                "discovered",
                operation_id="op_noop_transition",
            ),
        )

        with self.assertRaises(LedgerError):
            self.ledger.transition_asset(
                self.asset["asset_id"],
                "metadata_verified",
                operation_id="op_noop_transition",
            )

    def test_duplicate_job_creation_binds_its_operation_id(self) -> None:
        self.seed_asset()
        self.ledger.create_job(self.job)
        self.assertEqual(
            self.job,
            self.ledger.create_job(
                self.job,
                operation_id="op_duplicate_job",
            ),
        )

        changed_job = copy.deepcopy(self.job)
        changed_job["max_attempts"] = 4
        with self.assertRaises(LedgerError):
            self.ledger.create_job(
                changed_job,
                operation_id="op_duplicate_job",
            )

    def test_built_wheel_validates_records_outside_the_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            project_directory = temporary_path / "project"
            wheel_directory = temporary_path / "wheel"
            installed_directory = temporary_path / "installed"
            project_directory.mkdir()
            for filename in ("README.md", "pyproject.toml"):
                shutil.copy2(ROOT / filename, project_directory / filename)
            shutil.copytree(ROOT / "schemas", project_directory / "schemas")
            shutil.copytree(ROOT / "src", project_directory / "src")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    str(project_directory),
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheel_directory),
                ],
                cwd=temporary_path,
                check=True,
                text=True,
                capture_output=True,
            )
            wheel = next(wheel_directory.glob("*.whl"))
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    str(wheel),
                    "--no-deps",
                    "--target",
                    str(installed_directory),
                ],
                cwd=temporary_path,
                check=True,
                text=True,
                capture_output=True,
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, sys; "
                        "sys.path.insert(0, sys.argv[1]); "
                        "from performing_fire_corpus.ledger import validate_record; "
                        "[validate_record(record) for record in json.loads(sys.argv[2])]"
                    ),
                    str(installed_directory),
                    json.dumps([fixture(record_type) for record_type in RECORD_TYPES]),
                ],
                cwd=temporary_path,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, validation.returncode, validation.stderr)

    def test_claim_is_capability_scoped_atomic_and_expiring(self) -> None:
        self.seed_asset()
        self.ledger.create_job(self.job)
        self.assertIsNone(
            self.ledger.claim_job("worker_fixture_01", {"other-capability"}, now=T0)
        )
        self.ledger.close()

        barrier = threading.Barrier(2)
        results: list[dict[str, object] | None] = []

        def claim(worker: str) -> None:
            with Ledger(self.database) as ledger:
                barrier.wait()
                results.append(
                    ledger.claim_job(worker, {"metadata-discovery"}, now=T0)
                )

        threads = [
            threading.Thread(target=claim, args=(f"worker_fixture_0{index}",))
            for index in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, sum(result is not None for result in results))
        self.ledger = Ledger(self.database)
        lease = next(result for result in results if result is not None)
        with self.assertRaises(LeaseError):
            self.ledger.complete_job(
                lease["lease_id"], "worker_fixture_wrong", now=T0 + timedelta(seconds=1)
            )
        with self.assertRaises(LeaseError):
            self.ledger.complete_job(
                lease["lease_id"], lease["holder_id"], now=T0 + timedelta(minutes=6)
            )
        self.assertEqual(1, self.ledger.recover_expired(now=T0 + timedelta(minutes=6)))
        resumed = self.ledger.claim_job(
            "worker_fixture_03",
            {"metadata-discovery"},
            now=T0 + timedelta(minutes=6),
        )
        self.assertEqual(self.job["job_id"], resumed["job_id"])

    def test_heartbeat_disconnect_checkpoint_resume_and_retry_exhaustion(self) -> None:
        self.seed_asset()
        self.ledger.create_job(self.job)
        lease = self.ledger.claim_job(
            "worker_fixture_01", {"metadata-discovery"}, now=T0, lease_seconds=10
        )
        expires = self.ledger.heartbeat(
            lease["lease_id"],
            lease["holder_id"],
            now=T0 + timedelta(seconds=5),
            lease_seconds=20,
        )
        self.assertEqual("2026-01-01T00:00:25Z", expires)
        checkpoint = {"sequence": 2, "summary": "Synthetic durable checkpoint."}
        self.ledger.write_checkpoint(
            lease["lease_id"], lease["holder_id"], checkpoint, now=T0 + timedelta(seconds=6)
        )
        self.ledger.release_lease(
            lease["lease_id"], lease["holder_id"], now=T0 + timedelta(seconds=7)
        )
        resumed = self.ledger.claim_job(
            "worker_fixture_02", {"metadata-discovery"}, now=T0 + timedelta(seconds=8)
        )
        self.assertEqual(checkpoint, resumed["checkpoint"])

        for expected in (1, 2, 3):
            result = self.ledger.fail_job(
                resumed["lease_id"],
                resumed["holder_id"],
                now=T0 + timedelta(seconds=8 + expected),
            )
            self.assertEqual(expected, result["attempt_count"])
            if expected < 3:
                resumed = self.ledger.claim_job(
                    "worker_fixture_02",
                    {"metadata-discovery"},
                    now=T0 + timedelta(seconds=20 + expected),
                )
        self.assertEqual("exhausted", result["retry_state"])
        self.assertIsNone(
            self.ledger.claim_job(
                "worker_fixture_02", {"metadata-discovery"}, now=T0 + timedelta(minutes=1)
            )
        )

    def test_cross_lane_payloads_and_duplicate_receipts_are_rejected(self) -> None:
        self.seed_asset()
        crossing = copy.deepcopy(self.job)
        crossing["required_capabilities"] = ["trusted-laptop"]
        crossing.pop("output_object_key")
        with self.assertRaises(LedgerError):
            self.ledger.create_job(crossing)
        bad = copy.deepcopy(self.job)
        bad["checkpoint"]["summary"] = "/tmp/private.bin"
        with self.assertRaises(Exception):
            self.ledger.create_job(bad)

        self.ledger.upsert(self.raw_object)
        duplicate = copy.deepcopy(self.raw_object)
        duplicate["object_id"] = "object_duplicate_001"
        with self.assertRaises(LedgerError):
            self.ledger.upsert(duplicate)

    def test_progress_reconstructs_after_restart_and_cli_reports_it(self) -> None:
        self.seed_asset()
        self.ledger.create_job(self.job)
        self.ledger.add_link(
            self.asset["asset_id"], "https://github.com/example/project/issues/2"
        )
        self.ledger.upsert(fixture("evidence"))
        lease = self.ledger.claim_job(
            "worker_fixture_01", {"metadata-discovery"}, now=T0
        )
        self.ledger.close()
        self.ledger = Ledger(self.database)
        progress = self.ledger.progress(now=T0 + timedelta(seconds=1))
        self.assertEqual({"discovered": 1}, progress["states"])
        self.assertEqual(1, progress["leases"]["active"])
        self.assertTrue(progress["evidence_links"])
        self.assertTrue(progress["work_links"])
        self.assertIn("active leases", progress["next_safe_action"])

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "performing_fire_corpus",
                "progress",
                "--database",
                str(self.database),
            ],
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src")},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("discovered", next(iter(json.loads(result.stdout)["states"])))
        self.assertEqual(self.job["job_id"], lease["job_id"])


if __name__ == "__main__":
    unittest.main()
