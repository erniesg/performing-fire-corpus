from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.trusted_vm_worker import (
    BoundedTrustedVMAcquisitionExecutor,
    TrustedVMExecutionError,
    TrustedVMWorkerError,
    run_trusted_vm_worker_once,
)
from performing_fire_corpus.ledger import Ledger


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
        self.fail_heartbeat_number: int | None = None

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
        if self.fail_heartbeat_number == len(self.heartbeats) + 1:
            raise RuntimeError("provider lease payload must not persist")
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


class FakeExecutionResponse:
    def __init__(
        self,
        body: bytes,
        *,
        media_type: str = "video/mp4",
        final_url: str = "https://antiegg.kr/media/synthetic.mp4",
        content_length: int | None = None,
        failure: Exception | None = None,
        clock: "FakeClock | None" = None,
    ) -> None:
        self.body = body
        self.media_type = media_type
        self.final_url = final_url
        self.content_length = len(body) if content_length is None else content_length
        self.failure = failure
        self.clock = clock

    def iter_bytes(self, chunk_size: int):
        del chunk_size
        midpoint = max(1, len(self.body) // 2)
        yield self.body[:midpoint]
        if self.clock is not None:
            self.clock.advance(seconds=10)
        if self.failure is not None:
            raise self.failure
        yield self.body[midpoint:]


class FakeExecutionHTTP:
    def __init__(self, response: FakeExecutionResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def open(
        self,
        url: str,
        *,
        timeout_seconds: float,
    ) -> FakeExecutionResponse:
        self.calls.append(f"{url}:{timeout_seconds}")
        return self.response


class FakeExecutionStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.heads: list[str] = []
        self.creates: list[str] = []
        self.lose_create_response = False

    def head_object(self, key: str) -> dict[str, object] | None:
        self.heads.append(key)
        value = self.objects.get(key)
        return None if value is None else copy.deepcopy(value)

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        self.creates.append(key)
        if key in self.objects:
            return False
        self.objects[key] = {
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
        }
        self.uploaded = path.read_bytes()
        if self.lose_create_response:
            raise ConnectionError("provider response must never persist")
        return True

    def delete_exact_object(self, key: str) -> bool:
        raise AssertionError(f"worker must never delete {key}")


class FakeClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class FakeRatePermit:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, datetime]] = []

    def allow(self, *, job_id: str, source_id: str, now: datetime) -> bool:
        self.calls.append((job_id, source_id, now))
        return self.allowed


class FakeExecutionContextResolver:
    def __init__(self, content: bytes, **overrides: object) -> None:
        self.content = content
        self.overrides = overrides
        self.calls = 0

    def resolve_execution_context(
        self,
        *,
        job: dict[str, object],
        authority: dict[str, object],
    ) -> dict[str, object]:
        del job, authority
        self.calls += 1
        value: dict[str, object] = {
            "public_url": "https://antiegg.kr/media/synthetic.mp4",
            "source_locator_id": "locator_synthetic_worker_001",
            "rights_id": "rights_synthetic_worker_001",
            "selection_id": "selection_synthetic_worker_001",
            "run_plan_id": "run_plan_synthetic_worker_001",
            "evidence_id": "evidence_synthetic_worker_001",
            "policy_snapshot_sha256": "b" * 64,
            "rights_snapshot_sha256": "c" * 64,
            "retention_class": "approved_raw",
            "creation_run_id": "run_synthetic_worker_001",
            "evidence_ref": "evidence:issue-39",
            "downstream_job_ids": ["job_synthetic_derived_001"],
            "maximum_elapsed_seconds": 5,
            "request_timeout_seconds": 2,
            "maximum_source_requests": 1,
        }
        value.update(self.overrides)
        return value


def execution_job(content: bytes) -> dict[str, object]:
    value = job()
    digest = hashlib.sha256(content).hexdigest()
    value["target_object_key"] = (
        "performing-fire/v1/raw/antiegg-fluxus/"
        f"asset_synthetic_worker_001/{digest}"
    )
    return value


