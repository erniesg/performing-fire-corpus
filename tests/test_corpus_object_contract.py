from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import (
    CorpusObjectError,
    bind_object_receipt,
    build_derivation_lineage,
    build_derivation_manifest,
    build_retention_authority,
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
from performing_fire_corpus.ledger import Ledger, LedgerError


SHA = hashlib.sha256(b"synthetic raw object").hexdigest()
DERIVED_SHA = hashlib.sha256(b"synthetic transcript").hexdigest()
RIGHTS_SHA = hashlib.sha256(b"synthetic rights snapshot").hexdigest()


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.list_calls = 0
        self.head_calls = 0
        self.create_failure: Exception | None = None
        self.delete_failure: Exception | None = None
        self.persist_before_create_failure = False

    def head_object(self, key: str) -> dict[str, object] | None:
        self.head_calls += 1
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


class FakeCorpusAuthority:
    def __init__(
        self,
        receipts: list[Mapping[str, object]],
        manifests: list[Mapping[str, object]],
    ) -> None:
        self.receipts = [dict(value) for value in receipts]
        self.manifests = [dict(value) for value in manifests]

    def get_corpus_receipt_by_key(
        self, object_key: str
    ) -> dict[str, object] | None:
        matches = [
            value for value in self.receipts if value["object_key"] == object_key
        ]
        return None if not matches else dict(matches[0])

    def get_corpus_receipt(
        self, receipt_id: str
    ) -> dict[str, object] | None:
        matches = [
            value for value in self.receipts if value["receipt_id"] == receipt_id
        ]
        return None if not matches else dict(matches[0])

    def list_corpus_receipts(
        self, source_id: str, asset_id: str
    ) -> list[dict[str, object]]:
        return [
            dict(value)
            for value in self.receipts
            if value["source_id"] == source_id and value["asset_id"] == asset_id
        ]

    def list_derivation_manifests(
        self, source_id: str, asset_id: str
    ) -> list[dict[str, object]]:
        return [
            dict(value)
            for value in self.manifests
            if value["source_id"] == source_id and value["asset_id"] == asset_id
        ]

    def exact_cleanup_guard(self) -> object:
        return nullcontext(self)


def receipt(
    *,
    object_key: str,
    asset_id: str = "asset_synthetic_001",
    source_id: str = "source_synthetic_001",
    sha256: str = SHA,
    retention_class: str = "reviewed-retain-30d",
    creation_run_id: str = "run_synthetic_001",
    create_disposition: str = "created",
    retrieval_decision: str = "approved",
    rights_snapshot_sha256: str = RIGHTS_SHA,
    object_kind: str = "raw",
    transformation_id: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "object_receipt",
        "object_kind": object_kind,
        "source_id": source_id,
        "asset_id": asset_id,
        "object_key": object_key,
        "byte_size": 20,
        "media_type": "video/mp4",
        "sha256": sha256,
        "rights_snapshot_sha256": rights_snapshot_sha256,
        "retention_class": retention_class,
        "creation_run_id": creation_run_id,
        "retrieval_decision": retrieval_decision,
        "evidence_ref": "evidence:issue-37",
        "verification_state": "verified",
        "create_disposition": create_disposition,
    }
    if transformation_id is not None:
        value["transformation_id"] = transformation_id
    return bind_object_receipt(value)


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

    def call(
        self,
        storage: FakeStorage,
        *,
        receipt_authority: FakeCorpusAuthority | None = None,
    ) -> dict[str, object]:
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
            retrieval_decision="approved",
            evidence_ref="evidence:issue-37",
            receipt_authority=(
                FakeCorpusAuthority([], [])
                if receipt_authority is None
                else receipt_authority
            ),
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
        self.assertEqual(
            "reused_after_ambiguous_create", result["create_disposition"]
        )
        self.assertNotIn("signed provider response", json.dumps(result))

        absent = FakeStorage()
        absent.create_failure = ConnectionError("provider detail")
        with self.assertRaises(CorpusObjectError) as raised:
            self.call(absent)
        self.assertEqual("immutable_create_unconfirmed", raised.exception.code)
        self.assertNotIn("provider detail", str(raised.exception))

    def test_manifest_namespace_rejects_extra_or_invalid_segments(self) -> None:
        storage = FakeStorage()
        malformed = (
            "performing-fire/v1/manifests/source_synthetic_001/"
            f"asset_synthetic_001/manifest_bad/extra/{SHA}"
        )
        with self.assertRaises(CorpusObjectError) as raised:
            immutable_create_and_verify(
                storage,
                key=malformed,
                path=self.path,
                object_kind="manifest",
                source_id="source_synthetic_001",
                asset_id="asset_synthetic_001",
                byte_size=20,
                media_type="application/json",
                sha256=SHA,
                rights_snapshot_sha256=RIGHTS_SHA,
                retention_class="reviewed-retain-30d",
                creation_run_id="run_synthetic_001",
                retrieval_decision="approved",
                evidence_ref="evidence:issue-37",
                receipt_authority=FakeCorpusAuthority([], []),
            )
        self.assertEqual("object_key_mismatch", raised.exception.code)
        self.assertEqual([], storage.created)

    def test_terminal_rerun_reuses_the_authoritative_created_receipt(self) -> None:
        storage = FakeStorage()
        created = self.call(storage)
        authority = FakeCorpusAuthority([created], [])
        rerun = self.call(storage, receipt_authority=authority)
        self.assertEqual(created, rerun)
        self.assertEqual("created", rerun["create_disposition"])

    def test_inapplicable_transformation_fails_before_storage_is_touched(self) -> None:
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError) as raised:
            immutable_create_and_verify(
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
                retrieval_decision="approved",
                evidence_ref="evidence:issue-37",
                transformation_id="transform_not_applicable",
                receipt_authority=FakeCorpusAuthority([], []),
            )
        self.assertEqual("transformation_not_applicable", raised.exception.code)
        self.assertEqual(0, storage.head_calls)
        self.assertEqual([], storage.created)


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
        derived_receipt = receipt(
            object_key=derived_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
        )
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
        for unsafe_parameters in (
            {"api_key": "synthetic-but-forbidden"},
            {"model_id": "/var/lib/private/model"},
        ):
            unsafe = copy.deepcopy(arguments)
            unsafe["parameters"] = unsafe_parameters
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
        output = receipt(
            object_key=derived_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
            rights_snapshot_sha256=hashlib.sha256(
                b"unrelated weaker rights"
            ).hexdigest(),
        )
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

    def test_manifest_computes_the_most_restrictive_input_decision(self) -> None:
        restrictive_rights = hashlib.sha256(b"restrictive rights").hexdigest()
        second_sha = hashlib.sha256(b"second raw input").hexdigest()
        first_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        second_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            second_sha,
        )
        derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        output = receipt(
            object_key=derived_key,
            sha256=DERIVED_SHA,
            rights_snapshot_sha256=RIGHTS_SHA,
            retrieval_decision="approved",
            object_kind="derived",
            transformation_id="transform_transcript_v1",
        )
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
                inputs=[
                    receipt(object_key=first_key),
                    receipt(
                        object_key=second_key,
                        sha256=second_sha,
                        rights_snapshot_sha256=restrictive_rights,
                        retrieval_decision="metadata_only",
                    ),
                ],
                outputs=[output],
                rights_inheritance="most_restrictive",
                redaction_state="reviewed_synthetic",
                evidence_ref="evidence:issue-37",
            )
        self.assertEqual("manifest_rights_mismatch", raised.exception.code)

        restrictive_output = bind_object_receipt(
            {
                **{
                    key: value
                    for key, value in output.items()
                    if key != "receipt_id"
                },
                "rights_snapshot_sha256": restrictive_rights,
                "retrieval_decision": "metadata_only",
            }
        )
        manifest = build_derivation_manifest(
            manifest_id="manifest_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            transformation_id="transform_transcript_v1",
            tool_id="tool_synthetic_transcriber",
            tool_version="1.2.3",
            contract_version=1,
            parameters={"language": "ko"},
            inputs=[
                receipt(object_key=first_key),
                receipt(
                    object_key=second_key,
                    sha256=second_sha,
                    rights_snapshot_sha256=restrictive_rights,
                    retrieval_decision="metadata_only",
                ),
            ],
            outputs=[restrictive_output],
            rights_inheritance="most_restrictive",
            redaction_state="reviewed_synthetic",
            evidence_ref="evidence:issue-37",
        )
        self.assertEqual(
            "metadata_only", manifest["effective_retrieval_decision"]
        )

    def test_equal_restrictive_rights_are_combined_and_hash_lists_are_unique(
        self,
    ) -> None:
        second_rights = hashlib.sha256(b"second restrictive rights").hexdigest()
        combined_rights = hashlib.sha256(
            json.dumps(
                sorted([RIGHTS_SHA, second_rights]),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        raw_key = raw_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            SHA,
        )
        same_hash_derived_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_prior_v1",
            SHA,
        )
        output_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            DERIVED_SHA,
        )
        inputs = [
            receipt(
                object_key=raw_key,
                retrieval_decision="metadata_only",
            ),
            receipt(
                object_key=same_hash_derived_key,
                object_kind="derived",
                transformation_id="transform_prior_v1",
                retrieval_decision="metadata_only",
                rights_snapshot_sha256=second_rights,
            ),
        ]
        output = receipt(
            object_key=output_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
            retrieval_decision="metadata_only",
            rights_snapshot_sha256=combined_rights,
        )
        manifest = build_derivation_manifest(
            manifest_id="manifest_combined_rights_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            transformation_id="transform_transcript_v1",
            tool_id="tool_synthetic_transcriber",
            tool_version="1.2.3",
            contract_version=1,
            parameters={"language": "ko"},
            inputs=inputs,
            outputs=[output],
            rights_inheritance="most_restrictive",
            redaction_state="reviewed_synthetic",
            evidence_ref="evidence:issue-37",
        )
        self.assertEqual(combined_rights, manifest["effective_rights_snapshot_sha256"])
        self.assertEqual([SHA], manifest["input_sha256"])
        Draft202012Validator(
            json.loads(
                (
                    ROOT / "schemas" / "v1" / "derivation-manifest.json"
                ).read_text(encoding="utf-8")
            )
        ).validate(manifest)


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
        self.derived_receipt = receipt(
            object_key=self.derived_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
        )
        self.manifest = build_derivation_manifest(
            manifest_id="manifest_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            transformation_id="transform_transcript_v1",
            tool_id="tool_synthetic_transcriber",
            tool_version="1.2.3",
            contract_version=1,
            parameters={"language": "ko"},
            inputs=[self.raw_receipt],
            outputs=[self.derived_receipt],
            rights_inheritance="most_restrictive",
            redaction_state="reviewed_synthetic",
            evidence_ref="evidence:issue-37",
        )
        self.object_authority = FakeCorpusAuthority(
            [self.raw_receipt, self.derived_receipt], [self.manifest]
        )
        self.root_only_object_authority = FakeCorpusAuthority(
            [self.raw_receipt], []
        )
        self.lineage = self.make_lineage(self.object_authority)
        self.root_only_lineage = self.make_lineage(
            self.root_only_object_authority
        )
        self.authority = self.make_authority()

    def make_lineage(
        self,
        authority: FakeCorpusAuthority,
        *,
        root_receipt_id: str | None = None,
    ) -> dict[str, object]:
        return build_derivation_lineage(
            lineage_id="lineage_synthetic_001",
            authority=authority,
            root_receipt_id=(
                str(self.raw_receipt["receipt_id"])
                if root_receipt_id is None
                else root_receipt_id
            ),
            evidence_ref="evidence:issue-37",
        )

    def make_authority(
        self,
        *,
        legal_hold_state: str = "none",
        expires_at: str = "2026-07-25T00:00:00Z",
        decided_at: str = "2026-07-24T00:00:00Z",
        valid_until: str = "2026-08-01T00:00:00Z",
    ) -> dict[str, object]:
        return build_retention_authority(
            authority_id="retention_authority_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            retention_class="reviewed-retain-30d",
            expires_at=expires_at,
            legal_hold_state=legal_hold_state,
            legal_hold_basis_sha256=(
                hashlib.sha256(b"synthetic legal hold").hexdigest()
                if legal_hold_state == "active"
                else None
            ),
            decided_at=decided_at,
            valid_until=valid_until,
            evidence_ref="evidence:issue-37",
        )

    def make_work(
        self,
        *,
        root: Mapping[str, object] | None = None,
        derived: list[Mapping[str, object]] | None = None,
        lineage: Mapping[str, object] | None = None,
        object_authority: FakeCorpusAuthority | None = None,
        authority: Mapping[str, object] | None = None,
        cleanup_authority: str = "same_proof_disposable",
        cleanup_run_id: str = "run_synthetic_001",
        reason_code: str = "proof_teardown",
        current_time: str = "2026-07-26T00:00:00Z",
    ) -> dict[str, object]:
        selected_lineage = self.lineage if lineage is None else lineage
        selected_root = self.raw_receipt if root is None else root
        selected_derived = [self.derived_receipt] if derived is None else derived
        selected_authority = object_authority
        if selected_authority is None:
            selected_authority = FakeCorpusAuthority(
                [selected_root, *selected_derived],
                (
                    []
                    if selected_lineage["manifest_ids"] == []
                    else [self.manifest]
                ),
            )
        return build_retention_work(
            work_id="retention_work_synthetic_001",
            object_authority=selected_authority,
            lineage_snapshot=selected_lineage,
            retention_authority=self.authority if authority is None else authority,
            current_time=current_time,
            cleanup_authority=cleanup_authority,
            cleanup_run_id=cleanup_run_id,
            reason_code=reason_code,
            evidence_ref="evidence:issue-37",
        )

    def execute(
        self,
        storage: FakeStorage,
        work: Mapping[str, object],
        *,
        authority: Mapping[str, object] | None = None,
        lineage: Mapping[str, object] | None = None,
        object_authority: FakeCorpusAuthority | None = None,
        current_time: str = "2026-07-26T00:00:00Z",
    ) -> dict[str, object]:
        return execute_exact_cleanup(
            storage,
            work,
            object_authority=(
                self.object_authority
                if object_authority is None
                else object_authority
            ),
            current_retention_authority=(
                self.authority if authority is None else authority
            ),
            current_lineage_snapshot=self.lineage if lineage is None else lineage,
            current_time=current_time,
        )

    def test_retention_work_propagates_exact_keys_and_holds_normal_corpus_data(self) -> None:
        work = self.make_work(
            cleanup_authority="held_for_review",
            cleanup_run_id="run_retention_review_001",
            reason_code="retention_expired",
        )
        self.assertEqual("awaiting_review", work["state"])
        self.assertEqual([self.derived_key, self.raw_key], work["exact_object_keys"])
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError) as raised:
            self.execute(storage, work)
        self.assertEqual("cleanup_authority_required", raised.exception.code)
        self.assertEqual([], storage.deleted)

    def test_legal_hold_conflict_never_deletes(self) -> None:
        active = self.make_authority(legal_hold_state="active")
        work = self.make_work(authority=active)
        self.assertEqual("legal_hold_conflict", work["state"])
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError):
            self.execute(storage, work, authority=active)
        self.assertEqual([], storage.deleted)

    def test_same_proof_exact_cleanup_is_idempotent_and_emits_tombstones(self) -> None:
        work = self.make_work()
        storage = FakeStorage()
        for item in (self.raw_receipt, self.derived_receipt):
            storage.objects[str(item["object_key"])] = {
                "byte_size": item["byte_size"],
                "media_type": item["media_type"],
                "sha256": item["sha256"],
            }
        first = self.execute(storage, work)
        second = self.execute(storage, work)
        self.assertEqual("complete", first["state"])
        self.assertEqual("complete", second["state"])
        self.assertEqual(2, len(first["tombstones"]))
        self.assertEqual(2, len(second["tombstones"]))
        self.assertEqual(0, storage.list_calls)

    def test_failed_exact_cleanup_is_durable_and_does_not_broaden_scope(self) -> None:
        work = self.make_work(
            derived=[],
            lineage=self.root_only_lineage,
        )
        storage = FakeStorage()
        storage.objects[self.raw_key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        storage.delete_failure = ConnectionError("private provider failure")
        result = self.execute(
            storage,
            work,
            lineage=self.root_only_lineage,
            object_authority=self.root_only_object_authority,
        )
        self.assertEqual("failed_cleanup", result["state"])
        self.assertEqual([self.raw_key], result["failed_object_keys"])
        self.assertNotIn("private provider failure", json.dumps(result))
        self.assertEqual(0, storage.list_calls)

    def test_same_proof_cleanup_rejects_reused_or_cross_run_objects(self) -> None:
        reused = receipt(
            object_key=self.raw_key,
            create_disposition="reused",
        )
        reused_authority = FakeCorpusAuthority([reused], [])
        reused_lineage = self.make_lineage(
            reused_authority,
            root_receipt_id=str(reused["receipt_id"]),
        )
        with self.assertRaises(CorpusObjectError) as reused_error:
            self.make_work(
                root=reused,
                derived=[],
                lineage=reused_lineage,
                object_authority=reused_authority,
            )
        self.assertEqual("same_proof_authority_mismatch", reused_error.exception.code)

        ambiguous = receipt(
            object_key=self.raw_key,
            create_disposition="reused_after_ambiguous_create",
        )
        ambiguous_authority = FakeCorpusAuthority([ambiguous], [])
        ambiguous_lineage = self.make_lineage(
            ambiguous_authority,
            root_receipt_id=str(ambiguous["receipt_id"]),
        )
        with self.assertRaises(CorpusObjectError) as ambiguous_error:
            self.make_work(
                root=ambiguous,
                derived=[],
                lineage=ambiguous_lineage,
                object_authority=ambiguous_authority,
            )
        self.assertEqual(
            "same_proof_authority_mismatch", ambiguous_error.exception.code
        )

        cross_run = receipt(
            object_key=self.raw_key,
            creation_run_id="run_other_001",
        )
        cross_run_authority = FakeCorpusAuthority([cross_run], [])
        cross_run_lineage = self.make_lineage(
            cross_run_authority,
            root_receipt_id=str(cross_run["receipt_id"]),
        )
        with self.assertRaises(CorpusObjectError) as cross_run_error:
            self.make_work(
                root=cross_run,
                derived=[],
                lineage=cross_run_lineage,
                object_authority=cross_run_authority,
            )
        self.assertEqual(
            "same_proof_authority_mismatch", cross_run_error.exception.code
        )

    def test_executor_revalidates_tampered_work_before_any_delete(self) -> None:
        work = self.make_work(
            derived=[],
            lineage=self.root_only_lineage,
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
            self.execute(
                storage,
                work,
                lineage=self.root_only_lineage,
                object_authority=self.root_only_object_authority,
            )
        self.assertEqual("object_key_mismatch", raised.exception.code)
        self.assertEqual([], storage.deleted)

    def test_current_legal_hold_and_lineage_are_revalidated_at_delete_time(self) -> None:
        work = self.make_work()
        active = self.make_authority(
            legal_hold_state="active",
            decided_at="2026-07-25T12:00:00Z",
        )
        storage = FakeStorage()
        with self.assertRaises(CorpusObjectError) as hold:
            self.execute(storage, work, authority=active)
        self.assertEqual("legal_hold_conflict", hold.exception.code)
        self.assertEqual([], storage.deleted)

        changed_lineage = self.root_only_lineage
        with self.assertRaises(CorpusObjectError) as lineage:
            self.execute(storage, work, lineage=changed_lineage)
        self.assertEqual("derivation_lineage_stale", lineage.exception.code)
        self.assertEqual([], storage.deleted)

    def test_retention_targets_are_exactly_manifest_derived(self) -> None:
        with self.assertRaises(CorpusObjectError) as omitted:
            self.make_work(derived=[])
        self.assertEqual("lineage_receipt_missing", omitted.exception.code)

        unrelated_sha = hashlib.sha256(b"unrelated derivative").hexdigest()
        unrelated_key = derived_object_key(
            "performing-fire/",
            "source_synthetic_001",
            "asset_synthetic_001",
            "transform_transcript_v1",
            unrelated_sha,
        )
        unrelated = receipt(
            object_key=unrelated_key,
            sha256=unrelated_sha,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
        )
        with self.assertRaises(CorpusObjectError) as extra:
            self.make_work(derived=[self.derived_receipt, unrelated])
        self.assertEqual("incomplete_derivation_lineage", extra.exception.code)

    def test_authoritative_receipts_and_new_descendants_are_rechecked(
        self,
    ) -> None:
        root_work = self.make_work(
            derived=[],
            lineage=self.root_only_lineage,
            object_authority=self.root_only_object_authority,
        )
        storage = FakeStorage()
        storage.objects[self.raw_key] = {
            "byte_size": 20,
            "media_type": "video/mp4",
            "sha256": SHA,
        }
        with self.assertRaises(CorpusObjectError) as stale:
            self.execute(
                storage,
                root_work,
                lineage=self.root_only_lineage,
                object_authority=self.object_authority,
            )
        self.assertEqual("derivation_lineage_stale", stale.exception.code)
        self.assertEqual([], storage.deleted)

        forged_root = receipt(
            object_key=self.raw_key,
            creation_run_id="run_forged_001",
        )
        forged_authority = FakeCorpusAuthority([forged_root], [])
        forged_lineage = self.make_lineage(
            forged_authority,
            root_receipt_id=str(forged_root["receipt_id"]),
        )
        forged_work = self.make_work(
            root=forged_root,
            derived=[],
            lineage=forged_lineage,
            object_authority=forged_authority,
            cleanup_run_id="run_forged_001",
        )
        with self.assertRaises(CorpusObjectError):
            self.execute(
                storage,
                forged_work,
                lineage=forged_lineage,
                object_authority=self.root_only_object_authority,
            )
        self.assertEqual([], storage.deleted)

    def test_descendants_cannot_inherit_a_different_retention_class(self) -> None:
        mismatched = receipt(
            object_key=self.derived_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
            retention_class="reviewed-retain-365d",
        )
        mismatched_manifest = build_derivation_manifest(
            manifest_id="manifest_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            transformation_id="transform_transcript_v1",
            tool_id="tool_synthetic_transcriber",
            tool_version="1.2.3",
            contract_version=1,
            parameters={"language": "ko"},
            inputs=[self.raw_receipt],
            outputs=[mismatched],
            rights_inheritance="most_restrictive",
            redaction_state="reviewed_synthetic",
            evidence_ref="evidence:issue-37",
        )
        authority = FakeCorpusAuthority(
            [self.raw_receipt, mismatched], [mismatched_manifest]
        )
        lineage = self.make_lineage(authority)
        with self.assertRaises(CorpusObjectError) as raised:
            self.make_work(
                derived=[mismatched],
                lineage=lineage,
                object_authority=authority,
            )
        self.assertEqual("retention_lineage_mismatch", raised.exception.code)

    def test_lineage_rechecks_manifest_rights_against_receipts(self) -> None:
        tampered = copy.deepcopy(self.manifest)
        tampered["input_rights_snapshot_sha256"] = [
            hashlib.sha256(b"altered rights").hexdigest()
        ]
        payload = {
            key: value
            for key, value in tampered.items()
            if key != "manifest_sha256"
        }
        tampered["manifest_sha256"] = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        authority = FakeCorpusAuthority(
            [self.raw_receipt, self.derived_receipt], [tampered]
        )
        with self.assertRaises(CorpusObjectError) as raised:
            self.make_lineage(authority)
        self.assertEqual(
            "lineage_manifest_receipt_mismatch", raised.exception.code
        )

    def test_runtime_timestamps_are_normalized_to_schema_contract(self) -> None:
        authority = self.make_authority(
            expires_at="2026-07-25T08:00:00.987654+08:00",
            decided_at="2026-07-24T08:00:00.123456+08:00",
            valid_until="2026-08-01T08:00:00.999999+08:00",
        )
        self.assertEqual("2026-07-25T00:00:00Z", authority["expires_at"])
        work = self.make_work(
            authority=authority,
            current_time="2026-07-26T08:00:00.444444+08:00",
        )
        self.assertEqual("2026-07-26T00:00:00Z", work["evaluated_at"])

    def test_real_ledger_is_the_complete_guarded_cleanup_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = ROOT / "tests" / "fixtures" / "records" / "v1"
            records = {
                name: json.loads(
                    (fixture_root / f"{name}.json").read_text(encoding="utf-8")
                )
                for name in ("source", "asset", "rights")
            }
            records["source"]["source_id"] = "source_synthetic_001"
            records["asset"]["source_id"] = "source_synthetic_001"
            records["asset"]["asset_id"] = "asset_synthetic_001"
            records["rights"]["asset_id"] = "asset_synthetic_001"
            with Ledger(Path(temporary) / "ledger.sqlite3") as ledger:
                for name in ("source", "asset", "rights"):
                    ledger.upsert(records[name])
                for value in (
                    self.raw_receipt,
                    self.derived_receipt,
                    self.manifest,
                ):
                    ledger.upsert(value)
                lineage = build_derivation_lineage(
                    lineage_id="lineage_synthetic_ledger_001",
                    authority=ledger,
                    root_receipt_id=str(self.raw_receipt["receipt_id"]),
                    evidence_ref="evidence:issue-37",
                )
                work = build_retention_work(
                    work_id="retention_work_synthetic_ledger_001",
                    object_authority=ledger,
                    lineage_snapshot=lineage,
                    retention_authority=self.authority,
                    current_time="2026-07-26T00:00:00Z",
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
                result = execute_exact_cleanup(
                    storage,
                    work,
                    object_authority=ledger,
                    current_retention_authority=self.authority,
                    current_lineage_snapshot=lineage,
                    current_time="2026-07-26T00:00:00Z",
                )
        self.assertEqual("complete", result["state"])
        self.assertEqual({self.raw_key, self.derived_key}, set(storage.deleted))


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
        self.assertEqual("invalid_object_receipt", raised.exception.code)

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

        forged_authority = {
            **self.expected,
            "creation_run_id": "run_forged_001",
            "create_disposition": "created",
        }
        with self.assertRaises(CorpusObjectError) as authority_error:
            reconcile_receipt_commit(
                self.storage,
                expected_receipt=forged_authority,
                receipt_artifact=None,
                ledger_record=None,
            )
        self.assertEqual(
            "invalid_object_receipt", authority_error.exception.code
        )

    def test_reconciled_receipt_commits_through_the_real_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records = {}
            fixture_root = ROOT / "tests" / "fixtures" / "records" / "v1"
            for name in ("source", "asset", "rights"):
                records[name] = json.loads(
                    (fixture_root / f"{name}.json").read_text(encoding="utf-8")
                )
            records["source"]["source_id"] = "source_synthetic_001"
            records["asset"]["source_id"] = "source_synthetic_001"
            records["asset"]["asset_id"] = "asset_synthetic_001"
            records["rights"]["asset_id"] = "asset_synthetic_001"

            with Ledger(Path(temporary) / "ledger.sqlite3") as ledger:
                for name in ("source", "asset", "rights"):
                    ledger.upsert(records[name])
                result = reconcile_receipt_commit(
                    self.storage,
                    expected_receipt=self.expected,
                    receipt_artifact=self.expected,
                    ledger_record=None,
                )
                self.assertEqual(
                    "write_ledger_from_receipt", result["next_action"]
                )
                ledger.upsert(result["verified_receipt"])
                self.assertEqual(
                    self.expected,
                    ledger.get_record(
                        "object_receipt", str(self.expected["receipt_id"])
                    ),
                )
                for state in (
                    "metadata_verified",
                    "approved_for_ingest",
                    "transfer_pending",
                    "raw_in_object_store",
                ):
                    ledger.transition_asset(
                        "asset_synthetic_001",
                        state,
                        operation_id=f"operation_{state}",
                    )
                self.assertEqual(
                    "raw_in_object_store",
                    ledger.asset_state("asset_synthetic_001"),
                )

    def test_real_ledger_rejects_schema_valid_but_unbound_receipt(self) -> None:
        forged = {
            **self.expected,
            "creation_run_id": "run_forged_001",
        }
        with tempfile.TemporaryDirectory() as temporary:
            records = {}
            fixture_root = ROOT / "tests" / "fixtures" / "records" / "v1"
            for name in ("source", "asset", "rights"):
                records[name] = json.loads(
                    (fixture_root / f"{name}.json").read_text(encoding="utf-8")
                )
            records["source"]["source_id"] = "source_synthetic_001"
            records["asset"]["source_id"] = "source_synthetic_001"
            records["asset"]["asset_id"] = "asset_synthetic_001"
            records["rights"]["asset_id"] = "asset_synthetic_001"
            with Ledger(Path(temporary) / "ledger.sqlite3") as ledger:
                for name in ("source", "asset", "rights"):
                    ledger.upsert(records[name])
                with self.assertRaises(LedgerError):
                    ledger.upsert(forged)


class SchemaTests(unittest.TestCase):
    SCHEMA_NAMES = (
        "raw-object",
        "derived-object",
        "derivation-manifest",
        "derivation-lineage",
        "object-receipt",
        "retention-authority",
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
        derived_receipt = receipt(
            object_key=derived_key,
            sha256=DERIVED_SHA,
            object_kind="derived",
            transformation_id="transform_transcript_v1",
        )
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
        object_authority = FakeCorpusAuthority(
            [raw_receipt, derived_receipt], [manifest]
        )
        lineage = build_derivation_lineage(
            lineage_id="lineage_synthetic_001",
            authority=object_authority,
            root_receipt_id=str(raw_receipt["receipt_id"]),
            evidence_ref="evidence:issue-37",
        )
        authority = build_retention_authority(
            authority_id="retention_authority_synthetic_001",
            source_id="source_synthetic_001",
            asset_id="asset_synthetic_001",
            retention_class="reviewed-retain-30d",
            expires_at="2026-07-25T00:00:00Z",
            legal_hold_state="none",
            legal_hold_basis_sha256=None,
            decided_at="2026-07-24T00:00:00Z",
            valid_until="2026-08-01T00:00:00Z",
            evidence_ref="evidence:issue-37",
        )
        work = build_retention_work(
            work_id="retention_work_synthetic_001",
            object_authority=object_authority,
            lineage_snapshot=lineage,
            retention_authority=authority,
            current_time="2026-07-26T00:00:00Z",
            cleanup_authority="same_proof_disposable",
            cleanup_run_id="run_synthetic_001",
            reason_code="proof_teardown",
            evidence_ref="evidence:issue-37",
        )
        storage = FakeStorage()
        cleanup = execute_exact_cleanup(
            storage,
            work,
            object_authority=object_authority,
            current_retention_authority=authority,
            current_lineage_snapshot=lineage,
            current_time="2026-07-26T00:00:00Z",
        )
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
                "retrieval_decision": "approved",
            },
            "derived-object": {
                "schema_version": 1,
                "record_type": "derived_object",
                "source_id": "source_synthetic_001",
                "asset_id": "asset_synthetic_001",
                "transformation_id": "transform_transcript_v1",
                "input_receipt_ids": [str(raw_receipt["receipt_id"])],
                "object_key": derived_key,
                "sha256": DERIVED_SHA,
                "byte_size": 20,
                "media_type": "application/json",
                "rights_snapshot_sha256": RIGHTS_SHA,
                "retention_class": "reviewed-retain-30d",
                "retrieval_decision": "approved",
                "redaction_state": "reviewed_synthetic",
            },
            "derivation-manifest": manifest,
            "derivation-lineage": lineage,
            "object-receipt": raw_receipt,
            "retention-authority": authority,
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
