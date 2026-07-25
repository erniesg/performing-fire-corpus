from __future__ import annotations

import copy
import json
import socket
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.trusted_vm_worker import (
    TrustedVMWorkerError,
    run_trusted_vm_worker_once,
)


NOW = datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc)
OBJECT_KEY = (
    "performing-fire/v1/raw/antiegg-fluxus/"
    "asset_synthetic_worker_001/" + "a" * 64
)


def capability() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "trusted_vm_worker_capability",
        "worker_id": "worker_synthetic_vm_001",
        "capabilities": ["bounded-public-acquisition", "r2-immutable-create"],
        "max_concurrency": 1,
        "maximum_asset_bytes": 1024,
        "issued_at": "2026-07-25T01:00:00Z",
        "expires_at": "2026-07-25T03:00:00Z",
    }


def job() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "trusted_vm_acquisition_job",
        "job_id": "job_synthetic_worker_001",
        "source_id": "antiegg-fluxus",
        "asset_id": "asset_synthetic_worker_001",
        "source_locator_id": "locator_synthetic_worker_001",
        "rights_id": "rights_synthetic_worker_001",
        "selection_id": "selection_synthetic_worker_001",
        "run_plan_id": "run_plan_synthetic_worker_001",
        "evidence_id": "evidence_synthetic_worker_001",
        "policy_snapshot_sha256": "b" * 64,
        "policy_expires_at": "2026-07-25T03:00:00Z",
        "expected_mime_type": "video/mp4",
        "maximum_bytes": 1024,
        "target_object_key": OBJECT_KEY,
        "required_capabilities": [
            "bounded-public-acquisition",
            "r2-immutable-create",
        ],
    }


def authority() -> dict[str, object]:
    return {
        "job_id": "job_synthetic_worker_001",
        "policy_snapshot_sha256": "b" * 64,
        "checked_at": "2026-07-25T02:00:00Z",
        "expires_at": "2026-07-25T03:00:00Z",
        "gates": {
            "rights": True,
            "robots": True,
            "access": True,
            "mime": True,
            "bytes": True,
            "retention": True,
            "storage_scope": True,
            "selection": True,
        },
    }


class FakeControlPlane:
    def __init__(self, job_value: dict[str, object] | None = None) -> None:
        self.job = copy.deepcopy(job_value)
        self.terminal: dict[str, object] | None = None
        self.checkpoints: list[dict[str, object]] = []
        self.heartbeats: list[dict[str, object]] = []
        self.released: list[str] = []
        self.blockers: list[dict[str, object]] = []

    def claim_one(self, worker: dict[str, object], *, now: datetime):
        if self.job is None or self.terminal is not None:
            return None
        return {
            "lease": {
                "schema_version": 1,
                "record_type": "trusted_vm_worker_lease",
                "lease_id": "lease_synthetic_worker_001",
                "job_id": self.job["job_id"],
                "worker_id": worker["worker_id"],
                "acquired_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": (
                    now + timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z"),
            },
            "job": copy.deepcopy(self.job),
        }

    def heartbeat(
        self, lease_id: str, *, now: datetime
    ) -> dict[str, object]:
        value = {
            "schema_version": 1,
            "record_type": "trusted_vm_worker_heartbeat",
            "lease_id": lease_id,
            "job_id": self.job["job_id"],
            "worker_id": "worker_synthetic_vm_001",
            "heartbeat_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (
                now + timedelta(minutes=5)
            ).isoformat().replace("+00:00", "Z"),
        }
        self.heartbeats.append(value)
        return value

    def checkpoint(self, value: dict[str, object]) -> None:
        self.checkpoints.append(copy.deepcopy(value))

    def complete(self, value: dict[str, object]) -> None:
        self.terminal = copy.deepcopy(value)

    def block(self, value: dict[str, object]) -> None:
        self.blockers.append(copy.deepcopy(value))
        self.terminal = copy.deepcopy(value)

    def release(self, lease_id: str, *, reason: str) -> None:
        self.released.append(f"{lease_id}:{reason}")


class FakeAuthorityResolver:
    def __init__(self, value: dict[str, object] | None = None) -> None:
        self.value = authority() if value is None else value

    def resolve_current_acquisition_authority(
        self, *, job: dict[str, object], now: datetime
    ) -> dict[str, object]:
        del job, now
        return copy.deepcopy(self.value)


