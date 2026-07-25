from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.corpus_objects import (  # noqa: E402
    bind_object_receipt,
    build_retention_authority,
    derived_object_key,
    raw_object_key,
    tombstone_object_key,
)
from performing_fire_corpus.derived_media import (  # noqa: E402
    DERIVED_MEDIA_OPERATIONS,
    DerivedMediaError,
    build_derived_media_result,
    build_transformation_profile,
    evaluate_derived_media_admission,
    evaluate_derived_media_conflicts,
    most_restrictive_retrieval_decision,
    plan_derived_media_job,
    propagate_derived_media_deletion,
    validate_derived_media_job,
    validate_derived_media_result,
    validate_transformation_profile,
)
from performing_fire_corpus.governance import CANONICAL_ENDPOINT_IDS  # noqa: E402
from performing_fire_corpus.qualification import (  # noqa: E402
    QUALIFICATION_OPERATIONS,
    asset_facts_sha256,
    compile_asset_qualification,
)
from performing_fire_corpus.redaction import sanitize  # noqa: E402


NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
SOURCE_ID = "njp-video-library"
ENDPOINT_ID = "njp-video-library-home"
HOST = "njpvideo.ggcf.kr"
ASSET_ID = "asset_derived_media_001"
PREFIX = "performing-fire/"
GOVERNANCE_OPERATIONS = (
    "acquisition_eligibility",
    "caption_retention",
    "deletion",
    "derivative_eligibility",
    "derived_processing",
    "indexing",
    "media_acquisition",
    "metadata_inventory",
    "prose_retention",
    "public_retrieval",
    "retention",
    "search_visibility",
)

# Small invented byte fixtures. Nothing here is derived from a real source.
SYNTHETIC_PAGE_BYTES = b"synthetic-page-v1\n"
SYNTHETIC_AUDIO_BYTES = b"synthetic-audio-v1\n"
SYNTHETIC_VIDEO_BYTES = b"synthetic-video-v1\n"
SYNTHETIC_OCR_FACT_BYTES = b"synthetic-ocr-facts-v1\n"
SYNTHETIC_TRANSCRIPT_FACT_BYTES = b"synthetic-transcript-facts-v1\n"
SYNTHETIC_VIDEO_FACT_BYTES = b"synthetic-video-facts-v1\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _test_digest(value: dict[str, object]) -> str:
    """Mirror the module's canonical digest so a forged record can be re-bound."""

    encoded = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_fields(record: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"job_id", "job_sha256"}
    }


INPUT_BYTES = {
    "ocr": SYNTHETIC_PAGE_BYTES,
    "transcription": SYNTHETIC_AUDIO_BYTES,
    "video_understanding": SYNTHETIC_VIDEO_BYTES,
}
OUTPUT_BYTES = {
    "ocr": SYNTHETIC_OCR_FACT_BYTES,
    "transcription": SYNTHETIC_TRANSCRIPT_FACT_BYTES,
    "video_understanding": SYNTHETIC_VIDEO_FACT_BYTES,
}
INPUT_MEDIA_TYPES = {
    "ocr": "image/png",
    "transcription": "audio/ogg",
    "video_understanding": "video/mp4",
}


def _canonical_sha256(payload: dict[str, object]) -> str:
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inventory_authority(asset_value: dict[str, object]) -> dict[str, object]:
    payload = {
        "schema_version": 1,
        "record_type": "inventory_authority",
        "inventory_record_id": asset_value["inventory_record_id"],
        "source_id": asset_value["source_id"],
        "endpoint_id": asset_value["endpoint_id"],
        "source_item_id": asset_value["source_item_id"],
        "source_scope_id": "source_scope_njp_video_library",
        "youtube_channel_id": None,
        "youtube_channel_lineage_sha256": None,
        "youtube_handle": None,
        "youtube_session_binding_sha256": None,
        "youtube_uploads_lineage_sha256": None,
        "youtube_uploads_manifest_sha256": None,
        "youtube_uploads_playlist_id": None,
        "youtube_video_ids": [],
    }
    return {**payload, "inventory_record_sha256": _canonical_sha256(payload)}


def asset() -> dict[str, object]:
    value: dict[str, object] = {
        "source_id": SOURCE_ID,
        "endpoint_id": ENDPOINT_ID,
        "asset_id": ASSET_ID,
        "inventory_record_id": "inventory_derived_media_001",
        "inventory_record_sha256": "0" * 64,
        "source_item_id": "derivedmedia001",
        "asset_kind": "media",
        "public_url": f"https://{HOST}/derived-media-object",
        "expected_host": HOST,
        "media_type": "video/mp4",
        "max_bytes": 4096,
        "access_state": "available",
        "retention_class": "selected_raw",
        "deletion_policy": "review_on_revocation",
        "derivative_policy": "operation_specific",
        "retrieval_policy": "public",
        "planned_object_key": raw_object_key(
            PREFIX, SOURCE_ID, ASSET_ID, digest(SYNTHETIC_VIDEO_BYTES)
        ),
    }
    value["inventory_record_sha256"] = inventory_authority(value)[
        "inventory_record_sha256"
    ]
    return value


def source_governance(
    source_id: str, endpoint_id: str | None, asset_id: str | None, suffix: str
) -> dict[str, object]:
    fact_states = {
        "access_control": "allowed",
        "api_availability": "available",
        "authentication": "not_required",
        "copyright_lawful_basis": "permitted",
        "platform_terms": "permitted",
        "robots": "allowed",
    }
    operation_states = {name: "approved" for name in GOVERNANCE_OPERATIONS}
    return {
        "schema_version": 1,
        "record_type": "source_governance",
        "source_governance_id": f"source_governance_{suffix}",
        "source_id": source_id,
        "endpoint_id": endpoint_id,
        "asset_id": asset_id,
        "fact_states": fact_states,
        "observations": [
            {
                "dimension": dimension,
                "state": state,
                "observed_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-08-30T00:00:00Z",
                "evidence_id": f"evidence_synthetic_{dimension}",
                "next_safe_action": "Revalidate the synthetic authority.",
            }
            for dimension, state in sorted(fact_states.items())
        ],
        "operation_states": operation_states,
        "decisions": [
            {
                "affected_operation": operation,
                "state": state,
                "authority_class": "source_policy_reviewer",
                "basis_code": "synthetic_reviewed_policy",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-08-30T00:00:00Z",
                "review_trigger": "Recheck when source policy changes.",
                "next_safe_action": "Use only the reviewed operation.",
            }
            for operation, state in sorted(operation_states.items())
        ],
        "blockers": [],
        "evaluated_at": "2026-07-23T00:00:00Z",
    }


