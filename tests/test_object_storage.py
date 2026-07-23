from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.cli import main
from performing_fire_corpus.ledger import Ledger
from performing_fire_corpus.policy import AcquisitionPolicyError
from performing_fire_corpus.storage import (
    REQUIRED_SECRET_NAMES,
    R2Config,
    load_r2_config,
    r2_readiness,
)
from performing_fire_corpus.transfer import (
    TransferError,
    immutable_object_key,
    plan_transfer,
    transfer_approved_asset,
)


RECORDS = ROOT / "tests" / "fixtures" / "records" / "v1"
PUBLIC_URL = "https://antiegg.kr/media/synthetic.mp4"


def fixture(name: str) -> dict[str, object]:
    return json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))


def approved_rights() -> dict[str, object]:
    return fixture("rights")


def make_plan(**overrides: object):
    values = {
        "asset_id": "asset_synthetic_video_001",
        "source_id": "source_synthetic_001",
        "public_url": PUBLIC_URL,
        "rights": approved_rights(),
        "allowed_media_types": {"video/mp4"},
        "maximum_bytes": 32,
        "staging_prefix": "proof-staging/",
        "retention_decision": "Delete the exact reviewed key after verification.",
        "evidence_ref": "evidence:issue-6",
    }
    values.update(overrides)
    return plan_transfer(**values)


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        media_type: str = "video/mp4",
        content_length: int | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.media_type = media_type
        self.content_length = (
            sum(len(chunk) for chunk in chunks)
            if content_length is None
            else content_length
        )
        self.failure = failure

    def iter_bytes(self, chunk_size: int):
        del chunk_size
        for chunk in self.chunks:
            yield chunk
        if self.failure is not None:
            raise self.failure


