from __future__ import annotations

import json
import unittest
from pathlib import Path

from adapter_conformance_suite import StandardAdapterConformanceMixin
from performing_fire_corpus.adapter_conformance import (
    MetadataResponse,
    OfflineConformanceHarness,
)
from performing_fire_corpus.njp_video_library_adapter import (
    NJPVideoLibraryAdapter,
    SourceShapeUnreviewed,
    VideoLibraryAssetCandidate,
)
from performing_fire_corpus.registry import load_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")


def invented_item(
    item_id: str,
    *,
    record_class: str = "record_class_video_work",
    language: str = "language_bilingual",
    year: str = "2026",
    duration: str = "PT3M",
    title: str = "Invented mutable catalogue label",
) -> dict[str, str]:
    return {
        "id": item_id,
        "record_class": record_class,
        "language": language,
        "year": year,
        "duration": duration,
        "title": title,
    }


def invented_page(
    items: list[dict[str, str]],
    *,
    next_cursor: str | None = None,
    next_ordinal: int | None = None,
    terminal: bool = True,
    expected_total: int | None = None,
    rejected_count: int = 0,
    access_state: str | None = None,
    assets: list[dict[str, str]] | None = None,
) -> bytes:
    metadata = [
        '<meta name="terminal" content="true">'
        if terminal
        else '<meta name="terminal" content="false">',
        f'<meta name="rejected-count" content="{rejected_count}">',
    ]
    if next_cursor is not None:
        metadata.append(
            f'<meta name="next-cursor" content="{next_cursor}">'
        )
    if next_ordinal is not None:
        metadata.append(
            f'<meta name="next-ordinal" content="{next_ordinal}">'
        )
    if expected_total is not None:
        metadata.append(
            f'<meta name="expected-total" content="{expected_total}">'
        )
    if access_state is not None:
        metadata.append(
            f'<meta name="access-state" content="{access_state}">'
        )
    records = [
        (
            f'<article data-catalogue-id="{item["id"]}" '
            f'data-record-class="{item["record_class"]}" '
            f'data-language="{item["language"]}" '
            f'data-year="{item["year"]}" '
            f'data-duration="{item["duration"]}" '
            f'data-title="{item["title"]}"></article>'
        )
        for item in items
    ]
    candidates = [
        (
            f'<a data-asset-for="{item["record_id"]}" '
            f'data-asset-kind="{item["asset_kind"]}" '
            f'data-asset-mime="{item["mime_type"]}" '
            f'href="{item["url"]}">Invented candidate</a>'
        )
        for item in (assets or [])
    ]
    return (
        "<!doctype html><html><head>"
        + "".join(metadata)
        + "</head><body>"
        + "".join(records)
        + "".join(candidates)
        + "</body></html>"
    ).encode()


def identity_variants(item: dict[str, str]) -> list[dict[str, str]]:
    changed = dict(item)
    changed["title"] = "Invented English alias"
    return [item, changed]


class InventedVideoLibraryAdapter(NJPVideoLibraryAdapter):
    reviewed_asset_path_prefixes = ("/invented-assets/",)

    def _require_reviewed_shape(self) -> None:
        return None


