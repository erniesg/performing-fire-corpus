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
    UploadsInventory,
    YouTubeAssetCandidate,
    YouTubeChannelResolverAdapter,
    YouTubeMetadataCoordinator,
    YouTubeQuotaLedger,
    YouTubeUploadsAdapter,
    YouTubeVideosAdapter,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")
RUN_ID = "youtube_metadata_run_invented_001"


class InventedMediaDownloader:
    @staticmethod
    def download() -> None:
        raise AssertionError("invented downloader should be network-denied")


def youtube_coordinator(
    *,
    max_quota_units: int = 100,
) -> YouTubeMetadataCoordinator:
    return YouTubeMetadataCoordinator(
        max_quota_units=max_quota_units,
        run_id=RUN_ID,
    )


def channel_item(
    item_id: str,
    *,
    channel_id: str | None = None,
    uploads_playlist_id: str | None = None,
    title: str = "Invented mutable channel label",
) -> dict[str, object]:
    return {
        "id": channel_id or f"UCinvented{item_id}",
        "contentDetails": {
            "relatedPlaylists": {
                "uploads": uploads_playlist_id or f"UUinvented{item_id}"
            }
        },
        "inventedTitle": title,
    }


def channel_page(
    items: list[dict[str, object]],
    *,
    access_state: str | None = None,
    **_: object,
) -> bytes:
    value: dict[str, object] = {
        "kind": "youtube#channelListResponse",
        "items": items,
    }
    if access_state is not None:
        value["accessState"] = access_state
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def channel_identity_variants(
    item: dict[str, object],
) -> list[dict[str, object]]:
    changed = dict(item)
    changed["inventedTitle"] = "Changed invented channel label"
    return [item, changed]


def channel_resolution(
    coordinator: YouTubeMetadataCoordinator,
) -> ChannelResolution:
    return coordinator.channel_adapter().resolve_channel(
        channel_page(
            [
                channel_item(
                    "Channel001",
                    channel_id="UCinventedChannel001",
                    uploads_playlist_id="UUinventedUploads001",
                )
            ]
        )
    )


def uploads_manifest(
    adapter: YouTubeUploadsAdapter,
    video_ids: tuple[str, ...],
) -> dict[str, object]:
    records = [
        {
            "record_id": f"youtube-video-{video_id}",
            "source_identity_sha256": "0" * 64,
            "metadata": {"resource_type": "resource_type_video"},
        }
        for video_id in video_ids
    ]
    return {
        "schema_version": 1,
        "manifest_type": "offline_adapter_conformance",
        "adapter_id": "youtube-uploads-playlist",
        "adapter_version": "1.0.0",
        "adapter_lineage_sha256": adapter.adapter_lineage_sha256(),
        "source_id": "njp-youtube-official",
        "endpoint_id": "njp-youtube-playlist-items-api",
        "state": "complete_for_observed_endpoint",
        "stop_reason": "terminal_page",
        "requests_attempted": 1,
        "pages_committed": 1,
        "duplicate_records": 0,
        "rejected_records": 0,
        "observed_unique_records": len(records),
        "expected_total": len(records),
        "unvisited_remainder": 0,
        "next_cursor_sha256": None,
        "records": records,
    }


def uploads_inventory(
    coordinator: YouTubeMetadataCoordinator,
    video_ids: tuple[str, ...],
) -> UploadsInventory:
    resolution = channel_resolution(coordinator)
    adapter = coordinator.uploads_adapter(resolution)
    return coordinator.finalize_uploads(
        resolution,
        uploads_manifest(adapter, video_ids),
    )


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
    coordinator = youtube_coordinator()
    return coordinator.uploads_adapter(channel_resolution(coordinator))


def video_item(
    item_id: str,
    *,
    video_id: str | None = None,
    title: str = "Invented mutable video label",
) -> dict[str, object]:
    return {
        "id": video_id or f"video{item_id}",
        "contentDetails": {"duration": "PT1M"},
        "status": {"privacyStatus": "public"},
        "inventedTitle": title,
    }


def video_page(
    items: list[dict[str, object]],
    *,
    access_state: str | None = None,
    **_: object,
) -> bytes:
    value: dict[str, object] = {
        "kind": "youtube#videoListResponse",
        "items": items,
    }
    if access_state is not None:
        value["accessState"] = access_state
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def video_identity_variants(
    item: dict[str, object],
) -> list[dict[str, object]]:
    changed = dict(item)
    changed["inventedTitle"] = "Changed invented video label"
    return [item, changed]


def videos_adapter() -> YouTubeVideosAdapter:
    coordinator = youtube_coordinator()
    inventory = uploads_inventory(
        coordinator,
        ("video001", "video002"),
    )
    return coordinator.videos_adapter(
        inventory,
        ("video001", "video002"),
    )


