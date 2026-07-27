from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from performing_fire_corpus.njp_site_inventory import (
    ROBOTS_URL,
    InventoryLimits,
    MetadataSafeResponse,
    NJPInventoryError,
    run_njp_site_inventories,
)


ROOT = Path(__file__).resolve().parents[1]
MEDIA_MORE = "https://njp.ggcf.kr/mediaObjects/more"
MAIN_URL = f"{MEDIA_MORE}?page=1"
ARCHIVE_URL = "https://njp.ggcf.kr/pages/videoarchive"
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
VERIFY = "performing_fire_corpus.njp_site_inventory._verify_exact_clean_head"
SHAPE = (
    "performing_fire_corpus.njp_site_inventory."
    "_observed_archive_shape_sha256"
)
ARCHIVE_SHAPE_SHA256 = (
    "e6f9a2911a325fb321202b5994b257ec50ae48bf91a60553f64e38cc33e8851b"
)


class FakeTransport:
    def __init__(self, responses: list[MetadataSafeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, float, int]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MetadataSafeResponse:
        self.calls.append((method, url, timeout_seconds, max_response_bytes))
        if not self.responses:
            raise AssertionError("unexpected live request")
        return self.responses.pop(0)


class FailingTransport(FakeTransport):
    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MetadataSafeResponse:
        self.calls.append((method, url, timeout_seconds, max_response_bytes))
        if url == ROBOTS_URL and len(self.calls) <= 2:
            from performing_fire_corpus.njp_site_inventory import NJPInventoryError

            raise NJPInventoryError("transport_timeout")
        if not self.responses:
            raise AssertionError("unexpected live request")
        return self.responses.pop(0)


def response(
    url: str,
    *,
    status: int = 200,
    mime_type: str = "text/html",
    body: bytes = b"",
    declared_bytes: int | None = None,
    observed_bytes: int | None = None,
    oversized: bool = False,
) -> MetadataSafeResponse:
    return MetadataSafeResponse(
        url=url,
        status=status,
        mime_type=mime_type,
        body=body,
        declared_bytes=(
            len(body) if declared_bytes is None else declared_bytes
        ),
        observed_bytes=observed_bytes,
        oversized=oversized,
    )


def allowed_responses(*, archive_status: int = 200) -> list[MetadataSafeResponse]:
    robots = b"User-agent: *\nAllow: /\n"
    archive_body = (
        ROOT / "tests/fixtures/njp/videoarchive-page.html"
    ).read_bytes()
    fragments = [
        media_fragment(1, 8),
        media_fragment(9, 8),
        media_fragment(17, 8),
        media_fragment(25, 5),
        b'<ul class="pagination"><a class="next"></a></ul>',
    ]
    return [
        response(ROBOTS_URL, mime_type="text/plain", body=robots),
        *[
            response(f"{MEDIA_MORE}?page={page}", body=body)
            for page, body in enumerate(fragments, 1)
        ],
        response(ROBOTS_URL, mime_type="text/plain", body=robots),
        response(
            ARCHIVE_URL,
            status=archive_status,
            body=archive_body if archive_status == 200 else b"",
        ),
    ]


def media_fragment(first: int, count: int) -> bytes:
    anchors = "".join(
        (
            f'<li><a class="subject" href="/mediaObjects/{identifier}">'
            f"합성 기록 {identifier}</a></li>"
        )
        for identifier in range(first, first + count)
    )
    return f'<ul class="media-list">{anchors}</ul>'.encode()


class NJPSiteInventoryTests(unittest.TestCase):
    def run_inventory(
        self,
        root: Path,
        transport: FakeTransport,
    ) -> dict[str, object]:
        tick = iter([0.0, 0.1, 0.2, 0.3])
        with mock.patch(VERIFY), mock.patch(
            SHAPE, return_value=ARCHIVE_SHAPE_SHA256
        ):
            return run_njp_site_inventories(
                run_label="issue29-synthetic",
                commit_sha=COMMIT,
                repo_root=ROOT,
                state_root=root / "state",
                aggregate_report=root / "aggregate.json",
                governance_path=ROOT / "config" / "source-governance.v1.json",
                limits=InventoryLimits(per_host_interval_seconds=0.01),
                transport=transport,
                now=lambda: NOW,
                monotonic=lambda: next(tick, 0.3),
                sleeper=lambda _: None,
            )

    def run_archive(
        self,
        root: Path,
        transport: FakeTransport,
        *,
        governance_path: Path | None = None,
        limits: InventoryLimits | None = None,
        monotonic: object | None = None,
        sleeper: object | None = None,
    ) -> dict[str, object]:
        selected_monotonic = monotonic or (lambda: 0.0)
        selected_sleeper = sleeper or (lambda _seconds: None)
        with mock.patch(VERIFY), mock.patch(
            SHAPE, return_value=ARCHIVE_SHAPE_SHA256
        ):
            return run_njp_site_inventories(
                run_label="issue95-archive-regression",
                commit_sha=COMMIT,
                repo_root=ROOT,
                source_ids=("njp-center-video-archive",),
                state_root=root / "state",
                aggregate_report=root / "aggregate.json",
                governance_path=(
                    governance_path
                    or ROOT / "config/source-governance.v1.json"
                ),
                limits=limits
                or InventoryLimits(
                    max_requests=2,
                    max_pages=1,
                    max_response_bytes=65536,
                    aggregate_bytes=131072,
                    max_retries=0,
                    per_host_interval_seconds=0.01,
                ),
                transport=transport,
                now=lambda: NOW,
                monotonic=selected_monotonic,  # type: ignore[arg-type]
                sleeper=selected_sleeper,  # type: ignore[arg-type]
            )

    def test_sources_have_independent_artifacts_and_do_not_sum_the_universe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(allowed_responses())
            aggregate = self.run_inventory(root, transport)

            self.assertFalse(aggregate["counts_are_additive"])
            self.assertEqual("unknown", aggregate["whole_njp_center_universe_state"])
            self.assertFalse(aggregate["attachment_bytes_requested"])
            self.assertEqual(
                [
                    ("GET", ROBOTS_URL),
                    ("GET", f"{MEDIA_MORE}?page=1"),
                    ("GET", f"{MEDIA_MORE}?page=2"),
                    ("GET", f"{MEDIA_MORE}?page=3"),
                    ("GET", f"{MEDIA_MORE}?page=4"),
                    ("GET", f"{MEDIA_MORE}?page=5"),
                    ("GET", ROBOTS_URL),
                    ("GET", ARCHIVE_URL),
                ],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            for source_id in ("njp-center-main", "njp-center-video-archive"):
                source_root = root / "state" / source_id
                for name in (
                    "run-plan.json",
                    "policy-snapshot.json",
                    "checkpoint.json",
                    "completeness-report.json",
                    "ledger.sqlite3",
                ):
                    self.assertTrue((source_root / name).exists(), name)
                report = json.loads(
                    (source_root / "completeness-report.json").read_text()
                )
                self.assertEqual(
                    "complete_for_observed_endpoint",
                    report["state"],
                )
                self.assertEqual(
                    5 if source_id == "njp-center-main" else 1,
                    report["pages_committed"],
                )
                self.assertEqual(
                    29 if source_id == "njp-center-main" else 8,
                    report["observed_unique_records"],
                )
                self.assertEqual(0, report["attachment_candidates"])
                self.assertIn("page_mechanism", report)
                # The operator has recorded the terms, lawful-basis and
                # retention decisions, so those policy blockers must clear.
                # Only the adapter shape gate is genuinely outstanding, and it
                # is not governance-derived.
                self.assertEqual("permitted", report["policy_states"]["platform_terms"])
                self.assertEqual(
                    "permitted", report["policy_states"]["copyright_lawful_basis"]
                )
                self.assertEqual("approved", report["policy_states"]["retention"])
                self.assertEqual(
                    set(),
                    {blocker["code"] for blocker in report["blockers"]},
                )
                self.assertTrue(
                    all(
                        "/attachment/" not in call[1]
                        and "/storage/upload/" not in call[1]
                        for call in transport.calls
                    )
                )

    def test_terminal_rerun_is_byte_identical_and_makes_no_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.run_inventory(root, FakeTransport(allowed_responses()))
            before = (root / "aggregate.json").read_bytes()
            no_network = FakeTransport([])

            second = self.run_inventory(root, no_network)

            self.assertEqual(first, second)
            self.assertEqual([], no_network.calls)
            self.assertEqual(before, (root / "aggregate.json").read_bytes())
            for source_id in ("njp-center-main", "njp-center-video-archive"):
                with sqlite3.connect(
                    root / "state" / source_id / "ledger.sqlite3"
                ) as connection:
                    expected_requests = (
                        6 if source_id == "njp-center-main" else 2
                    )
                    self.assertEqual(
                        expected_requests,
                        connection.execute(
                            "SELECT COUNT(*) FROM njp_inventory_request"
                        ).fetchone()[0],
                    )
                    # Only the adapter shape gate remains; the three policy
                    # blockers clear against the recorded governance decisions.
                    self.assertEqual(
                        0,
                        connection.execute(
                            "SELECT COUNT(*) FROM njp_inventory_blocker"
                        ).fetchone()[0],
                    )

    def test_video_archive_can_run_as_an_independent_source(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        archive_body = (
            ROOT / "tests/fixtures/njp/videoarchive-page.html"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [
                    response(
                        ROBOTS_URL,
                        mime_type="text/plain",
                        body=robots,
                    ),
                    response(ARCHIVE_URL, body=archive_body),
                ]
            )
            with mock.patch(VERIFY), mock.patch(
                SHAPE, return_value=ARCHIVE_SHAPE_SHA256
            ):
                aggregate = run_njp_site_inventories(
                    run_label="issue95-video-archive-only",
                    commit_sha=COMMIT,
                    repo_root=ROOT,
                    source_ids=("njp-center-video-archive",),
                    state_root=root / "state",
                    aggregate_report=root / "aggregate.json",
                    governance_path=(
                        ROOT / "config/source-governance.v1.json"
                    ),
                    limits=InventoryLimits(
                        max_requests=2,
                        max_pages=1,
                        max_response_bytes=65536,
                        aggregate_bytes=65536,
                        max_retries=0,
                        per_host_interval_seconds=0.01,
                    ),
                    transport=transport,
                    now=lambda: NOW,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )

            self.assertEqual(
                ["njp-center-video-archive"],
                aggregate["source_scope"],
            )
            self.assertEqual(1, len(aggregate["sources"]))
            self.assertEqual(
                "complete_for_observed_endpoint",
                aggregate["sources"][0]["state"],
            )
            self.assertEqual(
                8,
                aggregate["sources"][0]["observed_unique_records"],
            )
            self.assertEqual(
                [("GET", ROBOTS_URL), ("GET", ARCHIVE_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            self.assertEqual(COMMIT, aggregate["commit_sha"])
            self.assertTrue(aggregate["exact_head_verified"])
            self.assertEqual(
                ARCHIVE_SHAPE_SHA256,
                aggregate["sources"][0]["reviewed_shape_sha256"],
            )
            self.assertEqual(
                "This is one endpoint proof; its count does not measure "
                "the whole NJP Center universe.",
                aggregate["safe_scope_statement"],
            )
            plan = json.loads(
                (
                    root
                    / "state/njp-center-video-archive/run-plan.json"
                ).read_text()
            )
            self.assertEqual(COMMIT, plan["commit_sha"])
            self.assertTrue(plan["exact_head_verified"])
            self.assertEqual(
                aggregate["sources"][0]["reviewed_shape_sha256"],
                plan["reviewed_shape_sha256"],
            )

    def test_exact_head_check_cannot_be_bypassed_by_custom_transport(self) -> None:
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                NJPInventoryError, "exact_head_not_verified"
            ):
                run_njp_site_inventories(
                    run_label="issue95-exact-head",
                    commit_sha=COMMIT,
                    repo_root=root,
                    source_ids=("njp-center-video-archive",),
                    state_root=root / "state",
                    aggregate_report=root / "aggregate.json",
                    governance_path=(
                        ROOT / "config/source-governance.v1.json"
                    ),
                    transport=transport,
                )
        self.assertEqual([], transport.calls)

    def test_live_shape_digest_must_match_reviewed_receipt(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        archive_body = (
            ROOT / "tests/fixtures/njp/videoarchive-page.html"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [
                    response(
                        ROBOTS_URL,
                        mime_type="text/plain",
                        body=robots,
                    ),
                    response(ARCHIVE_URL, body=archive_body),
                ]
            )
            with mock.patch(VERIFY):
                aggregate = run_njp_site_inventories(
                    run_label="issue95-shape-mismatch",
                    commit_sha=COMMIT,
                    repo_root=ROOT,
                    source_ids=("njp-center-video-archive",),
                    state_root=root / "state",
                    aggregate_report=root / "aggregate.json",
                    governance_path=(
                        ROOT / "config/source-governance.v1.json"
                    ),
                    limits=InventoryLimits(
                        max_requests=2,
                        max_pages=1,
                        max_retries=0,
                        per_host_interval_seconds=0.01,
                    ),
                    transport=transport,
                    now=lambda: NOW,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _seconds: None,
                )
            source = aggregate["sources"][0]
            self.assertEqual(["source_shape_changed"], source["blocker_codes"])
            self.assertNotEqual(
                source["reviewed_shape_sha256"],
                source["observed_shape_sha256"],
            )
            self.assertEqual(0, source["observed_unique_records"])

    def test_governance_horizon_blocks_before_network(self) -> None:
        governance = json.loads(
            (ROOT / "config/source-governance.v1.json").read_text()
        )
        selected = deepcopy(governance)
        record = next(
            item
            for item in selected["records"]
            if item["source_id"] == "njp-center-video-archive"
            and item["endpoint_id"] == "njp-center-video-archive-page"
        )
        for observation in record["observations"]:
            observation["expires_at"] = "2026-07-26T12:00:10Z"
        for decision in record["decisions"]:
            if decision["affected_operation"] in {
                "metadata_inventory",
                "public_retrieval",
                "retention",
            }:
                decision["expires_at"] = "2026-07-26T12:00:10Z"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            governance_path = root / "governance.json"
            governance_path.write_text(
                json.dumps(selected, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            transport = FakeTransport([])
            aggregate = self.run_archive(
                root,
                transport,
                governance_path=governance_path,
            )
            self.assertEqual([], transport.calls)
            source = aggregate["sources"][0]
            self.assertEqual("blocked", source["state"])
            self.assertEqual(
                ["governance_not_authorized"],
                source["blocker_codes"],
            )
            report = json.loads(
                (
                    root
                    / "state/njp-center-video-archive/completeness-report.json"
                ).read_text()
            )
            self.assertEqual(0, report["requests_attempted"])
            self.assertEqual([], report["records"])

    def test_actual_and_declared_response_bounds_are_independent(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        archive_body = (
            ROOT / "tests/fixtures/njp/videoarchive-page.html"
        ).read_bytes()
        cases = {
            "actual": response(
                ARCHIVE_URL,
                body=b"x" * 65537,
                declared_bytes=65537,
            ),
            "declared": response(
                ARCHIVE_URL,
                body=archive_body,
                declared_bytes=65537,
            ),
        }
        for label, archive_response in cases.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    aggregate = self.run_archive(
                        root,
                        FakeTransport(
                            [
                                response(
                                    ROBOTS_URL,
                                    mime_type="text/plain",
                                    body=robots,
                                ),
                                archive_response,
                            ]
                        ),
                    )
                    self.assertEqual(
                        ["response_oversized"],
                        aggregate["sources"][0]["blocker_codes"],
                    )
                    self.assertEqual(
                        0,
                        aggregate["sources"][0]["observed_unique_records"],
                    )

    def test_aggregate_remaining_bytes_are_passed_to_transport(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        archive_body = (
            ROOT / "tests/fixtures/njp/videoarchive-page.html"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [
                    response(
                        ROBOTS_URL,
                        mime_type="text/plain",
                        body=robots,
                    ),
                    response(ARCHIVE_URL, body=archive_body),
                ]
            )
            aggregate = self.run_archive(
                root,
                transport,
                limits=InventoryLimits(
                    max_requests=2,
                    max_pages=1,
                    max_response_bytes=1000,
                    aggregate_bytes=1000,
                    max_retries=0,
                    per_host_interval_seconds=0.01,
                ),
            )
            self.assertEqual(
                1000 - len(robots),
                transport.calls[1][3],
            )
            self.assertEqual(
                ["response_oversized"],
                aggregate["sources"][0]["blocker_codes"],
            )

    def test_oversized_rate_limit_response_is_charged_and_not_retried(
        self,
    ) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        retry = MetadataSafeResponse(
            url=ARCHIVE_URL,
            status=429,
            mime_type="text/html",
            body=b"",
            declared_bytes=None,
            observed_bytes=1000 - len(robots),
            retry_after_seconds=0.5,
            oversized=True,
        )
        transport = FakeTransport(
            [
                response(
                    ROBOTS_URL,
                    mime_type="text/plain",
                    body=robots,
                ),
                retry,
                response(ARCHIVE_URL, body=b"must not be requested"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = self.run_archive(
                root,
                transport,
                limits=InventoryLimits(
                    max_requests=3,
                    max_pages=1,
                    max_response_bytes=1000,
                    aggregate_bytes=1000,
                    max_retries=1,
                    per_host_interval_seconds=0.01,
                ),
            )
            self.assertEqual(2, len(transport.calls))
            self.assertEqual(
                ["rate_limited"],
                aggregate["sources"][0]["blocker_codes"],
            )
            checkpoint = json.loads(
                (
                    root
                    / "state/njp-center-video-archive/checkpoint.json"
                ).read_text()
            )
            self.assertEqual(1000, checkpoint["aggregate_bytes"])

    def test_inventory_limit_caps_are_fixed(self) -> None:
        invalid = (
            {"max_requests": 17},
            {"max_pages": 9},
            {"max_response_bytes": 131073, "aggregate_bytes": 131073},
            {"aggregate_bytes": 524289},
            {"max_retries": 3},
            {"retry_after_seconds": 10.01},
            {"per_host_interval_seconds": 10.01},
            {"timeout_seconds": 30.01},
            {"elapsed_seconds": 120.01},
            {"elapsed_seconds": 1e308},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                NJPInventoryError
            ):
                InventoryLimits(**values)

    def test_elapsed_bound_after_parse_discards_records(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        archive_body = (
            ROOT / "tests/fixtures/njp/videoarchive-page.html"
        ).read_bytes()
        ticks = iter([0.0] * 8 + [31.0])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = self.run_archive(
                root,
                FakeTransport(
                    [
                        response(
                            ROBOTS_URL,
                            mime_type="text/plain",
                            body=robots,
                        ),
                        response(ARCHIVE_URL, body=archive_body),
                    ]
                ),
                monotonic=lambda: next(ticks, 31.0),
            )
            self.assertEqual(
                ["elapsed_bound"],
                aggregate["sources"][0]["blocker_codes"],
            )
            self.assertEqual(
                0,
                aggregate["sources"][0]["observed_unique_records"],
            )

    def test_rate_limit_is_shared_across_selected_same_host_sources(self) -> None:
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch(VERIFY), mock.patch(
                SHAPE, return_value=ARCHIVE_SHAPE_SHA256
            ):
                run_njp_site_inventories(
                    run_label="issue95-shared-rate",
                    commit_sha=COMMIT,
                    repo_root=ROOT,
                    state_root=root / "state",
                    aggregate_report=root / "aggregate.json",
                    governance_path=(
                        ROOT / "config/source-governance.v1.json"
                    ),
                    limits=InventoryLimits(
                        per_host_interval_seconds=0.25
                    ),
                    transport=FakeTransport(allowed_responses()),
                    now=lambda: NOW,
                    monotonic=lambda: 0.0,
                    sleeper=sleeps.append,
                )
        self.assertEqual(7, len(sleeps))
        self.assertEqual([0.25] * 7, sleeps)

    def test_one_source_403_does_not_stop_the_other_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aggregate = self.run_inventory(
                root,
                FakeTransport(allowed_responses(archive_status=403)),
            )
            by_source = {
                item["source_id"]: item for item in aggregate["sources"]
            }
            self.assertEqual(
                "public_get_available",
                by_source["njp-center-main"]["access_state"],
            )
            self.assertEqual(
                "access_forbidden",
                by_source["njp-center-video-archive"]["access_state"],
            )
            self.assertIn(
                "access_forbidden",
                by_source["njp-center-video-archive"]["blocker_codes"],
            )

    def test_robots_denial_prevents_endpoint_request_but_second_source_continues(
        self,
    ) -> None:
        denied = b"User-agent: *\nDisallow: /\n"
        allowed = b"User-agent: *\nAllow: /\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=denied),
                    response(ROBOTS_URL, mime_type="text/plain", body=allowed),
                    response(ARCHIVE_URL),
                ]
            )
            aggregate = self.run_inventory(root, transport)

            self.assertEqual(
                [("GET", ROBOTS_URL), ("GET", ROBOTS_URL), ("GET", ARCHIVE_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            by_source = {
                item["source_id"]: item for item in aggregate["sources"]
            }
            self.assertEqual(
                "robots_denied", by_source["njp-center-main"]["robots_state"]
            )
            self.assertEqual(
                "public_get_available",
                by_source["njp-center-video-archive"]["access_state"],
            )

    def test_transport_failure_is_bounded_and_second_source_continues(self) -> None:
        allowed = b"User-agent: *\nAllow: /\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FailingTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=allowed),
                    response(
                        ARCHIVE_URL,
                        body=(
                            ROOT
                            / "tests/fixtures/njp/videoarchive-page.html"
                        ).read_bytes(),
                    ),
                ]
            )
            aggregate = self.run_inventory(root, transport)

            by_source = {
                item["source_id"]: item for item in aggregate["sources"]
            }
            self.assertEqual(
                "transport_error", by_source["njp-center-main"]["robots_state"]
            )
            self.assertEqual(
                "public_get_available",
                by_source["njp-center-video-archive"]["access_state"],
            )
            with sqlite3.connect(
                root / "state" / "njp-center-main" / "ledger.sqlite3"
            ) as connection:
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM njp_inventory_request"
                    ).fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()