def governance_registry(asset_value: dict[str, object]) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for source_id, endpoint_ids in CANONICAL_ENDPOINT_IDS.items():
        records.append(
            source_governance(
                source_id, None, None, f"{source_id.replace('-', '_')}_source"
            )
        )
        for endpoint_id in endpoint_ids:
            records.append(
                source_governance(
                    source_id,
                    endpoint_id,
                    None,
                    f"{endpoint_id.replace('-', '_')}_endpoint",
                )
            )
    records.append(
        source_governance(
            str(asset_value["source_id"]),
            str(asset_value["endpoint_id"]),
            str(asset_value["asset_id"]),
            f"{str(asset_value['source_id']).replace('-', '_')}_asset",
        )
    )
    return {
        "schema_version": 1,
        "registry_id": "performing-fire-source-governance",
        "records": sorted(
            (copy.deepcopy(record) for record in records),
            key=lambda record: (
                str(record["source_id"]),
                str(record["endpoint_id"] or ""),
                str(record.get("asset_id") or ""),
                str(record["source_governance_id"]),
            ),
        ),
    }


def decisions(
    asset_value: dict[str, object], **states: str
) -> list[dict[str, object]]:
    snapshot = asset_facts_sha256(asset_value)
    return [
        {
            "operation": operation,
            "state": states.get(operation, "approved"),
            "decision_scope": (
                "source_policy"
                if operation == "metadata_retention"
                else "asset_specific"
            ),
            "basis_code": (
                "reviewed_metadata_basis"
                if operation == "metadata_retention"
                else "asset_specific_permission"
            ),
            "authority_class": "rights_reviewer",
            "evidence_ref": f"evidence_{operation}",
            "decided_at": "2026-07-24T00:00:00Z",
            "expires_at": "2026-08-30T00:00:00Z",
            "review_trigger": "Recheck when rights or asset facts change.",
            "asset_facts_sha256": snapshot,
            "retention_class": asset_value["retention_class"],
        }
        for operation in QUALIFICATION_OPERATIONS
    ]


def qualification(**states: str) -> dict[str, object]:
    asset_value = asset()
    return compile_asset_qualification(
        asset_value,
        inventory_authority(asset_value),
        governance_registry(asset_value),
        decisions(asset_value, **states),
        now=NOW,
    )


def profile(operation: str, **overrides: object) -> dict[str, object]:
    names = {
        "ocr": "ocr-result",
        "transcription": "transcription-result",
        "video_understanding": "video-understanding-result",
    }
    records = {
        "ocr": "ocr_result",
        "transcription": "transcription_result",
        "video_understanding": "video_understanding_result",
    }
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "transformation_profile",
        "profile_id": f"profile_{operation.replace('_', '-')}-local-v1",
        "profile_version": 1,
        "operation": operation,
        "contract_version": 1,
        "allowed_tool_classes": [f"local_offline_{operation}"],
        "allowed_tool_ids": [f"tool_local-{operation.replace('_', '-')}"],
        "minimum_tool_version": "1.0.0",
        "maximum_tool_version": "1.9.9",
        "allowed_input_media_types": [INPUT_MEDIA_TYPES[operation]],
        "allowed_languages": ["en", "ko"],
        "output_record_type": records[operation],
        "output_schema_id": (
            "https://performing-fire-corpus.invalid/schemas/v1/"
            f"{names[operation]}.json"
        ),
        "output_media_type": "application/json",
        "resource_bounds": {
            "maximum_input_bytes": 8388608,
            "maximum_output_bytes": 1048576,
            "maximum_cpu_seconds": 120,
            "maximum_memory_bytes": 1073741824,
            "maximum_disk_bytes": 2147483648,
            "maximum_elapsed_seconds": 300,
        },
        "retention_class": "selected_derived",
        "redaction_state": "structured_facts_only",
        "maximum_retrieval_decision": "metadata_only",
        "minimum_confidence_milli": 700,
        "external_service_policy": "local_offline_only",
        "model_trace_retention": "none",
    }
    value.update(overrides)
    return build_transformation_profile(value)


def receipt(operation: str, **overrides: object) -> dict[str, object]:
    content = INPUT_BYTES[operation]
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "object_receipt",
        "object_kind": "raw",
        "source_id": SOURCE_ID,
        "asset_id": ASSET_ID,
        "object_key": raw_object_key(PREFIX, SOURCE_ID, ASSET_ID, digest(content)),
        "byte_size": len(content),
        "media_type": INPUT_MEDIA_TYPES[operation],
        "sha256": digest(content),
        "rights_snapshot_sha256": "c" * 64,
        "retention_class": "selected_raw",
        "creation_run_id": "run_synthetic-derived-media",
        "retrieval_decision": "approved",
        "evidence_ref": "evidence_synthetic_receipt",
        "verification_state": "verified",
        "create_disposition": "created",
    }
    value.update(overrides)
    return bind_object_receipt(value)


def derived_receipt() -> dict[str, object]:
    content = SYNTHETIC_OCR_FACT_BYTES
    transformation_id = "transform_synthetic-ocr"
    return bind_object_receipt(
        {
            "schema_version": 1,
            "record_type": "object_receipt",
            "object_kind": "derived",
            "source_id": SOURCE_ID,
            "asset_id": ASSET_ID,
            "transformation_id": transformation_id,
            "object_key": derived_object_key(
                PREFIX, SOURCE_ID, ASSET_ID, transformation_id, digest(content)
            ),
            "byte_size": len(content),
            "media_type": "image/png",
            "sha256": digest(content),
            "rights_snapshot_sha256": "c" * 64,
            "retention_class": "selected_derived",
            "creation_run_id": "run_synthetic-derived-media",
            "retrieval_decision": "approved",
            "evidence_ref": "evidence_synthetic_receipt",
            "verification_state": "verified",
            "create_disposition": "created",
        }
    )


