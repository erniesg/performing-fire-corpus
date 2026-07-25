from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import (  # noqa: E402
    bind_object_receipt,
    derived_object_key,
    manifest_object_key,
    raw_object_key,
)
from performing_fire_corpus.trusted_laptop_worker import (  # noqa: E402
    BoundedTrustedLaptopWorker,
    TrustedLaptopWorkerError,
    reap_stale_disposable_caches,
    transformation_contract_id,
    validate_trusted_laptop_record,
)


NOW = datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)
INPUT = b"synthetic-public-input"
OUTPUT = b'{"synthetic":"derived"}'
INPUT_SHA256 = hashlib.sha256(INPUT).hexdigest()
OUTPUT_SHA256 = hashlib.sha256(OUTPUT).hexdigest()
RIGHTS_SHA256 = "a" * 64
DERIVATION_AUTHORITY_SHA256 = "b" * 64
PRIVACY_SHA256 = "c" * 64
SOURCE_ID = "source_synthetic_001"
ASSET_ID = "asset_synthetic_001"
INPUT_KEY = raw_object_key(
    "performing-fire/",
    SOURCE_ID,
    ASSET_ID,
    INPUT_SHA256,
)


def utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parameters() -> dict[str, object]:
    return {"task": "ocr", "output_format": "json", "language": "ko"}


def transformation_id(
    *,
    tool_version: str = "1.0.0",
    parameter_values: dict[str, object] | None = None,
) -> str:
    return transformation_contract_id(
        tool_id="tool_synthetic_ocr",
        tool_version=tool_version,
        contract_version=1,
        parameters=parameters() if parameter_values is None else parameter_values,
    )


def input_receipt() -> dict[str, object]:
    return bind_object_receipt(
        {
            "schema_version": 1,
            "record_type": "object_receipt",
            "object_kind": "raw",
            "source_id": SOURCE_ID,
            "asset_id": ASSET_ID,
            "object_key": INPUT_KEY,
            "byte_size": len(INPUT),
            "media_type": "video/mp4",
            "sha256": INPUT_SHA256,
            "rights_snapshot_sha256": RIGHTS_SHA256,
            "retention_class": "research_short",
            "creation_run_id": "run_synthetic_input_001",
            "retrieval_decision": "approved",
            "evidence_ref": "evidence:synthetic-input-001",
            "verification_state": "verified",
            "create_disposition": "created",
        }
    )


def capability(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "trusted_laptop_capability",
        "pairing_protocol": "outbound-https-v1",
        "capabilities": ["ocr"],
        "max_concurrency": 1,
        "maximum_input_bytes": 4096,
        "maximum_output_bytes": 4096,
        "maximum_cpu_seconds": 60,
        "maximum_memory_bytes": 64 * 1024 * 1024,
        "maximum_disk_bytes": 128 * 1024 * 1024,
        "maximum_elapsed_seconds": 120,
        "issued_at": utc(NOW - timedelta(minutes=5)),
        "expires_at": utc(NOW + timedelta(hours=1)),
    }
    value.update(overrides)
    return value


def pairing() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "trusted_laptop_pairing",
        "pairing_id": "pairing_synthetic_001",
        "transport": "outbound_https",
        "direction": "laptop_initiated",
        "paired_at": utc(NOW),
        "expires_at": utc(NOW + timedelta(hours=1)),
    }


