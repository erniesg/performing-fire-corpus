from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator

from performing_fire_corpus.observability import (
    METRIC_DEFINITIONS,
    ObservabilityError,
    assert_selected_log_is_safe,
    build_event,
    build_evidence_reference,
    build_metric,
    build_run_manifest,
    safe_serialize,
    secret_presence,
    validate_record,
)


UTC = timezone.utc
EVIDENCE_TIME = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
BOUNDS = {
    "requests": 2,
    "bytes": 4096,
    "pages": 1,
    "retries": 0,
    "elapsed_seconds": 1.5,
}
# Assembled from fragments so the literal machine-local path never appears in
# this repository; `tests/test_public_contract.py` scans for exactly that.
MACHINE_LOCAL_PATH = "/" + "home" + "/maintainer/corpus/run.log"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"
ARTIFACT_SHA256 = "b" * 64
ENVELOPE = {
    "operation": "bounded_discovery",
    "subject_ids": ("discovery_run_001", "antiegg"),
    "lane": "portable",
    "policy_version": "observability_v1",
    "attempt": 1,
    "bound_consumption": BOUNDS,
    "outcome_code": "succeeded",
    "evidence_time": EVIDENCE_TIME,
}


def evidence_reference(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        **ENVELOPE,
        "commit": COMMIT,
        "observed_head": COMMIT,
        "lane_status": "passed",
        "artifact_kind": "evidence_manifest",
        "artifact_sha256": ARTIFACT_SHA256,
    }
    arguments.update(overrides)
    return build_evidence_reference(**arguments)  # type: ignore[arg-type]


OBSERVABILITY_SCHEMAS = (
    "observability-event",
    "observability-metric",
    "run-manifest",
    "evidence-reference",
    "operator-blocker",
    "human-decision",
    "resume-token",
)
ENVELOPE_FIELDS = (
    "operation",
    "subject_ids",
    "lane",
    "policy_version",
    "attempt",
    "bound_consumption",
    "outcome_code",
    "evidence_time",
)


class SchemaContractTests(unittest.TestCase):
    def test_every_contract_is_versioned_strict_and_carries_the_envelope(self) -> None:
        for name in OBSERVABILITY_SCHEMAS:
            with self.subTest(schema=name):
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
                self.assertEqual(False, schema["additionalProperties"])
                self.assertEqual({"const": 1}, schema["properties"]["schema_version"])
                for field in ENVELOPE_FIELDS:
                    self.assertIn(field, schema["required"], f"{name}.{field}")


class SafeSerializerTests(unittest.TestCase):
    def test_allowlisted_values_round_trip_as_json(self) -> None:
        value = {
            "operation": "bounded_discovery",
            "attempt": 3,
            "elapsed_seconds": 0.5,
            "terminal": True,
            "cursor": None,
            "lanes": ["portable", "trusted-vm"],
        }
        serialized = safe_serialize(value)
        self.assertEqual(value, serialized)
        json.dumps(serialized)

    def test_bytes_and_exceptions_fail_closed_instead_of_stringifying(self) -> None:
        for unsafe in (
            b"raw response body",
            bytearray(b"raw response body"),
            memoryview(b"raw response body"),
            ValueError("provider said no"),
            RuntimeError("provider said no"),
            EVIDENCE_TIME,
            {"a", "b"},
            object(),
        ):
            with self.subTest(kind=type(unsafe).__name__):
                with self.assertRaises(ObservabilityError):
                    safe_serialize({"detail": unsafe})

    def test_nested_provider_payload_keys_fail_closed(self) -> None:
        with self.assertRaises(ObservabilityError):
            safe_serialize({"provider": {"Set-Cookie": "session=1"}})
        with self.assertRaises(ObservabilityError):
            safe_serialize({"provider": {1: "positional"}})

    def test_private_and_secret_like_text_fails_closed(self) -> None:
        unsafe_values = (
            MACHINE_LOCAL_PATH,
            "file:///tmp/download.bin",
            "curator@example.invalid",
            "https://bucket.invalid/object?signature=abcdef",
            "AKIA" + "QWERTYUIOPASDFGH",
            "bearer " + "inventedcanarytokenvalue0001",
            "line one\nline two",
            "x" * 513,
        )
        for value in unsafe_values:
            with self.subTest(value=value[:24]):
                with self.assertRaises(ObservabilityError):
                    safe_serialize({"detail": value})

    def test_non_finite_numbers_fail_closed(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ObservabilityError):
                    safe_serialize({"value": value})


