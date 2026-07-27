from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.acquisition import HTTPResponse
from performing_fire_corpus.antiegg_metadata_adapters import POSTS_PER_PAGE
from performing_fire_corpus.antiegg_posts_inventory import (
    PostsInventoryConfig,
    inventory_antiegg_posts,
)
from performing_fire_corpus.cli import build_parser
from performing_fire_corpus.ledger import Ledger
from performing_fire_corpus.registry import load_registry


ROBOTS_URL = "https://antiegg.kr/robots.txt"
POSTS_URL = "https://antiegg.kr/wp-json/wp/v2/posts"
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")
ALLOW_ALL = b"User-agent: *\nAllow: /\n"
DENY_API = b"User-agent: *\nDisallow: /wp-json/\n"


def post_item(identifier: int) -> dict[str, object]:
    return {
        "author": 7,
        "categories": [4],
        "date": "2026-01-02T03:04:05",
        "excerpt": {"protected": False, "rendered": "<p>Invented excerpt.</p>"},
        "featured_media": 0,
        "id": identifier,
        "link": f"https://antiegg.kr/{identifier}",
        "modified": "2026-02-03T04:05:06",
        "slug": f"invented-{identifier}",
        "tags": [],
        "title": {"rendered": "가상의 표시 제목"},
    }


