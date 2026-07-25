from __future__ import annotations

import json
import unittest
from pathlib import Path

from adapter_conformance_suite import StandardAdapterConformanceMixin
from performing_fire_corpus.adapter_conformance import (
    MetadataResponse,
    OfflineConformanceHarness,
)
from performing_fire_corpus.njp_center_adapters import (
    AttachmentCandidate,
    NJPCenterMainAdapter,
    NJPCenterVideoArchiveAdapter,
    SourceShapeUnreviewed,
)
from performing_fire_corpus.registry import load_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")


def invented_item(
    item_id: str,
    *,
    record_type: str = "record_type_programme",
    language: str = "language_ko",
    year: str = "2026",
    classification: str = "classification_public_programme",
    title: str = "Invented mutable label",
) -> dict[str, str]:
    return {
        "id": item_id,
        "record_type": record_type,
        "language": language,
        "year": year,
        "classification": classification,
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
    attachments: list[dict[str, str]] | None = None,
) -> bytes:
    metas = [
        '<meta name="terminal" content="true">'
        if terminal
        else '<meta name="terminal" content="false">',
        f'<meta name="rejected-count" content="{rejected_count}">',
    ]
    if next_cursor is not None:
        metas.append(f'<meta name="next-cursor" content="{next_cursor}">')
    if next_ordinal is not None:
        metas.append(f'<meta name="next-ordinal" content="{next_ordinal}">')
    if expected_total is not None:
        metas.append(f'<meta name="expected-total" content="{expected_total}">')
    if access_state is not None:
        metas.append(f'<meta name="access-state" content="{access_state}">')
    records = [
        (
            f'<article data-record-id="{item["id"]}" '
            f'data-record-type="{item["record_type"]}" '
            f'data-language="{item["language"]}" '
            f'data-year="{item["year"]}" '
            f'data-classification="{item["classification"]}" '
            f'data-title="{item["title"]}"></article>'
        )
        for item in items
    ]
    links = [
        (
            f'<a data-attachment-record="{item["record_id"]}" '
            f'data-attachment-mime="{item["mime_type"]}" '
            f'href="{item["url"]}">Invented attachment label</a>'
        )
        for item in (attachments or [])
    ]
    return (
        "<!doctype html><html><head>"
        + "".join(metas)
        + "</head><body>"
        + "".join(records)
        + "".join(links)
        + "</body></html>"
    ).encode()


def identity_variants(item: dict[str, str]) -> list[dict[str, str]]:
    changed = dict(item)
    changed["title"] = "Different invented mutable label"
    return [item, changed]


class InventedMainAdapter(NJPCenterMainAdapter):
    def _require_reviewed_shape(self) -> None:
        return None


class InventedVideoArchiveAdapter(NJPCenterVideoArchiveAdapter):
    def _require_reviewed_shape(self) -> None:
        return None


class _NJPConformance:
    registry = REGISTRY
    make_item = staticmethod(invented_item)
    make_page = staticmethod(invented_page)
    identity_variants = staticmethod(identity_variants)
    expected_mime_type = "text/html"
    unexpected_mime_type = "application/json"