def retention_authority(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "authority_id": "retention_authority_synthetic-derived-media",
        "source_id": SOURCE_ID,
        "asset_id": ASSET_ID,
        "retention_class": "selected_raw",
        "expires_at": "2026-12-31T00:00:00Z",
        "legal_hold_state": "none",
        "legal_hold_basis_sha256": None,
        "decided_at": "2026-07-24T00:00:00Z",
        "valid_until": "2026-08-30T00:00:00Z",
        "evidence_ref": "evidence_synthetic_retention",
    }
    value.update(overrides)
    return build_retention_authority(**value)  # type: ignore[arg-type]


def tombstone(operation: str, **overrides: object) -> dict[str, object]:
    """Build a content-bound tombstone the way exact cleanup would emit one."""

    target = receipt(operation)
    identity: dict[str, object] = {
        "retention_work_id": "retention_work_synthetic",
        "receipt_id": target["receipt_id"],
        "source_id": SOURCE_ID,
        "asset_id": ASSET_ID,
        "deleted_object_key": target["object_key"],
        "deleted_object_sha256": target["sha256"],
        "reason_code": "rights_revoked",
        "evidence_ref": "evidence_synthetic_tombstone",
    }
    identity.update(overrides)
    tombstone_id = "tombstone_" + hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "object_tombstone",
        "tombstone_id": tombstone_id,
        **identity,
        "tombstone_object_key": tombstone_object_key(
            PREFIX,
            str(identity["source_id"]),
            str(identity["asset_id"]),
            tombstone_id,
            str(identity["deleted_object_sha256"]),
        ),
        "deletion_state": "absent_exact_key",
        "deleted_at": "2026-07-25T00:00:00Z",
    }


class FakeDeletionAuthority:
    def __init__(self, tombstones: dict[str, dict[str, object]] | None = None) -> None:
        self.tombstones = tombstones or {}
        self.lookups: list[str] = []

    def resolve_tombstone_by_key(
        self, *, object_key: str
    ) -> dict[str, object] | None:
        self.lookups.append(object_key)
        return self.tombstones.get(object_key)


class UnavailableDeletionAuthority:
    def resolve_tombstone_by_key(self, *, object_key: str) -> dict[str, object] | None:
        raise RuntimeError("synthetic tombstone authority outage")


def tool(operation: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tool_id": f"tool_local-{operation.replace('_', '-')}",
        "tool_class": f"local_offline_{operation}",
        "tool_version": "1.2.0",
        "contract_version": 1,
    }
    value.update(overrides)
    return value


def plan(operation: str, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "profile": profile(operation),
        "qualification": qualification(),
        "input_receipt": receipt(operation),
        "retention_authority": retention_authority(),
        "deletion_authority": FakeDeletionAuthority(),
        "tool": tool(operation),
        "evidence_ref": "evidence_synthetic_derived_media",
        "language_hint": "en",
        "medium_hint": None,
        "now": NOW,
    }
    arguments.update(overrides)
    return plan_derived_media_job(**arguments)  # type: ignore[arg-type]


def result_draft(operation: str, job: dict[str, object]) -> dict[str, object]:
    content = OUTPUT_BYTES[operation]
    shared: dict[str, object] = {
        "schema_version": 1,
        "operation": operation,
        "job_id": job["job_id"],
        "profile_id": job["profile_id"],
        "profile_version": job["profile_version"],
        "profile_sha256": job["profile_sha256"],
        "source_id": job["source_id"],
        "asset_id": job["asset_id"],
        "input_object_key": job["input_object_key"],
        "input_sha256": job["input_sha256"],
        "output_object_key": (
            f"{PREFIX}v1/derived/{SOURCE_ID}/{ASSET_ID}/"
            f"transform_synthetic-{operation.replace('_', '-')}/{digest(content)}"
        ),
        "output_sha256": digest(content),
        "output_byte_size": len(content),
        "output_media_type": job["output_media_type"],
        "tool_id": job["tool_id"],
        "tool_class": job["tool_class"],
        "tool_version": job["tool_version"],
        "contract_version": job["contract_version"],
        "parameters_sha256": "e" * 64,
        "requested_language": "en",
        "detected_language": "en",
        "quality_state": "accepted",
        "rights_snapshot_sha256": job["rights_snapshot_sha256"],
        "retention_class": job["retention_class"],
        "retrieval_decision": job["retrieval_decision"],
        "redaction_state": job["redaction_state"],
        "interpretation": "model_output_not_ground_truth",
        "source_excerpt_retention": "none",
        "model_trace_retention": "none",
        "evidence_ref": job["evidence_ref"],
        "observed_at": "2026-07-25T12:05:00Z",
    }
    if operation == "ocr":
        shared.update(
            {
                "record_type": "ocr_result",
                "pages": [
                    {
                        "page_index": 0,
                        "width_px": 1240,
                        "height_px": 1754,
                        "block_count": 3,
                        "line_count": 9,
                        "word_count": 42,
                        "mean_confidence_milli": 900,
                    },
                    {
                        "page_index": 1,
                        "width_px": 1240,
                        "height_px": 1754,
                        "block_count": 2,
                        "line_count": 5,
                        "word_count": 18,
                        "mean_confidence_milli": 820,
                    },
                ],
                "word_count": 60,
                "line_count": 14,
                "mean_confidence_milli": 860,
                "minimum_observed_confidence_milli": 820,
            }
        )
    elif operation == "transcription":
        shared.update(
            {
                "record_type": "transcription_result",
                "segments": [
                    {
                        "segment_index": 0,
                        "start_ms": 0,
                        "end_ms": 4000,
                        "word_count": 11,
                        "confidence_milli": 910,
                    },
                    {
                        "segment_index": 1,
                        "start_ms": 4000,
                        "end_ms": 9000,
                        "word_count": 14,
                        "confidence_milli": 870,
                    },
                ],
                "segment_count": 2,
                "word_count": 25,
                "media_duration_ms": 9000,
                "mean_confidence_milli": 890,
                "minimum_observed_confidence_milli": 870,
                "waveform_retention": "none",
            }
        )
    else:
        shared.update(
            {
                "record_type": "video_understanding_result",
                "observations": [
                    {
                        "observation_index": 0,
                        "observation_kind": "shot",
                        "observation_label": "stage_lighting_change",
                        "start_ms": 0,
                        "end_ms": 5000,
                        "confidence_milli": 880,
                    },
                    {
                        "observation_index": 1,
                        "observation_kind": "event",
                        "observation_label": "performer_visible",
                        "start_ms": 2000,
                        "end_ms": 8000,
                        "confidence_milli": 760,
                    },
                ],
                "observation_count": 2,
                "shot_count": 1,
                "event_count": 1,
                "media_duration_ms": 9000,
                "mean_confidence_milli": 820,
                "minimum_observed_confidence_milli": 760,
                "frame_retention": "none",
            }
        )
    return shared