class FakeExecutor:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    def acquire_one(
        self,
        *,
        job: dict[str, object],
        authority: dict[str, object],
        lease_id: str,
    ) -> dict[str, object]:
        del job, authority, lease_id
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return {
            "object_key": OBJECT_KEY,
            "sha256": "a" * 64,
            "byte_size": 4,
            "object_receipt_id": "object_receipt_synthetic_worker_001",
            "provenance_receipt_id": "provenance_receipt_synthetic_worker_001",
            "downstream_job_ids": ["job_synthetic_derived_001"],
        }


class TrustedVMWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        for guard in (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                socket,
                "getaddrinfo",
                side_effect=AssertionError("DNS forbidden"),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("socket forbidden"),
            ),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def test_claims_one_approved_job_and_hands_off_exact_key(self) -> None:
        control = FakeControlPlane(job())
        executor = FakeExecutor()
        result = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(),
            executor=executor,
            now=NOW,
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual(1, executor.calls)
        self.assertEqual(OBJECT_KEY, result["object_key"])
        self.assertEqual(["job_synthetic_derived_001"], result["downstream_job_ids"])
        self.assertEqual(2, len(control.checkpoints))
        self.assertTrue(control.heartbeats)
        self.assertEqual(result, control.terminal)
        schema = json.loads(
            (
                ROOT / "schemas" / "v1" / "trusted-vm-worker.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        for record in (
            capability(),
            job(),
            control.checkpoints[0],
            control.checkpoints[1],
            control.heartbeats[0],
            result,
        ):
            with self.subTest(record_type=record["record_type"]):
                validator.validate(record)

    def test_gate_failure_is_durable_and_does_not_call_executor(self) -> None:
        blocked_authority = authority()
        blocked_authority["gates"]["rights"] = False
        control = FakeControlPlane(job())
        executor = FakeExecutor()
        result = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(blocked_authority),
            executor=executor,
            now=NOW,
        )
        self.assertEqual("blocked", result["status"])
        self.assertEqual("gate_rights_not_approved", result["outcome_code"])
        self.assertEqual(0, executor.calls)
        self.assertEqual(result, control.blockers[0])
        self.assertIn("resume_token", result)

    def test_payload_paths_bytes_and_stale_authority_fail_closed(self) -> None:
        for field, value in (
            ("machine_path", "/tmp/source.bin"),
            ("source_bytes", b"content"),
        ):
            with self.subTest(field=field):
                unsafe = job()
                unsafe[field] = value
                with self.assertRaises(TrustedVMWorkerError):
                    run_trusted_vm_worker_once(
                        capability(),
                        control_plane=FakeControlPlane(unsafe),
                        authority_resolver=FakeAuthorityResolver(),
                        executor=FakeExecutor(),
                        now=NOW,
                    )
        stale = authority()
        stale["expires_at"] = "2026-07-25T01:59:59Z"
        result = run_trusted_vm_worker_once(
            capability(),
            control_plane=FakeControlPlane(job()),
            authority_resolver=FakeAuthorityResolver(stale),
            executor=FakeExecutor(),
            now=NOW,
        )
        self.assertEqual("authority_not_current", result["outcome_code"])

    def test_terminal_resume_and_no_work_do_not_repeat_acquisition(self) -> None:
        control = FakeControlPlane(job())
        executor = FakeExecutor()
        first = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(),
            executor=executor,
            now=NOW,
        )
        second = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(),
            executor=executor,
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual("completed", first["status"])
        self.assertEqual({"status": "idle"}, second)
        self.assertEqual(1, executor.calls)

    def test_executor_failure_is_sanitized_and_receipt_conflict_releases(self) -> None:
        control = FakeControlPlane(job())
        blocked = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(),
            executor=FakeExecutor(
                failure=RuntimeError("provider body must not persist")
            ),
            now=NOW,
        )
        self.assertEqual("bounded_executor_failed", blocked["outcome_code"])
        self.assertNotIn("provider body", json.dumps(blocked))

        class WrongReceipt(FakeExecutor):
            def acquire_one(self, **kwargs):
                value = super().acquire_one(**kwargs)
                value["object_key"] = value["object_key"].replace(
                    "a" * 64, "c" * 64
                )
                return value

        conflict_control = FakeControlPlane(job())
        with self.assertRaisesRegex(
            TrustedVMWorkerError,
            "receipt",
        ):
            run_trusted_vm_worker_once(
                capability(),
                control_plane=conflict_control,
                authority_resolver=FakeAuthorityResolver(),
                executor=WrongReceipt(),
                now=NOW,
            )
        self.assertEqual(
            [
                "lease_synthetic_worker_001:"
                "executor_contract_failure"
            ],
            conflict_control.released,
        )


if __name__ == "__main__":
    unittest.main()
