from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", "evidence", "harness-backups", "harness-runs", "vm-runs"}
FORBIDDEN_SUFFIXES = {
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".wav",
}
PRIVATE_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")


def public_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts)
    ]


class PublicRepositoryContractTests(unittest.TestCase):
    def test_editable_dependency_bootstrap_keeps_evidence_checkout_clean(
        self,
    ) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("*.egg-info/", ignored)

    def test_local_rucksack_session_state_is_ignored_exactly(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".local/rucksack-vm-sessions.json", ignored)
        self.assertIn(".local/rucksack-vm-sessions.json.lock", ignored)

    def test_network_smoke_live_state_root_is_ignored_exactly(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".local/network-smoke/", ignored)

        check = subprocess.run(
            [
                "git",
                "check-ignore",
                "--",
                ".local/network-smoke/ledger.sqlite3",
                ".local/network-smoke/manifest.json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if check.returncode == 128:
            self.skipTest("git work tree unavailable")
        self.assertEqual(0, check.returncode)
        self.assertEqual(
            [
                ".local/network-smoke/ledger.sqlite3",
                ".local/network-smoke/manifest.json",
            ],
            sorted(check.stdout.split()),
        )

        tracked = subprocess.run(
            ["git", "ls-files", "--", ".local"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, tracked.returncode)
        self.assertEqual("", tracked.stdout.strip())

    def test_repository_contains_no_source_documents_or_media(self) -> None:
        forbidden = [
            path.relative_to(ROOT).as_posix()
            for path in public_files()
            if path.suffix.lower() in FORBIDDEN_SUFFIXES
        ]
        self.assertEqual([], forbidden)

    def test_text_files_do_not_contain_machine_local_home_paths(self) -> None:
        offenders: list[str] = []
        for path in public_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if PRIVATE_PATH.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_public_brief_pins_source_and_privacy_boundaries(self) -> None:
        brief = (ROOT / "docs" / "PROJECT_BRIEF.md").read_text(encoding="utf-8")
        for value in (
            "https://njpvideo.ggcf.kr/",
            "https://njp.ggcf.kr/",
            "https://antiegg.kr/25502/",
            "Forbidden in Git, GitHub, logs, screenshots, fixtures, and evidence",
            "Model/effort racing is disabled",
            "trusted-laptop",
        ):
            self.assertIn(value, brief)

    def test_network_smoke_run_is_documented_as_opt_in_and_metadata_only(
        self,
    ) -> None:
        smoke = (ROOT / "docs" / "network-acquisition-smoke.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "opt-in",
            "trusted VM",
            "inventory-public",
            "--max-requests 2",
            "--ledger",
            "--sanitized-manifest",
            "unauthenticated",
            "must not be added to portable CI",
            ".local/network-smoke/",
        ):
            self.assertIn(value, smoke)

    def test_metadata_readiness_proof_covers_restart_privacy_and_gap_matrix(
        self,
    ) -> None:
        proof = (ROOT / "docs" / "metadata-readiness-proof.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "900e63b",
            "https://antiegg.kr/robots.txt",
            "https://antiegg.kr/25502/",
            "response_oversized",
            "assets, jobs, requests, and blockers were not duplicated",
            "No response body was written",
            "First-usable-slice gap matrix",
            "follow-up issue 6",
        ):
            self.assertIn(value, proof)

    def test_metadata_readiness_proof_marks_observations_as_expired_hypotheses(
        self,
    ) -> None:
        proof = (ROOT / "docs" / "metadata-readiness-proof.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(proof.split())
        for value in (
            "historical evidence",
            "not a current source fact",
            "expired hypotheses",
            "has not yet produced a current observation",
            "no request was made",
            "environment gate rather than a source observation",
            "Next safe action",
            ".local/network-smoke/",
            "Sanitized observations (issue 7, historical)",
        ):
            self.assertIn(value, normalized)

    def test_r2_runbook_documents_the_low_level_held_transfer_boundary(self) -> None:
        runbook = (ROOT / "docs" / "r2-object-storage.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "r2 transfer-approved",
            "--plan",
            "--ledger",
            "--cache-directory",
            "--output",
            "does not authorize a live transfer",
        ):
            self.assertIn(value, runbook)

    def test_r2_runbook_documents_the_held_trusted_vm_one_object_command(self) -> None:
        runbook = (ROOT / "docs" / "r2-object-storage.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "trusted-vm acquire-one-to-r2",
            "--approval .local/r2-proof/approval.json",
            "--database .local/r2-proof/ledger.sqlite3",
            "--storage-config .agent/storage.yaml",
            "--cache-directory .local/r2-proof/cache",
            "--sanitized-output .local/r2-proof/receipts",
            "delete_after_verification",
            "infra/vm/verify.sh",
            "held",
            "Do not commit",
        ):
            self.assertIn(value, runbook)

    def test_current_r2_proof_specs_match_the_delete_only_operator(self) -> None:
        decision = (
            ROOT / "docs" / "issues" / "025-decide-current-one-asset-r2-proof.md"
        ).read_text(encoding="utf-8")
        run = (
            ROOT / "docs" / "issues" / "026-run-current-one-asset-r2-proof.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Cleanup decision: delete_after_verification", decision)
        self.assertIn("Retention is outside this proof", decision)
        self.assertIn("Retention is not supported by this operator", run)
        for unsupported_path in (
            "or reviewed retention",
            "cleanup or retention",
            "temporary or reviewed retained",
        ):
            self.assertNotIn(unsupported_path, decision)
            self.assertNotIn(unsupported_path, run)

    def test_rich_corpus_selection_keeps_inventory_and_authority_separate(
        self,
    ) -> None:
        policy = (ROOT / "docs" / "rich-corpus-selection.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "source universe and the selected rich corpus are different",
            "Authority before ranking",
            "Popularity and ease of download",
            "accepted selection inputs",
            "pipeline proof is excluded from automatic selection",
            "underrepresented",
            "stable IDs",
        ):
            self.assertIn(value, policy)

    def test_search_index_contract_is_field_level_and_fail_closed(self) -> None:
        contract = (
            ROOT / "docs" / "provenance-aware-search-index.md"
        ).read_text(encoding="utf-8")
        for value in (
            "complete known metadata universe",
            "Field-level boundary",
            "Query-time authority",
            "Unknown never defaults visible",
            "full source prose",
            "trusted authority boundary",
            "exact-field removal",
            "not a deployed search service",
            "performing-fire-sanitized-text-v1",
            "structural validation only",
            "central redaction module",
        ):
            self.assertIn(value, contract)

    def test_full_corpus_object_contract_is_explicit_and_fake_only(self) -> None:
        contract = (
            ROOT / "docs" / "full-corpus-object-storage.md"
        ).read_text(encoding="utf-8")
        for value in (
            "Full-corpus object-storage contract",
            "v1/raw/",
            "v1/derived/",
            "v1/manifests/",
            "v1/tombstones/",
            "exact-key `HEAD`",
            "most restrictive",
            "write_ledger_from_receipt",
            "write_receipt_from_ledger",
            "binds every immutable receipt fact",
            "`reused_after_ambiguous_create`",
            "`object_receipt` records",
            "complete derivation-lineage snapshot",
            "current retention/legal-hold authority",
            "revalidated immediately before deletion",
            "same_proof_disposable",
            "never authorizes deletion of a reused or pre-existing object",
            "No production operation is authorized",
            "fake storage",
            "must not list",
            "must not delete a bucket or prefix",
        ):
            self.assertIn(value, contract)

    def test_rights_qualification_is_operation_specific_and_content_free(
        self,
    ) -> None:
        contract = (ROOT / "docs" / "rights-qualification.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "An approval for one operation does not imply another",
            "exactly nine operations",
            "public visibility is never sufficient",
            "current platform authority",
            "401/403",
            "Counts are deliberately reported as unknown",
            "only the qualification ID, source ID",
            "exact immutable R2 object key",
            "grants no acquisition or deletion authority",
        ):
            self.assertIn(value, contract)

    def test_project_native_lifecycle_is_synthetic_and_fail_closed(self) -> None:
        contract = (ROOT / "docs" / "project-native-lifecycle.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(contract.split())
        for value in (
            "invented records only",
            "does not deploy an intake surface",
            "pseudonymous contribution ID",
            "specific, withdrawable consent",
            "intersection of input uses and audiences",
            "most restrictive input confidentiality",
            "earliest input retention expiry",
            "raw objects, derived objects, index documents",
            "content-free tombstone",
            "mandatory review time",
            "no indefinite default",
            "issue #46",
        ):
            self.assertIn(value, normalized)

    def test_derived_media_workflows_are_operation_specific_and_content_free(
        self,
    ) -> None:
        contract = (ROOT / "docs" / "derived-media-workflows.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(contract.split())
        for value in (
            "Separate profiles, never one opaque extraction job",
            "never executes a tool",
            "a missing decision is a denial, not a default",
            "an unavailable authority or a tombstone for a different key is a "
            "denial, never permission",
            "most restrictive",
            "output key and hash must differ from the input key and hash",
            "No recognized text",
            "No transcript text and no waveform",
            "No frames",
            "model_output_not_ground_truth",
            "No prompt, chain-of-thought, provider response",
            "Derived content itself remains in R2 under its rights class",
            "duplicate_transformation",
            "tool_version_drift",
            "remove_exact_field",
            "a partial inventory can never present itself as a finished propagation",
            "the descendant graph is exactly one level deep",
            "Chained derivation is out of scope here",
            "Scope follows the record that decided it",
            "reach every derivative of that asset",
            "Neither scope may leak into the other",
            "Every reason is a fixed literal",
            "always safe to log",
            "Prefer local offline tools",
        ):
            self.assertIn(value, normalized)


if __name__ == "__main__":
    unittest.main()
