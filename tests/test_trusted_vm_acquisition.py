from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.acquisition import HTTPResponse
from performing_fire_corpus.cli import main
from performing_fire_corpus.ledger import Ledger, LedgerError
from performing_fire_corpus.storage import R2Config, StorageError
from performing_fire_corpus.trusted_vm import (
    TrustedVMRunError,
    acquire_one_to_r2,
    load_trusted_vm_approval,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "a" * 32
ENVIRONMENT = {
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID,
    "R2_ACCESS_KEY_ID": "synthetic-access",
    "R2_SECRET_ACCESS_KEY": "synthetic-secret",
    "R2_ENDPOINT": f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
}
PUBLIC_URL = "https://antiegg.kr/media/synthetic.mp4"
ROBOTS_URL = "https://antiegg.kr/robots.txt"


def approval_value() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "trusted_vm_acquisition_approval",
        "asset_id": "asset_synthetic_video_001",
        "source_id": "source_synthetic_001",
        "public_url": PUBLIC_URL,
        "rights": {
            "schema_version": 1,
            "record_type": "rights",
            "rights_id": "rights_synthetic_video_001",
            "asset_id": "asset_synthetic_video_001",
            "state": "approved",
            "decision_reason": "Approved synthetic public proof asset.",
            "decision_at": "2026-07-23T10:00:00Z",
        },
        "expected_mime_type": "video/mp4",
        "maximum_bytes": 32,
        "proof_window": {
            "starts_at": "2026-07-23T11:00:00Z",
            "ends_at": "2026-07-23T13:00:00Z",
        },
        "staging_bucket": "synthetic-bucket",
        "staging_prefix": "proof/",
        "cleanup_decision": "delete_after_verification",
        "cleanup_deadline": "2026-07-23T12:30:00Z",
        "evidence_ref": "evidence:issue-18",
    }


class FakeRobots:
    def __init__(
        self,
        *,
        body: bytes = b"User-agent: *\nAllow: /\n",
        status: int = 200,
        mime_type: str = "text/plain",
        final_url: str = ROBOTS_URL,
    ) -> None:
        self.body = body
        self.status = status
        self.mime_type = mime_type
        self.final_url = final_url
        self.calls: list[tuple[str, float, int]] = []

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse:
        self.calls.append((url, timeout_seconds, max_response_bytes))
        return HTTPResponse(
            url=self.final_url,
            status=self.status,
            mime_type=self.mime_type,
            body=self.body,
            declared_bytes=len(self.body),
            observed_bytes=len(self.body),
        )