class DerivedMediaSchemaTests(unittest.TestCase):
    SCHEMA_NAMES = (
        "transformation-profile",
        "derived-media-job",
        "ocr-result",
        "transcription-result",
        "video-understanding-result",
    )

    def test_new_schemas_are_strict_versioned_and_self_describing(self) -> None:
        for name in self.SCHEMA_NAMES:
            with self.subTest(name=name):
                schema = json.loads(
                    (ROOT / "schemas" / "v1" / f"{name}.json").read_text(
                        encoding="utf-8"
                    )
                )
                Draft202012Validator.check_schema(schema)
                self.assertEqual(
                    f"https://performing-fire-corpus.invalid/schemas/v1/{name}.json",
                    schema["$id"],
                )
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual({"const": 1}, schema["properties"]["schema_version"])
                self.assertEqual(
                    sorted(schema["properties"]), sorted(schema["required"])
                )

    def test_new_schemas_are_not_duplicated_into_the_package_tree(self) -> None:
        for name in self.SCHEMA_NAMES:
            with self.subTest(name=name):
                self.assertFalse(
                    (
                        ROOT
                        / "src"
                        / "performing_fire_corpus"
                        / "schemas"
                        / "v1"
                        / f"{name}.json"
                    ).exists()
                )


class TransformationProfileTests(unittest.TestCase):
    def test_each_operation_has_its_own_versioned_profile_and_output(self) -> None:
        expected = {
            "ocr": "ocr_result",
            "transcription": "transcription_result",
            "video_understanding": "video_understanding_result",
        }
        self.assertEqual(tuple(expected), DERIVED_MEDIA_OPERATIONS)
        seen: set[str] = set()
        for operation in DERIVED_MEDIA_OPERATIONS:
            with self.subTest(operation=operation):
                record = profile(operation)
                self.assertEqual(expected[operation], record["output_record_type"])
                self.assertEqual(1, record["profile_version"])
                self.assertEqual("local_offline_only", record["external_service_policy"])
                self.assertEqual("none", record["model_trace_retention"])
                seen.add(str(record["profile_sha256"]))
        self.assertEqual(3, len(seen))

    def test_profile_output_record_must_match_its_operation(self) -> None:
        drifted = dict(profile("ocr"))
        drifted["output_record_type"] = "transcription_result"
        with self.assertRaises(DerivedMediaError):
            validate_transformation_profile(drifted)

    def test_profile_hash_must_bind_every_bound(self) -> None:
        tampered = dict(profile("ocr"))
        bounds = dict(tampered["resource_bounds"])  # type: ignore[arg-type]
        bounds["maximum_input_bytes"] = 1073741824
        tampered["resource_bounds"] = bounds
        with self.assertRaises(DerivedMediaError):
            validate_transformation_profile(tampered)

    def test_inverted_tool_version_range_is_rejected(self) -> None:
        with self.assertRaises(DerivedMediaError):
            profile("ocr", minimum_tool_version="2.0.0", maximum_tool_version="1.0.0")


