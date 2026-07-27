from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from performing_fire_corpus.njp_site_inventory import (
    ROBOTS_URL,
    MetadataSafeResponse,
    NJPInventoryError,
)
from performing_fire_corpus.cli import _njp_archive_shape_path
from performing_fire_corpus.njp_video_archive_shape import (
    VideoArchiveShapeError,
    review_video_archive_shape,
)


ARCHIVE_URL = "https://njp.ggcf.kr/pages/videoarchive"
COMMIT = "a" * 40
NOW = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = "config/source-governance.v1.json"
VERIFY = "performing_fire_corpus.njp_video_archive_shape._verify_exact_clean_head"
HOSTILE_VALUE = "private-canary-source-value"
PRIVATE_PROSE = "private source prose canary"


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
            raise AssertionError("unexpected network request")
        return self.responses.pop(0)


class RaisingTransport(FakeTransport):
    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MetadataSafeResponse:
        self.calls.append((method, url, timeout_seconds, max_response_bytes))
        raise NJPInventoryError(HOSTILE_VALUE)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def oversleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds + 2.0


class ScriptedClock:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)
        self.last = values[-1]

    def __call__(self) -> float:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def response(
    url: str,
    *,
    status: int = 200,
    mime_type: str = "text/html",
    body: bytes = b"",
    oversized: bool = False,
) -> MetadataSafeResponse:
    return MetadataSafeResponse(
        url=url,
        status=status,
        mime_type=mime_type,
        body=body,
        declared_bytes=len(body),
        oversized=oversized,
    )


def allowed_robots() -> MetadataSafeResponse:
    return response(
        ROBOTS_URL,
        mime_type="text/plain",
        body=b"User-agent: *\nAllow: /\n",
    )


def invented_page(json_body: str | None = None) -> bytes:
    embedded = (
        json_body
        if json_body is not None
        else (
        '{"private source prose canary":'
            '[1,"private-canary-source-value",false,null]}'
        )
    )
    return (
        "<!doctype html><html><head>"
        f'<meta name="description" content="{PRIVATE_PROSE}">'
        "</head><body>"
        f'<main id="{PRIVATE_PROSE}" class="{HOSTILE_VALUE}" '
        f'data-private="{PRIVATE_PROSE}">'
        f'<a href="/{HOSTILE_VALUE}/42?{PRIVATE_PROSE}=ko">'
        "비공개 합성 제목</a>"
        '<script id="private-json-id" type="application/json">'
        f"{embedded}"
        "</script>"
        "</main></body></html>"
    ).encode()


def review(
    output: Path,
    transport: FakeTransport,
    **overrides: object,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "commit_sha": COMMIT,
        "repo_root": REPO_ROOT,
        "governance_path": GOVERNANCE,
        "output_path": output,
        "transport": transport,
        "now": lambda: NOW,
        "monotonic": lambda: 0.0,
        "sleeper": lambda _: None,
    }
    arguments.update(overrides)
    return review_video_archive_shape(**arguments)