class VideoLibraryAdapterConformance(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = InventedVideoLibraryAdapter
    registry = REGISTRY
    make_item = staticmethod(invented_item)
    make_page = staticmethod(invented_page)
    identity_variants = staticmethod(identity_variants)
    expected_mime_type = "text/html"
    unexpected_mime_type = "application/json"


class NJPVideoLibraryAdapterTests(unittest.TestCase):
    def test_production_adapter_is_held_until_source_shape_is_reviewed(
        self,
    ) -> None:
        adapter = NJPVideoLibraryAdapter()
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.build_request(None)
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.parse_page(invented_page([]), cursor=None)

    def test_adapter_has_exact_provenance_and_closed_endpoint_governance(
        self,
    ) -> None:
        adapter = NJPVideoLibraryAdapter()
        self.assertEqual("njp-video-library", adapter.source_id)
        self.assertEqual("njp-video-library-home", adapter.endpoint_id)
        self.assertEqual(("njpvideo.ggcf.kr",), adapter.allowed_hosts)

        governance = json.loads(
            (ROOT / "config" / "source-governance.v1.json").read_text()
        )
        endpoint = next(
            record
            for record in governance["records"]
            if record["endpoint_id"] == "njp-video-library-home"
        )
        self.assertEqual({"unknown"}, set(endpoint["fact_states"].values()))
        self.assertEqual(
            {"pending"},
            set(endpoint["operation_states"].values()),
        )

    def test_public_identifier_or_canonical_url_defines_identity(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        first = invented_item("catalogue-001")
        changed = dict(first)
        changed["title"] = "완전히 다른 가상 표시 제목"
        self.assertEqual(
            adapter.stable_record_id(first),
            adapter.stable_record_id(changed),
        )
        self.assertNotEqual(
            adapter.stable_record_id(first),
            adapter.stable_record_id(invented_item("catalogue-002")),
        )

        canonical = {
            "canonical_url": (
                "https://njpvideo.ggcf.kr/catalogue/invented-record"
            )
        }
        self.assertEqual(
            adapter.stable_record_id(canonical),
            adapter.stable_record_id(dict(canonical, title="Alias")),
        )
        for unsafe in (
            {"canonical_url": "https://unreviewed.invalid/catalogue/1"},
            {"canonical_url": "https://NJPVIDEO.GGCF.KR/catalogue/1"},
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr:443/catalogue/1"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/1?token=secret"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/../record"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/%2e%2e/record"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/%2Frecord"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue//record"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/raw space"
                )
            },
            {
                "canonical_url": (
                    "https://njpvideo.ggcf.kr/catalogue/raw\x00control"
                )
            },
            {"title": "A title is not an identifier"},
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                adapter.stable_record_id(unsafe)

    def test_optional_facts_remain_absent_instead_of_being_invented(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        body = invented_page([invented_item("catalogue-001")])
        body = body.replace(b' data-year="2026"', b"")
        body = body.replace(b' data-duration="PT3M"', b"")
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="text/html",
                body=body,
                final_url=request.url,
            )
        )
        metadata = result["records"][0]["metadata"]
        self.assertNotIn("year", metadata)
        self.assertNotIn("duration", metadata)
        self.assertEqual("complete_for_observed_endpoint", result["state"])

    def test_missing_or_changed_record_shape_fails_closed(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        malformed = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body><article></article></body></html>"
        )
        with self.assertRaises(ValueError):
            adapter.parse_page(malformed, cursor=None)

        unknown_class = invented_page(
            [
                invented_item(
                    "catalogue-001",
                    record_class="record_class_unreviewed",
                )
            ]
        )
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="text/html",
                body=unknown_class,
                final_url=request.url,
            )
        )
        self.assertEqual("shape_drift", result["stop_reason"])
        self.assertEqual([], result["records"])

    def test_all_asset_kinds_are_candidates_and_never_requests(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        kinds = {
            "asset_kind_caption": "text/vtt",
            "asset_kind_document": "application/pdf",
            "asset_kind_image": "image/jpeg",
            "asset_kind_thumbnail": "image/jpeg",
            "asset_kind_video": "video/mp4",
        }
        body = invented_page(
            [invented_item("catalogue-001")],
            assets=[
                {
                    "record_id": "catalogue-001",
                    "asset_kind": kind,
                    "mime_type": mime,
                    "url": f"/invented-assets/{index}",
                }
                for index, (kind, mime) in enumerate(sorted(kinds.items()))
            ],
        )
        candidates = adapter.asset_candidates(body)
        self.assertEqual(set(kinds), {item.asset_kind for item in candidates})
        for candidate in candidates:
            self.assertIsInstance(candidate, VideoLibraryAssetCandidate)
            self.assertEqual("pending", candidate.rights_state)
            self.assertFalse(candidate.acquisition_eligible)
            self.assertFalse(candidate.retry_allowed)
        for forbidden in (
            "build_asset_request",
            "download",
            "fetch_asset",
        ):
            self.assertFalse(hasattr(adapter, forbidden))

    def test_unsafe_asset_locators_and_relationships_fail_closed(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        unsafe = (
            "https://unreviewed.invalid/invented-assets/1",
            "https://person:secret@njpvideo.ggcf.kr/invented-assets/1",
            "https://njpvideo.ggcf.kr:444/invented-assets/1",
            "https://njpvideo.ggcf.kr/invented-assets/1?signature=private",
            "https://njpvideo.ggcf.kr/unreviewed-path/1",
            "https://njpvideo.ggcf.kr/invented-assets/%2e%2e/private",
            "https://njpvideo.ggcf.kr/invented-assets/%2Fprivate",
            "https://njpvideo.ggcf.kr/invented-assets//private",
            "/unreviewed/../invented-assets/private",
            "//njpvideo.ggcf.kr/invented-assets/private",
            "/invented-assets/raw space",
            "/invented-assets/raw\x00control",
        )
        for url in unsafe:
            with self.subTest(url=url), self.assertRaises(ValueError):
                adapter.asset_candidates(
                    invented_page(
                        [invented_item("catalogue-001")],
                        assets=[
                            {
                                "record_id": "catalogue-001",
                                "asset_kind": "asset_kind_video",
                                "mime_type": "video/mp4",
                                "url": url,
                            }
                        ],
                    )
                )

    def test_canonical_url_records_can_own_asset_candidates(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        canonical_url = (
            "https://njpvideo.ggcf.kr/catalogue/invented-record"
        )
        body = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<article data-canonical-url="'
            + canonical_url.encode()
            + b'" data-record-class="record_class_video_work" '
            b'data-language="language_bilingual"></article>'
            b'<a data-asset-for-url="'
            + canonical_url.encode()
            + b'" data-asset-kind="asset_kind_thumbnail" '
            b'data-asset-mime="image/jpeg" '
            b'href="/invented-assets/thumbnail"></a>'
            b"</body></html>"
        )
        candidate = adapter.asset_candidates(body)[0]
        self.assertEqual(
            adapter.stable_record_id(
                {"canonical_url": canonical_url}
            ),
            candidate.relationship_record_id,
        )

    def test_asset_candidates_admit_the_current_paginated_response(
        self,
    ) -> None:
        adapter = InventedVideoLibraryAdapter()
        body = invented_page(
            [invented_item("catalogue-002")],
            terminal=False,
            next_cursor="page-003",
            next_ordinal=2,
            assets=[
                {
                    "record_id": "catalogue-002",
                    "asset_kind": "asset_kind_video",
                    "mime_type": "video/mp4",
                    "url": "/invented-assets/2",
                }
            ],
        )
        candidates = adapter.asset_candidates(
            body,
            cursor="page-002",
        )
        self.assertEqual(1, len(candidates))
        self.assertEqual(
            adapter.stable_record_id({"id": "catalogue-002"}),
            candidates[0].relationship_record_id,
        )
        malformed_ordinal = body.replace(
            b'<meta name="next-ordinal" content="2">',
            b'<meta name="next-ordinal" content="999">',
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(
                malformed_ordinal,
                cursor="page-002",
            )
        repeated_cursor = body.replace(
            b'<meta name="next-cursor" content="page-003">',
            b'<meta name="next-cursor" content="page-002">',
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(
                repeated_cursor,
                cursor="page-002",
            )

    def test_candidates_require_the_same_admitted_page_and_records(
        self,
    ) -> None:
        adapter = InventedVideoLibraryAdapter()
        asset = (
            b'<a data-asset-for="catalogue-001" '
            b'data-asset-kind="asset_kind_video" '
            b'data-asset-mime="video/mp4" '
            b'href="/invented-assets/1"></a>'
        )
        missing_terminal = (
            b"<!doctype html><html><head></head><body>"
            b'<article data-catalogue-id="catalogue-001" '
            b'data-record-class="record_class_video_work" '
            b'data-language="language_en"></article>'
            + asset
            + b"</body></html>"
        )
        missing_record_metadata = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<article data-catalogue-id="catalogue-001"></article>'
            + asset
            + b"</body></html>"
        )
        generic_html_id = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<article id="catalogue-001" '
            b'data-record-class="record_class_video_work" '
            b'data-language="language_en"></article>'
            + asset
            + b"</body></html>"
        )
        for body in (
            missing_terminal,
            missing_record_metadata,
            generic_html_id,
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                adapter.asset_candidates(body)

        partial_asset = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<article data-catalogue-id="catalogue-001" '
            b'data-record-class="record_class_video_work" '
            b'data-language="language_en"></article>'
            b'<a data-asset-kind="asset_kind_video" '
            b'data-asset-mime="video/mp4" '
            b'href="/invented-assets/1"></a>'
            b"</body></html>"
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(partial_asset)

        valueless_asset_marker = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body><a data-asset-for></a></body></html>"
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(valueless_asset_marker)

        blocked_page = invented_page(
            [invented_item("catalogue-001")],
            access_state="login_required",
            assets=[
                {
                    "record_id": "catalogue-001",
                    "asset_kind": "asset_kind_video",
                    "mime_type": "video/mp4",
                    "url": "/invented-assets/1",
                }
            ],
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(blocked_page)

        conflicting_records = invented_page(
            [
                invented_item(
                    "catalogue-001",
                    language="language_en",
                ),
                invented_item(
                    "catalogue-001",
                    language="language_ko",
                ),
            ],
            assets=[
                {
                    "record_id": "catalogue-001",
                    "asset_kind": "asset_kind_video",
                    "mime_type": "video/mp4",
                    "url": "/invented-assets/1",
                }
            ],
        )
        with self.assertRaises(ValueError):
            adapter.asset_candidates(conflicting_records)

    def test_duplicate_controls_and_attributes_fail_closed(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        duplicate_terminal = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="false">'
            b'<meta name="terminal" content="true">'
            b"</head><body></body></html>"
        )
        duplicate_identifier = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<article data-catalogue-id="catalogue-001" '
            b'data-catalogue-id="catalogue-002" '
            b'data-record-class="record_class_video_work" '
            b'data-language="language_en"></article>'
            b"</body></html>"
        )
        incoherent_terminal = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b'<meta name="next-cursor" content="page-002">'
            b'<meta name="next-ordinal" content="1">'
            b"</head><body></body></html>"
        )
        for body in (
            duplicate_terminal,
            duplicate_identifier,
            incoherent_terminal,
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                adapter.parse_page(body, cursor=None)

    def test_source_markers_require_their_exact_elements(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        changed_record_element = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<div data-catalogue-id="catalogue-001" '
            b'data-record-class="record_class_video_work" '
            b'data-language="language_en"></div>'
            b"</body></html>"
        )
        changed_asset_element = (
            b"<!doctype html><html><head>"
            b'<meta name="terminal" content="true">'
            b"</head><body>"
            b'<video data-asset-for="catalogue-001" '
            b'data-asset-kind="asset_kind_video" '
            b'data-asset-mime="video/mp4" '
            b'href="/invented-assets/1"></video>'
            b"</body></html>"
        )
        for body in (
            changed_record_element,
            changed_asset_element,
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                adapter.parse_page(body, cursor=None)

    def test_controls_and_document_structure_are_canonical(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        malformed = (
            (
                b"<!doctype html><html><head>"
                b'<meta name="terminal">'
                b"</head><body></body></html>"
            ),
            (
                b"<!doctype html><html><head>"
                b'<meta name="terminal" content="true">'
                b'<meta name="access-state">'
                b"</head><body></body></html>"
            ),
            (
                b"<!doctype html><html><head>"
                b'<meta name="terminal" content="true">'
                b'<meta name="next-page" content="page-002">'
                b"</head><body></body></html>"
            ),
            (
                b"<!doctype html><html><head></head><body>"
                b'<meta name="terminal" content="true">'
                b"</body></html>"
            ),
            (
                b"<!doctype html><html><head>"
                b'<meta name="terminal" content="true">'
                b'<article data-catalogue-id="catalogue-001" '
                b'data-record-class="record_class_video_work" '
                b'data-language="language_en"></article>'
                b"</head><body></body></html>"
            ),
        )
        for body in malformed:
            with self.subTest(body=body), self.assertRaises(ValueError):
                adapter.parse_page(body, cursor=None)

        for value in (" 1", "+1", "01", "١"):
            body = invented_page(
                [],
                terminal=False,
                next_cursor="page-002",
                next_ordinal=1,
            ).replace(
                b'<meta name="next-ordinal" content="1">',
                (
                    f'<meta name="next-ordinal" content="{value}">'
                ).encode(),
            )
            with self.subTest(value=value), self.assertRaises(ValueError):
                adapter.parse_page(body, cursor=None)

    def test_pagination_cursor_has_one_canonical_spelling(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        canonical = invented_page(
            [],
            terminal=False,
            next_cursor="page-002",
            next_ordinal=1,
        )
        self.assertEqual(
            "page-002",
            adapter.parse_page(canonical, cursor=None)["next_cursor"],
        )
        for cursor in (
            "page-2",
            "page-02",
            "page-0002",
            "page-001",
        ):
            with self.subTest(cursor=cursor), self.assertRaises(
                ValueError
            ):
                adapter.parse_page(
                    invented_page(
                        [],
                        terminal=False,
                        next_cursor=cursor,
                        next_ordinal=1,
                    ),
                    cursor=None,
                )
            with self.subTest(request_cursor=cursor), self.assertRaises(
                ValueError
            ):
                adapter.build_request(cursor)

        for cursor, ordinal, current in (
            ("page-999", 1, None),
            ("page-003", 1, None),
            ("page-004", 2, "page-002"),
        ):
            with self.subTest(
                cursor=cursor,
                ordinal=ordinal,
                current=current,
            ), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_page(
                        [],
                        terminal=False,
                        next_cursor=cursor,
                        next_ordinal=ordinal,
                    ),
                    cursor=current,
                )

        with self.assertRaises(ValueError):
            adapter.asset_candidates(
                invented_page(
                    [invented_item("catalogue-001")],
                    assets=[
                        {
                            "record_id": "different-record",
                            "asset_kind": "asset_kind_video",
                            "mime_type": "video/mp4",
                            "url": "/invented-assets/1",
                        }
                    ],
                )
            )

    def test_access_denial_is_durable_without_retry_or_eligibility(self) -> None:
        adapter = InventedVideoLibraryAdapter()
        candidate = adapter.asset_candidates(
            invented_page(
                [invented_item("catalogue-001")],
                assets=[
                    {
                        "record_id": "catalogue-001",
                        "asset_kind": "asset_kind_document",
                        "mime_type": "application/pdf",
                        "url": "/invented-assets/document",
                    }
                ],
            )
        )[0]
        blocked = adapter.record_asset_status(candidate, 403)
        self.assertEqual("access_forbidden", blocked.access_blocker)
        self.assertEqual("blocked", blocked.rights_state)
        self.assertFalse(blocked.acquisition_eligible)
        self.assertFalse(blocked.retry_allowed)
        with self.assertRaises(ValueError):
            adapter.record_asset_status(candidate, 200)


if __name__ == "__main__":
    unittest.main()