def page_body(identifiers: range | list[int]) -> bytes:
    return json.dumps(
        [post_item(identifier) for identifier in identifiers],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def headers(total: int) -> dict[str, str]:
    pages = 0 if total == 0 else -(-total // POSTS_PER_PAGE)
    return {"x-wp-total": str(total), "x-wp-totalpages": str(pages)}


class RoutingTransport:
    """Answers by URL, so every response carries the URL that was asked for."""

    def __init__(
        self,
        pages: list[tuple[bytes, dict[str, str]]],
        *,
        robots: bytes = ALLOW_ALL,
    ) -> None:
        self.pages = pages
        self.robots = robots
        self.calls: list[str] = []

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse:
        self.calls.append(url)
        if url == ROBOTS_URL:
            return HTTPResponse(
                url=url,
                status=200,
                mime_type="text/plain",
                body=self.robots,
                declared_bytes=len(self.robots),
            )
        query = parse_qs(urlsplit(url).query)
        page = int(query["page"][0])
        if page > len(self.pages):
            raise AssertionError(f"unexpected page request: {page}")
        body, page_headers = self.pages[page - 1]
        return HTTPResponse(
            url=url,
            status=200,
            mime_type="application/json",
            body=body,
            declared_bytes=len(body),
            headers=page_headers,
        )


class ANTIEGGPostsInventoryTests(unittest.TestCase):
    def config(self, root: Path, **overrides: object) -> PostsInventoryConfig:
        values: dict[str, object] = {
            "source": "antiegg-posts",
            "max_requests": 8,
            "max_pages": 4,
            "timeout_seconds": 3.0,
            "rate_limit_seconds": 0.0,
            "max_retries": 1,
            "max_elapsed_seconds": 10.0,
            "max_response_bytes": 1048576,
            "ledger_path": root / "ledger.sqlite3",
            "manifest_path": root / "manifest.json",
        }
        values.update(overrides)
        return PostsInventoryConfig(**values)

    #: Three pages, so the cursor advances more than once and the last page is
    #: short. Two pages would never exercise `page-002` -> `page-003`.
    total = 2 * POSTS_PER_PAGE + 50

    def catalogue_pages(self) -> list[tuple[bytes, dict[str, str]]]:
        starts = range(1, self.total + 1, POSTS_PER_PAGE)
        return [
            (
                page_body(range(start, min(start + POSTS_PER_PAGE, self.total + 1))),
                headers(self.total),
            )
            for start in starts
        ]

    def test_whole_catalogue_is_reproducible_with_a_ledger_and_manifest(
        self,
    ) -> None:
        total = self.total
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = RoutingTransport(self.catalogue_pages())

            manifest = inventory_antiegg_posts(
                self.config(root), transport=transport, registry=REGISTRY
            )

            self.assertEqual("complete", manifest["result"])
            self.assertEqual(total, len(manifest["record_ids"]))
            self.assertEqual(
                {
                    "declared_total": total,
                    "unique_records_retrieved": total,
                    "unvisited_remainder": 0,
                    "declared_total_matches_unique_records": True,
                    "is_completeness_guarantee": False,
                    "observed_at": manifest["completeness"]["observed_at"],
                },
                manifest["completeness"],
            )
            self.assertEqual(
                [ROBOTS_URL] + [f"{POSTS_URL}?"] * 3,
                [call.split("_fields")[0] for call in transport.calls],
            )
            self.assertEqual(
                ["page=1", "page=2", "page=3"],
                [
                    next(
                        part
                        for part in call.split("&")
                        if part.startswith("page=")
                    )
                    for call in transport.calls[1:]
                ],
            )
            self.assertEqual(
                manifest,
                json.loads((root / "manifest.json").read_text(encoding="utf-8")),
            )

            serialized = json.dumps(manifest, ensure_ascii=False)
            for prose in ("표시 제목", "Invented excerpt", "invented-1", "headers"):
                self.assertNotIn(prose, serialized)

            with Ledger(root / "ledger.sqlite3") as ledger:
                self.assertIsNotNone(
                    ledger.get_record("source", "source_antiegg_posts")
                )
                verdict = ledger.get_record(
                    "evidence", "evidence_antiegg_posts_verdict_001"
                )
                self.assertEqual(
                    {
                        "declared_total": total,
                        "result": "complete",
                        "unique_records_retrieved": total,
                    },
                    json.loads(verdict["summary"]),
                )
                self.assertIsNotNone(
                    ledger.get_record(
                        "evidence", "evidence_antiegg_posts_request_004"
                    )
                )

    def test_complete_requires_unique_ids_to_equal_the_declared_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # One terminal page that hands back fewer records than the endpoint
            # says it holds. Reaching the last page is not completeness.
            transport = RoutingTransport([(page_body([1, 2]), headers(5))])

            manifest = inventory_antiegg_posts(
                self.config(root), transport=transport, registry=REGISTRY
            )

            self.assertEqual("blocked", manifest["result"])
            self.assertEqual(
                "declared_total_mismatch", manifest["blocker"]["code"]
            )
            self.assertEqual(
                "complete_for_observed_endpoint",
                manifest["pagination"]["state"],
            )
            self.assertEqual(
                {"declared_total": 5, "unique_records_retrieved": 2},
                {
                    key: manifest["completeness"][key]
                    for key in ("declared_total", "unique_records_retrieved")
                },
            )
            self.assertEqual(3, manifest["completeness"]["unvisited_remainder"])

    def test_page_bound_reports_a_bounded_stop_rather_than_completeness(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = RoutingTransport(self.catalogue_pages())

            manifest = inventory_antiegg_posts(
                self.config(root, max_pages=1),
                transport=transport,
                registry=REGISTRY,
            )

            self.assertEqual("bounded_partial", manifest["result"])
            self.assertNotIn("blocker", manifest)
            self.assertEqual(
                "page_budget_exhausted", manifest["bounded_stop"]["code"]
            )
            self.assertEqual(POSTS_PER_PAGE, len(manifest["record_ids"]))
            self.assertEqual(
                self.total - POSTS_PER_PAGE,
                manifest["completeness"]["unvisited_remainder"],
            )
            self.assertEqual(
                len(transport.calls),
                manifest["pagination"]["requests_dispatched"],
            )

    def test_request_bound_never_counts_a_request_it_did_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = RoutingTransport(self.catalogue_pages())

            # Robots plus one page exhausts the budget, so the run stops while
            # the pagination state machine has a further request in hand.
            manifest = inventory_antiegg_posts(
                self.config(root, max_requests=2),
                transport=transport,
                registry=REGISTRY,
            )

            self.assertEqual("bounded_partial", manifest["result"])
            self.assertEqual(
                "request_budget_exhausted", manifest["bounded_stop"]["code"]
            )
            self.assertEqual(2, len(transport.calls))
            self.assertEqual(2, manifest["pagination"]["requests_dispatched"])
            self.assertEqual(2, manifest["record_counts"]["requests"])

    def test_robots_denial_blocks_before_any_metadata_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = RoutingTransport(
                self.catalogue_pages(), robots=DENY_API
            )

            manifest = inventory_antiegg_posts(
                self.config(root), transport=transport, registry=REGISTRY
            )

            self.assertEqual("blocked", manifest["result"])
            self.assertEqual("robots_denied", manifest["blocker"]["code"])
            self.assertEqual([ROBOTS_URL], transport.calls)
            self.assertEqual(
                {"catalogue_allowed": False, "outcome": "denied", "status": 200},
                manifest["robots_observation"],
            )

    def test_a_second_run_re_attempts_instead_of_replaying_a_stored_verdict(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = inventory_antiegg_posts(
                self.config(root, max_pages=1),
                transport=RoutingTransport(self.catalogue_pages()),
                registry=REGISTRY,
            )
            self.assertEqual("bounded_partial", first["result"])

            second_transport = RoutingTransport(self.catalogue_pages())
            second = inventory_antiegg_posts(
                self.config(root),
                transport=second_transport,
                registry=REGISTRY,
            )

            # Raising the bound changes the answer, and the run says so by
            # actually going back to the endpoint.
            self.assertEqual("complete", second["result"])
            self.assertEqual(self.total, len(second["record_ids"]))
            self.assertEqual(4, len(second_transport.calls))
            # The counts describe this run, not the ledger's whole history.
            self.assertEqual(4, second["record_counts"]["requests"])
            self.assertEqual(4, len(second["requests"]))
            self.assertEqual(4, second["pagination"]["requests_dispatched"])
            with Ledger(root / "ledger.sqlite3") as ledger:
                self.assertEqual(
                    "bounded_partial",
                    json.loads(
                        ledger.get_record(
                            "evidence", "evidence_antiegg_posts_verdict_001"
                        )["summary"]
                    )["result"],
                )
                self.assertEqual(
                    "complete",
                    json.loads(
                        ledger.get_record(
                            "evidence", "evidence_antiegg_posts_verdict_002"
                        )["summary"]
                    )["result"],
                )

    def test_cli_selects_the_posts_adapter_with_its_own_page_bound(self) -> None:
        arguments = build_parser().parse_args(
            [
                "inventory-public",
                "--source",
                "antiegg-posts",
                "--max-requests",
                "20",
                "--max-pages",
                "16",
                "--ledger",
                "ledger.sqlite3",
                "--sanitized-manifest",
                "manifest.json",
            ]
        )
        self.assertEqual("antiegg-posts", arguments.source)
        self.assertEqual(16, arguments.max_pages)


if __name__ == "__main__":
    unittest.main()