class ObservabilityEventTests(unittest.TestCase):
    def test_event_identifies_the_full_operating_envelope(self) -> None:
        event = build_event(**ENVELOPE, severity="info")  # type: ignore[arg-type]
        self.assertEqual("observability_event", event["record_type"])
        self.assertEqual(1, event["schema_version"])
        for field in (
            "operation",
            "subject_ids",
            "lane",
            "policy_version",
            "attempt",
            "bound_consumption",
            "outcome_code",
            "evidence_time",
        ):
            self.assertIn(field, event)
        self.assertEqual("2026-02-03T04:05:06Z", event["evidence_time"])
        self.assertEqual(sorted(BOUNDS), sorted(event["bound_consumption"]))

    def test_event_identity_is_deterministic(self) -> None:
        first = build_event(**ENVELOPE, severity="info")  # type: ignore[arg-type]
        second = build_event(**ENVELOPE, severity="info")  # type: ignore[arg-type]
        self.assertEqual(first, second)

    def test_unknown_field_is_rejected_by_the_strict_schema(self) -> None:
        event = build_event(**ENVELOPE, severity="info")  # type: ignore[arg-type]
        with self.assertRaises(ObservabilityError):
            validate_record(
                "observability-event",
                {**event, "provider_payload": {"status": 500}},
            )

    def test_naive_and_undeclared_envelope_values_fail_closed(self) -> None:
        cases = (
            {"evidence_time": datetime(2026, 2, 3, 4, 5, 6)},
            {"lane": "laptop"},
            {"outcome_code": "probably_fine"},
            {"attempt": 0},
            {"subject_ids": ()},
            {"subject_ids": ("Not A Stable ID",)},
            {"operation": "Bounded Discovery"},
            {"policy_version": "V1"},
            {"bound_consumption": {**BOUNDS, "extra": 1}},
            {"bound_consumption": {**BOUNDS, "requests": -1}},
        )
        for override in cases:
            with self.subTest(override=sorted(override)):
                with self.assertRaises(ObservabilityError):
                    build_event(
                        **{**ENVELOPE, **override},  # type: ignore[arg-type]
                        severity="info",
                    )

    def test_secret_values_never_reach_the_record(self) -> None:
        environ = {
            "R2_ACCESS_KEY_ID": "invented" + "canaryvalue0001",
            "CLOUDFLARE_ACCOUNT_ID": "",
        }
        event = build_event(
            **ENVELOPE,  # type: ignore[arg-type]
            severity="info",
            secret_names=("R2_ACCESS_KEY_ID", "CLOUDFLARE_ACCOUNT_ID"),
            environ=environ,
        )
        self.assertEqual(
            [
                {"secret_name": "R2_ACCESS_KEY_ID", "state": "present"},
                {"secret_name": "CLOUDFLARE_ACCOUNT_ID", "state": "missing"},
            ],
            event["secret_states"],
        )
        self.assertNotIn(environ["R2_ACCESS_KEY_ID"], json.dumps(event))

    def test_secret_name_must_be_an_allowlisted_identifier(self) -> None:
        with self.assertRaises(ObservabilityError):
            secret_presence(("r2 access key",), environ={})


