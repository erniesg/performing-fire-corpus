from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from performing_fire_corpus.cli import (
    _njp_archive_route_paths,
    build_parser,
    main as cli_main,
)
from performing_fire_corpus.njp_center_adapters import (
    NJPCenterVideoArchiveAdapter,
)
from performing_fire_corpus.njp_video_archive_route import (
    VideoArchiveRouteError,
    build_video_archive_attempt_receipt,
    route_video_archive_attempt,
    validate_video_archive_attempt_receipt,
    validate_video_archive_route_decision,
    write_video_archive_route_artifact,
)


COMMIT_SHA = "a" * 40
EVIDENCE_SHA256 = "b" * 64


def invented_plan() -> dict[str, object]:
    return {
        "record_type": "njp_inventory_run_plan",
        "schema_version": 1,
        "run_id": "njp_inventory_invented_njp_center_video_archive",
        "source_id": NJPCenterVideoArchiveAdapter.source_id,
        "endpoint_id": NJPCenterVideoArchiveAdapter.endpoint_id,
        "adapter_id": NJPCenterVideoArchiveAdapter.adapter_id,
        "adapter_version": NJPCenterVideoArchiveAdapter.adapter_version,
        "commit_sha": COMMIT_SHA,
        "exact_head_verified": True,
        "reviewed_shape_sha256": (
            NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
        ),
        "robots_url": "https://njp.ggcf.kr/robots.txt",
        "endpoint_url": NJPCenterVideoArchiveAdapter.public_url,
        "limits": {
            "aggregate_bytes": 65536,
            "elapsed_seconds": 30.0,
            "max_pages": 1,
            "max_requests": 2,
            "max_response_bytes": 65536,
            "max_retries": 0,
            "per_host_interval_seconds": 1.0,
            "retry_after_seconds": 2.0,
            "timeout_seconds": 10.0,
        },
        "allowed_methods": [
            "GET robots.txt",
            "GET Video Archive page",
        ],
        "attachment_requests_allowed": False,
        "catalogue_body_requests_allowed": True,
        "live_shape_digest_comparison_required": True,
    }


def invented_report(
    *,
    blocker_code: str | None = None,
) -> dict[str, object]:
    complete = blocker_code is None
    return {
        "record_type": "njp_inventory_completeness_report",
        "schema_version": 1,
        "run_id": "njp_inventory_invented_njp_center_video_archive",
        "source_id": NJPCenterVideoArchiveAdapter.source_id,
        "endpoint_id": NJPCenterVideoArchiveAdapter.endpoint_id,
        "commit_sha": COMMIT_SHA,
        "exact_head_verified": True,
        "reviewed_shape_sha256": (
            NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
        ),
        "observed_shape_sha256": (
            NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
            if complete
            else None
        ),
        "generated_at": "2026-07-28T04:00:00Z",
        "state": (
            "complete_for_observed_endpoint" if complete else "blocked"
        ),
        "shape_state": "shape_bound",
        "robots_state": (
            "robots_allowed"
            if complete
            else (
                "transport_error"
                if blocker_code == "transport_error"
                else "not_checked"
            )
        ),
        "access_state": (
            "public_get_available" if complete else "not_checked"
        ),
        "requests_attempted": 2 if complete else 1,
        "pages_committed": 1 if complete else 0,
        "observed_unique_records": 8 if complete else 0,
        "duplicate_records": 0,
        "alias_records": 0,
        "attachment_candidates": 0,
        "records": (
            [
                {
                    "record_id": f"invented-{index}",
                    "source_identity": f"{index:064x}",
                    "metadata": {
                        "canonical_detail_url": (
                            "https://njp.ggcf.kr/storage/upload/"
                            f"invented-{index}.pdf"
                        ),
                        "title": f"Invented {index}",
                    },
                }
                for index in range(8)
            ]
            if complete
            else []
        ),
        "unvisited_remainder": None if complete else "unknown",
        "page_mechanism": "registered_archive_page_html",
        "policy_states": {
            "access_control": "current_public_metadata_observation",
            "platform_terms": "permitted",
            "copyright_lawful_basis": "permitted",
            "retention": "approved",
            "metadata_inventory": "approved",
            "public_retrieval": "approved",
        },
        "blockers": (
            []
            if complete
            else [
                {
                    "blocker_id": f"blocker_{blocker_code}",
                    "code": blocker_code,
                    "next_safe_action": "Invented safe action.",
                }
            ]
        ),
    }


