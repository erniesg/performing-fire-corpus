from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.cli import build_parser  # noqa: E402
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
MATRIX = ROOT / "docs" / "product-readiness-matrix.md"
BACKTICKED = re.compile(r"`([^`\s]+)`")
REFERENCED_SUFFIXES = (".md", ".py", ".yaml", ".yml", ".toml", ".sql")
READINESS_STATES = (
    "contract-only",
    "implemented-offline",
    "live-proven",
    "held",
    "planned",
    "absent",
)


def implemented_command_chains(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, ...]]:
    """Every runnable subcommand chain reachable from the CLI parser."""
    chains: list[tuple[str, ...]] = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            nested = implemented_command_chains(subparser)
            if nested:
                chains.extend((name, *chain) for chain in nested)
            else:
                chains.append((name,))
    return chains


def documented_command_chains(text: str) -> set[tuple[str, ...]]:
    """Subcommand chains documented as `performing-fire-corpus ...` invocations."""
    chains: set[tuple[str, ...]] = set()
    for command in re.findall(r"`performing-fire-corpus ([^`]+)`", text):
        chain: list[str] = []
        for token in command.split():
            if token.startswith("-") or token.startswith("<"):
                break
            chain.append(token)
        if chain:
            chains.add(tuple(chain))
    return chains