class ObservabilityMetricTests(unittest.TestCase):
    def dimensions(self, **overrides: object) -> dict[str, object]:
        base = {
            "source_id": "antiegg",
            "worker_id": "worker_vm_01",
            "lane": "portable",
            "operation": "bounded_discovery",
        }
        base.update(overrides)
        return base

    def test_every_declared_metric_builds_with_its_fixed_kind_and_unit(self) -> None:
        for metric_name, (kind, unit) in METRIC_DEFINITIONS.items():
            with self.subTest(metric_name=metric_name):
                metric = build_metric(
                    **ENVELOPE,  # type: ignore[arg-type]
                    metric_name=metric_name,
                    value=1,
                    dimensions=self.dimensions(),
                )
                self.assertEqual(kind, metric["metric_kind"])
                self.assertEqual(unit, metric["unit"])
                self.assertEqual(
                    ["lane", "operation", "source_id", "worker_id"],
                    sorted(metric["dimensions"]),
                )

    def test_declared_metrics_cover_every_required_signal(self) -> None:
        for signal in (
            "request_total",
            "byte_total",
            "page_total",
            "retry_total",
            "rate_limit_wait_seconds",
            "lease_active",
            "checkpoint_total",
            "queue_age_seconds",
            "storage_object_total",
            "storage_byte_total",
            "transformation_total",
            "deletion_total",
            "blocker_open",
        ):
            self.assertIn(signal, METRIC_DEFINITIONS)

    def test_undeclared_or_high_cardinality_dimensions_fail_closed(self) -> None:
        cases = (
            {"metric_name": "response_body_size"},
            {"dimensions": self.dimensions(source_id="Not A Source")},
            {"dimensions": self.dimensions(worker_id="worker_" + "a" * 80)},
            {"dimensions": self.dimensions(worker_id="operator@example.invalid")},
            {"dimensions": {**self.dimensions(), "asset_id": "asset_001"}},
            {"dimensions": self.dimensions(lane="trusted-vm")},
            {"value": -1},
        )
        for override in cases:
            with self.subTest(override=sorted(override)):
                arguments = {
                    **ENVELOPE,
                    "metric_name": "request_total",
                    "value": 1,
                    "dimensions": self.dimensions(),
                    **override,
                }
                with self.assertRaises(ObservabilityError):
                    build_metric(**arguments)  # type: ignore[arg-type]


class EvidenceReferenceTests(unittest.TestCase):
    def test_evidence_binds_to_the_exact_head_commit(self) -> None:
        reference = evidence_reference()
        self.assertEqual(COMMIT, reference["commit"])
        self.assertEqual("exact_head", reference["head_state"])
        self.assertEqual(ARTIFACT_SHA256, reference["artifact_sha256"])

    def test_drifted_or_unknown_head_cannot_produce_evidence(self) -> None:
        for observed_head in (OTHER_COMMIT, "unknown", "", COMMIT.upper()):
            with self.subTest(observed_head=observed_head[:12]):
                with self.assertRaises(ObservabilityError):
                    evidence_reference(observed_head=observed_head)

    def test_only_a_lane_that_ran_carries_evidence(self) -> None:
        for lane_status in ("held", "skipped"):
            with self.subTest(lane_status=lane_status):
                with self.assertRaises(ObservabilityError):
                    evidence_reference(lane_status=lane_status)