def seed_execution_ledger(path: Path) -> Ledger:
    ledger = Ledger(path)
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "source",
            "source_id": "antiegg-fluxus",
            "public_url": "https://antiegg.kr/",
            "source_kind": "website",
            "metadata": {"fixture": "synthetic"},
        }
    )
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "asset",
            "asset_id": "asset_synthetic_worker_001",
            "source_id": "antiegg-fluxus",
            "public_url": "https://antiegg.kr/media/synthetic.mp4",
            "media_type": "video/mp4",
            "metadata": {"fixture": "synthetic"},
        }
    )
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "rights",
            "rights_id": "rights_synthetic_worker_001",
            "asset_id": "asset_synthetic_worker_001",
            "state": "approved",
            "decision_reason": "Synthetic worker fixture is approved.",
            "decision_at": "2026-07-25T01:00:00Z",
        }
    )
    return ledger


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

    def test_lease_loss_holds_only_the_job_with_exact_resume_state(self) -> None:
        before = FakeControlPlane(job())
        before.fail_heartbeat_number = 1
        executor = FakeExecutor()
        blocked_before = run_trusted_vm_worker_once(
            capability(),
            control_plane=before,
            authority_resolver=FakeAuthorityResolver(),
            executor=executor,
            now=NOW,
        )
        self.assertEqual(
            "lease_lost_before_acquisition",
            blocked_before["outcome_code"],
        )
        self.assertEqual(0, executor.calls)

        after = FakeControlPlane(job())
        after.fail_heartbeat_number = 2
        blocked_after = run_trusted_vm_worker_once(
            capability(),
            control_plane=after,
            authority_resolver=FakeAuthorityResolver(),
            executor=FakeExecutor(),
            now=NOW,
        )
        self.assertEqual(
            "lease_lost_after_verification",
            blocked_after["outcome_code"],
        )
        self.assertEqual("exact_key_verified", after.checkpoints[-1]["stage"])
        self.assertEqual(OBJECT_KEY, after.checkpoints[-1]["object_key"])
        self.assertNotIn("provider lease", json.dumps(blocked_after))