def table_rows(text: str, header_cell: str) -> list[list[str]]:
    """Body rows of the first markdown table whose first header cell matches."""
    rows: list[list[str]] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            collecting = False
            continue
        cells = [
            cell.strip().replace("\\|", "|")
            for cell in re.split(r"(?<!\\)\|", stripped.strip("|"))
        ]
        if not collecting:
            collecting = cells[0] == header_cell
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def referenced_paths(text: str) -> list[str]:
    return [
        token
        for token in BACKTICKED.findall(text)
        if "/" in token
        and "<" not in token
        and not token.startswith("/")
        and token.endswith(REFERENCED_SUFFIXES)
    ]


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

    def test_antiegg_metadata_adapters_are_documented_as_bound_and_prose_free(
        self,
    ) -> None:
        contract = (ROOT / "docs" / "antiegg-metadata-adapters.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(contract.split())
        for value in (
            "secondary Korean editorial and Fluxus context",
            "shape-bound",
            "sitemap adapter itself remains held",
            "numeric WordPress `id`",
            "`content` is absent from `_fields`",
            "two stable records",
            "is_completeness_guarantee: false",
            "blocks that endpoint only",
        ):
            self.assertIn(value, normalized)

        smoke = (ROOT / "docs" / "network-acquisition-smoke.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "docs/antiegg-metadata-adapters.md",
            "public readability is not ingestion",
        ):
            self.assertIn(value, " ".join(smoke.split()))

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

    def test_search_surface_is_local_rights_filtered_and_leak_aware(self) -> None:
        surface = (
            ROOT / "docs" / "rights-filtered-search-surface.md"
        ).read_text(encoding="utf-8")
        for value in (
            "hosted operator UI",
            "exact manifest keys only",
            "No prefix listing",
            "Deterministic upsert and restart",
            "ranked or serialized",
            "empty facets for every",
            "answer-independent",
            "never a signed URL",
            "cache and never a grant",
            "Reviewer replay",
            "Loopback API",
        ):
            self.assertIn(value, surface)

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

    def test_safe_observability_contract_is_allowlisted_and_fail_closed(self) -> None:
        contract = (
            ROOT / "docs" / "safe-observability-and-evidence.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(contract.split())
        for value in (
            "`safe_serialize` is an allowlist, not a filter",
            "exception objects are refused, not `str()`-ed",
            "unknown fields are refused by `additionalProperties: false`",
            "There is no code path that turns an unrecognized input into a "
            "diagnostic string",
            "reports only the name and `present` or `missing`",
            "never carry content or a high-cardinality identifier",
            "assembles invented canaries at run time from fragments",
            "a drifted or unestablished head raises instead of producing evidence",
            "Held is not passed",
            "held CI is not run evidence",
            "may satisfy only the lane it actually ran",
            "Red/green/refactor TDD",
            "Small focused PRs",
            "run `scripts/agent-evidence` and attach the manifest",
            "No merge bypass",
            "separate privacy-safe issue in `erniesg/rucksack`",
        ):
            self.assertIn(value, normalized)

    def test_corpus_evaluation_report_is_evidence_scoped_and_non_destructive(
        self,
    ) -> None:
        contract = (ROOT / "docs" / "corpus-evaluation.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(contract.split())
        for value in (
            "A bounded observation is never widened into a total",
            "a human has declared that source's reviewed endpoint list exhaustive",
            "An endpoint-scoped metric is never a whole-source total",
            "An unknown remainder is reported as unknown, never as zero",
            "Blocked coverage is reported, not bypassed",
            "No automatic destructive merge exists",
            "An unproven outcome is `unknown`, never `pass`",
            "contains no bulk-acquisition action at all",
            "A durable blocker becomes a `human_decision`, never an acquisition",
            "No source is a whole-source total",
            "empty rather than clean",
            "No live source was contacted",
        ):
            self.assertIn(value, normalized)

        # The aggregate report must not restate a count as a source total.
        self.assertNotIn("total of 29", normalized)
        self.assertIsNone(PRIVATE_PATH.search(contract))

    def test_operator_gates_contract_is_actionable_and_resumable(self) -> None:
        contract = (ROOT / "docs" / "operator-gates.md").read_text(encoding="utf-8")
        normalized = " ".join(contract.split())
        for value in (
            "A blocker is first-class durable state, not a stalled process",
            "the missing authority class",
            "one privacy-safe question",
            "the exact next safe action",
            "the unblocking command class",
            "a review trigger",
            "an expiry",
            "a durable resumable checkpoint",
            "every field in it is fixed literal text",
            "One blocked job does not hold unrelated work",
            "blocks that endpoint only",
            "A grant must name exactly the missing authority class and must expire",
            "An expired blocker cannot be granted",
            "never request secret values or protected material",
            "Resolving a human gate is not merge approval",
            "separate privacy-safe issue in `erniesg/rucksack`",
        ):
            self.assertIn(value, normalized)


class ProductReadinessContractTests(unittest.TestCase):
    """Keep README, brief, and runbook claims reconcilable with the matrix."""

    def setUp(self) -> None:
        self.matrix = MATRIX.read_text(encoding="utf-8")
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.brief = (ROOT / "docs" / "PROJECT_BRIEF.md").read_text(encoding="utf-8")
        # Prose wraps; sentences are asserted against the unwrapped text.
        self.flat_matrix = " ".join(self.matrix.split())
        self.flat_readme = " ".join(self.readme.split())

    def test_readme_status_replaces_the_stale_generated_ledger_wording(self) -> None:
        self.assertNotIn("implementation ledger is being generated", self.readme)
        self.assertIn("docs/product-readiness-matrix.md", self.readme)
        self.assertIn(
            "tested rights-aware corpus pipeline with one bounded source proof",
            self.readme,
        )

    def test_matrix_defines_every_readiness_state_it_uses(self) -> None:
        for state in READINESS_STATES:
            self.assertIn(f"`{state}`", self.matrix)

        used = {
            row[6]
            for row in table_rows(self.matrix, "Capability")
            if len(row) >= 7
        }
        self.assertNotEqual(set(), used)
        for cell in used:
            self.assertTrue(
                any(f"`{state}`" in cell for state in READINESS_STATES),
                f"undefined readiness state: {cell}",
            )

    def test_matrix_keeps_implemented_contracts_separate_from_live_proof(self) -> None:
        rows = table_rows(self.matrix, "Capability")
        self.assertGreaterEqual(len(rows), 10)
        live = [row for row in rows if "`live-proven`" in row[6]]
        self.assertEqual(
            2,
            len(live),
            "only issue 7 and the bounded mediaObjects run are live-proven",
        )
        self.assertTrue(
            any("metadata-readiness-proof.md" in row[5] for row in live)
        )
        self.assertTrue(
            any("29 reachable records" in row[5] for row in live)
        )

        for capability in (
            "Selected rich corpus",
            "Production ingestion",
            "Derived processing",
            "Provenance-aware search index",
            "Score-generation export",
        ):
            row = next(row for row in rows if row[0].startswith(capability))
            self.assertIn("`implemented-offline`", row[6])

    def test_matrix_distinguishes_every_required_product_dimension(self) -> None:
        capabilities = " ".join(row[0] for row in table_rows(self.matrix, "Capability"))
        for dimension in (
            "Source-universe inventory",
            "Selected rich corpus",
            "One-object R2 proof",
            "Production ingestion",
            "Derived processing",
            "Provenance-aware search index",
            "Score-generation export",
            "Project-native lifecycle",
            "Hosted operator UI",
            "Deployment",
        ):
            self.assertIn(dimension, capabilities)

    def test_matrix_rows_carry_a_test_evidence_lane_and_proof_or_blocker(self) -> None:
        for row in table_rows(self.matrix, "Capability"):
            capability, _surface, _path, test, lane, proof, state, _next = row[:8]
            self.assertNotEqual("", state, capability)
            if state.strip("`") in {"absent", "held"} or "`held`" in state:
                self.assertNotEqual("", proof, capability)
                continue
            self.assertIn("tests/", test, capability)
            self.assertNotEqual("", lane, capability)
            self.assertNotEqual("", proof, capability)

    def test_matrix_references_only_paths_that_exist(self) -> None:
        missing = [
            token
            for token in referenced_paths(self.matrix)
            if not (ROOT / token).exists()
        ]
        self.assertEqual([], missing)

    def test_every_implemented_command_is_documented_and_every_doc_command_exists(
        self,
    ) -> None:
        implemented = set(implemented_command_chains(build_parser()))
        documented = documented_command_chains(self.matrix)
        self.assertEqual(sorted(implemented), sorted(documented))

    def test_documented_commands_name_lane_side_effects_and_stop_conditions(
        self,
    ) -> None:
        lanes = {
            "portable",
            "network-acquisition",
            "trusted-vm",
            "trusted-laptop",
            "object-storage",
            "deploy",
        }
        rows = table_rows(self.matrix, "Command")
        self.assertGreaterEqual(len(rows), 12)
        for command, lane, secrets, effects, stop in (row[:5] for row in rows):
            self.assertTrue(
                any(f"`{name}`" in lane for name in lanes), f"{command}: lane {lane}"
            )
            self.assertNotEqual("", secrets, command)
            self.assertNotEqual("", effects, command)
            self.assertNotEqual("", stop, command)
            self.assertNotIn("/Users/", command)
            self.assertNotIn("/home/", command)

    def test_documented_commands_name_secrets_without_values(self) -> None:
        for name in (
            "CLOUDFLARE_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_ENDPOINT",
        ):
            self.assertIn(name, self.matrix)
            self.assertNotIn(f"{name}=", self.matrix)
            self.assertNotIn(f"{name}: ", self.matrix)

    def test_no_hosted_operator_ui_is_claimed_anywhere(self) -> None:
        self.assertIn("No hosted operator UI exists.", self.readme)
        row = next(
            row
            for row in table_rows(self.matrix, "Capability")
            if row[0] == "Hosted operator UI"
        )
        self.assertIn("`absent`", row[6])
        self.assertIn("local reference", self.readme)

        server_markers = re.compile(
            r"http\.server|socketserver|serve_forever|\bflask\b|\bfastapi\b|uvicorn",
            re.IGNORECASE,
        )
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src").rglob("*.py")
            if server_markers.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

    def test_no_bulk_mirror_capability_is_claimed(self) -> None:
        self.assertIn("does not mirror source sites in bulk", self.flat_readme)
        self.assertIn("does not mirror source sites in bulk", self.flat_matrix)
        for overclaim in (
            "mirrors the source",
            "full mirror",
            "complete mirror",
            "bulk download",
        ):
            self.assertNotIn(overclaim, self.flat_readme)
            self.assertNotIn(overclaim, self.flat_matrix)

    def test_source_counts_stay_unverified(self) -> None:
        self.assertIn("Counts are unverified.", self.matrix)
        self.assertIn("hypotheses", self.matrix)
        self.assertIn("unverified hypotheses", self.readme)
        self.assertIn("claims no complete source count", self.readme)
        self.assertIn("are hypotheses", self.brief)

    def test_source_boundary_is_pinned_to_the_reviewed_public_universe(self) -> None:
        self.assertIn("src/performing_fire_corpus/registry.py", self.matrix)
        self.assertIn("docs/PROJECT_BRIEF.md", self.matrix)
        self.assertIn("Public readability is not ingestion approval.", self.matrix)
        self.assertIn("commits no source prose, HTML bodies, media,", self.flat_matrix)

    def test_adding_a_later_source_stays_a_held_human_decision(self) -> None:
        spec = ROOT / "docs" / "issues" / "040-decide-whether-to-add-a-later-source.md"
        self.assertIn("rucksack-blocked", spec.read_text(encoding="utf-8"))
        self.assertIn(spec.relative_to(ROOT).as_posix(), self.matrix)
        self.assertIn(
            "Adding a source outside the reviewed public universe is a held human "
            "decision.",
            self.readme,
        )

    def test_github_actions_are_recorded_as_held_and_not_as_passing_evidence(
        self,
    ) -> None:
        rows = table_rows(self.matrix, "Workflow")
        documented = {row[0] for row in rows}
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            self.assertIn(
                f"`.github/workflows/{workflow.name}`",
                documented,
                workflow.name,
            )
        for held in ("deploy.yml", "rucksack-build.yml", "rucksack-ledger.yml"):
            row = next(row for row in rows if held in row[0])
            self.assertIn("Held", row[2])
        self.assertIn("RUCKSACK_AUTOPILOT_ENABLED", self.matrix)
        self.assertIn("Held is not passed.", self.matrix)
        self.assertIn(
            "evidence that a gate held, not evidence that a capability works",
            self.flat_readme,
        )

    def test_deployment_is_recorded_as_held_with_nothing_deployed(self) -> None:
        row = next(
            row
            for row in table_rows(self.matrix, "Capability")
            if row[0] == "Deployment"
        )
        self.assertIn("`held`", row[6])
        self.assertIn("Nothing has been deployed.", row[5])
        self.assertIn("Nothing is deployed.", self.readme)

    def test_model_and_effort_racing_stay_disabled(self) -> None:
        self.assertIn("Model/effort racing is disabled", self.brief)
        self.assertIn(
            "Model and effort racing is disabled for this project", self.matrix
        )

    def test_readiness_documents_carry_no_private_or_machine_local_material(
        self,
    ) -> None:
        for document in (self.matrix, self.readme):
            self.assertIsNone(PRIVATE_PATH.search(document))
            for marker in (
                "meeting notes",
                "chat excerpt",
                "private attachment",
                "proposal deck",
            ):
                self.assertNotIn(marker, document.lower())

    def test_absent_prd_or_demo_document_is_recorded_rather_than_invented(self) -> None:
        row = next(
            row
            for row in table_rows(self.matrix, "Capability")
            if row[0].startswith("PRD or demo")
        )
        self.assertIn("`absent`", row[6])
        self.assertIn("No PRD, demo, or pitch document exists", row[5])

        # `docs/issues/` is the work ledger, not product documentation.
        candidates = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs").glob("*.md")
            if re.search(r"\b(prd|demo|pitch)\b", path.stem.replace("-", " "))
        ]
        self.assertEqual([], candidates)
        self.assertIn("must reference these same readiness states", self.flat_matrix)

    def test_runtime_preflight_is_documented_before_validation(self) -> None:
        self.assertIn("sh scripts/preflight-python", self.matrix)
        self.assertIn('requires-python = ">=3.11"', self.matrix)
        self.assertIn('requires-python = ">=3.11"', self.readme)
        self.assertTrue((ROOT / "scripts" / "preflight-python").is_file())


if __name__ == "__main__":
    unittest.main()