def job(**overrides: object) -> dict[str, object]:
    parameter_values = parameters()
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "trusted_laptop_transformation_job",
        "job_id": "job_synthetic_laptop_001",
        "source_id": SOURCE_ID,
        "asset_id": ASSET_ID,
        "rights_id": "rights_synthetic_001",
        "transformation_id": transformation_id(),
        "input_receipt_id": input_receipt()["receipt_id"],
        "input_object_key": INPUT_KEY,
        "input_sha256": INPUT_SHA256,
        "input_byte_size": len(INPUT),
        "input_media_type": "video/mp4",
        "input_rights_snapshot_sha256": RIGHTS_SHA256,
        "derivation_authority_sha256": DERIVATION_AUTHORITY_SHA256,
        "privacy_snapshot_sha256": PRIVACY_SHA256,
        "retention_class": "research_short",
        "retrieval_decision": "approved",
        "required_capability": "ocr",
        "tool_id": "tool_synthetic_ocr",
        "tool_version": "1.0.0",
        "contract_version": 1,
        "parameters": parameter_values,
        "parameters_sha256": hashlib.sha256(
            json.dumps(
                parameter_values,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
        "output_media_type": "application/json",
        "redaction_state": "synthetic",
        "namespace_prefix": "performing-fire/",
        "creation_run_id": "run_synthetic_laptop_001",
        "evidence_ref": "evidence:synthetic-laptop-001",
        "attempt": 1,
        "maximum_attempts": 2,
        "maximum_input_bytes": 4096,
        "maximum_output_bytes": 4096,
        "maximum_cpu_seconds": 60,
        "maximum_memory_bytes": 64 * 1024 * 1024,
        "maximum_disk_bytes": 128 * 1024 * 1024,
        "maximum_elapsed_seconds": 120,
    }
    value.update(overrides)
    return value


def authority(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "job_id": "job_synthetic_laptop_001",
        "input_rights_snapshot_sha256": RIGHTS_SHA256,
        "derivation_authority_sha256": DERIVATION_AUTHORITY_SHA256,
        "privacy_snapshot_sha256": PRIVACY_SHA256,
        "retention_class": "research_short",
        "checked_at": utc(NOW),
        "expires_at": utc(NOW + timedelta(hours=1)),
        "gates": {
            "capability": True,
            "consent": True,
            "deletion": True,
            "derivative_rights": True,
            "privacy": True,
            "retention": True,
        },
    }
    value.update(overrides)
    return value


class FakeClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {
            INPUT_KEY: {
                "body": INPUT,
                "byte_size": len(INPUT),
                "media_type": "video/mp4",
                "sha256": INPUT_SHA256,
            }
        }
        self.downloads: list[str] = []
        self.created: list[str] = []
        self.create_failure: Exception | None = None
        self.persist_before_create_failure = False
        self.before_head: Callable[[str, int], None] | None = None
        self.before_download: Callable[[], None] | None = None
        self.head_counts: dict[str, int] = {}

    def head_object(self, key: str) -> dict[str, object] | None:
        self.head_counts[key] = self.head_counts.get(key, 0) + 1
        if self.before_head is not None:
            self.before_head(key, self.head_counts[key])
        value = self.objects.get(key)
        if value is None:
            return None
        return {
            "byte_size": value["byte_size"],
            "media_type": value["media_type"],
            "sha256": value["sha256"],
        }

    def download_exact_to_file(
        self,
        key: str,
        path: Path,
        *,
        maximum_bytes: int,
    ) -> None:
        self.downloads.append(key)
        if self.before_download is not None:
            self.before_download()
        value = self.objects[key]
        body = bytes(value["body"])
        if len(body) > maximum_bytes:
            raise RuntimeError("bounded download refused")
        path.write_bytes(body)

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        body = path.read_bytes()
        metadata = {
            "body": body,
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
        }
        if self.create_failure is not None:
            if self.persist_before_create_failure:
                self.objects[key] = metadata
            raise self.create_failure
        if key in self.objects:
            return False
        self.objects[key] = metadata
        self.created.append(key)
        return True


class FakeReceiptAuthority:
    def __init__(self) -> None:
        receipt = input_receipt()
        self.receipts_by_id = {str(receipt["receipt_id"]): receipt}
        self.receipts_by_key = {INPUT_KEY: receipt}
        self.tombstones: set[str] = set()
        self.upserts: list[dict[str, object]] = []
        self.upsert_override: dict[str, object] | None = None
        self.before_upsert: Callable[[dict[str, object]], None] | None = None
        self.tombstone_probe: Path | None = None

    def get_corpus_receipt(
        self, receipt_id: str
    ) -> dict[str, object] | None:
        value = self.receipts_by_id.get(receipt_id)
        return None if value is None else copy.deepcopy(value)

    def get_corpus_receipt_by_key(
        self, object_key: str
    ) -> dict[str, object] | None:
        value = self.receipts_by_key.get(object_key)
        return None if value is None else copy.deepcopy(value)

    def get_cleanup_tombstone_by_key(
        self, object_key: str
    ) -> dict[str, object] | None:
        if (
            self.tombstone_probe is not None
            and self.tombstone_probe.exists()
            and object_key == INPUT_KEY
        ):
            self.tombstones.add(object_key)
        if object_key not in self.tombstones:
            return None
        return {
            "schema_version": 1,
            "record_type": "object_tombstone",
            "object_key": object_key,
        }

    def upsert(
        self,
        record: dict[str, object],
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        del operation_id
        value = copy.deepcopy(record)
        if self.before_upsert is not None:
            self.before_upsert(value)
        self.upserts.append(value)
        if self.upsert_override is not None:
            return copy.deepcopy(self.upsert_override)
        if value.get("record_type") == "object_receipt":
            self.receipts_by_id[str(value["receipt_id"])] = value
            self.receipts_by_key[str(value["object_key"])] = value
        return value


class FakeAuthorityResolver:
    def __init__(self) -> None:
        self.values: list[dict[str, object] | None | Exception] = []
        self.calls = 0

    def resolve_current_derivation_authority(
        self,
        *,
        job: dict[str, object],
        now: datetime,
    ) -> dict[str, object] | None:
        del job, now
        self.calls += 1
        if not self.values:
            return authority()
        index = min(self.calls - 1, len(self.values) - 1)
        value = self.values[index]
        if isinstance(value, Exception):
            raise value
        return None if value is None else copy.deepcopy(value)


class FakeTransformer:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._calls = multiprocessing.get_context("forkserver").Value(
            "i", 0
        )
        self.output = OUTPUT
        self.cpu_seconds = 2
        self.memory_bytes = 1024
        self.disk_bytes = len(INPUT) + len(OUTPUT)
        self.advance_seconds = 3
        self.sleep_seconds = 0.0
        self.busy_seconds = 0.0
        self.allocate_bytes = 0
        self.ignore_cpu_signal = False
        self.descendant_busy_seconds = 0.0
        self.descendant_processes = 0
        self.heartbeat_counter: object | None = None
        self.require_heartbeat_during_sleep = False
        self.failure: Exception | None = None
        self.failure_after_work: Exception | None = None
        self.tombstone_marker_path: Path | None = None
        self.manifest_symlink_target: Path | None = None
        self.replace_cache_root: Path | None = None
        self.replacement_file: Path | None = None
        self.reap_root: Path | None = None
        self.reap_at: datetime | None = None

    @property
    def calls(self) -> int:
        return self._calls.value

    def transform(
        self,
        *,
        input_path: Path,
        output_path: Path,
        job: dict[str, object],
    ) -> dict[str, object]:
        del job
        with self._calls.get_lock():
            self._calls.value += 1
        if self.failure is not None:
            raise self.failure
        if self.ignore_cpu_signal:
            signal.signal(signal.SIGXCPU, signal.SIG_IGN)
        allocation = (
            bytearray(self.allocate_bytes)
            if self.allocate_bytes
            else None
        )
        if allocation is not None:
            allocation[0] = 1
        if self.busy_seconds:
            busy_until = time.monotonic() + self.busy_seconds
            while time.monotonic() < busy_until:
                pass
        descendants: list[subprocess.Popen[bytes]] = []
        if self.descendant_busy_seconds and self.descendant_processes:
            child_program = (
                "import sys,time;"
                "deadline=time.process_time()+float(sys.argv[1]);"
                "\nwhile time.process_time()<deadline: pass"
            )
            descendants = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        child_program,
                        str(self.descendant_busy_seconds),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(self.descendant_processes)
            ]
            for descendant in descendants:
                descendant.wait()
        effective_input_path = input_path
        effective_output_path = output_path
        if self.replace_cache_root is not None:
            cache_path = output_path.parent
            owned_path = self.replace_cache_root / "renamed-owned-cache"
            cache_path.rename(owned_path)
            cache_path.mkdir()
            marker = json.loads(
                (owned_path / ".disposable-v1").read_text(encoding="utf-8")
            )
            (cache_path / ".disposable-v1").write_text(
                json.dumps(marker), encoding="utf-8"
            )
            if self.replacement_file is not None:
                self.replacement_file.write_text(
                    "preserve", encoding="utf-8"
                )
                (cache_path / "replacement-link").symlink_to(
                    self.replacement_file
                )
            effective_input_path = owned_path / "input.bin"
            effective_output_path = owned_path / "output.bin"
        if self.manifest_symlink_target is not None:
            (effective_output_path.parent / "manifest.json").symlink_to(
                self.manifest_symlink_target
            )
        if effective_input_path.read_bytes() != INPUT:
            raise AssertionError("input was not verified before transform")
        effective_output_path.write_bytes(self.output)
        if self.tombstone_marker_path is not None:
            self.tombstone_marker_path.write_text(
                "arrived", encoding="utf-8"
            )
        if self.reap_root is not None and self.reap_at is not None:
            if reap_stale_disposable_caches(
                self.reap_root, now=self.reap_at
            ):
                raise AssertionError("active cache was reaped")
        heartbeat_before_sleep = (
            None
            if self.heartbeat_counter is None
            else self.heartbeat_counter.value
        )
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if (
            self.require_heartbeat_during_sleep
            and self.heartbeat_counter is not None
            and self.heartbeat_counter.value <= heartbeat_before_sleep
        ):
            raise AssertionError("transform lease was not renewed")
        self.clock.advance(self.advance_seconds)
        if self.failure_after_work is not None:
            raise self.failure_after_work
        return {
            "cpu_seconds": self.cpu_seconds,
            "peak_memory_bytes": self.memory_bytes,
            "working_disk_bytes": self.disk_bytes,
        }


class FakeControlPlane:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.completed: dict[str, dict[str, object]] = {}
        self.blockers: list[dict[str, object]] = []
        self.releases: list[dict[str, object]] = []
        self.heartbeat_calls = 0
        self.fail_heartbeat_number: int | None = None
        self.fail_checkpoint_stage_after_store: str | None = None
        self.block_heartbeat_number: int | None = None
        self.heartbeat_entered = threading.Event()
        self.heartbeat_release = threading.Event()
        self.heartbeat_extension_seconds = 5 * 60
        self.lease_duration_seconds = 5 * 60
        self.heartbeat_counter = multiprocessing.get_context(
            "forkserver"
        ).Value("i", 0)
        self.job_value: dict[str, object] | None = job()
        self.pair_entered: threading.Event | None = None
        self.pair_release: threading.Event | None = None

    def pair_outbound(
        self,
        capability_value: dict[str, object],
        *,
        now: datetime,
    ) -> dict[str, object]:
        del now
        if self.pair_entered is not None:
            self.pair_entered.set()
        if self.pair_release is not None:
            self.pair_release.wait(timeout=5)
        self.events.append(
            {
                "event": "pair",
                "capabilities": list(capability_value["capabilities"]),
            }
        )
        return pairing()

    def claim_one(
        self,
        pairing_value: dict[str, object],
        capability_value: dict[str, object],
        *,
        now: datetime,
    ) -> dict[str, object] | None:
        del capability_value
        self.events.append(
            {"event": "claim", "pairing_id": pairing_value["pairing_id"]}
        )
        if self.job_value is None:
            return None
        return {
            "job": copy.deepcopy(self.job_value),
            "lease": {
                "schema_version": 1,
                "record_type": "trusted_laptop_lease",
                "lease_id": "lease_synthetic_laptop_001",
                "pairing_id": pairing_value["pairing_id"],
                "job_id": self.job_value["job_id"],
                "acquired_at": utc(now),
                "expires_at": utc(
                    now + timedelta(seconds=self.lease_duration_seconds)
                ),
            },
        }

    def get_completed_result(
        self, job_id: str
    ) -> dict[str, object] | None:
        value = self.completed.get(job_id)
        return None if value is None else copy.deepcopy(value)

    def get_latest_checkpoint(
        self, job_id: str
    ) -> dict[str, object] | None:
        values = [
            event
            for event in self.events
            if event.get("record_type") == "trusted_laptop_checkpoint"
            and event.get("job_id") == job_id
        ]
        return None if not values else copy.deepcopy(values[-1])

    def heartbeat(
        self,
        lease_id: str,
        pairing_id: str,
        *,
        now: datetime,
        timeout_seconds: float,
    ) -> dict[str, object]:
        self.heartbeat_calls += 1
        with self.heartbeat_counter.get_lock():
            self.heartbeat_counter.value += 1
        if self.block_heartbeat_number == self.heartbeat_calls:
            self.heartbeat_entered.set()
            if not self.heartbeat_release.wait(timeout=timeout_seconds):
                raise TimeoutError("bounded synthetic heartbeat timeout")
        if self.fail_heartbeat_number == self.heartbeat_calls:
            raise ConnectionError("synthetic disconnect content must not persist")
        value = {
            "schema_version": 1,
            "record_type": "trusted_laptop_heartbeat",
            "lease_id": lease_id,
            "pairing_id": pairing_id,
            "job_id": "job_synthetic_laptop_001",
            "heartbeat_at": utc(now),
            "expires_at": utc(
                now + timedelta(seconds=self.heartbeat_extension_seconds)
            ),
        }
        self.events.append({"event": "heartbeat", "lease_id": lease_id})
        return value

    def checkpoint(self, value: dict[str, object]) -> None:
        self.events.append(copy.deepcopy(value))
        if self.fail_checkpoint_stage_after_store == value["stage"]:
            raise ConnectionError("synthetic checkpoint disconnect")

    def reconcile_latest_attempt(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        checkpoint = next(
            event
            for event in reversed(self.events)
            if event.get("record_type") == "trusted_laptop_checkpoint"
        )
        checkpoint["attempt_open"] = False
        resume_state = {
            key: value
            for key, value in checkpoint.items()
            if key
            not in {
                "schema_version",
                "record_type",
                "checkpoint_id",
                "resume_state_sha256",
                "recorded_at",
            }
        }
        resume_hash = trusted_laptop_worker._digest(resume_state)
        checkpoint["resume_state_sha256"] = resume_hash
        checkpoint["checkpoint_id"] = (
            "checkpoint_"
            + trusted_laptop_worker._digest(
                {"resume_state_sha256": resume_hash}
            )
        )

    def complete(self, value: dict[str, object]) -> None:
        saved = copy.deepcopy(value)
        self.events.append(saved)
        self.completed[str(value["job_id"])] = saved

    def block(self, value: dict[str, object]) -> None:
        saved = copy.deepcopy(value)
        self.events.append(saved)
        self.blockers.append(saved)

    def release(
        self,
        lease_id: str,
        pairing_id: str,
        *,
        reason: str,
    ) -> None:
        value = {
            "event": "release",
            "lease_id": lease_id,
            "pairing_id": pairing_id,
            "reason": reason,
        }
        self.events.append(value)
        self.releases.append(value)


class Harness:
    def __init__(self, cache_root: Path) -> None:
        self.clock = FakeClock()
        self.storage = FakeObjectStore()
        self.receipts = FakeReceiptAuthority()
        self.authority = FakeAuthorityResolver()
        self.transformer = FakeTransformer(self.clock)
        self.control = FakeControlPlane()
        self.transformer.heartbeat_counter = self.control.heartbeat_counter
        self.worker = BoundedTrustedLaptopWorker(
            control_plane=self.control,
            authority_resolver=self.authority,
            object_store=self.storage,
            receipt_authority=self.receipts,
            transformer=self.transformer,
            cache_root=cache_root,
            clock=self.clock,
        )


class TrustedLaptopSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema_path = ROOT / "schemas" / "v1" / "trusted-laptop-worker.json"
        cls.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(cls.schema)

    def test_schema_and_runtime_accept_strict_content_free_records(self) -> None:
        records = [
            capability(),
            pairing(),
            job(),
            {
                "schema_version": 1,
                "record_type": "trusted_laptop_lease",
                "lease_id": "lease_synthetic_laptop_001",
                "pairing_id": "pairing_synthetic_001",
                "job_id": "job_synthetic_laptop_001",
                "acquired_at": utc(NOW),
                "expires_at": utc(NOW + timedelta(minutes=5)),
            },
        ]
        for record in records:
            with self.subTest(record_type=record["record_type"]):
                self.validator.validate(record)
                self.assertEqual(validate_trusted_laptop_record(record), record)

    def test_queue_rejects_content_paths_devices_credentials_and_inbound_access(self) -> None:
        unsafe_values = (
            ("source_bytes", "encoded-media"),
            ("local_path", "/tmp/media.mp4"),
            ("device_id", "laptop-serial"),
            ("signed_url", "https://example.invalid/object"),
            ("credential", "synthetic-secret"),
        )
        for field, value in unsafe_values:
            unsafe = job()
            unsafe[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    self.validator.validate(unsafe)
                with self.assertRaises(TrustedLaptopWorkerError):
                    validate_trusted_laptop_record(unsafe)

        inbound = pairing()
        inbound["direction"] = "control_plane_initiated"
        with self.assertRaises(ValidationError):
            self.validator.validate(inbound)
        with self.assertRaises(TrustedLaptopWorkerError):
            validate_trusted_laptop_record(inbound)

    def test_transformation_version_and_parameters_bind_new_immutable_namespace(self) -> None:
        first = transformation_id()
        second = transformation_id(tool_version="1.0.1")
        third = transformation_id(
            parameter_values={"task": "ocr", "output_format": "text", "language": "ko"}
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertNotEqual(
            derived_object_key(
                "performing-fire/", SOURCE_ID, ASSET_ID, first, OUTPUT_SHA256
            ),
            derived_object_key(
                "performing-fire/", SOURCE_ID, ASSET_ID, second, OUTPUT_SHA256
            ),
        )

    def test_schema_and_runtime_share_capability_and_media_type_grammar(self) -> None:
        unordered = capability(capabilities=["transcription", "ocr"])
        self.validator.validate(unordered)
        self.assertEqual(validate_trusted_laptop_record(unordered), unordered)

        parameterized_media_type = job(
            input_media_type="VIDEO/MP4; charset=binary"
        )
        with self.assertRaises(ValidationError):
            self.validator.validate(parameterized_media_type)
        with self.assertRaises(TrustedLaptopWorkerError):
            validate_trusted_laptop_record(parameterized_media_type)

    def test_schema_rejects_runtime_invalid_checkpoint_stage_facts(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        lease_value = {
            "schema_version": 1,
            "record_type": "trusted_laptop_lease",
            "lease_id": "lease_synthetic_laptop_001",
            "pairing_id": "pairing_synthetic_001",
            "job_id": "job_synthetic_laptop_001",
            "acquired_at": utc(NOW),
            "expires_at": utc(NOW + timedelta(minutes=5)),
        }
        claimed = trusted_laptop_worker._checkpoint(
            pairing=pairing(),
            lease=lease_value,
            job=job(),
            stage="claimed",
            now=NOW,
        )
        claimed.update(
            {
                "output_object_key": derived_object_key(
                    "performing-fire/",
                    SOURCE_ID,
                    ASSET_ID,
                    transformation_id(),
                    OUTPUT_SHA256,
                ),
                "output_sha256": OUTPUT_SHA256,
                "output_byte_size": len(OUTPUT),
                "cpu_seconds": 1,
                "peak_memory_bytes": 1,
                "working_disk_bytes": len(INPUT) + len(OUTPUT),
                "elapsed_seconds": 1,
            }
        )
        resume_state = {
            key: value
            for key, value in claimed.items()
            if key
            not in {
                "schema_version",
                "record_type",
                "checkpoint_id",
                "resume_state_sha256",
                "recorded_at",
            }
        }
        resume_hash = trusted_laptop_worker._digest(resume_state)
        claimed["resume_state_sha256"] = resume_hash
        claimed["checkpoint_id"] = (
            "checkpoint_"
            + trusted_laptop_worker._digest(
                {"resume_state_sha256": resume_hash}
            )
        )

        with self.assertRaises(ValidationError):
            self.validator.validate(claimed)
        with self.assertRaises(TrustedLaptopWorkerError):
            validate_trusted_laptop_record(claimed)


class TrustedLaptopWorkerTests(unittest.TestCase):
    def test_cpu_hard_limit_never_exceeds_the_declared_bound(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with (
            mock.patch.object(
                resource,
                "getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            mock.patch.object(resource, "setrlimit") as setrlimit,
        ):
            trusted_laptop_worker._set_cpu_resource_limit(7)
        setrlimit.assert_called_once_with(resource.RLIMIT_CPU, (7, 7))

    def test_outbound_pairing_exact_download_transform_and_two_immutable_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            result = harness.worker.run_once(capability())

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["record_type"], "trusted_laptop_result")
            self.assertEqual(result["output_sha256"], OUTPUT_SHA256)
            self.assertEqual(harness.storage.downloads, [INPUT_KEY])
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(len(harness.storage.created), 2)
            self.assertIn(str(result["output_object_key"]), harness.storage.created)
            self.assertIn(str(result["manifest_object_key"]), harness.storage.created)
            self.assertTrue(
                any(
                    record.get("record_type") == "derivation_manifest"
                    for record in harness.receipts.upserts
                )
            )
            self.assertEqual(
                [event["event"] for event in harness.control.events[:2]],
                ["pair", "claim"],
            )
            schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "v1"
                    / "trusted-laptop-worker.json"
                ).read_text(encoding="utf-8")
            )
            validator = Draft202012Validator(schema)
            validator.validate(result)
            for event in harness.control.events:
                if str(event.get("record_type", "")).startswith(
                    "trusted_laptop_"
                ):
                    validator.validate(event)
            self.assertFalse(any(Path(directory).iterdir()))

    def test_current_authority_is_rechecked_before_download_and_before_create(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.worker.run_once(capability())
            self.assertGreaterEqual(harness.authority.calls, 3)

            blocked = Harness(Path(directory))
            expired = authority(expires_at=utc(NOW + timedelta(seconds=1)))
            blocked.authority.values = [authority(), expired]
            blocked.clock.advance(2)
            result = blocked.worker.run_once(capability())
            self.assertIsNone(result)
            self.assertEqual(blocked.storage.downloads, [])
            self.assertEqual(blocked.transformer.calls, 0)
            self.assertEqual(blocked.storage.created, [])
            self.assertEqual(
                blocked.control.blockers[0]["code"],
                "authority_expired_before_input",
            )

    def test_authority_is_validated_against_the_post_resolver_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            expiring = authority(
                expires_at=utc(NOW + timedelta(seconds=1))
            )

            def delayed_authority(
                *,
                job: dict[str, object],
                now: datetime,
            ) -> dict[str, object]:
                del job, now
                harness.clock.advance(2)
                return copy.deepcopy(expiring)

            harness.authority.resolve_current_derivation_authority = (
                delayed_authority  # type: ignore[method-assign]
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.downloads, [])
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "authority_expired_after_claim",
            )

    def test_claim_setup_consumes_the_job_elapsed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_elapsed_seconds=1)
            original_authority = (
                harness.authority.resolve_current_derivation_authority
            )

            def delayed_authority(
                *,
                job: dict[str, object],
                now: datetime,
            ) -> dict[str, object] | None:
                value = original_authority(job=job, now=now)
                harness.clock.advance(2)
                return value

            harness.authority.resolve_current_derivation_authority = (
                delayed_authority  # type: ignore[method-assign]
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.downloads, [])
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_failed_resume_lookup_cannot_reset_elapsed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_elapsed_seconds=1)
            original_lookup = harness.control.get_completed_result

            def failed_lookup(job_id: str) -> dict[str, object] | None:
                del job_id
                harness.clock.advance(2)
                raise ConnectionError("synthetic lookup failure")

            harness.control.get_completed_result = failed_lookup  # type: ignore[method-assign]
            self.assertIsNone(harness.worker.run_once(capability()))
            checkpoint = harness.control.get_latest_checkpoint(
                "job_synthetic_laptop_001"
            )
            self.assertIsNotNone(checkpoint)
            assert checkpoint is not None
            self.assertTrue(checkpoint["attempt_open"])
            harness.control.get_completed_result = original_lookup  # type: ignore[method-assign]
            harness.control.job_value = job(
                attempt=2,
                maximum_elapsed_seconds=1,
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 0)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "attempt_unreconciled",
            )

    def test_missing_derivative_rights_blocks_before_any_object_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            denied = authority()
            denied["gates"]["derivative_rights"] = False
            harness.authority.values = [denied]
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.downloads, [])
            self.assertEqual(harness.transformer.calls, 0)
            self.assertEqual(harness.storage.created, [])
            blocker = harness.control.blockers[0]
            self.assertEqual(blocker["code"], "derivative_rights_not_approved")
            self.assertEqual(
                blocker["required_authority_class"], "corpus_operator"
            )

    def test_exact_input_receipt_hash_and_size_are_verified_before_transform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.storage.objects[INPUT_KEY]["body"] = b"x" * len(INPUT)
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 0)
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[0]["code"], "input_hash_mismatch"
            )

    def test_tombstone_before_download_or_arriving_during_transform_prevents_create(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = Harness(Path(directory))
            before.receipts.tombstones.add(INPUT_KEY)
            self.assertIsNone(before.worker.run_once(capability()))
            self.assertEqual(before.storage.downloads, [])
            self.assertEqual(before.storage.created, [])
            self.assertEqual(
                before.control.blockers[0]["code"], "input_object_tombstoned"
            )

            during_head = Harness(Path(directory))

            def tombstone_during_head(key: str, call: int) -> None:
                if key == INPUT_KEY and call == 1:
                    during_head.receipts.tombstones.add(key)

            during_head.storage.before_head = tombstone_during_head
            self.assertIsNone(during_head.worker.run_once(capability()))
            self.assertEqual(during_head.storage.downloads, [])
            self.assertEqual(during_head.storage.created, [])
            self.assertEqual(
                during_head.control.blockers[0]["code"],
                "input_object_tombstoned",
            )

            during = Harness(Path(directory))
            during_marker = Path(directory) / "during-transform.marker"
            during.receipts.tombstone_probe = during_marker
            during.transformer.tombstone_marker_path = during_marker
            self.assertIsNone(during.worker.run_once(capability()))
            self.assertEqual(during.storage.created, [])
            self.assertEqual(
                during.control.blockers[0]["code"],
                "input_object_tombstoned_before_create",
            )

    def test_tombstone_or_authority_expiry_during_output_head_blocks_actual_create(self) -> None:
        expected_key = derived_object_key(
            "performing-fire/",
            SOURCE_ID,
            ASSET_ID,
            transformation_id(),
            OUTPUT_SHA256,
        )
        with tempfile.TemporaryDirectory() as directory:
            tombstone = Harness(Path(directory))

            def add_tombstone(key: str, call: int) -> None:
                if key == expected_key and call == 1:
                    tombstone.receipts.tombstones.add(key)

            tombstone.storage.before_head = add_tombstone
            self.assertIsNone(tombstone.worker.run_once(capability()))
            self.assertEqual(tombstone.storage.created, [])
            self.assertEqual(
                tombstone.control.blockers[0]["code"],
                "output_object_tombstoned",
            )

        with tempfile.TemporaryDirectory() as directory:
            expired = Harness(Path(directory))
            short_authority = authority(
                expires_at=utc(NOW + timedelta(seconds=10))
            )
            expired.authority.values = [short_authority]

            def advance_during_head(key: str, call: int) -> None:
                if key == expected_key and call == 1:
                    expired.clock.advance(11)

            expired.storage.before_head = advance_during_head
            self.assertIsNone(expired.worker.run_once(capability()))
            self.assertEqual(expired.storage.created, [])
            self.assertEqual(
                expired.control.blockers[0]["code"],
                "authority_expired_at_output_create",
            )

    def test_lease_disconnect_stops_before_create_releases_and_cleans_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_heartbeat_number = 3
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(len(harness.control.releases), 1)
            self.assertEqual(
                harness.control.blockers[0]["code"], "pairing_disconnected"
            )
            self.assertEqual(
                harness.control.blockers[0]["required_authority_class"], "none"
            )
            self.assertFalse(any(Path(directory).iterdir()))

    def test_resource_bounds_fail_closed_before_immutable_create(self) -> None:
        cases = (
            ("cpu_seconds", 61, "cpu_limit_exceeded"),
            ("memory_bytes", 64 * 1024 * 1024 + 1, "memory_limit_exceeded"),
            ("disk_bytes", 128 * 1024 * 1024 + 1, "disk_limit_exceeded"),
        )
        for field, value, code in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                harness = Harness(Path(directory))
                setattr(harness.transformer, field, value)
                self.assertIsNone(harness.worker.run_once(capability()))
                self.assertEqual(harness.storage.created, [])
                self.assertEqual(harness.control.blockers[0]["code"], code)

        with tempfile.TemporaryDirectory() as directory:
            elapsed = Harness(Path(directory))
            elapsed.transformer.sleep_seconds = 2
            elapsed.control.job_value = job(maximum_elapsed_seconds=1)
            self.assertIsNone(elapsed.worker.run_once(capability()))
            self.assertEqual(elapsed.storage.created, [])
            self.assertEqual(
                elapsed.control.blockers[0]["code"], "elapsed_limit_exceeded"
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Harness(Path(directory))
            output.transformer.output = b"x" * 4097
            self.assertIsNone(output.worker.run_once(capability()))
            self.assertEqual(output.storage.created, [])
            self.assertEqual(
                output.control.blockers[0]["code"], "output_limit_exceeded"
            )

        with tempfile.TemporaryDirectory() as directory:
            expired_capability = Harness(Path(directory))
            expected_output_key = derived_object_key(
                "performing-fire/",
                SOURCE_ID,
                ASSET_ID,
                transformation_id(),
                OUTPUT_SHA256,
            )

            def expire_during_output_head(key: str, call: int) -> None:
                if key == expected_output_key and call == 1:
                    expired_capability.clock.advance(2)

            expired_capability.storage.before_head = expire_during_output_head
            self.assertIsNone(
                expired_capability.worker.run_once(
                    capability(
                        expires_at=utc(NOW + timedelta(seconds=1))
                    )
                )
            )
            self.assertEqual(expired_capability.storage.created, [])
            self.assertEqual(
                expired_capability.control.blockers[0]["code"],
                "capability_expired",
            )

    def test_transformer_elapsed_limit_is_enforced_before_it_returns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.transformer.sleep_seconds = 5
            harness.control.job_value = job(maximum_elapsed_seconds=1)
            started = time.monotonic()
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(
                harness.control.blockers[0]["code"],
                "elapsed_limit_exceeded",
            )
            self.assertEqual(harness.storage.created, [])

    def test_transformer_cpu_and_memory_limits_do_not_trust_self_report(self) -> None:
        cases = (
            (
                "cpu_limit_exceeded",
                {"maximum_cpu_seconds": 1, "maximum_elapsed_seconds": 10},
                {"busy_seconds": 5.0, "ignore_cpu_signal": True},
            ),
            (
                "memory_limit_exceeded",
                {
                    "maximum_memory_bytes": 8 * 1024 * 1024,
                    "maximum_elapsed_seconds": 10,
                },
                {"allocate_bytes": 32 * 1024 * 1024, "sleep_seconds": 5.0},
            ),
        )
        for code, job_overrides, transformer_overrides in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                harness = Harness(Path(directory))
                harness.control.job_value = job(**job_overrides)
                for field, value in transformer_overrides.items():
                    setattr(harness.transformer, field, value)
                started = time.monotonic()
                self.assertIsNone(harness.worker.run_once(capability()))
                self.assertLess(time.monotonic() - started, 4)
                self.assertEqual(
                    harness.control.blockers[0]["code"], code
                )
                self.assertEqual(harness.storage.created, [])

    def test_transformer_resource_bounds_cover_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(
                maximum_cpu_seconds=1,
                maximum_elapsed_seconds=10,
            )
            harness.transformer.cpu_seconds = 0
            harness.transformer.advance_seconds = 0
            harness.transformer.descendant_processes = 2
            harness.transformer.descendant_busy_seconds = 0.75

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[0]["code"],
                "cpu_limit_exceeded",
            )

    def test_transform_uses_only_the_remaining_job_elapsed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_elapsed_seconds=1)
            harness.storage.before_download = lambda: harness.clock.advance(2)

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 0)
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[0]["code"],
                "elapsed_limit_exceeded",
            )

    def test_transform_watchdog_renews_the_lease_while_child_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.lease_duration_seconds = 5 * 60
            harness.control.heartbeat_extension_seconds = 1
            harness.control.job_value = job(maximum_elapsed_seconds=3)
            harness.transformer.advance_seconds = 0
            harness.transformer.sleep_seconds = 1.2
            harness.transformer.require_heartbeat_during_sleep = True

            self.assertIsNotNone(harness.worker.run_once(capability()))

    def test_blocked_heartbeat_cannot_pause_transform_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.lease_duration_seconds = 1.5
            harness.control.heartbeat_extension_seconds = 1.5
            harness.control.block_heartbeat_number = 5
            harness.control.job_value = job(maximum_elapsed_seconds=1)
            harness.transformer.advance_seconds = 0
            harness.transformer.sleep_seconds = 2
            started = time.monotonic()
            try:
                self.assertIsNone(harness.worker.run_once(capability()))
            finally:
                harness.control.heartbeat_release.set()
            self.assertTrue(harness.control.heartbeat_entered.is_set())
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "pairing_disconnected",
            )
            failed_checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type")
                == "trusted_laptop_checkpoint"
                and event.get("stage") == "attempt_failed"
            )
            self.assertGreater(
                float(failed_checkpoint["elapsed_seconds"]),
                0.04,
            )

    def test_heartbeat_renewal_is_checked_after_the_rpc_returns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.heartbeat_extension_seconds = 1
            original_heartbeat = harness.control.heartbeat

            def delayed_heartbeat(
                lease_id: str,
                pairing_id: str,
                *,
                now: datetime,
                timeout_seconds: float,
            ) -> dict[str, object]:
                value = original_heartbeat(
                    lease_id,
                    pairing_id,
                    now=now,
                    timeout_seconds=timeout_seconds,
                )
                if harness.control.heartbeat_calls == 6:
                    harness.clock.advance(2)
                return value

            harness.control.heartbeat = delayed_heartbeat  # type: ignore[method-assign]

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "pairing_disconnected",
            )

    def test_manifest_symlink_cannot_overwrite_an_unrelated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "unrelated.txt"
            unrelated.write_text("preserve", encoding="utf-8")
            harness = Harness(root)
            harness.transformer.manifest_symlink_target = unrelated
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(len(harness.storage.created), 1)
            self.assertFalse(
                any(
                    "/manifests/" in key
                    for key in harness.storage.created
                )
            )
            self.assertEqual(
                harness.control.blockers[0]["code"], "manifest_build_failed"
            )

    def test_replaced_cache_path_is_never_deleted_by_worker_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = Harness(root)
            replacement_file = root / "replacement-file-location.txt"
            harness.transformer.replace_cache_root = root
            harness.transformer.replacement_file = replacement_file
            self.assertIsNone(harness.worker.run_once(capability()))
            replacement_cache = next(
                root.glob(".performing-fire-laptop-cache-*")
            )
            self.assertTrue(replacement_cache.is_dir())
            renamed_owned_cache = root / "renamed-owned-cache"
            self.assertTrue(renamed_owned_cache.is_dir())
            self.assertEqual(list(renamed_owned_cache.iterdir()), [])
            self.assertEqual(
                replacement_file.read_text(encoding="utf-8"), "preserve"
            )

    def test_damaged_cache_marker_cannot_preserve_corpus_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = Harness(root)
            original_checkpoint = harness.control.checkpoint

            def damage_after_final_heartbeat(
                value: dict[str, object],
            ) -> None:
                original_checkpoint(value)
                if value["stage"] == "manifest_verified":
                    cache = next(
                        root.glob(".performing-fire-laptop-cache-*")
                    )
                    (cache / ".disposable-v1").write_text(
                        "invalid", encoding="ascii"
                    )

            harness.control.checkpoint = (
                damage_after_final_heartbeat  # type: ignore[method-assign]
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            cache = next(root.glob(".performing-fire-laptop-cache-*"))
            self.assertTrue(cache.is_dir())
            self.assertEqual(list(cache.iterdir()), [])

    def test_cache_root_is_expanded_once_for_reaping_and_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            fake_home = sandbox / "home"
            working_directory = sandbox / "working"
            fake_home.mkdir()
            working_directory.mkdir()
            original_directory = Path.cwd()
            try:
                os.chdir(working_directory)
                with mock.patch.dict(
                    os.environ, {"HOME": str(fake_home)}
                ):
                    harness = Harness(Path("~/trusted-cache"))
                    self.assertEqual(
                        harness.worker.cache_root,
                        fake_home / "trusted-cache",
                    )
                    self.assertIsNotNone(
                        harness.worker.run_once(capability())
                    )
            finally:
                os.chdir(original_directory)

            self.assertTrue((fake_home / "trusted-cache").is_dir())
            self.assertFalse((working_directory / "~").exists())

    def test_receipt_commit_must_return_the_exact_requested_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.receipts.upsert_override = input_receipt()
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(len(harness.storage.created), 1)
            self.assertEqual(
                harness.control.blockers[0]["code"],
                "receipt_commit_conflict",
            )
            self.assertFalse(
                any(
                    "/manifests/" in key
                    for key in harness.storage.created
                )
            )

    def test_renewed_lease_updates_active_cache_before_reaping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = Harness(root)
            harness.control.heartbeat_extension_seconds = 10 * 60
            harness.transformer.reap_root = root
            harness.transformer.reap_at = NOW + timedelta(minutes=6)
            self.assertIsNotNone(harness.worker.run_once(capability()))

    def test_lost_create_response_recovers_only_from_matching_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.storage.create_failure = TimeoutError("synthetic")
            harness.storage.persist_before_create_failure = True
            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            assert result is not None
            output_receipt = harness.receipts.get_corpus_receipt_by_key(
                str(result["output_object_key"])
            )
            assert output_receipt is not None
            self.assertEqual(
                output_receipt["create_disposition"],
                "reused_after_ambiguous_create",
            )

    def test_restart_reuses_exact_outputs_and_completed_result_without_reprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            first = harness.worker.run_once(capability())
            self.assertIsNotNone(first)
            first_create_count = len(harness.storage.created)
            first_transform_count = harness.transformer.calls

            second = harness.worker.run_once(capability())
            self.assertEqual(second, first)
            self.assertEqual(len(harness.storage.created), first_create_count)
            self.assertEqual(harness.transformer.calls, first_transform_count)

            assert first is not None
            harness.control.job_value = job(
                derivation_authority_sha256="d" * 64
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "completed_result_conflict",
            )
            harness.control.job_value = job()
            harness.receipts.tombstones.add(
                str(first["output_object_key"])
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "output_object_tombstoned",
            )
            self.assertEqual(harness.transformer.calls, first_transform_count)

    def test_result_identifier_binds_every_validated_result_fact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            assert result is not None
            corrupted = copy.deepcopy(result)
            corrupted["cpu_seconds"] = float(corrupted["cpu_seconds"]) + 1

            with self.assertRaises(TrustedLaptopWorkerError):
                validate_trusted_laptop_record(corrupted)

    def test_final_metrics_include_manifest_work_and_recheck_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))

            def advance_after_manifest_receipt(
                record: dict[str, object],
            ) -> None:
                if record.get("object_kind") == "manifest":
                    harness.clock.advance(2)

            harness.receipts.before_upsert = advance_after_manifest_receipt
            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            assert result is not None
            transform_checkpoint = next(
                event
                for event in harness.control.events
                if event.get("record_type") == "trusted_laptop_checkpoint"
                and event.get("stage") == "transform_verified"
            )
            self.assertGreaterEqual(float(result["elapsed_seconds"]), 2)
            self.assertGreater(
                float(result["elapsed_seconds"]),
                float(transform_checkpoint["elapsed_seconds"]),
            )
            self.assertGreater(
                float(result["working_disk_bytes"]),
                float(transform_checkpoint["working_disk_bytes"]),
            )

        with tempfile.TemporaryDirectory() as directory:
            exceeded = Harness(Path(directory))
            exceeded.control.job_value = job(maximum_elapsed_seconds=1)

            def exceed_after_manifest_receipt(
                record: dict[str, object],
            ) -> None:
                if record.get("object_kind") == "manifest":
                    exceeded.clock.advance(2)

            exceeded.receipts.before_upsert = exceed_after_manifest_receipt
            self.assertIsNone(exceeded.worker.run_once(capability()))
            self.assertEqual(
                exceeded.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_interrupted_output_checkpoint_resumes_without_duplicate_transform_or_create(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = "output_verified"
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(len(harness.storage.created), 1)
            self.assertEqual(
                harness.control.blockers[0]["code"], "pairing_disconnected"
            )

            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(attempt=2)
            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(len(harness.storage.created), 2)

    def test_output_resume_accumulates_elapsed_before_manifest_create(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_elapsed_seconds=10)
            harness.control.fail_checkpoint_stage_after_store = (
                "output_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(len(harness.storage.created), 1)
            checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type") == "trusted_laptop_checkpoint"
                and event.get("stage") == "output_verified"
            )
            checkpoint["elapsed_seconds"] = 5
            resume_state = {
                key: value
                for key, value in checkpoint.items()
                if key
                not in {
                    "schema_version",
                    "record_type",
                    "checkpoint_id",
                    "resume_state_sha256",
                    "recorded_at",
                }
            }
            resume_hash = trusted_laptop_worker._digest(resume_state)
            checkpoint["resume_state_sha256"] = resume_hash
            checkpoint["checkpoint_id"] = (
                "checkpoint_"
                + trusted_laptop_worker._digest(
                    {"resume_state_sha256": resume_hash}
                )
            )

            def advance_before_manifest_head(key: str, call: int) -> None:
                if "/manifests/" in key and call == 1:
                    harness.clock.advance(8)

            harness.storage.before_head = advance_before_manifest_head
            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(
                attempt=2,
                maximum_elapsed_seconds=10,
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(len(harness.storage.created), 1)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_resumed_checkpoint_metrics_must_fit_the_job_bounds(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = (
                "output_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type") == "trusted_laptop_checkpoint"
                and event.get("stage") == "output_verified"
            )
            checkpoint["cpu_seconds"] = 61
            resume_state = {
                key: value
                for key, value in checkpoint.items()
                if key
                not in {
                    "schema_version",
                    "record_type",
                    "checkpoint_id",
                    "resume_state_sha256",
                    "recorded_at",
                }
            }
            resume_hash = trusted_laptop_worker._digest(resume_state)
            checkpoint["resume_state_sha256"] = resume_hash
            checkpoint["checkpoint_id"] = (
                "checkpoint_"
                + trusted_laptop_worker._digest(
                    {"resume_state_sha256": resume_hash}
                )
            )
            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(attempt=2)

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(len(harness.storage.created), 1)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "cpu_limit_exceeded",
            )

    def test_resumed_output_receipt_must_match_checkpoint_hash_and_size(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = (
                "output_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))

            checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type") == "trusted_laptop_checkpoint"
                and event.get("stage") == "output_verified"
            )
            checkpoint["output_sha256"] = "d" * 64
            checkpoint["output_byte_size"] = int(
                checkpoint["output_byte_size"]
            ) + 1
            resume_state = {
                key: value
                for key, value in checkpoint.items()
                if key
                not in {
                    "schema_version",
                    "record_type",
                    "checkpoint_id",
                    "resume_state_sha256",
                    "recorded_at",
                }
            }
            resume_hash = trusted_laptop_worker._digest(resume_state)
            checkpoint["resume_state_sha256"] = resume_hash
            checkpoint["checkpoint_id"] = (
                "checkpoint_"
                + trusted_laptop_worker._digest(
                    {"resume_state_sha256": resume_hash}
                )
            )

            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(attempt=2)
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(len(harness.storage.created), 1)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "resume_receipt_mismatch",
            )

    def test_resumed_manifest_must_bind_this_exact_transformation(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            assert result is not None
            original_receipt = harness.receipts.get_corpus_receipt(
                str(result["manifest_receipt_id"])
            )
            assert original_receipt is not None
            foreign_body = b'{"synthetic":"foreign-manifest"}'
            foreign_sha256 = hashlib.sha256(foreign_body).hexdigest()
            foreign_key = manifest_object_key(
                "performing-fire/",
                SOURCE_ID,
                ASSET_ID,
                "manifest_" + "f" * 64,
                foreign_sha256,
            )
            foreign_receipt_value = {
                key: value
                for key, value in original_receipt.items()
                if key != "receipt_id"
            }
            foreign_receipt_value.update(
                {
                    "object_key": foreign_key,
                    "byte_size": len(foreign_body),
                    "sha256": foreign_sha256,
                }
            )
            foreign_receipt = bind_object_receipt(foreign_receipt_value)
            harness.storage.objects[foreign_key] = {
                "body": foreign_body,
                "byte_size": len(foreign_body),
                "media_type": "application/json",
                "sha256": foreign_sha256,
            }
            harness.receipts.receipts_by_id[
                str(foreign_receipt["receipt_id"])
            ] = foreign_receipt
            harness.receipts.receipts_by_key[foreign_key] = foreign_receipt
            corrupted = copy.deepcopy(result)
            corrupted["manifest_receipt_id"] = foreign_receipt["receipt_id"]
            corrupted["manifest_object_key"] = foreign_key
            corrupted["result_id"] = trusted_laptop_worker._result_id(
                corrupted
            )
            harness.control.completed[str(result["job_id"])] = corrupted

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "resume_manifest_mismatch",
            )

    def test_interrupted_transform_binds_resume_to_exact_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.created, [])

            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.transformer.output = b'{"synthetic":"changed"}'
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "transformation_resume_mismatch",
            )

    def test_resumed_transform_accumulates_prior_resource_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(attempt=2)

            result = harness.worker.run_once(capability())
            self.assertIsNotNone(result)
            assert result is not None
            self.assertGreaterEqual(float(result["cpu_seconds"]), 4)

        with tempfile.TemporaryDirectory() as directory:
            cpu_exhausted = Harness(Path(directory))
            cpu_exhausted.control.job_value = job(maximum_cpu_seconds=3)
            cpu_exhausted.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(
                cpu_exhausted.worker.run_once(capability())
            )
            cpu_exhausted.control.fail_checkpoint_stage_after_store = None
            cpu_exhausted.control.reconcile_latest_attempt()
            cpu_exhausted.control.job_value = job(
                attempt=2,
                maximum_cpu_seconds=3,
            )

            self.assertIsNone(
                cpu_exhausted.worker.run_once(capability())
            )
            self.assertEqual(
                cpu_exhausted.control.blockers[-1]["code"],
                "cpu_limit_exceeded",
            )

        with tempfile.TemporaryDirectory() as directory:
            elapsed_exhausted = Harness(Path(directory))
            elapsed_exhausted.control.job_value = job(
                maximum_elapsed_seconds=1
            )
            elapsed_exhausted.transformer.sleep_seconds = 0.6
            elapsed_exhausted.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(
                elapsed_exhausted.worker.run_once(capability())
            )
            elapsed_exhausted.control.fail_checkpoint_stage_after_store = None
            elapsed_exhausted.control.reconcile_latest_attempt()
            elapsed_exhausted.control.job_value = job(
                attempt=2,
                maximum_elapsed_seconds=1,
            )

            self.assertIsNone(
                elapsed_exhausted.worker.run_once(capability())
            )
            self.assertEqual(
                elapsed_exhausted.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_failed_transform_replay_preserves_new_cpu_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.transformer.cpu_seconds = 0
            harness.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(attempt=2)
            harness.transformer.busy_seconds = 0.3
            harness.transformer.failure_after_work = RuntimeError(
                "synthetic replay failure"
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type")
                == "trusted_laptop_checkpoint"
                and event.get("stage") == "transform_verified"
                and event.get("attempt_open") is False
            )
            self.assertGreater(float(checkpoint["cpu_seconds"]), 0.1)

    def test_failed_transform_consumption_is_durable_across_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(
                maximum_output_bytes=10,
                maximum_elapsed_seconds=1,
            )
            harness.transformer.output = b"ok"
            harness.transformer.advance_seconds = 0
            harness.transformer.sleep_seconds = 0.6
            harness.transformer.failure_after_work = RuntimeError(
                "synthetic transform failure"
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "transformer_failed",
            )
            failed_checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type")
                == "trusted_laptop_checkpoint"
                and event.get("stage") == "attempt_failed"
            )
            schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "v1"
                    / "trusted-laptop-worker.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(failed_checkpoint)
            self.assertEqual(
                validate_trusted_laptop_record(failed_checkpoint),
                failed_checkpoint,
            )
            self.assertGreater(
                float(failed_checkpoint["elapsed_seconds"]),
                0.5,
            )

            harness.control.job_value = job(
                attempt=2,
                maximum_output_bytes=10,
                maximum_elapsed_seconds=1,
            )
            harness.transformer.output = b"ok"
            harness.transformer.failure_after_work = None

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertIn(harness.transformer.calls, {1, 2})
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_unrecorded_transform_failure_cannot_reset_retry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_attempts=3)
            harness.transformer.failure_after_work = RuntimeError(
                "synthetic transform failure"
            )
            original_checkpoint = harness.control.checkpoint

            def drop_failure_checkpoint(value: dict[str, object]) -> None:
                if value["stage"] == "attempt_failed":
                    raise ConnectionError("synthetic disconnect")
                original_checkpoint(value)

            harness.control.checkpoint = (
                drop_failure_checkpoint  # type: ignore[method-assign]
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            harness.control.job_value = job(
                attempt=2,
                maximum_attempts=3,
            )
            harness.transformer.failure_after_work = None

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "attempt_unreconciled",
            )
            self.assertEqual(
                harness.control.blockers[-1][
                    "required_authority_class"
                ],
                "corpus_operator",
            )
            harness.control.job_value = job(
                attempt=3,
                maximum_attempts=3,
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "attempt_unreconciled",
            )

    def test_manifest_failure_consumes_elapsed_budget_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = job(maximum_elapsed_seconds=2)

            def fail_lineage(record: dict[str, object]) -> None:
                if record.get("record_type") == "derivation_manifest":
                    harness.clock.advance(3)
                    raise ConnectionError("synthetic lineage disconnect")

            harness.receipts.before_upsert = fail_lineage
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "manifest_commit_failed",
            )
            failed_checkpoint = next(
                event
                for event in reversed(harness.control.events)
                if event.get("record_type")
                == "trusted_laptop_checkpoint"
                and event.get("stage") == "output_verified"
            )
            self.assertGreaterEqual(
                float(failed_checkpoint["elapsed_seconds"]),
                3,
            )
            created = list(harness.storage.created)
            harness.receipts.before_upsert = None
            harness.control.job_value = job(
                attempt=2,
                maximum_elapsed_seconds=2,
            )

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(harness.storage.created, created)
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "elapsed_limit_exceeded",
            )

    def test_completed_result_must_fit_the_job_resource_bounds(self) -> None:
        from performing_fire_corpus import trusted_laptop_worker

        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            self.assertIsNotNone(harness.worker.run_once(capability()))
            completed = harness.control.completed[
                "job_synthetic_laptop_001"
            ]
            completed["cpu_seconds"] = 61
            completed["result_id"] = trusted_laptop_worker._result_id(
                completed
            )
            harness.control.job_value = job(attempt=2)

            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "cpu_limit_exceeded",
            )

    def test_resume_checkpoint_is_bound_to_the_entire_job_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.fail_checkpoint_stage_after_store = (
                "transform_verified"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)

            harness.control.fail_checkpoint_stage_after_store = None
            harness.control.reconcile_latest_attempt()
            harness.control.job_value = job(
                tool_version="1.0.1",
                transformation_id=transformation_id(
                    tool_version="1.0.1"
                ),
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            self.assertEqual(harness.transformer.calls, 1)
            self.assertEqual(harness.storage.created, [])
            self.assertEqual(
                harness.control.blockers[-1]["code"],
                "resume_checkpoint_mismatch",
            )

    def test_stale_worker_cache_is_reaped_but_unowned_paths_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / ".performing-fire-laptop-cache-deadbeef"
            stale.mkdir()
            (stale / ".disposable-v1").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "record_type": "disposable_trusted_laptop_cache",
                        "cache_id": "cache_deadbeef",
                        "lease_expires_at": utc(
                            NOW - timedelta(minutes=1)
                        ),
                    }
                ),
                encoding="utf-8",
            )
            (stale / "input.bin").write_bytes(INPUT)
            unrelated = root / "keep"
            unrelated.mkdir()
            (unrelated / "user.txt").write_text("keep", encoding="utf-8")

            self.assertEqual(
                reap_stale_disposable_caches(root, now=NOW), 1
            )
            self.assertFalse(stale.exists())
            self.assertEqual((unrelated / "user.txt").read_text(), "keep")

            active = root / ".performing-fire-laptop-cache-cafebabe"
            active.mkdir()
            (active / ".disposable-v1").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "record_type": "disposable_trusted_laptop_cache",
                        "cache_id": "cache_cafebabe",
                        "lease_expires_at": utc(
                            NOW + timedelta(minutes=5)
                        ),
                    }
                ),
                encoding="utf-8",
            )
            (active / "input.bin").write_bytes(INPUT)
            self.assertEqual(
                reap_stale_disposable_caches(root, now=NOW), 0
            )
            self.assertTrue((active / "input.bin").is_file())

    def test_dynamic_transit_and_blockers_never_contain_content_paths_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.transformer.failure = RuntimeError(
                "source bytes /tmp/private secret@example.invalid token=synthetic"
            )
            self.assertIsNone(harness.worker.run_once(capability()))
            serialized = json.dumps(harness.control.events, sort_keys=True)
            self.assertNotIn(INPUT.decode(), serialized)
            self.assertNotIn("/tmp/", serialized)
            self.assertNotIn("secret@", serialized)
            self.assertNotIn("token=", serialized)
            self.assertNotIn(str(Path(directory)), serialized)
            self.assertEqual(
                harness.control.blockers[0]["code"], "transformer_failed"
            )
            validate_trusted_laptop_record(harness.control.blockers[0])

    def test_capability_concurrency_retry_and_job_contracts_fail_before_claim_or_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            concurrency = Harness(Path(directory))
            with self.assertRaises(TrustedLaptopWorkerError):
                concurrency.worker.run_once(capability(max_concurrency=2))
            self.assertEqual(concurrency.control.events, [])

            retry = Harness(Path(directory))
            retry.control.job_value = job(attempt=3, maximum_attempts=2)
            self.assertIsNone(retry.worker.run_once(capability()))
            self.assertEqual(retry.storage.downloads, [])
            self.assertEqual(
                retry.control.blockers[0]["code"], "retry_budget_exhausted"
            )

            mismatch = Harness(Path(directory))
            mismatch.control.job_value = job(required_capability="transcription")
            self.assertIsNone(mismatch.worker.run_once(capability()))
            self.assertEqual(mismatch.storage.downloads, [])
            self.assertEqual(
                mismatch.control.blockers[0]["code"],
                "required_capability_unavailable",
            )

            disk = Harness(Path(directory))
            disk.control.job_value = job(maximum_disk_bytes=4096)
            self.assertIsNone(disk.worker.run_once(capability()))
            self.assertEqual(disk.storage.downloads, [])
            self.assertEqual(
                disk.control.blockers[0]["code"],
                "job_disk_bound_inconsistent",
            )

            long_prefix = "p" * 199 + "/"
            long_source = "source_" + "s" * 100
            long_asset = "asset_" + "a" * 100
            long_raw_key = raw_object_key(
                long_prefix,
                long_source,
                long_asset,
                INPUT_SHA256,
            )
            key_capacity = Harness(Path(directory))
            key_capacity.control.job_value = job(
                source_id=long_source,
                asset_id=long_asset,
                namespace_prefix=long_prefix,
                input_object_key=long_raw_key,
            )
            with self.assertRaises(TrustedLaptopWorkerError):
                key_capacity.worker.run_once(capability())
            self.assertEqual(key_capacity.storage.downloads, [])
            self.assertEqual(key_capacity.storage.head_counts, {})

    def test_worker_rejects_a_concurrent_run_on_the_same_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(Path(directory))
            harness.control.job_value = None
            entered = threading.Event()
            release = threading.Event()
            harness.control.pair_entered = entered
            harness.control.pair_release = release
            outcomes: list[dict[str, object] | None] = []
            failures: list[BaseException] = []

            def first_run() -> None:
                try:
                    outcomes.append(harness.worker.run_once(capability()))
                except BaseException as exc:
                    failures.append(exc)

            thread = threading.Thread(target=first_run)
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            try:
                with self.assertRaisesRegex(
                    TrustedLaptopWorkerError,
                    "already has an active run",
                ):
                    harness.worker.run_once(capability())
            finally:
                release.set()
                thread.join(timeout=2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(outcomes, [None])


if __name__ == "__main__":
    unittest.main()