class FakeHTTP:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def open(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self.response


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.uploads = 0
        self.fail_upload = False

    def head_object(self, key: str) -> dict[str, object] | None:
        return self.objects.get(key)

    def upload_file(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> None:
        self.uploads += 1
        if self.fail_upload:
            raise ConnectionError("synthetic interruption")
        self.asserted_bytes = path.read_bytes()
        self.objects[key] = {
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
        }

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        if key in self.objects:
            return False
        self.upload_file(
            key,
            path,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
        )
        return True


class RacingStorage(FakeStorage):
    def __init__(self, concurrent_metadata: dict[str, object]) -> None:
        super().__init__()
        self.concurrent_metadata = concurrent_metadata
        self.overwrote_conflict = False

    def upload_file(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> None:
        self.objects[key] = dict(self.concurrent_metadata)
        self.overwrote_conflict = True
        super().upload_file(
            key,
            path,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
        )

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        del path, byte_size, media_type, sha256
        self.objects[key] = dict(self.concurrent_metadata)
        return False


class UnverifiedCreateStorage(FakeStorage):
    def __init__(self, final_metadata: dict[str, object] | None) -> None:
        super().__init__()
        self.final_metadata = final_metadata

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        del path, byte_size, media_type, sha256
        self.uploads += 1
        if self.final_metadata is not None:
            self.objects[key] = dict(self.final_metadata)
        return True


class FakeReadinessStorage:
    def __init__(
        self,
        *,
        bucket: str = "corpus-public",
        staging_prefix: str = "proof-staging/",
        accessible: bool = True,
        failure: Exception | None = None,
    ) -> None:
        self.bucket = bucket
        self.staging_prefix = staging_prefix
        self.accessible = accessible
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    def probe_scope(self, bucket: str, staging_prefix: str) -> bool:
        self.calls.append((bucket, staging_prefix))
        if self.failure is not None:
            raise self.failure
        return (
            self.accessible
            and bucket == self.bucket
            and staging_prefix == self.staging_prefix
        )


class ReadinessTests(unittest.TestCase):
    def test_missing_configuration_and_secrets_fail_closed_without_values(self) -> None:
        result = r2_readiness(R2Config(bucket="", staging_prefix=""), environ={})
        rendered = json.dumps(result, sort_keys=True)
        self.assertFalse(result["ready"])
        self.assertIsNotNone(result["next_action"])
        for name in REQUIRED_SECRET_NAMES:
            self.assertEqual(result["checks"]["secrets"][name], "missing")
        self.assertNotIn("account_example_value", rendered)

    def test_fake_environment_is_deterministically_ready_and_redacted(self) -> None:
        environment = {
            name: f"synthetic-value-{index}"
            for index, name in enumerate(REQUIRED_SECRET_NAMES)
        }
        result = r2_readiness(
            R2Config(bucket="corpus-public", staging_prefix="proof-staging/"),
            environ=environment,
            storage_client=FakeReadinessStorage(),
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertTrue(result["ready"])
        self.assertIsNone(result["next_action"])
        self.assertNotIn("corpus-public", rendered)
        for value in environment.values():
            self.assertNotIn(value, rendered)

    def test_storage_scope_mismatch_or_probe_failure_fails_closed(self) -> None:
        environment = {name: "synthetic-present" for name in REQUIRED_SECRET_NAMES}
        config = R2Config(bucket="corpus-public", staging_prefix="proof-staging/")
        for storage in (
            FakeReadinessStorage(bucket="different-bucket"),
            FakeReadinessStorage(accessible=False),
            FakeReadinessStorage(failure=ConnectionError("private endpoint details")),
        ):
            with self.subTest(storage=storage):
                result = r2_readiness(
                    config,
                    environ=environment,
                    storage_client=storage,
                )
                rendered = json.dumps(result, sort_keys=True)
                self.assertFalse(result["ready"])
                self.assertEqual(
                    "missing", result["checks"]["storage"]["staging_scope"]
                )
                self.assertNotIn("corpus-public", rendered)
                self.assertNotIn("different-bucket", rendered)
                self.assertNotIn("private endpoint details", rendered)

    def test_loader_and_cli_report_missing_without_a_traceback(self) -> None:
        self.assertEqual(
            R2Config(bucket="", staging_prefix=""),
            load_r2_config(ROOT / "does-not-exist.yaml"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            durable_output = Path(temporary) / "readiness.json"
            with redirect_stdout(output):
                status = main(
                    [
                        "r2",
                        "readiness",
                        "--config",
                        str(ROOT / "does-not-exist.yaml"),
                        "--output",
                        str(durable_output),
                    ]
                )
            self.assertEqual(2, status)
            self.assertIn('"next_action"', output.getvalue())
            persisted = json.loads(durable_output.read_text(encoding="utf-8"))
            self.assertEqual(persisted, json.loads(output.getvalue()))
            self.assertFalse(persisted["ready"])

    def test_cli_persists_fake_storage_probe_success(self) -> None:
        environment = {name: "synthetic-present" for name in REQUIRED_SECRET_NAMES}
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "storage.yaml"
            config.write_text(
                "object_storage:\n"
                "  bucket: corpus-public\n"
                "  prefix: proof-staging/\n",
                encoding="utf-8",
            )
            durable_output = Path(temporary) / "readiness.json"
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "r2",
                        "readiness",
                        "--config",
                        str(config),
                        "--output",
                        str(durable_output),
                    ],
                    environ=environment,
                    storage_client=FakeReadinessStorage(),
                )
            self.assertEqual(0, status)
            self.assertTrue(
                json.loads(durable_output.read_text(encoding="utf-8"))["ready"]
            )


class TransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.ledger = Ledger(self.root / "ledger.sqlite3")
        self.ledger.upsert(fixture("source"))
        self.ledger.upsert(fixture("asset"))
        self.ledger.upsert(fixture("rights"))

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def run_transfer(
        self,
        response: FakeResponse,
        *,
        storage: FakeStorage | None = None,
        plan=None,
    ):
        selected_storage = storage or FakeStorage()
        receipt = transfer_approved_asset(
            plan or make_plan(),
            http_client=FakeHTTP(response),
            storage_client=selected_storage,
            ledger=self.ledger,
            cache_directory=self.cache,
        )
        return receipt, selected_storage

    def assert_cache_empty(self) -> None:
        self.assertEqual([], list(self.cache.iterdir()) if self.cache.exists() else [])

    def test_planner_blocks_rights_and_incomplete_gates_before_network(self) -> None:
        rights = copy.deepcopy(approved_rights())
        rights["state"] = "blocked"
        with self.assertRaises(AcquisitionPolicyError):
            make_plan(rights=rights)
        for field, value in (
            ("allowed_media_types", set()),
            ("maximum_bytes", 0),
            ("staging_prefix", "raw"),
            ("retention_decision", ""),
        ):
            with self.subTest(field=field), self.assertRaises(TransferError):
                make_plan(**{field: value})

    def test_streams_hashes_uploads_receipt_and_cleans_cache(self) -> None:
        content = [b"synthetic", b"-media"]
        receipt, storage = self.run_transfer(FakeResponse(content))
        self.assertEqual(1, storage.uploads)
        self.assertEqual(b"".join(content), storage.asserted_bytes)
        self.assertEqual("uploaded", receipt["attempt_state"])
        self.assertEqual("evidence:issue-6", receipt["evidence_ref"])
        self.assertEqual(receipt, self.ledger.get_record("object", receipt["object_id"]))
        self.assertTrue(
            receipt["object_key"].startswith(
                "proof-staging/v1/asset_synthetic_video_001/"
            )
        )
        self.assertNotIn("synthetic.mp4", receipt["object_key"])
        self.assert_cache_empty()

    def test_rejects_size_and_media_type_mismatches_and_cleans(self) -> None:
        with self.assertRaises(TransferError) as size:
            self.run_transfer(FakeResponse([b"x" * 33]))
        self.assertEqual("size_limit_exceeded", size.exception.code)
        self.assert_cache_empty()

        with self.assertRaises(TransferError) as media:
            self.run_transfer(FakeResponse([b"x"], media_type="image/jpeg"))
        self.assertEqual("media_type_mismatch", media.exception.code)
        self.assert_cache_empty()

        with self.assertRaises(TransferError) as length:
            self.run_transfer(FakeResponse([b"x"], content_length=2))
        self.assertEqual("size_mismatch", length.exception.code)
        self.assert_cache_empty()

    def test_interrupted_stream_and_upload_are_redacted_and_retryable(self) -> None:
        response = FakeResponse(
            [b"partial"],
            failure=ConnectionError("signed request and response body omitted"),
        )
        with self.assertRaises(TransferError) as interrupted:
            self.run_transfer(response)
        self.assertEqual("transfer_interrupted", interrupted.exception.code)
        self.assertNotIn("signed request", str(interrupted.exception))
        self.assert_cache_empty()

        storage = FakeStorage()
        storage.fail_upload = True
        with self.assertRaises(TransferError):
            self.run_transfer(FakeResponse([b"retry"]), storage=storage)
        self.assert_cache_empty()
        storage.fail_upload = False
        receipt, _ = self.run_transfer(FakeResponse([b"retry"]), storage=storage)
        self.assertEqual("uploaded", receipt["attempt_state"])
        self.assertEqual(2, storage.uploads)

    def test_matching_object_and_receipt_are_reused_without_upload(self) -> None:
        response = FakeResponse([b"idempotent"])
        receipt, storage = self.run_transfer(response)
        repeated, storage = self.run_transfer(response, storage=storage)
        self.assertEqual(receipt, repeated)
        self.assertEqual(1, storage.uploads)
        self.assert_cache_empty()

    def test_matching_existing_object_without_receipt_records_reuse(self) -> None:
        plan = make_plan()
        content = b"existing"
        digest = hashlib.sha256(content).hexdigest()
        key = immutable_object_key(plan, digest)
        storage = FakeStorage()
        storage.objects[key] = {"byte_size": len(content), "sha256": digest}
        receipt, _ = self.run_transfer(
            FakeResponse([content]), storage=storage, plan=plan
        )
        self.assertEqual("reused", receipt["attempt_state"])
        self.assertEqual(0, storage.uploads)

    def test_existing_object_hash_or_size_conflict_never_overwrites(self) -> None:
        content = b"conflict"
        plan = make_plan()
        digest = hashlib.sha256(content).hexdigest()
        key = immutable_object_key(plan, digest)
        storage = FakeStorage()
        storage.objects[key] = {"byte_size": len(content) + 1, "sha256": digest}
        with self.assertRaises(TransferError) as conflict:
            self.run_transfer(
                FakeResponse([content]), storage=storage, plan=plan
            )
        self.assertEqual("object_conflict", conflict.exception.code)
        self.assertEqual(0, storage.uploads)
        self.assert_cache_empty()

    def test_lost_create_race_reuses_match_or_blocks_conflict_without_overwrite(
        self,
    ) -> None:
        content = b"racing"
        digest = hashlib.sha256(content).hexdigest()
        matching = RacingStorage({"byte_size": len(content), "sha256": digest})
        receipt, _ = self.run_transfer(FakeResponse([content]), storage=matching)
        self.assertEqual("reused", receipt["attempt_state"])
        self.assertFalse(matching.overwrote_conflict)

        conflicting = RacingStorage({"byte_size": len(content) + 1, "sha256": digest})
        with self.assertRaises(TransferError) as conflict:
            self.run_transfer(FakeResponse([content]), storage=conflicting)
        self.assertEqual("object_conflict", conflict.exception.code)
        self.assertFalse(conflicting.overwrote_conflict)
        self.assert_cache_empty()

    def test_create_success_is_verified_before_receipt(self) -> None:
        content = b"verify-upload"
        digest = hashlib.sha256(content).hexdigest()
        for final_metadata in (
            None,
            {"byte_size": len(content) + 1, "sha256": digest},
        ):
            with self.subTest(final_metadata=final_metadata):
                storage = UnverifiedCreateStorage(final_metadata)
                with self.assertRaises(TransferError) as conflict:
                    self.run_transfer(FakeResponse([content]), storage=storage)
                self.assertEqual("object_conflict", conflict.exception.code)
                self.assertEqual(1, storage.uploads)
                self.assert_cache_empty()


if __name__ == "__main__":
    unittest.main()
