from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from adapter_conformance_suite import StandardAdapterConformanceMixin
from performing_fire_corpus.adapter_conformance import (
    MetadataResponse,
    OfflineConformanceHarness,
)
from performing_fire_corpus.antiegg_metadata_adapters import (
    ANTIEGGPostsMetadataAdapter,
    ANTIEGGSitemapAdapter,
    CONTROL_NAMESPACE,
    FORBIDDEN_POST_FIELDS,
    POSTS_PER_PAGE,
    POSTS_RESPONSE_FIELDS,
    POSTS_REVIEWED_MAX_PER_PAGE,
    SITEMAP_NAMESPACE,
    SourceShapeUnreviewed,
    XML_PROLOGUE,
)
from performing_fire_corpus.governance import (
    FACT_DIMENSIONS,
    PASSING_FACT_STATES,
    SOURCE_OPERATIONS,
    evaluate_source_operation,
)
from performing_fire_corpus.registry import load_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")
GOVERNANCE = json.loads(
    (ROOT / "config" / "source-governance.v1.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
FLUXUS_ARTICLE_URL = "https://antiegg.kr/25502"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "antiegg"
POSTS_PAGE_ONE = (FIXTURE_ROOT / "posts-page-1.json").read_bytes()
POSTS_PAGE_ONE_HEADERS = json.loads(
    (FIXTURE_ROOT / "posts-page-1.headers.json").read_text(encoding="utf-8")
)
POSTS_BEYOND_END = (FIXTURE_ROOT / "posts-beyond-end.json").read_bytes()
POSTS_BEYOND_END_HEADERS = json.loads(
    (FIXTURE_ROOT / "posts-beyond-end.headers.json").read_text(
        encoding="utf-8"
    )
)


def invented_sitemap_item(
    item_id: str,
    *,
    lastmod: str = "2026-01-02T03:04:05Z",
) -> dict[str, str]:
    return {
        "loc": f"https://antiegg.kr/{item_id}",
        "lastmod": lastmod,
    }


def invented_sitemap_page(
    items: list[dict[str, str]],
    *,
    next_cursor: str | None = None,
    next_ordinal: int | None = None,
    terminal: bool = True,
    expected_total: int | None = None,
    rejected_count: int = 0,
    access_state: str | None = None,
    container: str = "urlset",
) -> bytes:
    controls = [
        f'pf:terminal="{"true" if terminal else "false"}"',
        f'pf:rejected-count="{rejected_count}"',
    ]
    for name, value in (
        ("next-cursor", next_cursor),
        ("next-ordinal", next_ordinal),
        ("expected-total", expected_total),
        ("access-state", access_state),
    ):
        if value is not None:
            controls.append(f'pf:{name}="{value}"')
    entry_tag = "url" if container == "urlset" else "sitemap"
    entries = []
    for item in items:
        leaves = f"<loc>{item['loc']}</loc>"
        if "lastmod" in item:
            leaves += f"<lastmod>{item['lastmod']}</lastmod>"
        entries.append(f"<{entry_tag}>{leaves}</{entry_tag}>")
    return (
        XML_PROLOGUE
        + f'<{container} xmlns="{SITEMAP_NAMESPACE}"'
        + f' xmlns:pf="{CONTROL_NAMESPACE}" '
        + " ".join(controls)
        + ">"
        + "".join(entries)
        + f"</{container}>"
    ).encode("utf-8")


def sitemap_identity_variants(item: dict[str, str]) -> list[dict[str, str]]:
    changed = dict(item)
    changed["lastmod"] = "2026-05-06T07:08:09Z"
    return [item, changed]


def invented_post_item(
    item_id: str,
    *,
    post_format: str = "standard",
    lang: str = "ko",
    post_type: str = "post",
    status: str = "publish",
    date_gmt: str = "2026-01-02T03:04:05",
    modified_gmt: str = "2026-02-03T04:05:06",
) -> dict[str, object]:
    del post_format, lang, post_type, status
    number = int(item_id)
    return {
        "author": 7,
        "categories": [4],
        "date": date_gmt,
        "excerpt": {
            "protected": False,
            "rendered": "<p>Invented fixture excerpt.</p>",
        },
        "featured_media": 31,
        "id": number,
        "link": f"https://antiegg.kr/{number}",
        "modified": modified_gmt,
        "slug": f"fixture-{number}",
        "tags": [9],
        "title": {"rendered": "가상의 표시 제목"},
    }


def invented_posts_page(
    items: list[dict[str, object]],
    *,
    next_cursor: str | None = None,
    next_ordinal: int | None = None,
    terminal: bool = True,
    expected_total: int | None = None,
    rejected_count: int = 0,
    access_state: str | None = None,
) -> bytes:
    del (
        next_cursor,
        next_ordinal,
        terminal,
        expected_total,
        rejected_count,
        access_state,
    )
    return json.dumps(
        [dict(item) for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def post_identity_variants(item: dict[str, object]) -> list[dict[str, object]]:
    changed = dict(item)
    changed["title"] = {"rendered": "Completely different invented label"}
    changed["modified_gmt"] = "2026-06-07T08:09:10Z"
    return [item, changed]


def invented_governance(
    source_id: str,
    endpoint_id: str,
    *,
    governance_id: str,
    blockers: tuple[dict[str, object], ...] = (),
    decision_expires_at: str = "2026-08-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "source_governance",
        "source_governance_id": governance_id,
        "source_id": source_id,
        "endpoint_id": endpoint_id,
        "fact_states": dict(PASSING_FACT_STATES),
        "observations": [
            {
                "dimension": dimension,
                "state": PASSING_FACT_STATES[dimension],
                "observed_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-08-01T00:00:00Z",
                "evidence_id": f"evidence_invented_{dimension}",
                "next_safe_action": "Revalidate this invented fact after expiry.",
            }
            for dimension in FACT_DIMENSIONS
        ],
        "operation_states": {
            operation: (
                "approved" if operation == "metadata_inventory" else "pending"
            )
            for operation in SOURCE_OPERATIONS
        },
        "decisions": [
            {
                "affected_operation": "metadata_inventory",
                "state": "approved",
                "authority_class": "source_policy_reviewer",
                "basis_code": "invented_public_metadata",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": decision_expires_at,
                "review_trigger": "Recheck when the endpoint policy changes.",
                "next_safe_action": "Run only the bounded metadata inventory.",
            }
        ],
        "blockers": list(blockers),
        "evaluated_at": "2026-07-23T00:00:00Z",
    }


class InventedSitemapAdapter(ANTIEGGSitemapAdapter):
    def _require_reviewed_shape(self) -> None:
        return None


class InventedPostsAdapter(ANTIEGGPostsMetadataAdapter):
    def _require_reviewed_shape(self) -> None:
        return None


class SitemapAdapterConformance(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = InventedSitemapAdapter
    registry = REGISTRY
    make_item = staticmethod(invented_sitemap_item)
    make_page = staticmethod(invented_sitemap_page)
    identity_variants = staticmethod(sitemap_identity_variants)
    expected_mime_type = "text/xml"
    unexpected_mime_type = "text/html"


class ANTIEGGAdapterHoldTests(unittest.TestCase):
    def test_only_the_unreviewed_sitemap_adapter_remains_held(
        self,
    ) -> None:
        adapter = ANTIEGGSitemapAdapter()
        body = invented_sitemap_page([])
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.build_request(None)
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.parse_page(body, cursor=None)
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.detect_access_blocker(body)
        with self.assertRaises(SourceShapeUnreviewed):
            adapter.declared_total_observation(
                body, observed_at="2026-07-24T00:00:00Z"
            )

        posts = ANTIEGGPostsMetadataAdapter()
        self.assertIn("page=1", posts.build_request(None).url)
        self.assertEqual(
            2,
            len(
                posts.parse_page(
                    POSTS_PAGE_ONE,
                    cursor=None,
                    response_headers=POSTS_PAGE_ONE_HEADERS,
                )["records"]
            ),
        )

    def test_adapters_expose_no_prose_or_acquisition_entry_points(self) -> None:
        for adapter in (
            ANTIEGGSitemapAdapter(),
            ANTIEGGPostsMetadataAdapter(),
        ):
            for forbidden in (
                "build_asset_request",
                "download",
                "fetch_asset",
                "fetch_body",
                "retain_body",
                "rendered_html",
            ):
                with self.subTest(adapter=adapter.adapter_id, name=forbidden):
                    self.assertFalse(hasattr(adapter, forbidden))

    def test_adapters_are_bound_to_canonical_endpoints_and_governance(
        self,
    ) -> None:
        source = next(
            item
            for item in REGISTRY["sources"]
            if item["source_id"] == "antiegg-fluxus"
        )
        endpoints = {
            item["endpoint_id"]: item["public_url"]
            for item in source["endpoints"]
        }
        for adapter in (
            ANTIEGGSitemapAdapter(),
            ANTIEGGPostsMetadataAdapter(),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                self.assertEqual("antiegg-fluxus", adapter.source_id)
                self.assertEqual(("antiegg.kr",), adapter.allowed_hosts)
                self.assertEqual(
                    endpoints[adapter.endpoint_id], adapter.public_url
                )
                record = next(
                    item
                    for item in GOVERNANCE["records"]
                    if item["endpoint_id"] == adapter.endpoint_id
                )
                # Governance is recorded rather than closed. Nothing may be
                # inferred: any non-unknown fact state must carry exactly one
                # observation asserting that same state.
                for dimension, state in record["fact_states"].items():
                    matching = [
                        item
                        for item in record["observations"]
                        if item["dimension"] == dimension
                    ]
                    if state == "unknown":
                        self.assertEqual([], matching)
                    else:
                        self.assertEqual(1, len(matching))
                        self.assertEqual(state, matching[0]["state"])


class ANTIEGGIdentityTests(unittest.TestCase):
    def test_posts_identity_comes_from_numeric_id_not_display_fields(
        self,
    ) -> None:
        sitemap = InventedSitemapAdapter()
        posts = InventedPostsAdapter()
        sitemap_page = sitemap.parse_page(
            invented_sitemap_page([{"loc": FLUXUS_ARTICLE_URL}]),
            cursor=None,
        )
        posts_page = posts.parse_page(
            invented_posts_page([invented_post_item("25502")]),
            cursor=None,
            response_headers={"x-wp-total": "1", "x-wp-totalpages": "1"},
        )
        renamed = invented_post_item("25502")
        renamed["title"] = {"rendered": "An entirely different invented title"}
        moved = dict(renamed)
        moved["link"] = "https://antiegg.kr/a-new-public-slug"
        self.assertEqual(posts.stable_record_id(renamed), posts.stable_record_id(moved))
        self.assertEqual(
            posts_page["records"][0]["record_id"],
            posts.stable_record_id(moved),
        )
        self.assertNotEqual(
            sitemap_page["records"][0]["record_id"],
            posts_page["records"][0]["record_id"],
        )

    def test_display_labels_and_trailing_slashes_never_change_identity(
        self,
    ) -> None:
        sitemap = InventedSitemapAdapter()
        posts = InventedPostsAdapter()
        self.assertEqual(
            sitemap.stable_record_id({"loc": FLUXUS_ARTICLE_URL}),
            sitemap.stable_record_id({"loc": f"{FLUXUS_ARTICLE_URL}/"}),
        )
        renamed = invented_post_item("25502")
        renamed["title"] = {"rendered": "An entirely different invented title"}
        self.assertEqual(
            posts.stable_record_id(invented_post_item("25502")),
            posts.stable_record_id(renamed),
        )
        self.assertNotEqual(
            sitemap.stable_record_id({"loc": FLUXUS_ARTICLE_URL}),
            sitemap.stable_record_id({"loc": "https://antiegg.kr/25503"}),
        )
        self.assertNotEqual(
            posts.stable_record_id(invented_post_item("25502")),
            posts.stable_record_id(invented_post_item("25503")),
        )

    def test_unsafe_or_ambiguous_public_urls_fail_closed(self) -> None:
        sitemap = InventedSitemapAdapter()
        for unsafe in (
            "http://antiegg.kr/25502",
            "https://unreviewed.invalid/25502",
            "https://ANTIEGG.KR/25502",
            "https://antiegg.kr:443/25502",
            # Assembled at runtime: a literal credential-shaped URL would
            # trip the Rucksack publisher secret scan on every later edit
            # of this file (see rucksack#258). Identical string at runtime.
            "https://person:" + "secret@antiegg.kr/25502",
            "https://antiegg.kr/25502?tracking=campaign",
            "https://antiegg.kr/25502#section",
            "https://antiegg.kr/../25502",
            "https://antiegg.kr/%2e%2e/25502",
            "https://antiegg.kr//25502",
            "https://antiegg.kr/raw space",
            "https://antiegg.kr/raw\x00control",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                sitemap.stable_record_id({"loc": unsafe})
        for ambiguous in ({}, {"lastmod": "2026-01-02T03:04:05Z"}):
            with self.subTest(item=ambiguous), self.assertRaises(ValueError):
                sitemap.stable_record_id(ambiguous)
        with self.assertRaises(ValueError):
            sitemap.stable_record_id(
                {"loc": FLUXUS_ARTICLE_URL, "canonical_url": FLUXUS_ARTICLE_URL}
            )


class ANTIEGGProseBoundaryTests(unittest.TestCase):
    def test_post_prose_media_and_contact_fields_are_never_retained(
        self,
    ) -> None:
        adapter = InventedPostsAdapter()
        item = invented_post_item("25502")
        item["title"] = {"rendered": "가상의 기사 제목"}
        item["excerpt"] = {
            "protected": False,
            "rendered": "<p>Invented excerpt prose.</p>",
        }
        page = adapter.parse_page(
            invented_posts_page([item]),
            cursor=None,
            response_headers={"x-wp-total": "1", "x-wp-totalpages": "1"},
        )
        self.assertEqual(
            {"record_type"},
            set(page["records"][0]["metadata"]),
        )
        serialized = json.dumps(page, ensure_ascii=False, sort_keys=True)
        for prose in (
            "가상의 기사 제목",
            "Invented excerpt prose",
            "<p>",
            *FORBIDDEN_POST_FIELDS,
        ):
            with self.subTest(prose=prose):
                self.assertNotIn(prose, serialized)

    def test_sitemap_entries_retain_only_factual_metadata(self) -> None:
        adapter = InventedSitemapAdapter()
        page = adapter.parse_page(
            invented_sitemap_page([invented_sitemap_item("25502")]),
            cursor=None,
        )
        self.assertEqual(
            {"entry_kind": "entry_kind_public_document",
             "modified_at": "2026-01-02T03:04:05Z"},
            page["records"][0]["metadata"],
        )
        index = adapter.parse_page(
            invented_sitemap_page(
                [invented_sitemap_item("wp-sitemap-posts-post-1.xml")],
                container="sitemapindex",
            ),
            cursor=None,
        )
        self.assertEqual(
            "entry_kind_child_sitemap",
            index["records"][0]["metadata"]["entry_kind"],
        )

    def test_missing_reviewed_post_fields_fail_closed(
        self,
    ) -> None:
        adapter = InventedSitemapAdapter()
        page = adapter.parse_page(
            invented_sitemap_page([{"loc": FLUXUS_ARTICLE_URL}]),
            cursor=None,
        )
        self.assertNotIn("modified_at", page["records"][0]["metadata"])

        posts = InventedPostsAdapter()
        for missing in POSTS_RESPONSE_FIELDS:
            item = invented_post_item("25502")
            item.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                posts.parse_page(
                    invented_posts_page([item]),
                    cursor=None,
                    response_headers={
                        "x-wp-total": "1",
                        "x-wp-totalpages": "1",
                    },
                )


class ANTIEGGSitemapShapeTests(unittest.TestCase):
    def test_unreviewed_xml_constructs_fail_closed(self) -> None:
        adapter = InventedSitemapAdapter()
        canonical = invented_sitemap_page([invented_sitemap_item("25502")])
        malformed = (
            canonical.replace(
                b"<urlset",
                b"<!DOCTYPE urlset [<!ENTITY x \"y\">]><urlset",
            ),
            canonical.replace(
                b"<loc>", b"<loc><![CDATA[https://antiegg.kr/25502]]></loc><loc>"
            ),
            canonical.replace(b"25502</loc>", b"25502&amp;</loc>"),
            canonical.replace(b"<urlset", b"<?php echo 1; ?><urlset"),
            canonical.replace(XML_PROLOGUE.encode(), b"<?xml version='1.0'?>"),
            canonical.removeprefix(XML_PROLOGUE.encode()),
            canonical.replace(b"<lastmod>", b"<changefreq>daily</changefreq><lastmod>"),
            canonical.replace(b"<loc>", b'<loc rel="canonical">'),
            canonical.replace(b"<url>", b"<url><nested/></url><url>"),
            canonical.replace(b"<url>", b"Unreviewed prose<url>"),
            canonical.replace(b"pf:terminal", b"pf:unknown-control"),
            canonical.replace(b"<urlset", b"<feed"),
        )
        for body in malformed:
            with self.subTest(body=body[:96]), self.assertRaises(ValueError):
                adapter.parse_page(body, cursor=None)

    def test_modification_time_must_be_a_utc_instant(self) -> None:
        adapter = InventedSitemapAdapter()
        for lastmod in ("2026-01-02", "2026-01-02T03:04:05+09:00", "not-a-time"):
            with self.subTest(lastmod=lastmod), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_sitemap_page(
                        [invented_sitemap_item("25502", lastmod=lastmod)]
                    ),
                    cursor=None,
                )

    def test_pagination_cursor_has_one_canonical_spelling(self) -> None:
        adapter = InventedSitemapAdapter()
        self.assertEqual(
            "page-002",
            adapter.parse_page(
                invented_sitemap_page(
                    [], terminal=False, next_cursor="page-002", next_ordinal=1
                ),
                cursor=None,
            )["next_cursor"],
        )
        for cursor in ("page-2", "page-02", "page-0002", "page-001"):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_sitemap_page(
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
            ("page-004", 2, "page-002"),
        ):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_sitemap_page(
                        [],
                        terminal=False,
                        next_cursor=cursor,
                        next_ordinal=ordinal,
                    ),
                    cursor=current,
                )

        incoherent = invented_sitemap_page(
            [], terminal=True
        ).replace(b'pf:terminal="true"', b'pf:terminal="true" pf:next-cursor="page-002"')
        with self.assertRaises(ValueError):
            adapter.parse_page(incoherent, cursor=None)

    def test_bounded_pagination_resumes_through_the_shared_checkpoint(
        self,
    ) -> None:
        harness = OfflineConformanceHarness(
            InventedSitemapAdapter(), REGISTRY
        )
        request = harness.next_request()
        self.assertEqual(
            "https://antiegg.kr/sitemap_index.xml", request.url
        )
        first = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/xml",
                body=invented_sitemap_page(
                    [invented_sitemap_item("25502")],
                    terminal=False,
                    next_cursor="page-002",
                    next_ordinal=1,
                    expected_total=2,
                ),
                final_url=request.url,
            )
        )
        self.assertEqual("ready", first["state"])
        expected_bounds = copy.deepcopy(harness.bounds)
        checkpoint = harness.checkpoint()
        resumed = OfflineConformanceHarness.resume(
            InventedSitemapAdapter(),
            REGISTRY,
            checkpoint,
            expected_bounds=expected_bounds,
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
        )
        request = resumed.next_request()
        self.assertEqual(
            "https://antiegg.kr/sitemap_index.xml?page=2", request.url
        )
        final = resumed.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/xml",
                body=invented_sitemap_page(
                    [invented_sitemap_item("25503")],
                    expected_total=2,
                ),
                final_url=request.url,
            )
        )
        self.assertEqual("complete_for_observed_endpoint", final["state"])
        self.assertEqual(2, final["observed_unique_records"])
        self.assertEqual(0, final["unvisited_remainder"])


class ANTIEGGPostsShapeTests(unittest.TestCase):
    def test_reviewed_fixture_extracts_stable_prose_free_records(self) -> None:
        adapter = ANTIEGGPostsMetadataAdapter()
        page = adapter.parse_page(
            POSTS_PAGE_ONE,
            cursor=None,
            response_headers=POSTS_PAGE_ONE_HEADERS,
        )
        self.assertTrue(page["terminal"])
        self.assertEqual(2, page["expected_total"])
        self.assertEqual(
            [
                adapter.stable_record_id({"id": 25502}),
                adapter.stable_record_id({"id": 25503}),
            ],
            [record["record_id"] for record in page["records"]],
        )
        self.assertEqual(
            [{"record_type": "record_type_post"}] * 2,
            [record["metadata"] for record in page["records"]],
        )
        serialized = json.dumps(page, ensure_ascii=False)
        self.assertNotIn("Sanitized fixture title", serialized)
        self.assertNotIn("fixture excerpt", serialized)

        harness = OfflineConformanceHarness(adapter, REGISTRY)
        request = harness.next_request()
        manifest = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=POSTS_PAGE_ONE,
                final_url=request.url,
                headers=POSTS_PAGE_ONE_HEADERS,
            )
        )
        self.assertEqual("complete_for_observed_endpoint", manifest["state"])
        self.assertEqual(2, manifest["observed_unique_records"])
        self.assertEqual(0, manifest["unvisited_remainder"])

    def test_identifier_and_canonical_url_are_independently_required(self) -> None:
        adapter = ANTIEGGPostsMetadataAdapter()
        for change in (
            {"id": "25502"},
            {"id": 0},
            {"id": True},
            {"link": "https://unreviewed.invalid/25502"},
            {"link": None},
        ):
            item = invented_post_item("25502")
            item.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_posts_page([item]),
                    cursor=None,
                    response_headers={
                        "x-wp-total": "1",
                        "x-wp-totalpages": "1",
                    },
                )

    def test_mutated_reviewed_field_shape_fails_closed(self) -> None:
        adapter = ANTIEGGPostsMetadataAdapter()
        for change in (
            {"date": "2026-01-02T03:04:05Z"},
            {"modified": "2026-01-02"},
            {"categories": [4, True]},
            {"tags": ["9"]},
            {"title": "not-an-object"},
            {"excerpt": {"rendered": "missing protected"}},
            {"content": {"rendered": "not requested"}},
        ):
            item = invented_post_item("25502")
            item.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.parse_page(
                    invented_posts_page([item]),
                    cursor=None,
                    response_headers={
                        "x-wp-total": "1",
                        "x-wp-totalpages": "1",
                    },
                )

    def test_request_and_header_pagination_stay_within_reviewed_bounds(self) -> None:
        adapter = ANTIEGGPostsMetadataAdapter()
        self.assertLessEqual(POSTS_PER_PAGE, POSTS_REVIEWED_MAX_PER_PAGE)
        first = adapter.build_request(None)
        self.assertIn("page=1", first.url)
        self.assertIn(f"per_page={POSTS_PER_PAGE}", first.url)
        self.assertNotIn("content", first.url)
        self.assertIn("_fields=", first.url)

        continuing = adapter.parse_page(
            POSTS_PAGE_ONE,
            cursor=None,
            response_headers={
                "x-wp-total": str(POSTS_PER_PAGE + 1),
                "x-wp-totalpages": "2",
            },
        )
        self.assertFalse(continuing["terminal"])
        self.assertEqual("page-002", continuing["next_cursor"])
        self.assertIn("page=2", adapter.build_request("page-002").url)

        beyond = adapter.parse_page(
            POSTS_BEYOND_END,
            cursor="page-002",
            response_headers=POSTS_BEYOND_END_HEADERS,
        )
        self.assertTrue(beyond["terminal"])
        self.assertEqual([], beyond["records"])

        for headers in (
            None,
            {},
            {"x-wp-total": "2"},
            {"x-wp-total": "02", "x-wp-totalpages": "1"},
            {"x-wp-total": "2", "x-wp-totalpages": "0"},
        ):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                adapter.parse_page(
                    POSTS_PAGE_ONE,
                    cursor=None,
                    response_headers=headers,
                )

    def test_declared_total_is_only_a_timestamped_endpoint_observation(
        self,
    ) -> None:
        adapter = ANTIEGGPostsMetadataAdapter()
        body = invented_posts_page([invented_post_item("25502")])
        headers = {
            "x-wp-total": "1463",
            "x-wp-totalpages": str(-(-1463 // POSTS_PER_PAGE)),
        }
        observation = adapter.declared_total_observation(
            body,
            observed_at="2026-07-24T00:00:00Z",
            response_headers=headers,
        )
        self.assertEqual(
            {
                "observation_kind": "endpoint_declared_total",
                "source_id": "antiegg-fluxus",
                "endpoint_id": "antiegg-posts-api",
                "declared_total": 1463,
                "observed_records": 1,
                "observed_at": "2026-07-24T00:00:00Z",
                "is_completeness_guarantee": False,
            },
            observation,
        )
        for invalid in ("2026-07-24", "2026-07-24T00:00:00+09:00", ""):
            with self.subTest(observed_at=invalid), self.assertRaises(
                ValueError
            ):
                adapter.declared_total_observation(
                    body,
                    observed_at=invalid,
                    response_headers=headers,
                )

    def test_registry_uses_advertised_sitemap_and_fixture_records_redirect(
        self,
    ) -> None:
        redirect = json.loads(
            (FIXTURE_ROOT / "sitemap-redirect.json").read_text(
                encoding="utf-8"
            )
        )
        source = next(
            item
            for item in REGISTRY["sources"]
            if item["source_id"] == "antiegg-fluxus"
        )
        sitemap = next(
            item
            for item in source["endpoints"]
            if item["endpoint_id"] == "antiegg-sitemap"
        )
        self.assertEqual(301, redirect["status"])
        self.assertEqual(redirect["location"], sitemap["public_url"])


class ANTIEGGEndpointDecisionIsolationTests(unittest.TestCase):
    def test_one_endpoint_blocker_never_blocks_another_endpoint_or_source(
        self,
    ) -> None:
        blocked = invented_governance(
            "antiegg-fluxus",
            "antiegg-sitemap",
            governance_id="source_governance_invented_antiegg_sitemap",
            blockers=(
                {
                    "code": "robots_denied",
                    "endpoint_id": "antiegg-sitemap",
                    "observed_at": "2026-07-23T00:00:00Z",
                    "next_safe_action": "Recheck the endpoint robots rule.",
                },
            ),
        )
        posts = invented_governance(
            "antiegg-fluxus",
            "antiegg-posts-api",
            governance_id="source_governance_invented_antiegg_posts",
        )
        other_source = invented_governance(
            "njp-video-library",
            "njp-video-library-home",
            governance_id="source_governance_invented_njp_library",
        )

        blocked_result = evaluate_source_operation(
            blocked, "metadata_inventory", now=NOW
        )
        self.assertFalse(blocked_result["eligible"])
        self.assertIn("durable_blocker", blocked_result["reasons"])
        self.assertEqual("antiegg-sitemap", blocked_result["endpoint_id"])

        for unaffected in (posts, other_source):
            result = evaluate_source_operation(
                unaffected, "metadata_inventory", now=NOW
            )
            with self.subTest(endpoint=result["endpoint_id"]):
                self.assertTrue(result["eligible"], result["reasons"])
                self.assertEqual([], result["blockers"])

    def test_a_missing_or_stale_decision_blocks_only_its_own_endpoint(
        self,
    ) -> None:
        stale = invented_governance(
            "antiegg-fluxus",
            "antiegg-sitemap",
            governance_id="source_governance_invented_antiegg_sitemap",
            decision_expires_at="2026-07-23T12:00:00Z",
        )
        missing = invented_governance(
            "antiegg-fluxus",
            "antiegg-media-api",
            governance_id="source_governance_invented_antiegg_media",
        )
        missing["decisions"] = []
        posts = invented_governance(
            "antiegg-fluxus",
            "antiegg-posts-api",
            governance_id="source_governance_invented_antiegg_posts",
        )

        self.assertIn(
            "operation:decision_expired",
            evaluate_source_operation(stale, "metadata_inventory", now=NOW)[
                "reasons"
            ],
        )
        self.assertIn(
            "operation:decision_missing_or_conflicting",
            evaluate_source_operation(missing, "metadata_inventory", now=NOW)[
                "reasons"
            ],
        )
        self.assertTrue(
            evaluate_source_operation(posts, "metadata_inventory", now=NOW)[
                "eligible"
            ]
        )

    def test_prose_retention_stays_pending_for_every_antiegg_endpoint(
        self,
    ) -> None:
        for record in GOVERNANCE["records"]:
            if record["source_id"] != "antiegg-fluxus":
                continue
            with self.subTest(endpoint=record["endpoint_id"]):
                self.assertEqual(
                    "pending", record["operation_states"]["prose_retention"]
                )
                self.assertEqual(
                    "pending",
                    record["operation_states"]["media_acquisition"],
                )


if __name__ == "__main__":
    unittest.main()