class AdmissionTests(unittest.TestCase):
    def test_every_operation_admits_and_queues_a_complete_job(self) -> None:
        for operation in DERIVED_MEDIA_OPERATIONS:
            with self.subTest(operation=operation):
                job = plan(operation)
                self.assertEqual(operation, job["operation"])
                self.assertEqual(digest(INPUT_BYTES[operation]), job["input_sha256"])
                self.assertEqual(len(INPUT_BYTES[operation]), job["input_byte_size"])
                self.assertEqual("approved", job["rights_state"])
                self.assertEqual("not_applicable", job["consent_state"])
                self.assertEqual("metadata_only", job["retrieval_decision"])
                self.assertEqual(job, validate_derived_media_job(job))

    def test_queued_job_inherits_the_most_restrictive_retrieval_decision(self) -> None:
        self.assertEqual(
            "blocked", most_restrictive_retrieval_decision(("approved", "blocked"))
        )
        job = plan("ocr", profile=profile("ocr", maximum_retrieval_decision="approved"))
        self.assertEqual("approved", job["retrieval_decision"])
        # A blocked ceiling is inherited, and a blocked asset never queues.
        with self.assertRaises(DerivedMediaError):
            plan("ocr", profile=profile("ocr", maximum_retrieval_decision="blocked"))

    def test_missing_transformation_right_denies_the_job(self) -> None:
        denied = qualification(ocr="pending")
        admission = evaluate_derived_media_admission(
            profile=profile("ocr"),
            qualification=denied,
            input_receipt=receipt("ocr"),
            retention_authority=retention_authority(),
            deletion_authority=FakeDeletionAuthority(),
            tool=tool("ocr"),
            language_hint="en",
            now=NOW,
        )
        self.assertFalse(admission["eligible"])
        self.assertIn("rights:not_eligible", admission["reasons"])
        with self.assertRaises(DerivedMediaError):
            plan("ocr", qualification=denied)

    def test_each_denial_reason_is_reported_without_content(self) -> None:
        cases = (
            (
                "profile:tool_not_allowed",
                {"tool": tool("ocr", tool_id="tool_unapproved-cloud")},
            ),
            (
                "profile:tool_class_not_allowed",
                {"tool": tool("ocr", tool_class="hosted_model_api")},
            ),
            (
                "profile:tool_version_above_maximum",
                {"tool": tool("ocr", tool_version="2.5.0")},
            ),
            (
                "profile:tool_version_below_minimum",
                {"tool": tool("ocr", tool_version="0.9.0")},
            ),
            (
                "profile:media_type_not_allowed",
                {"input_receipt": receipt("transcription")},
            ),
            ("profile:language_not_supported", {"language_hint": "fr"}),
            (
                "input:not_raw_object",
                {"input_receipt": derived_receipt()},
            ),
            (
                "retrieval:blocked",
                {"input_receipt": receipt("ocr", retrieval_decision="blocked")},
            ),
            (
                "retention:legal_hold",
                {
                    "retention_authority": retention_authority(
                        legal_hold_state="active",
                        legal_hold_basis_sha256="f" * 64,
                    )
                },
            ),
            (
                "retention:expired",
                {
                    "retention_authority": retention_authority(
                        expires_at="2026-07-01T00:00:00Z"
                    )
                },
            ),
            (
                "retention:authority_stale",
                {
                    "retention_authority": retention_authority(
                        decided_at="2026-07-20T00:00:00Z",
                        valid_until="2026-07-24T00:00:00Z",
                    )
                },
            ),
            (
                "deletion:input_tombstoned",
                {
                    "deletion_authority": FakeDeletionAuthority(
                        {
                            str(receipt("ocr")["object_key"]): tombstone("ocr"),
                        }
                    )
                },
            ),
            (
                "deletion:authority_unavailable",
                {"deletion_authority": UnavailableDeletionAuthority()},
            ),
            ("consent:withdrawn", {"consent_state": "withdrawn"}),
        )
        for reason, overrides in cases:
            with self.subTest(reason=reason):
                arguments: dict[str, object] = {
                    "profile": profile("ocr"),
                    "qualification": qualification(),
                    "input_receipt": receipt("ocr"),
                    "retention_authority": retention_authority(),
                    "deletion_authority": FakeDeletionAuthority(),
                    "tool": tool("ocr"),
                    "language_hint": "en",
                    "now": NOW,
                }
                arguments.update(overrides)
                admission = evaluate_derived_media_admission(**arguments)  # type: ignore[arg-type]
                self.assertFalse(admission["eligible"])
                self.assertIn(reason, admission["reasons"])
                self.assertEqual(
                    sanitize(admission, environ={}), admission
                )

    def test_resource_exhaustion_denies_admission(self) -> None:
        bounded = profile(
            "ocr",
            resource_bounds={
                "maximum_input_bytes": 4,
                "maximum_output_bytes": 8,
                "maximum_cpu_seconds": 1,
                "maximum_memory_bytes": 1024,
                "maximum_disk_bytes": 2048,
                "maximum_elapsed_seconds": 1,
            },
        )
        admission = evaluate_derived_media_admission(
            profile=bounded,
            qualification=qualification(),
            input_receipt=receipt("ocr"),
            retention_authority=retention_authority(),
            deletion_authority=FakeDeletionAuthority(),
            tool=tool("ocr"),
            language_hint="en",
            now=NOW,
        )
        self.assertIn("bounds:input_too_large", admission["reasons"])
        with self.assertRaises(DerivedMediaError):
            plan("ocr", profile=bounded)

    def test_project_native_source_requires_granted_consent(self) -> None:
        admission = evaluate_derived_media_admission(
            profile=profile("ocr"),
            qualification=qualification(),
            input_receipt=receipt(
                "ocr",
                source_id="project-native-workshop",
                object_key=raw_object_key(
                    PREFIX,
                    "project-native-workshop",
                    ASSET_ID,
                    digest(SYNTHETIC_PAGE_BYTES),
                ),
            ),
            retention_authority=retention_authority(
                source_id="project-native-workshop"
            ),
            deletion_authority=FakeDeletionAuthority(),
            tool=tool("ocr"),
            language_hint="en",
            now=NOW,
        )
        self.assertIn("consent:required", admission["reasons"])

    def test_exact_key_deletion_authority_is_always_consulted(self) -> None:
        authority = FakeDeletionAuthority()
        plan("ocr", deletion_authority=authority)
        self.assertEqual([str(receipt("ocr")["object_key"])], authority.lookups)

    def test_a_tombstone_for_another_key_is_never_permission(self) -> None:
        mismatched = FakeDeletionAuthority(
            {str(receipt("ocr")["object_key"]): tombstone("transcription")}
        )
        admission = evaluate_derived_media_admission(
            profile=profile("ocr"),
            qualification=qualification(),
            input_receipt=receipt("ocr"),
            retention_authority=retention_authority(),
            deletion_authority=mismatched,
            tool=tool("ocr"),
            language_hint="en",
            now=NOW,
        )
        self.assertIn("deletion:authority_key_mismatch", admission["reasons"])

    def test_a_tampered_retention_authority_is_refused(self) -> None:
        tampered = dict(retention_authority())
        tampered["expires_at"] = "2027-12-31T00:00:00Z"
        tampered["legal_hold_state"] = "none"
        with self.assertRaises(DerivedMediaError):
            evaluate_derived_media_admission(
                profile=profile("ocr"),
                qualification=qualification(),
                input_receipt=receipt("ocr"),
                retention_authority=tampered,
                deletion_authority=FakeDeletionAuthority(),
                tool=tool("ocr"),
                language_hint="en",
                now=NOW,
            )

    def test_a_tampered_input_receipt_is_refused(self) -> None:
        tampered = dict(receipt("ocr"))
        tampered["retrieval_decision"] = "approved"
        tampered["rights_snapshot_sha256"] = "0" * 64
        with self.assertRaises(DerivedMediaError):
            evaluate_derived_media_admission(
                profile=profile("ocr"),
                qualification=qualification(),
                input_receipt=tampered,
                retention_authority=retention_authority(),
                deletion_authority=FakeDeletionAuthority(),
                tool=tool("ocr"),
                language_hint="en",
                now=NOW,
            )

    def test_unknown_consent_state_is_refused_without_echoing_it(self) -> None:
        with self.assertRaises(DerivedMediaError) as caught:
            evaluate_derived_media_admission(
                profile=profile("ocr"),
                qualification=qualification(),
                input_receipt=receipt("ocr"),
                retention_authority=retention_authority(),
                deletion_authority=FakeDeletionAuthority(),
                tool=tool("ocr"),
                language_hint="en",
                consent_state="withdrawn by someone@example.invalid on /var/notes",
                now=NOW,
            )
        self.assertNotIn("someone", str(caught.exception))
        self.assertNotIn("/var/", str(caught.exception))

    def test_interrupted_planning_resumes_to_the_identical_job(self) -> None:
        first = plan("transcription")
        second = plan("transcription")
        self.assertEqual(first, second)
        self.assertEqual(first["job_id"], second["job_id"])

    def test_tampered_job_identity_is_rejected(self) -> None:
        job = dict(plan("ocr"))
        job["tool_version"] = "1.5.0"
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_job(job)


