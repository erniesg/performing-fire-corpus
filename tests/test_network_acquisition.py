from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.acquisition import (
    HTTPResponse,
    AcquisitionConfig,
    inventory_public_source,
)
from performing_fire_corpus.cli import build_parser
from performing_fire_corpus.ledger import Ledger


ROBOTS_URL = "https://antiegg.kr/robots.txt"
ARTICLE_URL = "https://antiegg.kr/25502/"


class FakeTransport:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, float, int]] = []

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse:
        self.calls.append(("GET", url, timeout_seconds, max_response_bytes))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def response(
    url: str,
    *,
    status: int = 200,
    mime_type: str,
    body: bytes,
    declared_bytes: int | None = None,
    retry_after: str | None = None,
    location: str | None = None,
) -> HTTPResponse:
    return HTTPResponse(
        url=url,
        status=status,
        mime_type=mime_type,
        body=body,
        declared_bytes=declared_bytes,
        retry_after=retry_after,
        location=location,
    )


class NetworkAcquisitionTests(unittest.TestCase):
    def config(self, root: Path, **overrides: object) -> AcquisitionConfig:
        values: dict[str, object] = {
            "source": "antiegg-fluxus",
            "max_requests": 2,
            "timeout_seconds": 3.0,
            "rate_limit_seconds": 0.0,
            "max_retries": 1,
            "max_elapsed_seconds": 10.0,
            "max_response_bytes": 4096,
            "ledger_path": root / "ledger.sqlite3",
            "manifest_path": root / "manifest.json",
        }
        values.update(overrides)
        return AcquisitionConfig(**values)

    def test_success_is_sanitized_durable_and_resume_safe(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        metadata = (
            b"<html><head>"
            b'<meta property="og:title" content="Synthetic Fluxus metadata">'
            b'<meta property="og:type" content="article">'
            b'<link rel="canonical" href="https://antiegg.kr/25502/">'
            b"</head></html>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [
                    response(
                        ROBOTS_URL,
                        mime_type="text/plain",
                        body=robots,
                        declared_bytes=len(robots),
                    ),
                    response(
                        ARTICLE_URL,
                        mime_type="text/html",
                        body=metadata,
                        declared_bytes=len(metadata),
                    ),
                ]
            )

            first = inventory_public_source(self.config(root), transport=transport)

            self.assertEqual("completed", first["result"])
            self.assertEqual(2, len(first["requests"]))
            self.assertEqual(
                {
                    "public_url",
                    "status",
                    "mime_type",
                    "byte_count",
                    "recorded_at",
                    "retry_outcome",
                    "response_sha256",
                },
                set(first["requests"][0]),
            )
            serialized = json.dumps(first, sort_keys=True).lower()
            self.assertNotIn("<html", serialized)
            self.assertNotIn("user-agent", serialized)
            self.assertNotIn("headers", serialized)
            self.assertEqual(
                first,
                json.loads((root / "manifest.json").read_text(encoding="utf-8")),
            )
            with Ledger(root / "ledger.sqlite3") as ledger:
                asset = ledger.get_record("asset", "asset_antiegg_fluxus_25502")
                self.assertEqual(
                    {"title": "Synthetic Fluxus metadata"}, asset["metadata"]
                )
                self.assertEqual(
                    "metadata_verified",
                    ledger.asset_state("asset_antiegg_fluxus_25502"),
                )

            resumed_transport = FakeTransport([])
            resumed = inventory_public_source(
                self.config(root), transport=resumed_transport
            )

            self.assertEqual(first, resumed)
            self.assertEqual([], resumed_transport.calls)

    def test_robots_denial_writes_one_durable_blocker_without_catalogue_request(
        self,
    ) -> None:
        robots = b"User-agent: *\nDisallow: /25502/\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                [response(ROBOTS_URL, mime_type="text/plain", body=robots)]
            )

            manifest = inventory_public_source(
                self.config(root), transport=transport
            )

            self.assertEqual("blocked", manifest["result"])
            self.assertEqual("robots_denied", manifest["blocker"]["code"])
            self.assertIn("review robots policy", manifest["blocker"]["next_safe_action"])
            self.assertEqual([ROBOTS_URL], [call[1] for call in transport.calls])
            with Ledger(root / "ledger.sqlite3") as ledger:
                self.assertEqual(
                    "blocked", ledger.asset_state("asset_antiegg_fluxus_25502")
                )
                self.assertEqual(1, len(ledger.progress()["blockers"]))

            resumed_transport = FakeTransport([])
            resumed = inventory_public_source(
                self.config(root), transport=resumed_transport
            )
            self.assertEqual(manifest, resumed)
            self.assertEqual([], resumed_transport.calls)

    def test_rate_limit_exhaustion_and_oversized_response_fail_closed(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        with tempfile.TemporaryDirectory() as temporary:
            rate_root = Path(temporary) / "rate"
            rate_root.mkdir()
            rate_transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=robots),
                    response(
                        ARTICLE_URL,
                        status=429,
                        mime_type="text/html",
                        body=b"",
                        retry_after="1",
                    ),
                ]
            )

            rate_manifest = inventory_public_source(
                self.config(rate_root), transport=rate_transport
            )

            self.assertEqual("rate_limit_exhausted", rate_manifest["blocker"]["code"])
            self.assertEqual(
                "retry_exhausted",
                rate_manifest["requests"][-1]["retry_outcome"],
            )

            size_root = Path(temporary) / "size"
            size_root.mkdir()
            size_transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=robots),
                    response(
                        ARTICLE_URL,
                        mime_type="text/html",
                        body=b"",
                        declared_bytes=5000,
                    ),
                ]
            )

            size_manifest = inventory_public_source(
                self.config(size_root), transport=size_transport
            )

            self.assertEqual("response_oversized", size_manifest["blocker"]["code"])
            self.assertEqual(2, len(size_transport.calls))

    def test_observed_size_and_unsafe_metadata_fail_closed(self) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        with tempfile.TemporaryDirectory() as temporary:
            observed_root = Path(temporary) / "observed"
            observed_root.mkdir()
            oversized_body = (
                b"<html><head>"
                b'<meta property="og:title" content="Synthetic title">'
                b'<meta property="og:type" content="article">'
                b'<link rel="canonical" href="https://antiegg.kr/25502/">'
                b"</head></html>"
            )
            observed_transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=robots),
                    response(
                        ARTICLE_URL,
                        mime_type="text/html",
                        body=oversized_body,
                    ),
                ]
            )

            observed_manifest = inventory_public_source(
                self.config(observed_root, max_response_bytes=64),
                transport=observed_transport,
            )

            self.assertEqual(
                "response_oversized", observed_manifest["blocker"]["code"]
            )
            self.assertIsNone(
                observed_manifest["requests"][-1]["response_sha256"]
            )

            unsafe_root = Path(temporary) / "unsafe"
            unsafe_root.mkdir()
            unsafe_metadata = (
                b"<html><head>"
                b'<meta property="og:title" '
                b'content="Synthetic https://example.invalid title">'
                b'<meta property="og:type" content="article">'
                b'<link rel="canonical" href="https://antiegg.kr/25502/">'
                b"</head></html>"
            )
            unsafe_transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=robots),
                    response(
                        ARTICLE_URL,
                        mime_type="text/html",
                        body=unsafe_metadata,
                    ),
                ]
            )

            unsafe_manifest = inventory_public_source(
                self.config(unsafe_root), transport=unsafe_transport
            )

            self.assertEqual(
                "response_structure_changed", unsafe_manifest["blocker"]["code"]
            )

    def test_resume_after_partial_request_evidence_uses_new_stable_request_id(
        self,
    ) -> None:
        robots = b"User-agent: *\nAllow: /\n"
        metadata = (
            b"<html><head>"
            b'<meta property="og:title" content="Synthetic resumed metadata">'
            b'<meta property="og:type" content="article">'
            b'<link rel="canonical" href="https://antiegg.kr/25502/">'
            b"</head></html>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Ledger(root / "ledger.sqlite3") as ledger:
                ledger.upsert(
                    {
                        "schema_version": 1,
                        "record_type": "source",
                        "source_id": "source_antiegg_fluxus",
                        "public_url": ARTICLE_URL,
                        "source_kind": "article",
                        "metadata": {"adapter": "antiegg-fluxus"},
                    }
                )
                ledger.upsert(
                    {
                        "schema_version": 1,
                        "record_type": "evidence",
                        "evidence_id": "evidence_antiegg_fluxus_request_001",
                        "subject_id": "source_antiegg_fluxus",
                        "evidence_kind": "sanitized_public_request",
                        "recorded_at": "2026-01-01T00:00:00Z",
                        "summary": json.dumps(
                            {
                                "byte_count": 0,
                                "mime_type": "unknown",
                                "recorded_at": "2026-01-01T00:00:00Z",
                                "response_sha256": None,
                                "retry_outcome": "interrupted",
                                "status": 0,
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        "public_references": [ROBOTS_URL],
                    }
                )
            transport = FakeTransport(
                [
                    response(ROBOTS_URL, mime_type="text/plain", body=robots),
                    response(ARTICLE_URL, mime_type="text/html", body=metadata),
                ]
            )

            manifest = inventory_public_source(
                self.config(root), transport=transport
            )

            self.assertEqual("completed", manifest["result"])
            self.assertEqual(3, len(manifest["requests"]))
            with Ledger(root / "ledger.sqlite3") as ledger:
                self.assertIsNotNone(
                    ledger.get_record(
                        "evidence", "evidence_antiegg_fluxus_request_003"
                    )
                )

    def test_cli_requires_all_network_acquisition_bounds(self) -> None:
        parser = build_parser()
        arguments = parser.parse_args(
            [
                "inventory-public",
                "--source",
                "antiegg-fluxus",
                "--max-requests",
                "2",
                "--timeout",
                "3",
                "--rate-limit",
                "1",
                "--retries",
                "1",
                "--max-elapsed",
                "10",
                "--max-response-bytes",
                "4096",
                "--ledger",
                "ledger.sqlite3",
                "--sanitized-manifest",
                "manifest.json",
            ]
        )
        self.assertEqual("inventory-public", arguments.command)

    def test_cli_defaults_are_conservative_and_paths_remain_explicit(self) -> None:
        arguments = build_parser().parse_args(
            [
                "inventory-public",
                "--ledger",
                "ledger.sqlite3",
                "--sanitized-manifest",
                "manifest.json",
            ]
        )
        self.assertEqual("antiegg-fluxus", arguments.source)
        self.assertEqual(2, arguments.max_requests)
        self.assertGreater(arguments.timeout, 0)
        self.assertGreater(arguments.rate_limit, 0)
        self.assertGreaterEqual(arguments.retries, 0)
        self.assertGreater(arguments.max_elapsed, arguments.timeout)
        self.assertGreater(arguments.max_response_bytes, 0)


if __name__ == "__main__":
    unittest.main()