class NJPVideoArchiveShapeTests(unittest.TestCase):
    @mock.patch(VERIFY)
    def test_shape_review_retains_only_categorical_structure(
        self,
        verify: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shape.json"
            transport = FakeTransport(
                [
                    allowed_robots(),
                    response(ARCHIVE_URL, body=invented_page()),
                ]
            )

            report = review(output, transport)

            self.assertEqual("shape_observed", report["state"])
            self.assertEqual([], report["blocker_codes"])
            self.assertTrue(report["exact_head_verified"])
            verify.assert_called_once_with(REPO_ROOT, COMMIT)
            self.assertEqual(
                [("GET", ROBOTS_URL), ("GET", ARCHIVE_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            serialized = json.dumps(report, ensure_ascii=False)
            for private in (
                HOSTILE_VALUE,
                PRIVATE_PROSE,
                "비공개 합성 제목",
                "private-json-id",
            ):
                self.assertNotIn(private, serialized)
            self.assertNotIn("/42", serialized)
            self.assertNotIn("data-private", serialized)
            self.assertIn('"host_scope": "same-host"', serialized)
            self.assertIn('"category": "data"', serialized)
            self.assertIn('"path_segments": ["slug", "numeric"]', serialized)
            self.assertEqual(report, json.loads(output.read_text()))
            self.assertFalse(report["plan"]["raw_body_retained"])
            self.assertFalse(report["plan"]["prose_retained"])
            self.assertEqual(
                0,
                report["structure"]["html_recovery_events"],
            )
            shapes = report["structure"]["json_shapes"]
            type_counts = {
                item["type"]: item["count"]
                for item in shapes
                if item["depth"] == 2
            }
            self.assertEqual(
                {"boolean": 1, "null": 1, "number": 1, "string": 1},
                type_counts,
            )

    @mock.patch(VERIFY)
    def test_robots_denial_prevents_archive_request(
        self,
        verify: mock.Mock,
    ) -> None:
        denied = response(
            ROBOTS_URL,
            mime_type="text/plain",
            body=b"User-agent: *\nDisallow: /\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport([denied])
            report = review(Path(temporary) / "shape.json", transport)

            self.assertEqual("blocked", report["state"])
            self.assertEqual(["robots_denied"], report["blocker_codes"])
            self.assertEqual(
                [("GET", ROBOTS_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_access_and_size_failures_return_fixed_blockers(
        self,
        verify: mock.Mock,
    ) -> None:
        for page, expected in (
            (response(ARCHIVE_URL, status=403), "access_forbidden"),
            (
                response(ARCHIVE_URL, body=b"", oversized=True),
                "response_oversized",
            ),
            (
                response(
                    "https://njp.ggcf.kr/login",
                    status=302,
                ),
                "disallowed_redirect",
            ),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                report = review(
                    Path(temporary) / "shape.json",
                    FakeTransport([allowed_robots(), page]),
                )
                self.assertEqual("blocked", report["state"])
                self.assertEqual([expected], report["blocker_codes"])
                self.assertIsNone(report["structure"])
        self.assertEqual(3, verify.call_count)

    @mock.patch(VERIFY)
    def test_byte_limit_is_enforced_independently_of_transport(
        self,
        verify: mock.Mock,
    ) -> None:
        oversized_robots = response(
            ROBOTS_URL,
            mime_type="text/plain",
            body=b"User-agent: *\nAllow: /\n" + (b" " * 1100),
        )
        oversized_page = response(
            ARCHIVE_URL,
            body=b"x" * 1100,
        )
        for responses, expected in (
            ([oversized_robots], "robots_ambiguous"),
            ([allowed_robots(), oversized_page], "response_oversized"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                report = review(
                    Path(temporary) / "shape.json",
                    FakeTransport(responses),
                    max_response_bytes=1024,
                )
                self.assertEqual([expected], report["blocker_codes"])
        self.assertEqual(2, verify.call_count)

    @mock.patch(VERIFY)
    def test_transport_failure_never_serializes_exception_text(
        self,
        verify: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport = RaisingTransport([])
            report = review(Path(temporary) / "shape.json", transport)

            self.assertEqual(["transport_error"], report["blocker_codes"])
            self.assertNotIn(HOSTILE_VALUE, json.dumps(report))
            self.assertEqual(
                "transport_error",
                report["requests"][0]["failure_code"],
            )
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_expired_governance_blocks_before_network(
        self,
        verify: mock.Mock,
    ) -> None:
        registry = json.loads(
            (REPO_ROOT / GOVERNANCE).read_text(encoding="utf-8")
        )
        for record in registry["records"]:
            if (
                record["source_id"] == "njp-center-video-archive"
                and record["endpoint_id"]
                == "njp-center-video-archive-page"
            ):
                for observation in record["observations"]:
                    observation["expires_at"] = "2026-07-27T19:59:59Z"
                for decision in record["decisions"]:
                    decision["expires_at"] = "2026-07-27T19:59:59Z"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / GOVERNANCE
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(registry), encoding="utf-8")
            transport = FakeTransport([])
            report = review(
                root / "shape.json",
                transport,
                repo_root=root,
            )

            self.assertEqual("blocked", report["state"])
            self.assertEqual(
                ["governance_not_authorized"],
                report["blocker_codes"],
            )
            self.assertFalse(report["governance"]["authorized"])
            self.assertEqual([], report["requests"])
            self.assertEqual([], transport.calls)
            verify.assert_called_once_with(root.resolve(), COMMIT)

    @mock.patch(VERIFY)
    def test_governance_must_cover_the_full_elapsed_horizon(
        self,
        verify: mock.Mock,
    ) -> None:
        registry = json.loads(
            (REPO_ROOT / GOVERNANCE).read_text(encoding="utf-8")
        )
        for record in registry["records"]:
            if (
                record["source_id"] == "njp-center-video-archive"
                and record["endpoint_id"]
                == "njp-center-video-archive-page"
            ):
                for observation in record["observations"]:
                    observation["expires_at"] = "2026-07-27T20:00:01Z"
                for decision in record["decisions"]:
                    decision["expires_at"] = "2026-07-27T20:00:01Z"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / GOVERNANCE
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(registry), encoding="utf-8")
            transport = FakeTransport([])
            report = review(
                root / "shape.json",
                transport,
                repo_root=root,
                elapsed_seconds=30.0,
            )

            self.assertEqual(
                ["governance_not_authorized"],
                report["blocker_codes"],
            )
            self.assertEqual([], transport.calls)
            verify.assert_called_once_with(root.resolve(), COMMIT)

    @mock.patch(VERIFY)
    def test_elapsed_bound_prevents_request_after_sleep(
        self,
        verify: mock.Mock,
    ) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport([allowed_robots()])
            report = review(
                Path(temporary) / "shape.json",
                transport,
                monotonic=clock,
                sleeper=clock.oversleep,
                elapsed_seconds=2.0,
                per_host_interval_seconds=1.0,
            )

            self.assertEqual(["elapsed_bound"], report["blocker_codes"])
            self.assertEqual([1.0], clock.sleeps)
            self.assertEqual(
                [("GET", ROBOTS_URL)],
                [(method, url) for method, url, _, _ in transport.calls],
            )
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_elapsed_bound_interrupts_structure_parsing(
        self,
        verify: mock.Mock,
    ) -> None:
        clock = ScriptedClock(([0.0] * 7) + [2.0])
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport(
                [
                    allowed_robots(),
                    response(ARCHIVE_URL, body=invented_page()),
                ]
            )
            report = review(
                Path(temporary) / "shape.json",
                transport,
                monotonic=clock,
                elapsed_seconds=1.0,
                per_host_interval_seconds=0.1,
            )

            self.assertEqual(["elapsed_bound"], report["blocker_codes"])
            self.assertIsNone(report["structure"])
            self.assertEqual(2, len(transport.calls))
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_malformed_json_blocks_shape(
        self,
        verify: mock.Mock,
    ) -> None:
        malformed_values = (
            '{"broken":',
            " ",
            '{"value": NaN}',
            ("[" * 10000) + "0" + ("]" * 10000),
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed[:20]), tempfile.TemporaryDirectory() as temporary:
                report = review(
                    Path(temporary) / "shape.json",
                    FakeTransport(
                        [
                            allowed_robots(),
                            response(
                                ARCHIVE_URL,
                                body=invented_page(malformed),
                            ),
                        ]
                    ),
                )

                self.assertEqual(
                    ["source_shape_unreadable"],
                    report["blocker_codes"],
                )
                self.assertTrue(report["structure"]["json_unreadable"])
        self.assertEqual(len(malformed_values), verify.call_count)

    @mock.patch(VERIFY)
    def test_json_key_shape_changes_receipt_without_exposing_keys(
        self,
        verify: mock.Mock,
    ) -> None:
        reports = []
        with tempfile.TemporaryDirectory() as temporary:
            for index, key in enumerate(("alpha-private-key", "beta-private-key")):
                reports.append(
                    review(
                        Path(temporary) / f"shape-{index}.json",
                        FakeTransport(
                            [
                                allowed_robots(),
                                response(
                                    ARCHIVE_URL,
                                    body=invented_page(
                                        json.dumps({key: 1})
                                    ),
                                ),
                            ]
                        ),
                    )
                )

        self.assertNotEqual(
            reports[0]["structure"]["structure_sha256"],
            reports[1]["structure"]["structure_sha256"],
        )
        serialized = json.dumps(reports)
        self.assertNotIn("alpha-private-key", serialized)
        self.assertNotIn("beta-private-key", serialized)
        self.assertEqual(2, verify.call_count)

    @mock.patch(VERIFY)
    def test_json_node_bound_is_explicit_not_silent_sampling(
        self,
        verify: mock.Mock,
    ) -> None:
        large_json = json.dumps({"items": list(range(2100))})
        with tempfile.TemporaryDirectory() as temporary:
            report = review(
                Path(temporary) / "shape.json",
                FakeTransport(
                    [
                        allowed_robots(),
                        response(
                            ARCHIVE_URL,
                            body=invented_page(large_json),
                        ),
                    ]
                ),
            )

            self.assertEqual(["shape_summary_bound"], report["blocker_codes"])
            self.assertTrue(report["structure"]["summary_truncated"])
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_html_recovery_is_not_reported_as_capacity_truncation(
        self,
        verify: mock.Mock,
    ) -> None:
        optional_end_tags = (
            "<!doctype html><html><head><title>x</title></head><body>"
            "<ul><li>one<li>two</ul></body></html>"
        ).encode()
        with tempfile.TemporaryDirectory() as temporary:
            report = review(
                Path(temporary) / "shape.json",
                FakeTransport(
                    [
                        allowed_robots(),
                        response(ARCHIVE_URL, body=optional_end_tags),
                    ]
                ),
            )

            self.assertEqual("shape_observed", report["state"])
            self.assertEqual([], report["blocker_codes"])
            self.assertGreater(
                report["structure"]["html_recovery_events"],
                0,
            )
            self.assertFalse(report["structure"]["summary_truncated"])
            verify.assert_called_once()

    @mock.patch(VERIFY)
    def test_exact_head_verification_cannot_be_bypassed_by_transport(
        self,
        verify: mock.Mock,
    ) -> None:
        verify.side_effect = VideoArchiveShapeError("exact_head_not_verified")
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                VideoArchiveShapeError,
                "exact_head_not_verified",
            ):
                review(Path(temporary) / "shape.json", transport)
        self.assertEqual([], transport.calls)
        verify.assert_called_once_with(REPO_ROOT, COMMIT)

    @mock.patch(VERIFY)
    def test_invalid_plan_fails_before_verification_or_network(
        self,
        verify: mock.Mock,
    ) -> None:
        transport = FakeTransport([])
        invalid_plans = (
            {"commit_sha": "short"},
            {"commit_sha": COMMIT, "elapsed_seconds": 1e308},
        )
        with tempfile.TemporaryDirectory() as temporary:
            for override in invalid_plans:
                with self.subTest(override=override):
                    with self.assertRaises(VideoArchiveShapeError):
                        review_video_archive_shape(
                            repo_root=REPO_ROOT,
                            governance_path=GOVERNANCE,
                            output_path=Path(temporary) / "shape.json",
                            transport=transport,
                            **override,
                        )
        self.assertEqual([], transport.calls)
        verify.assert_not_called()

    def test_cli_output_path_rejects_ignored_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external"
            external.mkdir()
            local = root / ".local"
            local.mkdir()
            (local / "njp-video-archive-shape").symlink_to(
                external,
                target_is_directory=True,
            )
            with mock.patch(
                "performing_fire_corpus.cli.Path.cwd",
                return_value=root,
            ):
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    _njp_archive_shape_path(
                        ".local/njp-video-archive-shape/shape.json"
                    )
