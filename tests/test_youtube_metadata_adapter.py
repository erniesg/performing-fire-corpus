from __future__ import annotations

import json
import unittest
from pathlib import Path

from adapter_conformance_suite import StandardAdapterConformanceMixin
from performing_fire_corpus.adapter_conformance import (
    AdapterConformanceError,
    MetadataResponse,
    OfflineConformanceHarness,
    deny_live_network,
    validate_adapter_declaration,
)
from performing_fire_corpus.governance import load_source_governance_registry
from performing_fire_corpus.registry import load_registry
from performing_fire_corpus.youtube_metadata_adapter import (
    ChannelResolution,
    YouTubeAssetCandidate,
    YouTubeChannelResolverAdapter,
    YouTubeMetadataCoordinator,
    YouTubeQuotaLedger,
    YouTubeUploadsAdapter,
    YouTubeVideosAdapter,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")


class InventedMediaDownloader:
    @staticmethod
    def download() -> None:
        raise AssertionError("invented downloader should be network-denied")


def upload_item(
    item_id: str,
    *,
    video_id: str | None = None,
    published_at: str = "2026-01-02T03:04:05Z",
    title: str = "Invented mutable title",
) -> dict[str, object]:
    details: dict[str, object] = {}
    if video_id is not None:
        details["videoId"] = video_id
        details["videoPublishedAt"] = published_at
    return {
        "id": item_id,
        "contentDetails": details,
        "inventedTitle": title,
    }


def upload_page(
    items: list[dict[str, object]],
    *,
    next_cursor: str | None = None,
    next_ordinal: int | None = None,
    terminal: bool = True,
    expected_total: int | None = None,
    rejected_count: int = 0,
    access_state: str | None = None,
) -> bytes:
    value: dict[str, object] = {
        "kind": "youtube#playlistItemListResponse",
        "items": items,
        "nextPageToken": (
            next_cursor.removeprefix("opaque-").split("~", 1)[-1]
            if next_cursor is not None
            else None
        ),
        "nextOrdinal": next_ordinal,
        "terminal": terminal,
        "rejectedCount": rejected_count,
    }
    if expected_total is not None:
        value["pageInfo"] = {"totalResults": expected_total}
    if access_state is not None:
        value["accessState"] = access_state
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def identity_variants(item: dict[str, object]) -> list[dict[str, object]]:
    changed = dict(item)
    changed["inventedTitle"] = "Changed invented title"
    return [item, changed]


def uploads_adapter() -> YouTubeUploadsAdapter:
    return YouTubeUploadsAdapter(
        ChannelResolution(
            handle="@NamJunePaikArtCenter",
            channel_id="UCinventedChannel001",
            uploads_playlist_id="UUinventedUploads001",
        )
    )


class UploadsAdapterConformance(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = staticmethod(uploads_adapter)
    registry = REGISTRY
    make_item = staticmethod(
        lambda item_id, **kwargs: upload_item(
            item_id,
            video_id=f"video{item_id}",
            **kwargs,
        )
    )
    make_page = staticmethod(upload_page)
    identity_variants = staticmethod(identity_variants)
    next_cursor = "opaque-1~PageToken002"
    alternate_cursor = "opaque-2~PageToken003"
    server_supplies_ordinal = False
    additional_network_entry_points = (
        (InventedMediaDownloader, "download"),
    )


class YouTubeMetadataAdapterTests(unittest.TestCase):
    def test_registry_binds_only_official_api_endpoints(self) -> None:
        for adapter in (
            YouTubeChannelResolverAdapter(),
            uploads_adapter(),
            YouTubeVideosAdapter(("video001",)),
        ):
            declaration = validate_adapter_declaration(adapter, REGISTRY)
            self.assertEqual(
                ["www.googleapis.com"],
                declaration["allowed_hosts"],
            )
            self.assertNotIn("youtube.com/@", declaration["canonical_endpoint_url"])

    def test_every_youtube_endpoint_has_a_closed_governance_record(self) -> None:
        governance = load_source_governance_registry(
            ROOT / "config" / "source-governance.v1.json",
            source_registry=REGISTRY,
        )
        records = {
            item["endpoint_id"]: item
            for item in governance["records"]
            if item["source_id"] == "njp-youtube-official"
            and item["endpoint_id"] is not None
        }
        self.assertEqual(
            {
                "njp-youtube-channels-api",
                "njp-youtube-handle",
                "njp-youtube-playlist-items-api",
                "njp-youtube-videos-api",
            },
            set(records),
        )
        for record in records.values():
            self.assertEqual({"unknown"}, set(record["fact_states"].values()))
            self.assertEqual(
                {"pending"},
                set(record["operation_states"].values()),
            )

    def test_handle_resolution_requires_one_exact_channel(self) -> None:
        adapter = YouTubeChannelResolverAdapter()
        body = json.dumps(
            {
                "kind": "youtube#channelListResponse",
                "items": [
                    {
                        "id": "UCinventedChannel001",
                        "contentDetails": {
                            "relatedPlaylists": {
                                "uploads": "UUinventedUploads001"
                            }
                        },
                    }
                ],
            }
        ).encode()
        resolution = adapter.resolve_channel(body)
        self.assertEqual("@NamJunePaikArtCenter", resolution.handle)
        self.assertEqual("UCinventedChannel001", resolution.channel_id)
        for items in ([], json.loads(body)["items"] * 2):
            ambiguous = json.dumps(
                {
                    "kind": "youtube#channelListResponse",
                    "items": items,
                }
            ).encode()
            with self.subTest(size=len(items)), self.assertRaises(ValueError):
                adapter.resolve_channel(ambiguous)

    def test_opaque_page_token_is_checkpoint_bound_and_not_manifested(self) -> None:
        adapter = uploads_adapter()
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        request = harness.next_request()
        first = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=upload_page(
                    [upload_item("item001", video_id="video001")],
                    next_cursor="opaque-1~PageToken002",
                    next_ordinal=1,
                    terminal=False,
                    expected_total=2,
                ),
                final_url=request.url,
            )
        )
        self.assertNotIn("PageToken002", json.dumps(first))
        next_request = harness.next_request()
        self.assertIn("pageToken=PageToken002", next_request.url)
        self.assertNotIn("key=", next_request.url)

    def test_quota_ledger_is_bounded_resume_safe_and_one_unit_per_list(self) -> None:
        ledger = YouTubeQuotaLedger(max_units=3)
        ledger.reserve("channels.list")
        ledger.reserve("playlistItems.list")
        checkpoint = ledger.checkpoint()
        resumed = YouTubeQuotaLedger.resume(
            checkpoint,
            expected_max_units=3,
            expected_sha256=checkpoint["checkpoint_sha256"],
        )
        resumed.reserve("videos.list")
        self.assertEqual(3, resumed.consumed_units)
        with self.assertRaises(ValueError):
            resumed.reserve("videos.list")
        tampered = dict(checkpoint)
        tampered["consumed_units"] = 0
        with self.assertRaises(ValueError):
            YouTubeQuotaLedger.resume(
                tampered,
                expected_max_units=3,
                expected_sha256=checkpoint["checkpoint_sha256"],
            )

    def test_video_statuses_are_sanitized_and_missing_ids_are_unavailable(self) -> None:
        adapter = YouTubeVideosAdapter(
            ("age001", "missing001", "public001", "region001")
        )
        body = json.dumps(
            {
                "kind": "youtube#videoListResponse",
                "items": [
                    {
                        "id": "public001",
                        "contentDetails": {"duration": "PT2M3S"},
                        "status": {"privacyStatus": "public"},
                    },
                    {
                        "id": "region001",
                        "contentDetails": {
                            "duration": "PT1H",
                            "regionRestriction": {"blocked": ["KR"]},
                        },
                        "status": {"privacyStatus": "public"},
                    },
                    {
                        "id": "age001",
                        "contentDetails": {
                            "contentRating": {"inventedRating": "restricted"},
                            "duration": "PT30S",
                        },
                        "status": {"privacyStatus": "public"},
                    },
                ],
            }
        ).encode()
        page = adapter.parse_page(body, cursor=None)
        by_id = {
            record["record_id"]: record["metadata"]
            for record in page["records"]
        }
        self.assertEqual("availability_public", by_id["youtube-video-public001"]["availability"])
        self.assertEqual("availability_region_blocked", by_id["youtube-video-region001"]["availability"])
        self.assertEqual("availability_age_gated", by_id["youtube-video-age001"]["availability"])
        self.assertEqual("availability_unavailable", by_id["youtube-video-missing001"]["availability"])

    def test_assets_default_closed_and_no_caption_or_media_request_exists(self) -> None:
        coordinator = YouTubeMetadataCoordinator()
        candidates = coordinator.asset_candidates("video001")
        self.assertEqual(
            {"audio", "caption", "thumbnail", "video"},
            {candidate.asset_kind for candidate in candidates},
        )
        self.assertTrue(
            all(
                isinstance(candidate, YouTubeAssetCandidate)
                and candidate.rights_state == "pending"
                and not candidate.acquisition_eligible
                for candidate in candidates
            )
        )
        for forbidden in (
            "build_caption_request",
            "build_media_request",
            "download",
        ):
            self.assertFalse(hasattr(coordinator, forbidden))

    def test_portable_adapters_do_not_read_environment_or_network(self) -> None:
        with deny_live_network(
            additional_entry_points=(
                (InventedMediaDownloader, "download"),
            )
        ):
            request = YouTubeChannelResolverAdapter().build_request(None)
            with self.assertRaises(AdapterConformanceError):
                InventedMediaDownloader.download()
        self.assertNotIn("key=", request.url)
        self.assertNotIn("token", request.url.lower())
        with self.assertRaises(AdapterConformanceError):
            unsafe = uploads_adapter()
            unsafe.allowed_query_parameters = (
                "key",
                "maxResults",
                "pageToken",
                "part",
                "playlistId",
            )
            validate_adapter_declaration(unsafe, REGISTRY)