class DerivedResultTests(unittest.TestCase):
    def test_each_operation_separates_derived_facts_from_its_source(self) -> None:
        for operation in DERIVED_MEDIA_OPERATIONS:
            with self.subTest(operation=operation):
                job = plan(operation)
                record = build_derived_media_result(result_draft(operation, job))
                self.assertNotEqual(
                    record["input_object_key"], record["output_object_key"]
                )
                self.assertNotEqual(record["input_sha256"], record["output_sha256"])
                self.assertEqual(
                    "model_output_not_ground_truth", record["interpretation"]
                )
                self.assertEqual("none", record["source_excerpt_retention"])
                self.assertEqual("none", record["model_trace_retention"])
                self.assertEqual(
                    "evidence_synthetic_derived_media", record["evidence_ref"]
                )
                self.assertEqual(record, validate_derived_media_result(record, job=job))

    def test_operation_specific_facts_are_structured_and_content_free(self) -> None:
        ocr = build_derived_media_result(result_draft("ocr", plan("ocr")))
        self.assertEqual(2, len(ocr["pages"]))
        self.assertEqual(60, ocr["word_count"])

        transcript = build_derived_media_result(
            result_draft("transcription", plan("transcription"))
        )
        self.assertEqual("none", transcript["waveform_retention"])
        self.assertEqual(2, transcript["segment_count"])

        video = build_derived_media_result(
            result_draft("video_understanding", plan("video_understanding"))
        )
        self.assertEqual("none", video["frame_retention"])
        self.assertEqual(1, video["shot_count"])
        self.assertEqual(1, video["event_count"])

    def test_no_prompt_trace_or_excerpt_can_enter_a_derived_record(self) -> None:
        unsafe_fields = (
            "prompt",
            "chain_of_thought",
            "provider_response",
            "frame_bytes",
            "waveform",
            "source_excerpt",
            "recognized_text",
        )
        job = plan("ocr")
        for field in unsafe_fields:
            with self.subTest(field=field):
                draft = result_draft("ocr", job)
                draft[field] = "invented synthetic value"
                with self.assertRaises(DerivedMediaError):
                    build_derived_media_result(draft)

    def test_result_must_match_its_admitted_job(self) -> None:
        job = plan("ocr")
        other = plan("transcription")
        record = build_derived_media_result(result_draft("ocr", job))
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(record, job=other)

    def test_output_beyond_the_admitted_bound_is_rejected(self) -> None:
        job = plan("ocr")
        draft = result_draft("ocr", job)
        draft["output_byte_size"] = 1048577
        record = build_derived_media_result(draft)
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(record, job=job)

    def test_counts_and_confidence_must_agree_with_their_facts(self) -> None:
        job = plan("ocr")
        drifted = result_draft("ocr", job)
        drifted["word_count"] = 61
        with self.assertRaises(DerivedMediaError):
            build_derived_media_result(drifted)

        job = plan("transcription")
        overlapping = result_draft("transcription", job)
        segments = list(overlapping["segments"])  # type: ignore[arg-type]
        segments[1] = {**segments[1], "start_ms": 1000}
        overlapping["segments"] = segments
        with self.assertRaises(DerivedMediaError):
            build_derived_media_result(overlapping)

    def test_a_result_cannot_outlive_the_rights_decision(self) -> None:
        job = plan("ocr")
        stale = result_draft("ocr", job)
        stale["observed_at"] = "2099-01-01T00:00:00Z"
        record = build_derived_media_result(stale)
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(record, job=job)

        early = result_draft("ocr", job)
        early["observed_at"] = "2026-01-01T00:00:00Z"
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(
                build_derived_media_result(early), job=job
            )

    def test_repeated_video_observation_spans_are_rejected(self) -> None:
        job = plan("video_understanding")
        draft = result_draft("video_understanding", job)
        observations = list(draft["observations"])  # type: ignore[arg-type]
        observations[1] = {
            **observations[0],
            "observation_index": 1,
            "confidence_milli": 760,
        }
        draft["observations"] = observations
        draft["shot_count"] = 2
        draft["event_count"] = 0
        with self.assertRaises(DerivedMediaError):
            build_derived_media_result(draft)

    def test_tampered_result_hash_is_rejected(self) -> None:
        record = dict(build_derived_media_result(result_draft("ocr", plan("ocr"))))
        record["quality_state"] = "low_confidence"
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(record)


