"""Canary-driven secret scanning across specs, docs, fixtures, and evidence.

Every canary below is invented and assembled at run time from fragments, so
the literal never appears anywhere in the repository. That lets the same
constants prove two things at once: the detector really catches them, and the
repository really does not contain them.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.observability import (
    ObservabilityError,
    assert_selected_log_is_safe,
    build_event,
    build_evidence_reference,
    build_run_manifest,
    safe_serialize,
)
from performing_fire_corpus.redaction import contains_secret_like_text
from performing_fire_corpus.operator_gates import OperatorGateError, partition_work


SCANNED_ROOTS = (
    ".agent",
    "config",
    "docs",
    "infra",
    "schemas",
    "scripts",
    "src",
    "tests/fixtures",
)
SCANNED_FILES = ("AGENTS.md", "README.md", "pyproject.toml")
IGNORED_PARTS = {".git", "evidence", "harness-backups", "harness-runs", "vm-runs"}
# `rucksack github token OWNER/REPO ...` is the command that mints a token. The
# repository slug after the verb is a public identifier, not a credential value,
# so the generic "label then token-shaped value" heuristic misreads it. This is
# the only documented exemption; every other line in every scanned file is held
# to the detector.
COMMAND_NOT_A_CREDENTIAL = re.compile(
    r"^rucksack github token [A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\s|$)"
)

# Invented canaries. Each is built from fragments so the whole value is never
# written down in this repository, in a fixture, or in generated evidence.
CANARIES = (
    "AKIA" + "QWERTYUIOPASDFGH",
    "ghp_" + "inventedcanary000000abcdef",
    "sk-" + "inventedcanary000000abcdef",
    "xoxb-" + "1234567890-invented-canary-value",
    "glpat-" + "inventedcanary000000abcdef",
    "authorization bearer " + "inventedcanarytokenvalue0001",
)
UTC = timezone.utc
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BOUNDS = {
    "requests": 1,
    "bytes": 128,
    "pages": 1,
    "retries": 0,
    "elapsed_seconds": 0.25,
}
ENVELOPE = {
    "operation": "run_evidence",
    "subject_ids": ("run_canary_001",),
    "lane": "portable",
    "policy_version": "observability_v1",
    "attempt": 1,
    "bound_consumption": BOUNDS,
    "outcome_code": "succeeded",
    "evidence_time": datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC),
}


def scanned_files() -> list[Path]:
    paths: list[Path] = []
    for name in SCANNED_FILES:
        candidate = ROOT / name
        if candidate.is_file():
            paths.append(candidate)
    for root in SCANNED_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        paths.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts)
        )
    return paths


class CanaryDetectorTests(unittest.TestCase):
    def test_every_canary_is_detected_as_credential_like(self) -> None:
        for canary in CANARIES:
            with self.subTest(canary=canary[:8]):
                self.assertTrue(contains_secret_like_text(canary))

    def test_no_canary_appears_in_specs_docs_fixtures_or_config(self) -> None:
        offenders: list[str] = []
        for path in scanned_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(canary in text for canary in CANARIES):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_repository_specs_and_fixtures_carry_no_credential_like_text(self) -> None:
        offenders: list[str] = []
        for path in scanned_files():
            if path.suffix == ".py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if COMMAND_NOT_A_CREDENTIAL.match(line.strip()):
                    continue
                if contains_secret_like_text(line):
                    offenders.append(
                        f"{path.relative_to(ROOT).as_posix()}:{number}"
                    )
        self.assertEqual([], offenders)


class CanaryRejectionTests(unittest.TestCase):
    def test_the_serializer_refuses_every_canary(self) -> None:
        for canary in CANARIES:
            with self.subTest(canary=canary[:8]):
                with self.assertRaises(ObservabilityError):
                    safe_serialize({"detail": canary})

    def test_selected_evidence_logs_refuse_every_canary(self) -> None:
        for canary in CANARIES:
            with self.subTest(canary=canary[:8]):
                with self.assertRaises(ObservabilityError):
                    assert_selected_log_is_safe(
                        (f"operation=run_evidence detail={canary}",), environ={}
                    )

    def test_a_generated_sanitized_manifest_never_carries_a_canary(self) -> None:
        reference = build_evidence_reference(
            **ENVELOPE,  # type: ignore[arg-type]
            commit=COMMIT,
            observed_head=COMMIT,
            lane_status="passed",
            artifact_kind="sanitized_manifest",
            artifact_sha256="c" * 64,
        )
        manifest = build_run_manifest(
            **ENVELOPE,  # type: ignore[arg-type]
            run_id="run_canary_001",
            commit=COMMIT,
            observed_head=COMMIT,
            evidence_references=(reference,),
            lane_results=(
                {
                    "lane_id": "python-test",
                    "lane": "portable",
                    "status": "passed",
                    "held_reason": None,
                    "evidence_reference_id": reference["evidence_reference_id"],
                },
            ),
        )
        serialized = json.dumps(manifest, sort_keys=True)
        for canary in CANARIES:
            self.assertNotIn(canary, serialized)
        self.assertFalse(contains_secret_like_text(serialized))

    def test_an_environment_secret_value_never_reaches_a_record(self) -> None:
        environ = {"R2_SECRET_ACCESS_KEY": CANARIES[0]}
        event = build_event(
            **ENVELOPE,  # type: ignore[arg-type]
            severity="info",
            secret_names=("R2_SECRET_ACCESS_KEY",),
            environ=environ,
        )
        self.assertEqual(
            [{"secret_name": "R2_SECRET_ACCESS_KEY", "state": "present"}],
            event["secret_states"],
        )
        self.assertNotIn(CANARIES[0], json.dumps(event))

    def test_queued_work_carrying_a_canary_fails_closed(self) -> None:
        with self.assertRaises(OperatorGateError):
            partition_work(
                (
                    {
                        "job_id": "job_canary_001",
                        "endpoint_id": CANARIES[1],
                        "source_id": "antiegg",
                        "worker_id": "worker_vm_01",
                    },
                ),
                (),
            )


if __name__ == "__main__":
    unittest.main()