class BoundedTrustedVMAcquisitionExecutorTests(unittest.TestCase):
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
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.ledger = seed_execution_ledger(self.root / "ledger.sqlite3")
        self.addCleanup(self.ledger.close)

    def executor(
        self,
        *,
        content: bytes,
        storage: FakeExecutionStorage | None = None,
        response: FakeExecutionResponse | None = None,
        clock: FakeClock | None = None,
        rate: FakeRatePermit | None = None,
        context: FakeExecutionContextResolver | None = None,
        receipt_authority: object | None = None,
    ) -> tuple[
        BoundedTrustedVMAcquisitionExecutor,
        FakeExecutionHTTP,
        FakeExecutionStorage,
        FakeRatePermit,
    ]:
        selected_clock = clock or FakeClock()
        selected_http = FakeExecutionHTTP(
            response or FakeExecutionResponse(content)
        )
        selected_storage = storage or FakeExecutionStorage()
        selected_rate = rate or FakeRatePermit()
        return (
            BoundedTrustedVMAcquisitionExecutor(
                context_resolver=context or FakeExecutionContextResolver(content),
                http_client=selected_http,
                storage_client=selected_storage,
                receipt_authority=receipt_authority or self.ledger,
                cache_directory=self.cache,
                clock=selected_clock,
                rate_permit=selected_rate,
            ),
            selected_http,
            selected_storage,
            selected_rate,
        )

    def assert_cache_empty(self) -> None:
        self.assertEqual(
            [],
            list(self.cache.iterdir()) if self.cache.exists() else [],
        )

    def test_adapter_fetches_once_persists_receipt_and_reuses_exact_key(self) -> None:
        content = b"bounded-worker-object"
        item = execution_job(content)
        executor, http, storage, rate = self.executor(content=content)

        first = executor.acquire_one(
            job=item,
            authority=authority(),
            lease_id="lease_synthetic_worker_001",
        )
        second = executor.acquire_one(
            job=item,
            authority=authority(),
            lease_id="lease_synthetic_worker_002",
        )

        self.assertEqual(first, second)
        self.assertEqual([item["target_object_key"]], storage.creates)
        self.assertEqual(
            ["https://antiegg.kr/media/synthetic.mp4:2.0"],
            http.calls,
        )
        self.assertEqual(1, len(rate.calls))
        self.assertEqual(content, storage.uploaded)
        self.assertEqual(
            first["object_receipt_id"],
            self.ledger.get_corpus_receipt_by_key(
                str(item["target_object_key"])
            )["receipt_id"],
        )
        self.assertTrue(str(first["provenance_receipt_id"]).startswith("provenance_"))
        self.assert_cache_empty()

    def test_lost_create_response_recovers_by_exact_head_without_retry(self) -> None:
        content = b"ambiguous-create"
        storage = FakeExecutionStorage()
        storage.lose_create_response = True
        executor, http, _, _ = self.executor(
            content=content,
            storage=storage,
        )

        receipt = executor.acquire_one(
            job=execution_job(content),
            authority=authority(),
            lease_id="lease_synthetic_worker_001",
        )

        durable = self.ledger.get_corpus_receipt(
            str(receipt["object_receipt_id"])
        )
        self.assertEqual("reused_after_ambiguous_create", durable["create_disposition"])
        self.assertEqual(1, len(http.calls))
        self.assertEqual(1, len(storage.creates))
        self.assert_cache_empty()

    def test_restart_recovers_preexisting_exact_object_without_source_request(self) -> None:
        content = b"crash-after-create"
        item = execution_job(content)
        storage = FakeExecutionStorage()
        storage.objects[str(item["target_object_key"])] = {
            "byte_size": len(content),
            "media_type": "video/mp4",
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        executor, http, _, rate = self.executor(
            content=content,
            storage=storage,
        )

        receipt = executor.acquire_one(
            job=item,
            authority=authority(),
            lease_id="lease_synthetic_worker_002",
        )

        self.assertEqual([], http.calls)
        self.assertEqual([], rate.calls)
        self.assertEqual([], storage.creates)
        self.assertEqual(item["target_object_key"], receipt["object_key"])
        self.assert_cache_empty()

        class TombstonedAuthority:
            def get_corpus_receipt_by_key(self, object_key: str):
                return self.ledger.get_corpus_receipt_by_key(object_key)

            def get_cleanup_tombstone_by_key(self, object_key: str):
                del object_key
                return {"tombstone_id": "tombstone_synthetic_worker_001"}

            def upsert(self, record, *, operation_id=None):
                return self.ledger.upsert(record, operation_id=operation_id)

            def __init__(self, ledger: Ledger) -> None:
                self.ledger = ledger

        held, held_http, _, _ = self.executor(
            content=content,
            storage=storage,
            receipt_authority=TombstonedAuthority(self.ledger),
        )
        with self.assertRaisesRegex(
            TrustedVMExecutionError,
            "object_tombstoned",
        ):
            held.acquire_one(
                job=item,
                authority=authority(),
                lease_id="lease_synthetic_worker_003",
            )
        self.assertEqual([], held_http.calls)

    def test_rate_elapsed_stream_and_shape_failures_clean_disposable_cache(self) -> None:
        content = b"bounded-failure"
        denied, denied_http, _, denied_rate = self.executor(
            content=content,
            rate=FakeRatePermit(False),
        )
        with self.assertRaisesRegex(
            TrustedVMExecutionError,
            "source_rate_not_ready",
        ):
            denied.acquire_one(
                job=execution_job(content),
                authority=authority(),
                lease_id="lease_synthetic_worker_001",
            )
        self.assertEqual([], denied_http.calls)
        self.assertEqual(1, len(denied_rate.calls))

        clock = FakeClock()
        elapsed_response = FakeExecutionResponse(content, clock=clock)
        elapsed, _, _, _ = self.executor(
            content=content,
            response=elapsed_response,
            clock=clock,
        )
        with self.assertRaisesRegex(
            TrustedVMExecutionError,
            "elapsed_budget_exhausted",
        ):
            elapsed.acquire_one(
                job=execution_job(content),
                authority=authority(),
                lease_id="lease_synthetic_worker_001",
            )
        self.assert_cache_empty()

        for response, code in (
            (
                FakeExecutionResponse(
                    content,
                    final_url="https://example.invalid/redirected",
                ),
                "source_url_mismatch",
            ),
            (
                FakeExecutionResponse(content, media_type="text/html"),
                "source_mime_mismatch",
            ),
            (
                FakeExecutionResponse(content, content_length=2048),
                "source_size_exceeded",
            ),
            (
                FakeExecutionResponse(
                    content,
                    failure=ConnectionError("private response body"),
                ),
                "source_stream_failed",
            ),
        ):
            with self.subTest(code=code):
                executor, _, _, _ = self.executor(
                    content=content,
                    response=response,
                )
                with self.assertRaisesRegex(TrustedVMExecutionError, code) as raised:
                    executor.acquire_one(
                        job=execution_job(content),
                        authority=authority(),
                        lease_id="lease_synthetic_worker_001",
                    )
                self.assertNotIn("private response body", str(raised.exception))
                self.assert_cache_empty()

    def test_target_hash_and_context_mismatch_fail_before_source_request(self) -> None:
        content = b"context-mismatch"
        for overrides in (
            {"maximum_source_requests": 2},
            {"public_url": "file:///tmp/source.bin"},
            {"downstream_job_ids": ["job_z", "job_a"]},
            {"rights_id": "rights_different"},
            {"request_timeout_seconds": 6},
        ):
            with self.subTest(overrides=overrides):
                executor, http, _, _ = self.executor(
                    content=content,
                    context=FakeExecutionContextResolver(content, **overrides),
                )
                with self.assertRaises(TrustedVMWorkerError):
                    executor.acquire_one(
                        job=execution_job(content),
                        authority=authority(),
                        lease_id="lease_synthetic_worker_001",
                    )
                self.assertEqual([], http.calls)

        wrong_hash = execution_job(content)
        wrong_hash["target_object_key"] = str(
            wrong_hash["target_object_key"]
        ).removesuffix(hashlib.sha256(content).hexdigest()) + "f" * 64
        executor, http, storage, _ = self.executor(content=content)
        with self.assertRaisesRegex(
            TrustedVMExecutionError,
            "source_hash_mismatch",
        ):
            executor.acquire_one(
                job=wrong_hash,
                authority=authority(),
                lease_id="lease_synthetic_worker_001",
            )
        self.assertEqual(1, len(http.calls))
        self.assertEqual([], storage.creates)
        self.assert_cache_empty()

    def test_authority_expiry_after_stream_blocks_before_object_create(self) -> None:
        content = b"authority-window"
        clock = FakeClock()
        response = FakeExecutionResponse(content, clock=clock)
        context = FakeExecutionContextResolver(
            content,
            maximum_elapsed_seconds=20,
            request_timeout_seconds=2,
        )
        executor, http, storage, _ = self.executor(
            content=content,
            response=response,
            clock=clock,
            context=context,
        )
        expiring = authority()
        expiring["expires_at"] = "2026-07-25T02:00:05Z"

        with self.assertRaisesRegex(
            TrustedVMExecutionError,
            "authority_expired_before_create",
        ):
            executor.acquire_one(
                job=execution_job(content),
                authority=expiring,
                lease_id="lease_synthetic_worker_001",
            )

        self.assertEqual(1, len(http.calls))
        self.assertEqual([], storage.creates)
        self.assert_cache_empty()

    def test_supervisor_uses_fresh_clock_and_releases_disconnect(self) -> None:
        clock = FakeClock()
        control = FakeControlPlane(job())
        executor = FakeExecutor()
        original = executor.acquire_one

        def advance_then_acquire(**kwargs):
            clock.advance(seconds=30)
            return original(**kwargs)

        executor.acquire_one = advance_then_acquire
        result = run_trusted_vm_worker_once(
            capability(),
            control_plane=control,
            authority_resolver=FakeAuthorityResolver(),
            executor=executor,
            now=NOW,
            clock=clock,
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual(
            "2026-07-25T02:00:30Z",
            control.heartbeats[-1]["heartbeat_at"],
        )

        class DisconnectingExecutor(FakeExecutor):
            def acquire_one(self, **kwargs):
                del kwargs
                raise KeyboardInterrupt()

        interrupted = FakeControlPlane(job())
        with self.assertRaises(KeyboardInterrupt):
            run_trusted_vm_worker_once(
                capability(),
                control_plane=interrupted,
                authority_resolver=FakeAuthorityResolver(),
                executor=DisconnectingExecutor(),
                now=NOW,
            )
        self.assertEqual(
            ["lease_synthetic_worker_001:worker_interrupted"],
            interrupted.released,
        )


if __name__ == "__main__":
    unittest.main()