class VideoArchiveRouteTests(unittest.TestCase):
    def test_successful_vm_attempt_completes_without_laptop(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )

        decision = route_video_archive_attempt(receipt)

        self.assertEqual(decision["state"], "complete")
        self.assertEqual(decision["action"], "complete_on_vm")
        self.assertIsNone(decision["next_lane"])
        self.assertFalse(decision["fallback_authorized"])
        self.assertFalse(decision["attachment_bytes_requested"])

    def test_generic_transport_error_does_not_authorize_fallback(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="transport_error"),
            invented_plan(),
            lane="trusted-vm",
        )

        decision = route_video_archive_attempt(receipt)

        self.assertEqual(decision["state"], "held")
        self.assertEqual(decision["action"], "hold_vm_blocker")
        self.assertFalse(decision["fallback_authorized"])

    def test_classified_vm_host_capability_mismatch_queues_laptop(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="transport_error"),
            invented_plan(),
            lane="trusted-vm",
            capability_mismatch_code="runner_dns_capability_unavailable",
            capability_evidence_sha256=EVIDENCE_SHA256,
        )

        decision = route_video_archive_attempt(receipt)

        self.assertEqual(decision["state"], "queued")
        self.assertEqual(
            decision["action"], "queue_trusted_laptop_exact_plan"
        )
        self.assertEqual(decision["next_lane"], "trusted-laptop")
        self.assertTrue(decision["fallback_authorized"])

    def test_policy_access_rate_shape_and_bound_blockers_never_fallback(
        self,
    ) -> None:
        blocker_codes = (
            "governance_not_authorized",
            "robots_denied",
            "robots_ambiguous",
            "access_forbidden",
            "rate_limited",
            "source_shape_changed",
            "mime_mismatch",
            "response_oversized",
            "elapsed_bound",
            "retention_pending",
            "platform_terms_pending",
            "copyright_rights_pending",
            "login_required",
        )
        for blocker_code in blocker_codes:
            with self.subTest(blocker_code=blocker_code):
                with self.assertRaisesRegex(
                    VideoArchiveRouteError,
                    "invalid_capability_mismatch",
                ):
                    build_video_archive_attempt_receipt(
                        invented_report(blocker_code=blocker_code),
                        invented_plan(),
                        lane="trusted-vm",
                        capability_mismatch_code=(
                            "runner_outbound_https_capability_unavailable"
                        ),
                        capability_evidence_sha256=EVIDENCE_SHA256,
                    )
                receipt = build_video_archive_attempt_receipt(
                    invented_report(blocker_code=blocker_code),
                    invented_plan(),
                    lane="trusted-vm",
                )
                self.assertEqual(
                    route_video_archive_attempt(receipt)["action"],
                    "hold_vm_blocker",
                )

    def test_laptop_attempt_requires_matching_vm_receipt_and_plan(self) -> None:
        vm_receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="transport_error"),
            invented_plan(),
            lane="trusted-vm",
            capability_mismatch_code=(
                "runner_outbound_https_capability_unavailable"
            ),
            capability_evidence_sha256=EVIDENCE_SHA256,
        )
        changed_plan = invented_plan()
        changed_plan["limits"] = {
            **changed_plan["limits"],  # type: ignore[arg-type]
            "timeout_seconds": 9.0,
        }

        with self.assertRaisesRegex(
            VideoArchiveRouteError,
            "invalid_inventory_plan",
        ):
            build_video_archive_attempt_receipt(
                invented_report(),
                changed_plan,
                lane="trusted-laptop",
                parent_vm_receipt=vm_receipt,
            )

    def test_plan_requires_exact_reviewed_keys_urls_and_methods(self) -> None:
        mutations = {
            "extra operation": lambda plan: plan.update(
                {"unreviewed_operation": "GET linked PDF"}
            ),
            "robots URL": lambda plan: plan.update(
                {"robots_url": "https://unreviewed.invalid/robots.txt"}
            ),
            "endpoint URL": lambda plan: plan.update(
                {"endpoint_url": "https://unreviewed.invalid/archive"}
            ),
            "allowed methods": lambda plan: plan.update(
                {
                    "allowed_methods": [
                        "GET robots.txt",
                        "GET Video Archive page",
                        "GET linked PDF",
                    ]
                }
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                plan = invented_plan()
                mutate(plan)
                with self.assertRaisesRegex(
                    VideoArchiveRouteError,
                    "invalid_inventory_plan",
                ):
                    build_video_archive_attempt_receipt(
                        invented_report(),
                        plan,
                        lane="trusted-vm",
                    )

    def test_successful_laptop_receipt_resumes_vm_coordination(self) -> None:
        vm_receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="transport_error"),
            invented_plan(),
            lane="trusted-vm",
            capability_mismatch_code="runner_tls_capability_unavailable",
            capability_evidence_sha256=EVIDENCE_SHA256,
        )
        laptop_receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-laptop",
            parent_vm_receipt=vm_receipt,
        )

        decision = route_video_archive_attempt(
            vm_receipt,
            laptop_receipt,
        )

        self.assertEqual(decision["state"], "complete")
        self.assertEqual(
            decision["action"], "resume_vm_from_laptop_receipt"
        )
        self.assertEqual(decision["next_lane"], "trusted-vm")
        self.assertEqual(
            decision["laptop_receipt_id"],
            laptop_receipt["receipt_id"],
        )

    def test_laptop_blocker_holds_without_recursive_fallback(self) -> None:
        vm_receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="transport_error"),
            invented_plan(),
            lane="trusted-vm",
            capability_mismatch_code="runner_ip_route_capability_unavailable",
            capability_evidence_sha256=EVIDENCE_SHA256,
        )
        laptop_receipt = build_video_archive_attempt_receipt(
            invented_report(blocker_code="rate_limited"),
            invented_plan(),
            lane="trusted-laptop",
            parent_vm_receipt=vm_receipt,
        )

        decision = route_video_archive_attempt(
            vm_receipt,
            laptop_receipt,
        )

        self.assertEqual(decision["state"], "held")
        self.assertEqual(decision["action"], "hold_laptop_blocker")
        self.assertIsNone(decision["next_lane"])

    def test_attempt_receipt_is_content_free_and_tamper_evident(self) -> None:
        report = invented_report()
        receipt = build_video_archive_attempt_receipt(
            report,
            invented_plan(),
            lane="trusted-vm",
        )
        serialized = json.dumps(receipt, sort_keys=True)
        self.assertNotIn("records", receipt)
        self.assertNotIn("Invented", serialized)
        self.assertNotIn("storage/upload", serialized)

        changed = copy.deepcopy(receipt)
        changed["report_sha256"] = "c" * 64
        with self.assertRaisesRegex(
            VideoArchiveRouteError,
            "invalid_attempt_receipt_id",
        ):
            validate_video_archive_attempt_receipt(changed)

    def test_artifact_writer_persists_only_validated_values(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )
        decision = route_video_archive_attempt(receipt)
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "attempt.json"
            decision_path = Path(directory) / "route.json"
            write_video_archive_route_artifact(receipt_path, receipt)
            write_video_archive_route_artifact(decision_path, decision)

            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")),
                receipt,
            )
            self.assertEqual(
                json.loads(decision_path.read_text(encoding="utf-8")),
                decision,
            )

        changed = copy.deepcopy(decision)
        changed["action"] = "queue_trusted_laptop_exact_plan"
        with self.assertRaisesRegex(
            VideoArchiveRouteError,
            "invalid_route_decision",
        ):
            validate_video_archive_route_decision(changed)

    def test_artifact_writer_never_overwrites_existing_target(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "attempt.json"
            target.write_text("existing-target", encoding="utf-8")

            with self.assertRaisesRegex(
                VideoArchiveRouteError,
                "route_artifact_write_blocked",
            ):
                write_video_archive_route_artifact(target, receipt)

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "existing-target",
            )
            self.assertFalse((Path(directory) / "attempt.json.tmp").exists())

    def test_artifact_writer_never_follows_target_symlink(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "unrelated.json"
            unrelated.write_text("unrelated-target", encoding="utf-8")
            target = root / "attempt.json"
            target.symlink_to(unrelated)

            with self.assertRaisesRegex(
                VideoArchiveRouteError,
                "route_artifact_write_blocked",
            ):
                write_video_archive_route_artifact(target, receipt)

            self.assertTrue(target.is_symlink())
            self.assertEqual(
                unrelated.read_text(encoding="utf-8"),
                "unrelated-target",
            )
            self.assertFalse((root / "attempt.json.tmp").exists())

    def test_artifact_writer_never_reuses_existing_temporary_path(
        self,
    ) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary = root / "attempt.json.tmp"
            temporary.write_text("existing-temporary", encoding="utf-8")
            target = root / "attempt.json"

            with self.assertRaisesRegex(
                VideoArchiveRouteError,
                "route_artifact_write_blocked",
            ):
                write_video_archive_route_artifact(target, receipt)

            self.assertEqual(
                temporary.read_text(encoding="utf-8"),
                "existing-temporary",
            )
            self.assertFalse(target.exists())

    def test_artifact_writer_never_follows_temporary_symlink(self) -> None:
        receipt = build_video_archive_attempt_receipt(
            invented_report(),
            invented_plan(),
            lane="trusted-vm",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "unrelated.json"
            unrelated.write_text("unrelated-temporary", encoding="utf-8")
            temporary = root / "attempt.json.tmp"
            temporary.symlink_to(unrelated)
            target = root / "attempt.json"

            with self.assertRaisesRegex(
                VideoArchiveRouteError,
                "route_artifact_write_blocked",
            ):
                write_video_archive_route_artifact(target, receipt)

            self.assertTrue(temporary.is_symlink())
            self.assertEqual(
                unrelated.read_text(encoding="utf-8"),
                "unrelated-temporary",
            )
            self.assertFalse(target.exists())

    def test_cli_records_complete_vm_receipt_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / ".local/njp-center-inventory/invented"
            run_root.mkdir(parents=True)
            (run_root / "report.json").write_text(
                json.dumps(invented_report()),
                encoding="utf-8",
            )
            (run_root / "plan.json").write_text(
                json.dumps(invented_plan()),
                encoding="utf-8",
            )
            arguments = [
                "record-njp-video-archive-attempt",
                "--lane",
                "trusted-vm",
                "--report",
                ".local/njp-center-inventory/invented/report.json",
                "--plan",
                ".local/njp-center-inventory/invented/plan.json",
                "--attempt-output",
                ".local/njp-center-inventory/invented/attempt.json",
                "--route-output",
                ".local/njp-center-inventory/invented/route.json",
            ]
            with (
                mock.patch(
                    "performing_fire_corpus.cli.Path.cwd",
                    return_value=root,
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                result = cli_main(arguments)

            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(output.getvalue())["action"],
                "complete_on_vm",
            )
            self.assertTrue((run_root / "attempt.json").is_file())
            self.assertTrue((run_root / "route.json").is_file())

    def test_cli_route_paths_reject_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            local = root / ".local"
            local.mkdir()
            (local / "njp-center-inventory").symlink_to(
                external,
                target_is_directory=True,
            )
            arguments = build_parser().parse_args(
                [
                    "record-njp-video-archive-attempt",
                    "--lane",
                    "trusted-vm",
                    "--report",
                    ".local/njp-center-inventory/report.json",
                    "--plan",
                    ".local/njp-center-inventory/plan.json",
                    "--attempt-output",
                    ".local/njp-center-inventory/attempt.json",
                    "--route-output",
                    ".local/njp-center-inventory/route.json",
                ]
            )
            with mock.patch(
                "performing_fire_corpus.cli.Path.cwd",
                return_value=root,
            ):
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    _njp_archive_route_paths(arguments)


if __name__ == "__main__":
    unittest.main()