class RunManifestTests(unittest.TestCase):
    def manifest(self, **overrides: object) -> dict[str, object]:
        reference = evidence_reference()
        arguments: dict[str, object] = {
            **ENVELOPE,
            "subject_ids": ("run_evidence_001", "antiegg"),
            "outcome_code": "held_by_billing",
            "run_id": "run_evidence_001",
            "commit": COMMIT,
            "observed_head": COMMIT,
            "evidence_references": (reference,),
            "lane_results": (
                {
                    "lane_id": "python-test",
                    "lane": "portable",
                    "status": "passed",
                    "held_reason": None,
                    "evidence_reference_id": reference["evidence_reference_id"],
                },
                {
                    "lane_id": "hosted-checks",
                    "lane": "portable",
                    "status": "held",
                    "held_reason": "billing_limit",
                    "evidence_reference_id": None,
                },
            ),
        }
        arguments.update(overrides)
        return build_run_manifest(**arguments)  # type: ignore[arg-type]

    def test_manifest_records_local_evidence_and_held_hosted_checks(self) -> None:
        manifest = self.manifest()
        self.assertEqual("exact_head", manifest["head_state"])
        self.assertEqual(COMMIT, manifest["commit"])
        statuses = {
            result["lane_id"]: result["status"] for result in manifest["lane_results"]
        }
        self.assertEqual({"python-test": "passed", "hosted-checks": "held"}, statuses)
        held = manifest["lane_results"][1]
        self.assertEqual("billing_limit", held["held_reason"])
        self.assertIsNone(held["evidence_reference_id"])

    def test_held_lane_is_never_passed_failed_or_evidence_backed(self) -> None:
        reference = evidence_reference()
        unsafe_results = (
            (
                {
                    "lane_id": "hosted-checks",
                    "lane": "portable",
                    "status": "passed",
                    "held_reason": "billing_limit",
                    "evidence_reference_id": reference["evidence_reference_id"],
                },
            ),
            (
                {
                    "lane_id": "hosted-checks",
                    "lane": "portable",
                    "status": "failed",
                    "held_reason": "spending_limit",
                    "evidence_reference_id": reference["evidence_reference_id"],
                },
            ),
            (
                {
                    "lane_id": "hosted-checks",
                    "lane": "portable",
                    "status": "held",
                    "held_reason": "billing_limit",
                    "evidence_reference_id": reference["evidence_reference_id"],
                },
            ),
            (
                {
                    "lane_id": "hosted-checks",
                    "lane": "portable",
                    "status": "held",
                    "held_reason": None,
                    "evidence_reference_id": None,
                },
            ),
        )
        for lane_results in unsafe_results:
            with self.subTest(status=lane_results[0]["status"]):
                with self.assertRaises(ObservabilityError):
                    self.manifest(
                        lane_results=lane_results,
                        evidence_references=(reference,),
                    )

    def test_evidence_satisfies_only_the_lane_it_actually_ran(self) -> None:
        reference = evidence_reference(lane="trusted-vm")
        with self.assertRaises(ObservabilityError):
            self.manifest(
                lane_results=(
                    {
                        "lane_id": "python-test",
                        "lane": "portable",
                        "status": "passed",
                        "held_reason": None,
                        "evidence_reference_id": reference["evidence_reference_id"],
                    },
                ),
                evidence_references=(reference,),
                outcome_code="succeeded",
            )

    def test_a_skipped_lane_carries_no_evidence(self) -> None:
        reference = evidence_reference()
        with self.assertRaises(ObservabilityError):
            self.manifest(
                lane_results=(
                    {
                        "lane_id": "e2e",
                        "lane": "portable",
                        "status": "skipped",
                        "held_reason": None,
                        "evidence_reference_id": reference["evidence_reference_id"],
                    },
                ),
                evidence_references=(reference,),
                outcome_code="skipped_not_run",
            )

    def test_a_manifest_with_held_lanes_cannot_claim_success(self) -> None:
        with self.assertRaises(ObservabilityError):
            self.manifest(outcome_code="succeeded")

    def test_a_failed_lane_forces_a_failed_closed_outcome(self) -> None:
        reference = evidence_reference(lane_status="failed", outcome_code="failed_closed")
        with self.assertRaises(ObservabilityError):
            self.manifest(
                lane_results=(
                    {
                        "lane_id": "python-test",
                        "lane": "portable",
                        "status": "failed",
                        "held_reason": None,
                        "evidence_reference_id": reference["evidence_reference_id"],
                    },
                ),
                evidence_references=(reference,),
                outcome_code="succeeded",
            )

    def test_drifted_head_cannot_produce_a_run_manifest(self) -> None:
        with self.assertRaises(ObservabilityError):
            self.manifest(observed_head=OTHER_COMMIT)

    def test_evidence_from_another_commit_is_refused(self) -> None:
        reference = evidence_reference(commit=OTHER_COMMIT, observed_head=OTHER_COMMIT)
        with self.assertRaises(ObservabilityError):
            self.manifest(
                lane_results=(
                    {
                        "lane_id": "python-test",
                        "lane": "portable",
                        "status": "passed",
                        "held_reason": None,
                        "evidence_reference_id": reference["evidence_reference_id"],
                    },
                ),
                evidence_references=(reference,),
                outcome_code="succeeded",
            )


class SelectedLogTests(unittest.TestCase):
    def test_content_free_log_lines_are_accepted(self) -> None:
        lines = (
            "operation=bounded_discovery lane=portable outcome_code=succeeded",
            "metric=request_total value=2 source_id=antiegg",
        )
        self.assertEqual(lines, assert_selected_log_is_safe(lines, environ={}))

    def test_unsafe_log_lines_fail_closed(self) -> None:
        for line in (
            f"wrote {MACHINE_LOCAL_PATH}",
            "contact curator@example.invalid for the transcript",
            "authorization bearer " + "inventedcanarytokenvalue0001",
            "signed https://bucket.invalid/object?x-amz-signature=abcdef",
        ):
            with self.subTest(line=line[:24]):
                with self.assertRaises(ObservabilityError):
                    assert_selected_log_is_safe((line,), environ={})


if __name__ == "__main__":
    unittest.main()
