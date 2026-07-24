from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import (
    CorpusObjectError,
    build_derivation_manifest,
    build_retention_work,
    cluster_exact_content,
    derived_object_key,
    execute_exact_cleanup,
    immutable_create_and_verify,
    manifest_object_key,
    raw_object_key,
    reconcile_receipt_commit,
    tombstone_object_key,
)


SHA = hashlib.sha256(b"synthetic raw object").hexdigest()
DERIVED_SHA = hashlib.sha256(b"synthetic transcript").hexdigest()
RIGHTS_SHA = hashlib.sha256(b"synthetic rights snapshot").hexdigest()


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.list_calls = 0
        self.create_failure: Exception | None = None
        self.delete_failure: Exception | None = None
        self.persist_before_create_failure = False

    def head_object(self, key: str) -> dict[str, object] | None:
        value = self.objects.get(key)
        return None if value is None else dict(value)

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        metadata = {
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
        self.created.append(key)
        self.objects[key] = metadata
        return True

    def delete_exact_object(self, key: str) -> bool:
        self.deleted.append(key)
        if self.delete_failure is not None:
            raise self.delete_failure
        return self.objects.pop(key, None) is not None

    def list_objects(self, prefix: str) -> list[str]:
        del prefix
        self.list_calls += 1
        raise AssertionError("broad listing is forbidden")


def receipt(
    *,
    object_key: str,
    asset_id: str = "asset_synthetic_001",
    source_id: str = "source_synthetic_001",
    sha256: str = SHA,
    receipt_id: str = "receipt_synthetic_001",
    retention_class: str = "reviewed-retain-30d",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "object_receipt",
        "receipt_id": receipt_id,
        "object_kind": "raw",
        "source_id": source_id,
        "asset_id": asset_id,
        "object_key": object_key,
        "byte_size": 20,
        "media_type": "video/mp4",
        "sha256": sha256,
        "rights_snapshot_sha256": RIGHTS_SHA,
        "retention_class": retention_class,
        "creation_run_id": "run_synthetic_001",
        "evidence_ref": "evidence:issue-37",
        "verification_state": "verified",
        "create_disposition": "created",
    }


class NamespaceTests(unittest.TestCase):
    def test_versioned_namespaces_include_stable_ids_and_lowercase_hash(self) -> None:
        raw = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        derived = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        manifest = manifest_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "manifest_synthetic_001",
            SHA,
        )
        tombstone = tombstone_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "tombstone_synthetic_001",
            SHA,
        )
        self.assertEqual(
            f"performing-fire/v1/raw/source_synthetic_001/"
            f"asset_synthetic_001/{SHA}",
            raw,
        )
        self.assertIn(
            f"/derived/source_synthetic_001/asset_synthetic_001/"
            f"transform_transcript_v1/{DERIVED_SHA}",
            derived,
        )
        self.assertIn("/manifests/", manifest)
        self.assertIn("/tombstones/", tombstone)

    def test_namespaces_reject_unsafe_or_descriptive_values(self) -> None:
        unsafe = (
            ("../escape/", "source_synthetic_001", "asset_synthetic_001", SHA),
            (
                "performing-fire/",
                "source_private title",
                "asset_synthetic_001",
                SHA,
            ),
            (
                "performing-fire/",
                "source_synthetic_001",
                "asset_/" + "Users/person/file",
                SHA,
            ),
            (
                "performing-fire/",
                "source_synthetic_001",
                "asset_synthetic_001",
                SHA.upper(),
            ),
        )
        for values in unsafe:
            with self.subTest(values=values), self.assertRaises(CorpusObjectError):
                raw_object_key(*values)


class ImmutableCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "object.bin"
        self.path.write_bytes(b"synthetic raw object")
        self.key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, storage: FakeStorage) -> dict[str, object]:
        return immutable_create_and_verify(
            storage,
            key=self.key,
            path=self.path,
            object_kind="raw",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            byte_size=20,
            media_type="video/mp4",
            sha256=SHA,
            rights_snapshot_sha256=RIGHTS_SHA,
            retention_class="reviewed-retain-30d",
            creation_run_id="run_synthetic_001",
            evidence_ref="evidence:issue-37",
        )

    def test_create_requires_matching_exact_head_before_verified_receipt(self) -> None:
        storage = FakeStorage()
        result = self.call(storage)
        self.assertEqual("verified", result["verification_state"])
        self.assertEqual("created", result["create_disposition"])
        self.assertEqual(self.key, result["object_key"])
        self.assertEqual([self.key], storage.created)
        self.assertEqual(0, storage.list_calls)

    def test_matching_existing_object_is_reused_without_collapsing_receipt(self) -> None:
        storage = FakeStorage()
        storage.objects[self.key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        result = self.call(storage)
        self.assertEqual("reused", result["create_disposition"])
        self.assertEqual([], storage.created)

    def test_conflicting_exact_key_fails_closed(self) -> None:
        storage = FakeStorage()
        storage.objects[self.key] = {
            "byte_size": 19,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        with self.assertRaises(CorpusObjectError) as raised:
            self.call(storage)
        self.assertEqual("immutable_object_conflict", raised.exception.code)

    def test_lost_create_response_recovers_only_from_matching_exact_head(self) -> None:
        storage = FakeStorage()
        storage.create_failure = ConnectionError("signed provider response")
        storage.persist_before_create_failure = True
        result = self.call(storage)
        self.assertEqual("recovered_after_lost_response", result["create_disposition"])
        self.assertNotIn("signed provider response", json.dumps(result))

        absent = FakeStorage()
        absent.create_failure = ConnectionError("provider detail")
        with self.assertRaises(CorpusObjectError) as raised:
            self.call(absent)
        self.assertEqual("immutable_create_unconfirmed", raised.exception.code)
        self.assertNotIn("provider detail", str(raised.exception))


class ProvenanceAndManifestTests(unittest.TestCase):
    def test_deduplication_preserves_edges_and_uses_most_restrictive_rights(self) -> None:
        cluster = cluster_exact_content(
            [
                {
                    "source_id": "source_synthetic_001",
                    "asset_id": "asset_synthetic_001",
                    "sha256": SHA,
                    "rights_snapshot_sha256": RIGHTS_SHA,
                    "retrieval_decision": "approved",
                },
                {
                    "source_id": "source_synthetic_002",
                    "asset_id": "asset_synthetic_002",
                    "sha256": SHA,
                    "rights_snapshot_sha256": hashlib.sha256(
                        b"second rights"
                    ).hexdigest(),
                    "retrieval_decision": "metadata_only",
                },
            ]
        )
        self.assertEqual(SHA, cluster["sha256"])
        self.assertEqual("metadata_only", cluster["effective_retrieval_decision"])
        self.assertEqual(2, len(cluster["provenance_edges"]))
        self.assertEqual(
            {
                "asset_synthetic_001",
                "asset_synthetic_002",
            },
            {edge["asset_id"] for edge in cluster["provenance_edges"]},
        )

    def test_deduplication_rejects_mixed_hashes(self) -> None:
        values = [
            {
                "source_id": "source_synthetic_001",
                "asset_id": "asset_synthetic_001",
                "sha256": SHA,
                "rights_snapshot_sha256": RIGHTS_SHA,
                "retrieval_decision": "approved",
            },
            {
                "source_id": "source_synthetic_002",
                "asset_id": "asset_synthetic_002",
                "sha256": DERIVED_SHA,
                "rights_snapshot_sha256": RIGHTS_SHA,
                "retrieval_decision": "approved",
            },
        ]
        with self.assertRaises(CorpusObjectError):
            cluster_exact_content(values)

    def test_manifest_is_deterministic_and_contains_no_machine_paths(self) -> None:
        raw_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        raw_receipt = receipt(object_key=raw_key)
        derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        derived_receipt = {
            **receipt(
                object_key=derived_key,
                sha256=DERIVED_SHA,
                receipt_id="receipt_synthetic_derived_001",
            ),
            "object_kind": "derived",
            "transformation_id": "transform_transcript_v1",
        }
        arguments = {
            "manifest_id": "manifest_synthetic_001",
            "source_id": "source_synthetic_001",
            "asset_id": "asset_synthetic_001",
            "transformation_id": "transform_transcript_v1",
            "tool_id": "tool_synthetic_transcriber",
            "tool_version": "1.2.3",
            "contract_version": 1,
            "parameters": {"language": "ko", "temperature_milli": 0},
            "inputs": [raw_receipt],
            "outputs": [derived_receipt],
            "rights_inheritance": "most_restrictive",
            "redaction_state": "reviewed_synthetic",
            "evidence_ref": "evidence:issue-37",
        }
        first = build_derivation_manifest(**arguments)
        second = build_derivation_manifest(**copy.deepcopy(arguments))
        self.assertEqual(first, second)
        self.assertEqual([raw_key], first["input_object_keys"])
        self.assertEqual([derived_key], first["output_object_keys"])
        self.assertEqual(
            hashlib.sha256(
                json.dumps(
                    arguments["parameters"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest(),
            first["parameters_sha256"],
        )

        unsafe = copy.deepcopy(arguments)
        unsafe["parameters"] = {
            "model_path": "/" + "Users/person/private/model"
        }
        with self.assertRaises(CorpusObjectError):
            build_derivation_manifest(**unsafe)

    def test_manifest_rejects_weakened_or_unrelated_output_rights(self) -> None:
        raw_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        output = {
            **receipt(
                object_key=derived_key,
                sha256=DERIVED_SHA,
                receipt_id="receipt_synthetic_derived_001",
            ),
            "object_kind": "derived",
            "transformation_id": "transform_transcript_v1",
            "rights_snapshot_sha256": hashlib.sha256(
                b"unrelated weaker rights"
            ).hexdigest(),
        }
        with self.assertRaises(CorpusObjectError) as raised:
            build_derivation_manifest(
                manifest_id="manifest_synthetic_001",
                source_id="source_synthetic_001",
                asset_id="asset_synthetic_001",
                transformation_id="transform_transcript_v1",
                tool_id="tool_synthetic_transcriber",
                tool_version="1.2.3",
                contract_version=1,
                parameters={"language": "ko"},
                inputs=[receipt(object_key=raw_key)],
                outputs=[output],
                rights_inheritance="most_restrictive",
                redaction_state="reviewed_synthetic",
                evidence_ref="evidence:issue-37",
            )
        self.assertEqual("manifest_rights_mismatch", raised.exception.code)


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        self.derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        self.raw_receipt = receipt(object_key=self.raw_key)
        self.derived_receipt = {
            **receipt(
                object_key=self.derived_key,
                sha256=DERIVED_SHA,
                receipt_id="receipt_synthetic_derived_001",
            ),
            "object_kind": "derived",
            "transformation_id": "transform_transcript_v1",
        }

    def test_retention_work_propagates_exact_keys_and_holds_normal_corpus_data(self) -> None:
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=self.raw_receipt,
            derived_receipts=[self.derived_receipt],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="none",
            cleanup_authority="held_for_review",
            cleanup_run_id="run_retention_review_001",
            reason_code="retention_expired",
            evidence_ref="evidence:issue-37",
        )
        self.assertEqual("awaiting_review", work["state"])
        self.assertEqual([self.derived_key, self.raw_key], work["exact_object_keys"])
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError) as raised:
            execute_exact_cleanup(storage, work)
        self.assertEqual("cleanup_authority_required", raised.exception.code)
        self.assertEqual([], storage.deleted)

    def test_legal_hold_conflict_never_deletes(self) -> None:
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=self.raw_receipt,
            derived_receipts=[self.derived_receipt],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="active",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="retention_expired",
            evidence_ref="evidence:issue-37",
        )
        self.assertEqual("legal_hold_conflict", work["state"])
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError):
            execute_exact_cleanup(storage, work)
        self.assertEqual([], storage.deleted)

    def test_same_proof_exact_cleanup_is_idempotent_and_emits_tombstones(self) -> None:
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=self.raw_receipt,
            derived_receipts=[self.derived_receipt],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="none",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="proof_teardown",
            evidence_ref="evidence:issue-37",
        )
        storage = FakeStorage()
        for item in (self.raw_receipt, self.derived_receipt):
            storage.objects[str(item["object_key"])] = {
                "byte_size": item["byte_size"],
                "media_type": item["media_type"],
                "sha256": item["sha256"],
            }
        first = execute_exact_cleanup(storage, work)
        second = execute_exact_cleanup(storage, work)
        self.assertEqual("complete", first["state"])
        self.assertEqual("complete", second["state"])
        self.assertEqual(2, len(first["tombstones"]))
        self.assertEqual(2, len(second["tombstones"]))
        self.assertEqual(0, storage.list_calls)

    def test_failed_exact_cleanup_is_durable_and_does_not_broaden_scope(self) -> None:
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=self.raw_receipt,
            derived_receipts=[],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="none",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="proof_teardown",
            evidence_ref="evidence:issue-37",
        )
        storage = FakeStorage()
        storage.objects[self.raw_key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        storage.delete_failure = ConnectionError("private provider failure")
        result = execute_exact_cleanup(storage, work)
        self.assertEqual("failed_cleanup", result["state"])
        self.assertEqual([self.raw_key], result["failed_object_keys"])
        self.assertNotIn("private provider failure", json.dumps(result))
        self.assertEqual(0, storage.list_calls)

    def test_same_proof_cleanup_rejects_reused_or_cross_run_objects(self) -> None:
        reused = {**self.raw_receipt, "create_disposition": "reused"}
        with self.assertRaises(CorpusObjectError) as reused_error:
            build_retention_work(
                work_id="retention_work_synthetic_001",
                root_receipt=reused,
                derived_receipts=[],
                expires_at="2026-07-25T00:00:00Z",
                current_time="2026-07-26T00:00:00Z",
                legal_hold_state="none",
                cleanup_authority="same_proof_disposable",
                cleanup_run_id="run_synthetic_001",
                reason_code="proof_teardown",
                evidence_ref="evidence:issue-37",
            )
        self.assertEqual("same_proof_authority_mismatch", reused_error.exception.code)

        cross_run = {**self.raw_receipt, "creation_run_id": "run_other_001"}
        with self.assertRaises(CorpusObjectError) as cross_run_error:
            build_retention_work(
                work_id="retention_work_synthetic_001",
                root_receipt=cross_run,
                derived_receipts=[],
                expires_at="2026-07-25T00:00:00Z",
                current_time="2026-07-26T00:00:00Z",
                legal_hold_state="none",
                cleanup_authority="same_proof_disposable",
                cleanup_run_id="run_synthetic_001",
                reason_code="proof_teardown",
                evidence_ref="evidence:issue-37",
            )
        self.assertEqual(
            "same_proof_authority_mismatch", cross_run_error.exception.code
        )

    def test_executor_revalidates_tampered_work_before_any_delete(self) -> None:
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=self.raw_receipt,
            derived_receipts=[],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="none",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="proof_teardown",
            evidence_ref="evidence:issue-37",
        )
        unrelated_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_999",
            "asset_synthetic_999",
            SHA,
        )
        work["targets"][0]["object_key"] = unrelated_key
        work["exact_object_keys"] = [unrelated_key]
        storage = FakeStorage()
        storage.objects[unrelated_key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        with self.assertRaises(CorpusObjectError) as raised:
            execute_exact_cleanup(storage, work)
        self.assertEqual("invalid_retention_work", raised.exception.code)
        self.assertEqual([], storage.deleted)


class ReceiptReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        self.expected = receipt(object_key=self.key)
        self.storage = FakeStorage()
        self.storage.objects[self.key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }

    def test_receipt_before_ledger_resumes_with_ledger_only(self) -> None:
        result = reconcile_receipt_commit(
            self.storage,
            expected_receipt=self.expected,
            receipt_artifact=self.expected,
            ledger_record=None,
        )
        self.assertEqual("write_ledger_from_receipt", result["next_action"])
        self.assertEqual(self.expected, result["verified_receipt"])

    def test_ledger_before_receipt_resumes_with_receipt_only(self) -> None:
        result = reconcile_receipt_commit(
            self.storage,
            expected_receipt=self.expected,
            receipt_artifact=None,
            ledger_record=self.expected,
        )
        self.assertEqual("write_receipt_from_ledger", result["next_action"])

    def test_complete_reconciliation_is_idempotent(self) -> None:
        result = reconcile_receipt_commit(
            self.storage,
            expected_receipt=self.expected,
            receipt_artifact=self.expected,
            ledger_record=self.expected,
        )
        self.assertEqual("complete", result["next_action"])
        self.assertEqual(0, self.storage.list_calls)

    def test_reconciliation_rejects_absent_object_or_conflicting_records(self) -> None:
        self.storage.objects.clear()
        with self.assertRaises(CorpusObjectError) as absent:
            reconcile_receipt_commit(
                self.storage,
                expected_receipt=self.expected,
                receipt_artifact=self.expected,
                ledger_record=None,
            )
        self.assertEqual("verified_object_missing", absent.exception.code)

        self.storage.objects[self.key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        conflict = {**self.expected, "retention_class": "different-policy"}
        with self.assertRaises(CorpusObjectError) as raised:
            reconcile_receipt_commit(
                self.storage,
                expected_receipt=self.expected,
                receipt_artifact=conflict,
                ledger_record=None,
            )
        self.assertEqual("receipt_commit_conflict", raised.exception.code)

    def test_reconciliation_rejects_extra_fields_and_key_fact_mismatch(self) -> None:
        extra = {**self.expected, "unexpected": "synthetic"}
        with self.assertRaises(CorpusObjectError) as extra_error:
            reconcile_receipt_commit(
                self.storage,
                expected_receipt=extra,
                receipt_artifact=None,
                ledger_record=None,
            )
        self.assertEqual("invalid_object_receipt", extra_error.exception.code)

        different_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_002",
            "asset_synthetic_001",
            SHA,
        )
        mismatch = {**self.expected, "object_key": different_key}
        with self.assertRaises(CorpusObjectError) as mismatch_error:
            reconcile_receipt_commit(
                self.storage,
                expected_receipt=mismatch,
                receipt_artifact=None,
                ledger_record=None,
            )
        self.assertEqual("object_key_mismatch", mismatch_error.exception.code)


class SchemaTests(unittest.TestCase):
    SCHEMA_NAMES = (
        "raw-object",
        "derived-object",
        "derivation-manifest",
        "object-receipt",
        "retention-work",
        "object-tombstone",
    )

    def validator(self, name: str) -> Draft202012Validator:
        schema = json.loads(
            (ROOT / "schemas" / "v1" / f"{name}.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def records(self) -> dict[str, dict[str, object]]:
        raw_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        raw_receipt = receipt(object_key=raw_key)
        derived_receipt = {
            **receipt(
                object_key=derived_key,
                sha256=DERIVED_SHA,
                receipt_id="receipt_synthetic_derived_001",
            ),
            "object_kind": "derived",
            "transformation_id": "transform_transcript_v1",
        }
        manifest = build_derivation_manifest(
            manifest_id="manifest_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            transformation_id="transform_transcript_v1",
            tool_id="tool_synthetic_transcriber",
            tool_version="1.2.3",
            contract_version=1,
            parameters={"language": "ko", "temperature_milli": 0},
            inputs=[raw_receipt],
            outputs=[derived_receipt],
            rights_inheritance="most_restrictive",
            redaction_state="reviewed_synthetic",
            evidence_ref="evidence:issue-37",
        )
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            root_receipt=raw_receipt,
            derived_receipts=[derived_receipt],
            expires_at="2026-07-25T00:00:00Z",
            current_time="2026-07-26T00:00:00Z",
            legal_hold_state="none",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="proof_teardown",
            evidence_ref="evidence:issue-37",
        )
        storage = FakeStorage()
        cleanup = execute_exact_cleanup(storage, work)
        return {
            "raw-object": {
                "schema_version": 1,
                "record_type": "raw_object",
                "source_id": "source_synthetic_001",
                "asset_id": "asset_synthetic_001",
                "object_key": raw_key,
                "sha256": SHA,
                "byte_size": 20,
                "media_type": "video/mp4",
                "rights_snapshot_sha256": RIGHTS_SHA,
                "retention_class": "reviewed-retain-30d",
            },
            "derived-object": {
                "schema_version": 1,
                "record_type": "derived_object",
                "source_id": "source_synthetic_001",
                "asset_id": "asset_synthetic_001",
                "transformation_id": "transform_transcript_v1",
                "input_receipt_ids": ["receipt_synthetic_001"],
                "object_key": derived_key,
                "sha256": DERIVED_SHA,
                "byte_size": 20,
                "media_type": "application/json",
                "rights_snapshot_sha256": RIGHTS_SHA,
                "retention_class": "reviewed-retain-30d",
                "redaction_state": "reviewed_synthetic",
            },
            "derivation-manifest": manifest,
            "object-receipt": raw_receipt,
            "retention-work": work,
            "object-tombstone": cleanup["tombstones"][0],
        }

    def test_full_corpus_schemas_are_strict_versioned_and_accept_records(self) -> None:
        records = self.records()
        for name in self.SCHEMA_NAMES:
            with self.subTest(name=name):
                schema = json.loads(
                    (ROOT / "schemas" / "v1" / f"{name}.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    f"https://performing-fire-corpus.invalid/schemas/v1/{name}.json",
                    schema["$id"],
                )
                self.assertFalse(schema["additionalProperties"])
                self.validator(name).validate(records[name])

    def test_full_corpus_schemas_reject_unknown_or_unsafe_fields(self) -> None:
        records = self.records()
        for name in self.SCHEMA_NAMES:
            with self.subTest(name=name):
                value = copy.deepcopy(records[name])
                value["title"] = "Private descriptive title"
                with self.assertRaises(ValidationError):
                    self.validator(name).validate(value)

        unsafe = copy.deepcopy(records["object-receipt"])
        unsafe["object_key"] = "/" + "Users/person/private/file"
        with self.assertRaises(ValidationError):
            self.validator("object-receipt").validate(unsafe)

        uppercase = copy.deepcopy(records["raw-object"])
        uppercase["sha256"] = SHA.upper()
        with self.assertRaises(ValidationError):
            self.validator("raw-object").validate(uppercase)


if __name__ == "__main__":
    unittest.main()
