from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from performing_fire_corpus.njp_site_inventory import (
    ROBOTS_URL,
    InventoryLimits,
    MetadataSafeResponse,
    run_njp_site_inventories,
)


ROOT = Path(__file__).resolve().parents[1]
MEDIA_MORE = "https://njp.ggcf.kr/mediaObjects/more"
MAIN_URL = f"{MEDIA_MORE}?page=1"
ARCHIVE_URL = "https://njp.ggcf.kr/pages/videoarchive"
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


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
) -> MetadataSafeResponse:
    return MetadataSafeResponse(
        url=url,
        status=status,
        mime_type=mime_type,
        body=body,
        declared_bytes=len(body),
    )


def allowed_responses(*, archive_status: int = 200) -> list[MetadataSafeResponse]:
    robots = b"User-agent: *\nAllow: /\n"
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
        response(ARCHIVE_URL, status=archive_status),
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
        return run_njp_site_inventories(
            run_label="issue29-synthetic",
            state_root=root / "state",
            aggregate_report=root / "aggregate.json",
            governance_path=ROOT / "config" / "source-governance.v1.json",
            limits=InventoryLimits(per_host_interval_seconds=0.01),
            transport=transport,
            now=lambda: NOW,
            monotonic=lambda: next(tick, 0.3),
            sleeper=lambda _: None,
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
                    ("HEAD", ARCHIVE_URL),
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
                expected_bound = source_id == "njp-center-main"
                self.assertEqual(
                    (
                        "complete_for_observed_endpoint"
                        if expected_bound
                        else "blocked"
                    ),
                    report["state"],
                )
                self.assertEqual(5 if expected_bound else 0, report["pages_committed"])
                self.assertEqual(
                    29 if expected_bound else 0,
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
                    set() if expected_bound else {"source_shape_unreviewed"},
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
                        0 if source_id == "njp-center-main" else 1,
                        connection.execute(
                            "SELECT COUNT(*) FROM njp_inventory_blocker"
                        ).fetchone()[0],
                    )

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
                [("GET", ROBOTS_URL), ("GET", ROBOTS_URL), ("HEAD", ARCHIVE_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            by_source = {
                item["source_id"]: item for item in aggregate["sources"]
            }
            self.assertEqual(
                "robots_denied", by_source["njp-center-main"]["robots_state"]
            )
            self.assertEqual(
                "public_head_available",
                by_source["njp-center-video-archive"]["access_state"],
            )

    def test_transport_failure_is_bounded_and_second_source_continues(self) -> None:
        allowed = b"User-agent: *\nAllow: /\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FailingTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=allowed),
                    response(ARCHIVE_URL),
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
                "public_head_available",
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