class FakeAssetResponse:
    def __init__(
        self,
        body: bytes = b"data",
        *,
        media_type: str = "video/mp4",
        content_length: int | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.body = body
        self.media_type = media_type
        self.content_length = len(body) if content_length is None else content_length
        self.final_url = PUBLIC_URL
        self.failure = failure

    def iter_bytes(self, chunk_size: int):
        del chunk_size
        yield self.body
        if self.failure is not None:
            raise self.failure


class FakeAssetHTTP:
    def __init__(self, response: FakeAssetResponse | None = None) -> None:
        self.response = response or FakeAssetResponse()
        self.calls: list[str] = []

    def open(self, url: str) -> FakeAssetResponse:
        self.calls.append(url)
        return self.response


class FakeStorage:
    def __init__(self, *, scope_ready: bool = True) -> None:
        self.scope_ready = scope_ready
        self.objects: dict[str, dict[str, object]] = {}
        self.probes: list[tuple[str, str]] = []
        self.uploads: list[str] = []
        self.deletes: list[str] = []
        self.fail_cleanup = False
        self.conflict_after_upload = False
        self.lose_create_response = False

    def probe_scope(self, bucket: str, staging_prefix: str) -> bool:
        self.probes.append((bucket, staging_prefix))
        return self.scope_ready

    def head_object(self, key: str) -> dict[str, object] | None:
        value = self.objects.get(key)
        if value is not None and self.conflict_after_upload:
            return {**value, "sha256": "f" * 64}
        return value

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        self.uploads.append(key)
        if key in self.objects:
            return False
        self.objects[key] = {
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
        }
        self.uploaded_body = path.read_bytes()
        if self.lose_create_response:
            raise StorageError(
                "r2_create_failed",
                "Retry the bounded exact-key operation safely.",
            )
        return True

    def delete_exact_object(self, key: str) -> bool:
        self.deletes.append(key)
        if self.fail_cleanup:
            raise StorageError("r2_delete_failed", "Retry exact-key cleanup safely.")
        self.objects.pop(key, None)
        return True


class LostCreateResponseStorage(FakeStorage):
    def __init__(self, *, state: str) -> None:
        super().__init__()
        self.state = state

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        self.uploads.append(key)
        self.uploaded_body = path.read_bytes()
        if self.state == "conflict":
            self.objects[key] = {
                "byte_size": byte_size,
                "media_type": media_type,
                "sha256": "f" * 64,
            }
        elif self.state != "absent":
            raise AssertionError("unsupported synthetic create state")
        raise StorageError(
            "r2_create_failed",
            "Retry the bounded exact-key operation safely.",
        )


class TrustedVMAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        guards = (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("live network access is forbidden"),
            ),
            patch.object(
                socket,
                "getaddrinfo",
                side_effect=AssertionError("live DNS access is forbidden"),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("live socket access is forbidden"),
            ),
        )
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.approval_path = self.root / "approval.json"

    def write_approval(self, value: dict[str, object] | None = None) -> Path:
        self.approval_path.write_text(
            json.dumps(value or approval_value()), encoding="utf-8"
        )
        return self.approval_path

    def load(self, value: dict[str, object] | None = None):
        return load_trusted_vm_approval(self.write_approval(value), now=NOW)

    def run_acquisition(
        self,
        *,
        value: dict[str, object] | None = None,
        environment: dict[str, str] | None = None,
        storage: FakeStorage | None = None,
        robots: FakeRobots | None = None,
        asset_http: FakeAssetHTTP | None = None,
    ):
        selected_storage = storage or FakeStorage()
        selected_robots = robots or FakeRobots()
        selected_asset = asset_http or FakeAssetHTTP()
        result = acquire_one_to_r2(
            self.load(value),
            config=R2Config("synthetic-bucket", "proof/"),
            ledger_path=self.root / "ledger.sqlite3",
            cache_directory=self.root / "cache",
            sanitized_output=self.root / "receipts",
            environ=ENVIRONMENT if environment is None else environment,
            storage_client=selected_storage,
            robots_transport=selected_robots,
            asset_http_client=selected_asset,
            now=NOW,
        )
        return result, selected_storage, selected_robots, selected_asset

    def test_every_approval_gate_fails_before_network(self) -> None:
        mutations = {
            "extra asset selector": lambda value: value.update(
                {"asset_ids": ["asset_other"]}
            ),
            "invalid source": lambda value: value.update({"source_id": "source_"}),
            "non-https URL": lambda value: value.update(
                {"public_url": "http://antiegg.kr/media/synthetic.mp4"}
            ),
            "rights mismatch": lambda value: value["rights"].update(
                {"asset_id": "asset_other"}
            ),
            "multiple MIME types": lambda value: value.update(
                {"expected_mime_type": ["video/mp4", "image/jpeg"]}
            ),
            "zero bytes": lambda value: value.update({"maximum_bytes": 0}),
            "expired proof": lambda value: value["proof_window"].update(
                {"ends_at": "2026-07-23T11:30:00Z"}
            ),
            "broad prefix": lambda value: value.update({"staging_prefix": "/"}),
            "retention": lambda value: value.update(
                {"cleanup_decision": "retain"}
            ),
            "expired cleanup": lambda value: value.update(
                {"cleanup_deadline": "2026-07-23T11:30:00Z"}
            ),
        }
        for name, mutate in mutations.items():
            value = copy.deepcopy(approval_value())
            mutate(value)
            robots = FakeRobots()
            asset = FakeAssetHTTP()
            storage = FakeStorage()
            with self.subTest(name=name), self.assertRaises(TrustedVMRunError):
                acquire_one_to_r2(
                    load_trusted_vm_approval(
                        self.write_approval(value), now=NOW
                    ),
                    config=R2Config("synthetic-bucket", "proof/"),
                    ledger_path=self.root / f"{name}.sqlite3",
                    cache_directory=self.root / f"{name}-cache",
                    sanitized_output=self.root / f"{name}-receipts",
                    environ=ENVIRONMENT,
                    storage_client=storage,
                    robots_transport=robots,
                    asset_http_client=asset,
                    now=NOW,
                )
            self.assertEqual([], robots.calls)
            self.assertEqual([], asset.calls)
            self.assertEqual([], storage.probes)

    def test_configuration_and_readiness_fail_before_public_requests(self) -> None:
        for environment, config, code in (
            (
                {**ENVIRONMENT, "R2_ENDPOINT": "https://example.invalid"},
                R2Config("synthetic-bucket", "proof/"),
                "r2_configuration_invalid",
            ),
            (
                ENVIRONMENT,
                R2Config("different-bucket", "proof/"),
                "approval_scope_mismatch",
            ),
            (
                ENVIRONMENT,
                R2Config("synthetic-bucket", "proof/"),
                "r2_not_ready",
            ),
        ):
            storage = FakeStorage(scope_ready=code != "r2_not_ready")
            robots = FakeRobots()
            asset = FakeAssetHTTP()
            with self.subTest(code=code), self.assertRaises(
                TrustedVMRunError
            ) as raised:
                acquire_one_to_r2(
                    self.load(),
                    config=config,
                    ledger_path=self.root / f"{code}.sqlite3",
                    cache_directory=self.root / f"{code}-cache",
                    sanitized_output=self.root / f"{code}-receipts",
                    environ=environment,
                    storage_client=storage,
                    robots_transport=robots,
                    asset_http_client=asset,
                    now=NOW,
                )
            self.assertEqual(code, raised.exception.code)
            self.assertEqual([], robots.calls)
            self.assertEqual([], asset.calls)
            manifest = json.loads(
                (self.root / f"{code}-receipts" / "manifest.json").read_text()
            )
            self.assertEqual("blocked", manifest["status"])

    def test_robots_denial_stops_before_asset_and_persists_request_fact(self) -> None:
        robots = FakeRobots(body=b"User-agent: *\nDisallow: /\n")
        asset = FakeAssetHTTP()
        with self.assertRaises(TrustedVMRunError) as raised:
            self.run_acquisition(robots=robots, asset_http=asset)
        self.assertEqual("robots_denied", raised.exception.code)
        self.assertEqual(1, len(robots.calls))
        self.assertEqual([], asset.calls)
        fact = json.loads(
            (self.root / "receipts" / "request-fact.json").read_text()
        )
        self.assertEqual("robots_denied", fact["outcome_code"])
        self.assertNotIn("Disallow", json.dumps(fact))

    def test_mime_size_interruption_and_verification_conflict_are_blocked(self) -> None:
        cases = (
            (
                FakeAssetResponse(media_type="image/jpeg"),
                FakeStorage(),
                "media_type_mismatch",
            ),
            (
                FakeAssetResponse(body=b"x" * 33),
                FakeStorage(),
                "size_limit_exceeded",
            ),
            (
                FakeAssetResponse(
                    failure=ConnectionError("private signed response body")
                ),
                FakeStorage(),
                "transfer_interrupted",
            ),
            (
                FakeAssetResponse(),
                FakeStorage(),
                "object_conflict",
            ),
        )
        cases[-1][1].conflict_after_upload = True
        for response, storage, code in cases:
            output = self.root / f"{code}-receipts"
            with self.subTest(code=code), self.assertRaises(
                TrustedVMRunError
            ) as raised:
                acquire_one_to_r2(
                    self.load(),
                    config=R2Config("synthetic-bucket", "proof/"),
                    ledger_path=self.root / f"{code}.sqlite3",
                    cache_directory=self.root / f"{code}-cache",
                    sanitized_output=output,
                    environ=ENVIRONMENT,
                    storage_client=storage,
                    robots_transport=FakeRobots(),
                    asset_http_client=FakeAssetHTTP(response),
                    now=NOW,
                )
            self.assertEqual(code, raised.exception.code)
            self.assertEqual([], list((self.root / f"{code}-cache").glob("*")))
            rendered = (output / "manifest.json").read_text()
            self.assertNotIn("private signed response body", rendered)
            if code == "object_conflict":
                self.assertTrue((output / "object.json").is_file())
                self.assertEqual([], storage.deletes)

    def test_ledger_failure_after_upload_verifies_and_cleans_exact_key(self) -> None:
        original_upsert = Ledger.upsert

        def fail_object_receipt(ledger, record, *, operation_id=None):
            if record.get("record_type") == "object":
                raise LedgerError("synthetic receipt failure")
            return original_upsert(
                ledger,
                record,
                operation_id=operation_id,
            )

        storage = FakeStorage()
        with patch.object(Ledger, "upsert", new=fail_object_receipt):
            with self.assertRaises(TrustedVMRunError) as raised:
                self.run_acquisition(storage=storage)

        self.assertEqual("receipt_conflict", raised.exception.code)
        self.assertEqual(storage.uploads, storage.deletes)
        self.assertEqual({}, storage.objects)
        output = self.root / "receipts"
        self.assertTrue((output / "object.json").is_file())
        self.assertTrue((output / "verification.json").is_file())
        self.assertTrue((output / "cleanup.json").is_file())
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual("blocked", manifest["status"])

    def test_lost_create_response_verifies_and_cleans_exact_key(self) -> None:
        storage = FakeStorage()
        storage.lose_create_response = True

        with self.assertRaises(TrustedVMRunError) as raised:
            self.run_acquisition(storage=storage)

        self.assertEqual("transfer_interrupted", raised.exception.code)
        self.assertEqual(storage.uploads, storage.deletes)
        self.assertEqual({}, storage.objects)
        output = self.root / "receipts"
        self.assertTrue((output / "object.json").is_file())
        self.assertTrue((output / "verification.json").is_file())
        self.assertTrue((output / "cleanup.json").is_file())
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual("blocked", manifest["status"])

    def test_lost_create_response_absent_records_attempt_without_object_claim(self) -> None:
        storage = LostCreateResponseStorage(state="absent")
        robots = FakeRobots()
        asset = FakeAssetHTTP()

        with self.assertRaises(TrustedVMRunError) as raised:
            self.run_acquisition(
                storage=storage,
                robots=robots,
                asset_http=asset,
            )

        self.assertEqual("transfer_interrupted", raised.exception.code)
        self.assertEqual({}, storage.objects)
        self.assertEqual([], storage.deletes)
        self.assertEqual(1, len(robots.calls))
        self.assertEqual([PUBLIC_URL], asset.calls)
        output = self.root / "receipts"
        self.assertFalse((output / "object.json").exists())
        self.assertFalse((output / "verification.json").exists())
        self.assertFalse((output / "cleanup.json").exists())
        attempt = json.loads((output / "upload-attempt.json").read_text())
        self.assertEqual("absent_after_unknown_create", attempt["outcome_code"])
        self.assertEqual("absent", attempt["state"])

    def test_lost_create_response_conflict_records_attempt_without_delete(self) -> None:
        storage = LostCreateResponseStorage(state="conflict")
        robots = FakeRobots()
        asset = FakeAssetHTTP()

        with self.assertRaises(TrustedVMRunError) as raised:
            self.run_acquisition(
                storage=storage,
                robots=robots,
                asset_http=asset,
            )

        self.assertEqual("transfer_interrupted", raised.exception.code)
        self.assertEqual(1, len(storage.objects))
        self.assertEqual([], storage.deletes)
        self.assertEqual(1, len(robots.calls))
        self.assertEqual([PUBLIC_URL], asset.calls)
        output = self.root / "receipts"
        self.assertFalse((output / "object.json").exists())
        self.assertFalse((output / "verification.json").exists())
        self.assertFalse((output / "cleanup.json").exists())
        attempt = json.loads((output / "upload-attempt.json").read_text())
        self.assertEqual(
            "metadata_conflict_after_unknown_create",
            attempt["outcome_code"],
        )
        self.assertEqual("conflict", attempt["state"])

    def test_success_uploads_verifies_deletes_and_emits_sanitized_artifacts(self) -> None:
        manifest, storage, robots, asset = self.run_acquisition()
        self.assertEqual("complete", manifest["status"])
        self.assertEqual(1, len(robots.calls))
        self.assertEqual([PUBLIC_URL], asset.calls)
        self.assertEqual(1, len(storage.uploads))
        self.assertEqual(storage.uploads, storage.deletes)
        self.assertEqual({}, storage.objects)
        self.assertEqual([], list((self.root / "cache").glob("*")))
        self.assertEqual(
            {
                "cleanup.json",
                "manifest.json",
                "object.json",
                "readiness.json",
                "request-fact.json",
                "verification.json",
            },
            {path.name for path in (self.root / "receipts").iterdir()},
        )
        verification = json.loads(
            (self.root / "receipts" / "verification.json").read_text()
        )
        cleanup = json.loads(
            (self.root / "receipts" / "cleanup.json").read_text()
        )
        self.assertEqual(storage.uploads[0], verification["object_key"])
        self.assertEqual(storage.uploads[0], cleanup["object_key"])
        self.assertEqual("absent", cleanup["state"])
        rendered = "\n".join(
            path.read_text()
            for path in sorted((self.root / "receipts").iterdir())
        )
        for secret in ENVIRONMENT.values():
            self.assertNotIn(secret, rendered)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("User-agent", rendered)
        self.assertNotIn("data", rendered)

    def test_unrelated_ambient_environment_values_do_not_block_safe_receipts(self) -> None:
        environment = {**ENVIRONMENT, "UNRELATED_FLAG": "1"}
        manifest, _, _, _ = self.run_acquisition(environment=environment)
        self.assertEqual("complete", manifest["status"])

    def test_cleanup_failure_is_durable_and_complete_resume_is_network_free(self) -> None:
        storage = FakeStorage()
        storage.fail_cleanup = True
        with self.assertRaises(TrustedVMRunError) as raised:
            self.run_acquisition(storage=storage)
        self.assertEqual("cleanup_failed", raised.exception.code)
        self.assertEqual(1, len(storage.objects))
        self.assertTrue((self.root / "receipts" / "object.json").is_file())
        self.assertTrue((self.root / "receipts" / "verification.json").is_file())

        retry_root = self.root / "resume"
        original_output = self.root / "receipts"
        retry_root.mkdir()
        for path in original_output.iterdir():
            if path.name != "manifest.json":
                (retry_root / path.name).write_bytes(path.read_bytes())
        storage.fail_cleanup = False
        acquire_one_to_r2(
            self.load(),
            config=R2Config("synthetic-bucket", "proof/"),
            ledger_path=self.root / "ledger.sqlite3",
            cache_directory=self.root / "resume-cache",
            sanitized_output=retry_root,
            environ=ENVIRONMENT,
            storage_client=storage,
            robots_transport=FakeRobots(),
            asset_http_client=FakeAssetHTTP(),
            now=NOW,
        )
        self.assertEqual({}, storage.objects)

        no_storage = FakeStorage()
        no_robots = FakeRobots()
        no_asset = FakeAssetHTTP()
        complete = acquire_one_to_r2(
            self.load(),
            config=R2Config("synthetic-bucket", "proof/"),
            ledger_path=self.root / "ledger.sqlite3",
            cache_directory=self.root / "resume-cache",
            sanitized_output=retry_root,
            environ=ENVIRONMENT,
            storage_client=no_storage,
            robots_transport=no_robots,
            asset_http_client=no_asset,
            now=NOW,
        )
        self.assertEqual("complete", complete["status"])
        self.assertEqual([], no_storage.probes)
        self.assertEqual([], no_robots.calls)
        self.assertEqual([], no_asset.calls)

    def test_cli_requires_safe_repository_relative_paths(self) -> None:
        parser_output = io.StringIO()
        with redirect_stdout(parser_output):
            with self.assertRaises(SystemExit):
                main(["trusted-vm", "acquire-one-to-r2"])

        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "trusted-vm",
                    "acquire-one-to-r2",
                    "--approval",
                    str(self.write_approval()),
                    "--database",
                    str(self.root / "ledger.sqlite3"),
                    "--storage-config",
                    ".agent/storage.yaml",
                    "--cache-directory",
                    ".local/r2-proof/cache",
                    "--sanitized-output",
                    ".local/r2-proof/receipts",
                ],
                environ=ENVIRONMENT,
                storage_client=FakeStorage(),
                http_client=FakeAssetHTTP(),
                robots_transport=FakeRobots(),
            )
        self.assertEqual(4, status)
        self.assertEqual("unsafe_path", json.loads(output.getvalue())["code"])

    def test_cli_runs_one_fake_object_from_the_exact_held_paths(self) -> None:
        proof_root = self.root / ".local" / "r2-proof"
        proof_root.mkdir(parents=True)
        agent_root = self.root / ".agent"
        agent_root.mkdir()
        (agent_root / "storage.yaml").write_text(
            "object_storage:\n"
            "  bucket: synthetic-bucket\n"
            "  prefix: proof/\n",
            encoding="utf-8",
        )
        current = datetime.now(timezone.utc)
        value = approval_value()
        value["rights"]["decision_at"] = (
            current - timedelta(hours=2)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        value["proof_window"] = {
            "starts_at": (current - timedelta(hours=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "ends_at": (current + timedelta(hours=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }
        value["cleanup_deadline"] = (current + timedelta(minutes=30)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        (proof_root / "approval.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        previous = Path.cwd()
        output = io.StringIO()
        try:
            os.chdir(self.root)
            with redirect_stdout(output):
                status = main(
                    [
                        "trusted-vm",
                        "acquire-one-to-r2",
                        "--approval",
                        ".local/r2-proof/approval.json",
                        "--database",
                        ".local/r2-proof/ledger.sqlite3",
                        "--storage-config",
                        ".agent/storage.yaml",
                        "--cache-directory",
                        ".local/r2-proof/cache",
                        "--sanitized-output",
                        ".local/r2-proof/receipts",
                    ],
                    environ=ENVIRONMENT,
                    storage_client=FakeStorage(),
                    http_client=FakeAssetHTTP(),
                    robots_transport=FakeRobots(),
                )
        finally:
            os.chdir(previous)
        self.assertEqual(0, status)
        self.assertEqual({"status": "complete"}, json.loads(output.getvalue()))
        self.assertTrue((proof_root / "receipts" / "manifest.json").is_file())

    def test_cli_persists_invalid_approval_without_constructing_clients(self) -> None:
        proof_root = self.root / ".local" / "r2-proof"
        proof_root.mkdir(parents=True)
        agent_root = self.root / ".agent"
        agent_root.mkdir()
        (agent_root / "storage.yaml").write_text(
            "object_storage:\n"
            "  bucket: synthetic-bucket\n"
            "  prefix: proof/\n",
            encoding="utf-8",
        )
        (proof_root / "approval.json").write_text(
            '{"schema_version": 1}', encoding="utf-8"
        )
        storage = FakeStorage()
        robots = FakeRobots()
        asset = FakeAssetHTTP()
        previous = Path.cwd()
        output = io.StringIO()
        try:
            os.chdir(self.root)
            with redirect_stdout(output):
                status = main(
                    [
                        "trusted-vm",
                        "acquire-one-to-r2",
                        "--approval",
                        ".local/r2-proof/approval.json",
                        "--database",
                        ".local/r2-proof/ledger.sqlite3",
                        "--storage-config",
                        ".agent/storage.yaml",
                        "--cache-directory",
                        ".local/r2-proof/cache",
                        "--sanitized-output",
                        ".local/r2-proof/receipts",
                    ],
                    environ=ENVIRONMENT,
                    storage_client=storage,
                    http_client=asset,
                    robots_transport=robots,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(4, status)
        self.assertEqual("approval_invalid", json.loads(output.getvalue())["code"])
        manifest = json.loads(
            (proof_root / "receipts" / "manifest.json").read_text()
        )
        self.assertEqual("blocked", manifest["status"])
        self.assertEqual("approval_invalid", manifest["outcome_code"])
        self.assertEqual([], storage.probes)
        self.assertEqual([], robots.calls)
        self.assertEqual([], asset.calls)


if __name__ == "__main__":
    unittest.main()