class ChannelResolverAdapterConformance(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = staticmethod(
        lambda: youtube_coordinator().channel_adapter()
    )
    registry = REGISTRY
    make_item = staticmethod(channel_item)
    make_page = staticmethod(channel_page)
    identity_variants = staticmethod(channel_identity_variants)
    supports_pagination = False
    source_can_return_multiple = False
    source_can_duplicate_items = False
    additional_network_entry_points = (
        (InventedMediaDownloader, "download"),
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


class VideosAdapterConformance(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = staticmethod(videos_adapter)
    registry = REGISTRY
    make_item = staticmethod(video_item)
    make_page = staticmethod(video_page)
    identity_variants = staticmethod(video_identity_variants)
    supports_pagination = False
    source_can_duplicate_items = False
    additional_network_entry_points = (
        (InventedMediaDownloader, "download"),
    )


class YouTubeMetadataAdapterTests(unittest.TestCase):
    def test_registry_binds_only_official_api_endpoints(self) -> None:
        coordinator = youtube_coordinator()
        inventory = uploads_inventory(coordinator, ("video001",))
        for adapter in (
            coordinator.channel_adapter(),
            uploads_adapter(),
            coordinator.videos_adapter(inventory, ("video001",)),
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
        adapter = youtube_coordinator().channel_adapter()
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

    def test_impossible_publish_timestamp_shape_drifts(self) -> None:
        harness = OfflineConformanceHarness(uploads_adapter(), REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=upload_page(
                    [
                        upload_item(
                            "item001",
                            video_id="video001",
                            published_at="2026-02-31T03:04:05Z",
                        )
                    ]
                ),
                final_url=request.url,
            )
        )
        self.assertEqual("shape_drift", result["stop_reason"])

    def test_quota_ledger_is_bounded_resume_safe_and_one_unit_per_list(self) -> None:
        ledger = YouTubeQuotaLedger(max_units=3, run_id=RUN_ID)
        ledger.reserve("channels.list")
        ledger.reserve("playlistItems.list")
        checkpoint = ledger.checkpoint()
        resumed = YouTubeQuotaLedger.resume(
            checkpoint,
            expected_max_units=3,
            expected_run_id=RUN_ID,
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
                expected_run_id=RUN_ID,
                expected_sha256=checkpoint["checkpoint_sha256"],
            )

    def test_every_request_reserves_quota_and_checkpoint_restores_it(self) -> None:
        coordinator = youtube_coordinator(max_quota_units=3)
        channel = coordinator.channel_adapter()
        channel.build_request(None)
        resolution = channel_resolution(coordinator)
        uploads = coordinator.uploads_adapter(resolution)
        harness = OfflineConformanceHarness(uploads, REGISTRY)
        harness.next_request()
        harness.record_retry("temporary_unavailable")
        checkpoint = harness.checkpoint()
        self.assertEqual(
            2,
            checkpoint["adapter_runtime_checkpoint"]["quota"][
                "consumed_units"
            ],
        )

        resumed_coordinator = youtube_coordinator(max_quota_units=3)
        resumed = OfflineConformanceHarness.resume(
            resumed_coordinator.uploads_adapter(
                channel_resolution(resumed_coordinator)
            ),
            REGISTRY,
            checkpoint,
            expected_bounds=harness.bounds,
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
        )
        resumed.next_request()
        self.assertEqual(3, resumed_coordinator.quota.consumed_units)
        with self.assertRaises(ValueError):
            resumed_coordinator.videos_adapter(
                uploads_inventory(resumed_coordinator, ("video001",)),
                ("video001",),
            ).build_request(None)

    def test_quota_error_is_a_durable_body_free_blocker(self) -> None:
        harness = OfflineConformanceHarness(uploads_adapter(), REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            MetadataResponse(
                status=403,
                mime_type="application/json",
                body=b'{"invented":"not retained"}',
                final_url=request.url,
                error_reason=youtube_coordinator().classify_error(
                    status=403,
                    reason="quotaExceeded",
                ),
            )
        )
        self.assertEqual("quota_exhausted", result["stop_reason"])
        self.assertNotIn("invented", json.dumps(result))

    def test_video_statuses_are_sanitized_and_missing_ids_are_unavailable(self) -> None:
        coordinator = youtube_coordinator()
        video_ids = ("age001", "missing001", "public001", "region001")
        adapter = coordinator.videos_adapter(
            uploads_inventory(coordinator, video_ids),
            video_ids,
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
                            "contentRating": {
                                "ytRating": "ytAgeRestricted"
                            },
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

    def test_live_lifecycle_is_explicit_and_foreign_ids_cannot_be_enriched(
        self,
    ) -> None:
        coordinator = youtube_coordinator()
        resolution = channel_resolution(coordinator)
        uploads = coordinator.uploads_adapter(resolution)
        manifest = uploads_manifest(uploads, ("live001",))
        tampered = dict(manifest)
        tampered["adapter_lineage_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            coordinator.finalize_uploads(resolution, tampered)
        inventory = coordinator.finalize_uploads(resolution, manifest)
        with self.assertRaises(ValueError):
            coordinator.videos_adapter(inventory, ("foreign001",))
        adapter = coordinator.videos_adapter(inventory, ("live001",))
        page = adapter.parse_page(
            video_page(
                [
                    {
                        "id": "live001",
                        "contentDetails": {"duration": "PT1H"},
                        "liveStreamingDetails": {
                            "actualStartTime": "2026-07-24T01:02:03Z"
                        },
                        "status": {"privacyStatus": "public"},
                    }
                ]
            ),
            cursor=None,
        )
        self.assertEqual(
            "broadcast_state_live",
            page["records"][0]["metadata"]["broadcast_state"],
        )

    def test_assets_default_closed_and_no_caption_or_media_request_exists(self) -> None:
        coordinator = youtube_coordinator()
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
            request = youtube_coordinator().channel_adapter().build_request(
                None
            )
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