class ConflictTests(unittest.TestCase):
    def test_clear_run_reports_no_conflict(self) -> None:
        job = plan("ocr")
        record = build_derived_media_result(result_draft("ocr", job))
        review = evaluate_derived_media_conflicts(
            job, profile=profile("ocr"), results=[record]
        )
        self.assertTrue(review["clear"])
        self.assertEqual([], review["conflicts"])

    def test_duplicate_transformation_by_profile_and_input_hash(self) -> None:
        job = plan("ocr")
        prior = plan("ocr", now=datetime(2026, 7, 25, 13, 0, 0, tzinfo=timezone.utc))
        self.assertNotEqual(job["job_id"], prior["job_id"])
        review = evaluate_derived_media_conflicts(
            job, profile=profile("ocr"), prior_jobs=[prior]
        )
        self.assertIn("duplicate_transformation", review["conflicts"])
        self.assertEqual([prior["job_id"]], review["duplicate_job_ids"])

    def test_conflicting_output_receipts_are_detected(self) -> None:
        job = plan("ocr")
        first = build_derived_media_result(result_draft("ocr", job))
        other = result_draft("ocr", job)
        other["output_sha256"] = digest(b"another-synthetic-fact-object\n")
        other["output_object_key"] = (
            f"{PREFIX}v1/derived/{SOURCE_ID}/{ASSET_ID}/transform_synthetic-ocr/"
            + digest(b"another-synthetic-fact-object\n")
        )
        second = build_derived_media_result(other)
        review = evaluate_derived_media_conflicts(
            job, profile=profile("ocr"), results=[first, second]
        )
        self.assertIn("conflicting_output_receipt", review["conflicts"])

    def test_low_confidence_and_unsupported_language_are_detected(self) -> None:
        job = plan("video_understanding")
        draft = result_draft("video_understanding", job)
        observations = list(draft["observations"])  # type: ignore[arg-type]
        observations[1] = {**observations[1], "confidence_milli": 120}
        draft["observations"] = observations
        draft["mean_confidence_milli"] = (880 + 120) // 2
        draft["minimum_observed_confidence_milli"] = 120
        draft["detected_language"] = "ja"
        record = build_derived_media_result(draft)
        review = evaluate_derived_media_conflicts(
            job, profile=profile("video_understanding"), results=[record]
        )
        self.assertIn("low_confidence", review["conflicts"])
        self.assertIn("unsupported_language", review["conflicts"])

    def test_tool_version_drift_is_detected(self) -> None:
        job = plan("ocr")
        draft = result_draft("ocr", job)
        draft["tool_version"] = "1.4.0"
        record = build_derived_media_result(draft)
        review = evaluate_derived_media_conflicts(
            job, profile=profile("ocr"), results=[record]
        )
        self.assertIn("tool_version_drift", review["conflicts"])

    def test_an_unapproved_tool_cannot_masquerade_as_an_admitted_one(self) -> None:
        job = plan("ocr")
        draft = result_draft("ocr", job)
        draft["tool_id"] = "tool_unapproved-cloud-vision"
        draft["tool_class"] = "hosted_model_api"
        record = build_derived_media_result(draft)
        with self.assertRaises(DerivedMediaError):
            validate_derived_media_result(record, job=job)

    def test_a_forged_job_is_caught_against_its_own_profile(self) -> None:
        forged = _without_fields(plan("ocr"))
        forged["tool_id"] = "tool_unapproved-cloud-vision"
        forged["tool_class"] = "hosted_model_api"
        forged["tool_version"] = "9.9.9"
        forged["input_media_type"] = "application/pdf"
        forged["job_id"] = "derivedjob_" + _test_digest(forged)[:24]
        forged["job_sha256"] = _test_digest(forged)
        # The job is internally consistent and validates on its own...
        self.assertEqual(forged, validate_derived_media_job(forged))
        # ...but the one review holding both records refuses it.
        review = evaluate_derived_media_conflicts(forged, profile=profile("ocr"))
        self.assertFalse(review["clear"])
        for code in (
            "tool_not_allowed",
            "tool_version_drift",
            "media_type_not_allowed",
        ):
            self.assertIn(code, review["conflicts"])

    def test_two_derived_keys_for_one_job_are_a_conflict(self) -> None:
        job = plan("ocr")
        first = build_derived_media_result(result_draft("ocr", job))
        other = result_draft("ocr", job)
        other["output_object_key"] = str(other["output_object_key"]).replace(
            "transform_synthetic-ocr", "transform_synthetic-ocr-two"
        )
        second = build_derived_media_result(other)
        self.assertEqual(first["output_sha256"], second["output_sha256"])
        review = evaluate_derived_media_conflicts(
            job, profile=profile("ocr"), results=[first, second]
        )
        self.assertIn("conflicting_output_receipt", review["conflicts"])

    def test_conflict_review_requires_the_jobs_exact_profile(self) -> None:
        job = plan("ocr")
        with self.assertRaises(DerivedMediaError):
            evaluate_derived_media_conflicts(job, profile=profile("transcription"))