class MainAdapterConformance(
    _NJPConformance,
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = InventedMainAdapter


class VideoArchiveAdapterConformance(
    _NJPConformance,
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = InventedVideoArchiveAdapter


class NJPCenterAdapterTests(unittest.TestCase):
    def test_production_adapters_are_held_until_source_shape_is_reviewed(
        self,
    ) -> None:
        for adapter in (
            NJPCenterMainAdapter(),
            NJPCenterVideoArchiveAdapter(),
        ):
            with self.subTest(source=adapter.source_id), self.assertRaises(
                SourceShapeUnreviewed
            ):
                adapter.build_request(None)

    def test_each_adapter_has_an_endpoint_specific_closed_policy(self) -> None:
        governance = json.loads(
            (ROOT / "config" / "source-governance.v1.json").read_text()
        )
        by_endpoint = {
            record["endpoint_id"]: record
            for record in governance["records"]
            if record["endpoint_id"] is not None
        }
        for endpoint_id in (
            "njp-center-main-home",
            "njp-center-video-archive-page",
        ):
            record = by_endpoint[endpoint_id]
            self.assertEqual(
                {"unknown"},
                set(record["fact_states"].values()),
            )
            self.assertEqual(
                {"pending"},
                set(record["operation_states"].values()),
            )

    def test_sources_remain_distinct_and_titles_do_not_define_identity(self) -> None:
        item = invented_item("record-001")
        main = InventedMainAdapter()
        archive = InventedVideoArchiveAdapter()
        self.assertNotEqual(main.source_id, archive.source_id)
        self.assertNotEqual(
            main.stable_record_id(item),
            archive.stable_record_id(item),
        )
        changed = dict(item)
        changed["title"] = "Changed bilingual display label"
        self.assertEqual(
            main.stable_record_id(item),
            main.stable_record_id(changed),
        )
        same_title_other_id = dict(item)
        same_title_other_id["id"] = "record-002"
        self.assertNotEqual(
            main.stable_record_id(item),
            main.stable_record_id(same_title_other_id),
        )

    def test_bilingual_variants_stay_language_specific_and_never_merge(self) -> None:
        adapter = InventedMainAdapter()
        shared_title = "동일한 초청 제목 / Identical invented label"
        body = invented_page(
            [
                invented_item(
                    "record-ko-001",
                    language="language_ko",
                    title=shared_title,
                ),
                invented_item(
                    "record-en-001",
                    language="language_en",
                    title=shared_title,
                ),
                invented_item(
                    "record-both-001",
                    language="language_bilingual",
                    title=shared_title,
                ),
            ]
        )
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
        self.assertEqual("complete_for_observed_endpoint", result["state"])
        self.assertEqual(0, result["duplicate_records"])
        self.assertEqual(3, len(result["records"]))
        self.assertEqual(
            3,
            len({record["record_id"] for record in result["records"]}),
        )
        self.assertEqual(
            ["language_bilingual", "language_en", "language_ko"],
            sorted(
                record["metadata"]["language"] for record in result["records"]
            ),
        )

    def test_one_source_blocker_never_blocks_or_authorizes_the_other(self) -> None:
        main = InventedMainAdapter()
        archive = InventedVideoArchiveAdapter()
        body = invented_page(
            [invented_item("record-001")],
            attachments=[
                {
                    "record_id": "record-001",
                    "mime_type": "application/pdf",
                    "url": "/storage/upload/invented-public-document.pdf",
                }
            ],
        )
        main_candidate = main.attachment_candidates(body)[0]
        archive_candidate = archive.attachment_candidates(body)[0]
        self.assertNotEqual(
            main_candidate.source_id,
            archive_candidate.source_id,
        )
        self.assertNotEqual(
            main_candidate.relationship_record_id,
            archive_candidate.relationship_record_id,
        )
        blocked = main.record_attachment_status(main_candidate, 403)
        self.assertEqual("blocked", blocked.rights_state)
        self.assertEqual("pending", archive_candidate.rights_state)
        self.assertIsNone(archive_candidate.access_blocker)
        self.assertFalse(archive_candidate.acquisition_eligible)
        for adapter, other in (
            (archive, main_candidate),
            (main, archive_candidate),
        ):
            with self.subTest(adapter=adapter.source_id), self.assertRaises(
                ValueError
            ):
                adapter.record_attachment_status(other, 403)

    def test_missing_year_is_an_explicit_unknown_observation(self) -> None:
        adapter = InventedVideoArchiveAdapter()
        body = invented_page([invented_item("record-001")]).replace(
            b' data-year="2026"',
            b"",
        )
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
        self.assertEqual("complete_for_observed_endpoint", result["state"])
        self.assertNotIn("year", result["records"][0]["metadata"])

    def test_attachment_candidates_are_ineligible_and_exact_403_is_durable(self) -> None:
        adapter = InventedMainAdapter()
        body = invented_page(
            [invented_item("record-001")],
            attachments=[
                {
                    "record_id": "record-001",
                    "mime_type": "application/pdf",
                    "url": "/storage/upload/invented-public-document.pdf",
                }
            ],
        )
        candidates = adapter.attachment_candidates(body)
        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("pending", candidate.rights_state)
        self.assertFalse(candidate.acquisition_eligible)
        blocked = adapter.record_attachment_status(candidate, 403)
        self.assertEqual("access_forbidden", blocked.access_blocker)
        self.assertEqual("blocked", blocked.rights_state)
        self.assertEqual(candidate.public_url, blocked.public_url)
        self.assertFalse(blocked.retry_allowed)

        unsafe = AttachmentCandidate(
            source_id=adapter.source_id,
            relationship_record_id=candidate.relationship_record_id,
            public_url="https://unreviewed.invalid/file.pdf",
            claimed_mime_type="application/pdf",
            rights_state="approved",
            acquisition_eligible=True,
            retry_allowed=True,
        )
        with self.assertRaises(ValueError):
            adapter.record_attachment_status(unsafe, 403)

    def test_attachment_locators_fail_closed_on_credentials_or_other_hosts(self) -> None:
        adapter = InventedMainAdapter()
        for url in (
            "https://person:secret@njp.ggcf.kr/storage/upload/file.pdf",
            "https://njp.ggcf.kr:444/storage/upload/file.pdf",
            "https://unreviewed.invalid/storage/upload/file.pdf",
            "/storage/upload/file.pdf?token=opaque",
        ):
            body = invented_page(
                [invented_item("record-001")],
                attachments=[
                    {
                        "record_id": "record-001",
                        "mime_type": "application/pdf",
                        "url": url,
                    }
                ],
            )
            with self.subTest(url=url), self.assertRaises(ValueError):
                adapter.attachment_candidates(body)

    def test_missing_or_changed_record_shape_fails_closed(self) -> None:
        adapter = InventedVideoArchiveAdapter()
        malformed = (
            b'<html><head><meta name="terminal" content="true"></head>'
            b'<body><article data-record-id="record-001"></article></body></html>'
        )
        with self.assertRaises(ValueError):
            adapter.parse_page(malformed, cursor=None)

        truncated = invented_page([invented_item("record-001")]).replace(
            b"</article></body></html>",
            b"",
        )
        with self.assertRaises(ValueError):
            adapter.parse_page(truncated, cursor=None)

    def test_attachment_must_reference_a_record_on_the_same_page(self) -> None:
        adapter = InventedMainAdapter()
        orphan = invented_page(
            [invented_item("record-001")],
            attachments=[
                {
                    "record_id": "record-999",
                    "mime_type": "application/pdf",
                    "url": "/storage/upload/invented.pdf",
                }
            ],
        )
        with self.assertRaises(ValueError):
            adapter.attachment_candidates(orphan)

    def test_long_public_id_still_has_a_bounded_source_identity(self) -> None:
        adapter = InventedMainAdapter()
        body = invented_page([invented_item("a" * 128)])
        page = adapter.parse_page(body, cursor=None)
        self.assertEqual(64, len(page["records"][0]["source_identity"]))