class DeletionPropagationTests(unittest.TestCase):
    def records(self) -> list[dict[str, object]]:
        return [
            build_derived_media_result(result_draft(operation, plan(operation)))
            for operation in DERIVED_MEDIA_OPERATIONS
        ]

    def trigger(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "reason_code": "rights_revoked",
            "source_id": SOURCE_ID,
            "asset_id": ASSET_ID,
            "input_object_key": raw_object_key(
                PREFIX, SOURCE_ID, ASSET_ID, digest(SYNTHETIC_PAGE_BYTES)
            ),
            "input_sha256": digest(SYNTHETIC_PAGE_BYTES),
            "derived_data_treatment": "delete_on_withdrawal",
        }
        value.update(overrides)
        return value

    def test_revocation_reaches_every_derivative_of_the_whole_asset(self) -> None:
        records = self.records()
        plan_result = propagate_derived_media_deletion(self.trigger(), records)
        self.assertTrue(plan_result["complete"])
        self.assertEqual(
            sorted(str(record["result_id"]) for record in records),
            plan_result["result_ids"],
        )

    def test_exact_key_deletion_stays_on_that_one_object(self) -> None:
        records = self.records()
        plan_result = propagate_derived_media_deletion(
            self.trigger(reason_code="exact_key_deleted"), records
        )
        self.assertEqual([records[0]["result_id"]], plan_result["result_ids"])

    def test_revocation_reaches_derived_index_and_export_targets(self) -> None:
        records = self.records()
        ocr = records[0]
        plan_result = propagate_derived_media_deletion(
            self.trigger(reason_code="exact_key_deleted"),
            records,
            index_entries=[
                {
                    "index_document_id": "index_document_synthetic",
                    "field_id": "field_ocr_word_count",
                    "result_id": ocr["result_id"],
                }
            ],
            export_entries=[
                {"export_id": "export_score_generation_1", "result_id": ocr["result_id"]}
            ],
        )
        self.assertTrue(plan_result["complete"])
        self.assertEqual("delete", plan_result["derived_action"])
        self.assertEqual([ocr["result_id"]], plan_result["result_ids"])
        self.assertEqual(
            [ocr["output_object_key"]], plan_result["derived_object_keys"]
        )
        self.assertEqual(
            [
                {
                    "index_document_id": "index_document_synthetic",
                    "field_id": "field_ocr_word_count",
                    "reindex_action": "remove_exact_field",
                }
            ],
            plan_result["index_targets"],
        )
        self.assertEqual(["export_score_generation_1"], plan_result["export_targets"])

    def test_every_reason_code_propagates(self) -> None:
        records = self.records()
        for reason in (
            "consent_withdrawn",
            "exact_key_deleted",
            "retention_expired",
            "rights_revoked",
            "source_corrected",
            "transformation_replaced",
        ):
            with self.subTest(reason=reason):
                plan_result = propagate_derived_media_deletion(
                    self.trigger(reason_code=reason), records
                )
                self.assertEqual(reason, plan_result["reason_code"])
                # Rights, consent, and retention are asset-scoped; the rest
                # name one exact object.
                expected = 3 if reason in {
                    "consent_withdrawn",
                    "retention_expired",
                    "rights_revoked",
                } else 1
                self.assertEqual(expected, len(plan_result["result_ids"]))

    def test_review_treatment_does_not_claim_deletion(self) -> None:
        plan_result = propagate_derived_media_deletion(
            self.trigger(derived_data_treatment="review_on_withdrawal"),
            self.records(),
        )
        self.assertEqual("review", plan_result["derived_action"])

    def test_unknown_downstream_entry_blocks_completeness(self) -> None:
        plan_result = propagate_derived_media_deletion(
            self.trigger(),
            self.records(),
            export_entries=[
                {"export_id": "export_unknown", "result_id": "ocrresult_" + "9" * 24}
            ],
        )
        self.assertFalse(plan_result["complete"])
        self.assertEqual(
            ["ocrresult_" + "9" * 24], plan_result["unresolved_result_ids"]
        )

    def test_a_different_asset_is_never_swept_in(self) -> None:
        plan_result = propagate_derived_media_deletion(
            self.trigger(asset_id="asset_unrelated_002"), self.records()
        )
        self.assertEqual([], plan_result["result_ids"])
        self.assertEqual([], plan_result["derived_object_keys"])

    def test_a_chained_derivative_and_its_export_are_swept_too(self) -> None:
        records = self.records()
        first = records[0]
        chained_draft = result_draft("ocr", plan("ocr"))
        chained_draft["input_object_key"] = first["output_object_key"]
        chained_draft["input_sha256"] = first["output_sha256"]
        chained_draft["output_object_key"] = str(
            chained_draft["output_object_key"]
        ).replace("transform_synthetic-ocr", "transform_synthetic-ocr-second")
        chained_draft["output_sha256"] = digest(b"synthetic-second-generation-v1\n")
        chained = build_derived_media_result(chained_draft)
        plan_result = propagate_derived_media_deletion(
            self.trigger(reason_code="exact_key_deleted"),
            [*records, chained],
            export_entries=[
                {
                    "export_id": "export_score_generation_2",
                    "result_id": chained["result_id"],
                }
            ],
        )
        self.assertIn(chained["result_id"], plan_result["result_ids"])
        self.assertEqual(["export_score_generation_2"], plan_result["export_targets"])
        self.assertTrue(plan_result["complete"])

    def test_free_text_never_reaches_the_deletion_plan(self) -> None:
        records = self.records()
        unsafe = (
            {
                "index_entries": [
                    {
                        "index_document_id": "/var/scans/page one.bin",
                        "field_id": "field_ocr",
                        "result_id": str(records[0]["result_id"]),
                    }
                ]
            },
            {
                "export_entries": [
                    {
                        "export_id": "recognized line: contact someone@example.invalid",
                        "result_id": str(records[0]["result_id"]),
                    }
                ]
            },
        )
        for overrides in unsafe:
            with self.subTest(entry=sorted(overrides)[0]):
                with self.assertRaises(DerivedMediaError):
                    propagate_derived_media_deletion(
                        self.trigger(), records, **overrides  # type: ignore[arg-type]
                    )

    def test_the_plan_survives_central_redaction(self) -> None:
        plan_result = propagate_derived_media_deletion(self.trigger(), self.records())
        self.assertEqual(sanitize(plan_result, environ={}), plan_result)

    def test_unknown_reason_code_is_refused(self) -> None:
        with self.assertRaises(DerivedMediaError):
            propagate_derived_media_deletion(
                self.trigger(reason_code="because_we_felt_like_it"), self.records()
            )


class SanitizedRecordTests(unittest.TestCase):
    def test_every_derived_media_record_survives_central_redaction(self) -> None:
        for operation in DERIVED_MEDIA_OPERATIONS:
            with self.subTest(operation=operation):
                job = plan(operation)
                record = build_derived_media_result(result_draft(operation, job))
                for value in (profile(operation), job, record):
                    self.assertEqual(sanitize(value, environ={}), value)

    def test_records_carry_no_machine_local_path_or_public_url(self) -> None:
        job = plan("ocr")
        record = build_derived_media_result(result_draft("ocr", job))
        for value in (job, record):
            text = json.dumps(value, sort_keys=True)
            self.assertNotIn("/tmp/", text)
            self.assertNotIn("/home/", text)
            self.assertNotIn(HOST, text)
            self.assertNotIn("derived-media-object", text)
        # The only URI in a job is the non-resolvable output schema identifier.
        self.assertEqual(
            [
                "https://performing-fire-corpus.invalid/schemas/v1/ocr-result.json",
            ],
            [item for item in job.values() if str(item).startswith("https://")],
        )
        self.assertEqual(
            [], [item for item in record.values() if str(item).startswith("https://")]
        )


if __name__ == "__main__":
    unittest.main()
