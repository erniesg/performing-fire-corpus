from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
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
    YouTubeQuotaStore,
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
    quota_store: YouTubeQuotaStore | None = None,
) -> YouTubeMetadataCoordinator:
    return YouTubeMetadataCoordinator(
        max_quota_units=max_quota_units,
        run_id=RUN_ID,
        quota_store=quota_store
        or YouTubeQuotaStore(sqlite3.connect(":memory:")),
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
    request = coordinator.begin_channel(REGISTRY)
    if request is None:
        raise AssertionError("invented channel request was unexpectedly blocked")
    result = coordinator.ingest_channel(
        MetadataResponse(
            status=200,
            mime_type="application/json",
            body=channel_page(
                [
                    channel_item(
                        "Channel001",
                        channel_id="UCinventedChannel001",
                        uploads_playlist_id="UUinventedUploads001",
                    )
                ]
            ),
            final_url=request.url,
        )
    )
    if result["state"] != "complete_for_observed_endpoint":
        raise AssertionError("invented channel harness did not complete")
    return coordinator.finalize_channel()


def complete_uploads(
    coordinator: YouTubeMetadataCoordinator,
    resolution: ChannelResolution,
    video_ids: tuple[str, ...],
    *,
    published_at: str = "2026-01-02T03:04:05Z",
) -> UploadsInventory:
    request = coordinator.begin_uploads(resolution, REGISTRY)
    if request is None:
        raise AssertionError("invented uploads request was unexpectedly blocked")
    result = coordinator.ingest_uploads(
        MetadataResponse(
            status=200,
            mime_type="application/json",
            body=upload_page(
                [
                    upload_item(
                        f"item{index:03d}",
                        video_id=video_id,
                        published_at=published_at,
                    )
                    for index, video_id in enumerate(video_ids, start=1)
                ],
                expected_total=len(video_ids),
            ),
            final_url=request.url,
        )
    )
    if result["state"] != "complete_for_observed_endpoint":
        raise AssertionError("invented uploads harness did not complete")
    return coordinator.finalize_uploads()


def uploads_inventory(
    coordinator: YouTubeMetadataCoordinator,
    video_ids: tuple[str, ...],
) -> UploadsInventory:
    resolution = channel_resolution(coordinator)
    return complete_uploads(
        coordinator,
        resolution,
        video_ids,
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
    return coordinator._new_uploads_adapter(
        channel_resolution(coordinator)
    )


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
        lambda: youtube_coordinator()._new_channel_adapter()
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

    def _resume_adapter(
        self,
        adapter: YouTubeChannelResolverAdapter,
    ) -> YouTubeChannelResolverAdapter:
        return YouTubeChannelResolverAdapter(adapter._session)


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

    def _resume_adapter(
        self,
        adapter: YouTubeUploadsAdapter,
    ) -> YouTubeUploadsAdapter:
        return YouTubeUploadsAdapter(
            adapter._session,
            adapter.resolution,
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

    def _resume_adapter(
        self,
        adapter: YouTubeVideosAdapter,
    ) -> YouTubeVideosAdapter:
        return YouTubeVideosAdapter(
            adapter._session,
            adapter.inventory,
            adapter.video_ids,
        )


class YouTubeMetadataAdapterTests(unittest.TestCase):
    def test_registry_binds_only_official_api_endpoints(self) -> None:
        coordinator = youtube_coordinator()
        inventory = uploads_inventory(coordinator, ("video001",))
        for adapter in (
            coordinator._new_channel_adapter(),
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
        coordinator = youtube_coordinator()
        adapter = coordinator._new_channel_adapter()
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
        forged_resolution = adapter._resolve_channel(body)
        with self.assertRaises(ValueError):
            coordinator.begin_uploads(
                forged_resolution,
                REGISTRY,
            )
        request = coordinator.begin_channel(REGISTRY)
        self.assertIsNotNone(request)
        coordinator.ingest_channel(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=body,
                final_url=request.url,
            )
        )
        resolution = coordinator.finalize_channel()
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
                coordinator._new_channel_adapter()._resolve_channel(
                    ambiguous
                )

    def test_channel_retry_checkpoint_resumes_through_coordinator(
        self,
    ) -> None:
        quota_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        coordinator = youtube_coordinator(quota_store=quota_store)
        first_request = coordinator.begin_channel(REGISTRY)
        self.assertIsNotNone(first_request)
        coordinator.record_channel_retry("temporary_unavailable")
        checkpoint = coordinator.channel_checkpoint()
        resumed = youtube_coordinator(quota_store=quota_store)
        resumed.resume_channel(
            REGISTRY,
            checkpoint,
            expected_bounds=checkpoint["bounds"],
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
        )
        retried_request = resumed.next_channel_request()
        self.assertEqual(first_request, retried_request)
        resumed.ingest_channel(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=channel_page([channel_item("Channel001")]),
                final_url=retried_request.url,
            )
        )
        self.assertEqual(
            "UCinventedChannel001",
            resumed.finalize_channel().channel_id,
        )
        self.assertEqual(2, resumed.quota.consumed_units)

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

    def test_six_character_page_token_remains_resumable(self) -> None:
        harness = OfflineConformanceHarness(uploads_adapter(), REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=upload_page(
                    [upload_item("item001", video_id="video001")],
                    next_cursor="opaque-1~CAUQAA",
                    next_ordinal=1,
                    terminal=False,
                    expected_total=2,
                ),
                final_url=request.url,
            )
        )
        self.assertEqual("ready", result["state"])
        self.assertIn(
            "pageToken=CAUQAA",
            harness.next_request().url,
        )

    def test_opaque_ids_are_shape_checked_without_word_scanning(self) -> None:
        coordinator = youtube_coordinator()
        request = coordinator.begin_channel(REGISTRY)
        self.assertIsNotNone(request)
        coordinator.ingest_channel(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=channel_page(
                    [
                        channel_item(
                            "Channel001",
                            channel_id="UCinventedChannel001",
                            uploads_playlist_id="UUrawIdentifier001",
                        )
                    ]
                ),
                final_url=request.url,
            )
        )
        resolution = coordinator.finalize_channel()
        uploads = coordinator._new_uploads_adapter(resolution)
        validate_adapter_declaration(uploads, REGISTRY)
        request = OfflineConformanceHarness(uploads, REGISTRY).next_request()
        self.assertIn("playlistId=UUrawIdentifier001", request.url)

        video_coordinator = youtube_coordinator()
        video_inventory = uploads_inventory(
            video_coordinator,
            ("rawIdentifier001",),
        )
        videos = video_coordinator.videos_adapter(
            video_inventory,
            ("rawIdentifier001",),
        )
        validate_adapter_declaration(videos, REGISTRY)
        self.assertIn(
            "id=rawIdentifier001",
            OfflineConformanceHarness(videos, REGISTRY).next_request().url,
        )
        self.assertEqual(
            ("rawIdentifier001",),
            video_inventory.video_ids,
        )

        account_like_coordinator = youtube_coordinator()
        account_like_inventory = uploads_inventory(
            account_like_coordinator,
            ("user_abcdef",),
        )
        account_like_videos = account_like_coordinator.videos_adapter(
            account_like_inventory,
            ("user_abcdef",),
        )
        request = OfflineConformanceHarness(
            account_like_videos,
            REGISTRY,
        ).next_request()
        self.assertIn("id=user_abcdef", request.url)

    def test_base64url_prefix_video_ids_complete_inventory(self) -> None:
        coordinator = youtube_coordinator()
        inventory = complete_uploads(
            coordinator,
            channel_resolution(coordinator),
            ("-abcDEF123", "_abcDEF123"),
        )
        self.assertEqual(
            ("-abcDEF123", "_abcDEF123"),
            inventory.video_ids,
        )

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

    def test_fractional_rfc3339_timestamps_are_preserved(self) -> None:
        coordinator = youtube_coordinator()
        inventory = complete_uploads(
            coordinator,
            channel_resolution(coordinator),
            ("live001",),
            published_at="2026-07-24T01:02:03.123456789Z",
        )
        adapter = coordinator.videos_adapter(
            inventory,
            ("live001",),
        )
        page = adapter.parse_page(
            video_page(
                [
                    {
                        "id": "live001",
                        "contentDetails": {"duration": "PT1M"},
                        "liveStreamingDetails": {
                            "actualStartTime": (
                                "2026-07-24T01:02:03.000Z"
                            )
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

    def test_quota_ledger_is_bounded_resume_safe_and_one_unit_per_list(self) -> None:
        quota_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        ledger = YouTubeQuotaLedger(
            max_units=3,
            run_id=RUN_ID,
            store=quota_store,
        )
        ledger.reserve("channels.list")
        ledger.reserve("playlistItems.list")
        checkpoint = ledger.checkpoint()
        resumed = YouTubeQuotaLedger.resume(
            checkpoint,
            expected_max_units=3,
            expected_run_id=RUN_ID,
            expected_sha256=checkpoint["checkpoint_sha256"],
            store=quota_store,
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
                store=quota_store,
            )

    def test_quota_checkpoint_cannot_clone_into_fresh_authority(self) -> None:
        source_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        ledger = YouTubeQuotaLedger(
            max_units=3,
            run_id=RUN_ID,
            store=source_store,
        )
        ledger.reserve("channels.list")
        checkpoint = ledger.checkpoint()
        fresh_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        with self.assertRaisesRegex(ValueError, "authority changed"):
            YouTubeQuotaLedger.resume(
                checkpoint,
                expected_max_units=3,
                expected_run_id=RUN_ID,
                expected_sha256=checkpoint["checkpoint_sha256"],
                store=fresh_store,
            )

    def test_quota_checkpoint_reads_one_atomic_snapshot(self) -> None:
        quota_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        ledger = YouTubeQuotaLedger(
            max_units=3,
            run_id=RUN_ID,
            store=quota_store,
        )
        ledger.reserve("channels.list")
        original_snapshot = quota_store.snapshot
        snapshot_calls = 0

        def counted_snapshot(*, run_id: str) -> object:
            nonlocal snapshot_calls
            snapshot_calls += 1
            return original_snapshot(run_id=run_id)

        quota_store.snapshot = counted_snapshot
        checkpoint = ledger.checkpoint()
        self.assertEqual(1, snapshot_calls)
        resumed = YouTubeQuotaLedger.resume(
            checkpoint,
            expected_max_units=3,
            expected_run_id=RUN_ID,
            expected_sha256=checkpoint["checkpoint_sha256"],
            store=quota_store,
        )
        self.assertEqual(1, resumed.consumed_units)

    def test_every_request_reserves_quota_and_checkpoint_restores_it(self) -> None:
        quota_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        coordinator = youtube_coordinator(
            max_quota_units=3,
            quota_store=quota_store,
        )
        resolution = channel_resolution(coordinator)
        self.assertIsNotNone(
            coordinator.begin_uploads(resolution, REGISTRY)
        )
        coordinator.record_uploads_retry("temporary_unavailable")
        checkpoint = coordinator.uploads_checkpoint()
        self.assertEqual(
            2,
            checkpoint["adapter_runtime_checkpoint"]["quota"][
                "consumed_units"
            ],
        )
        resumed_coordinator = youtube_coordinator(
            max_quota_units=3,
            quota_store=quota_store,
        )
        resumed_resolution = (
            resumed_coordinator.issued_channel_resolution()
        )
        resumed_coordinator.resume_uploads(
            resumed_resolution,
            REGISTRY,
            checkpoint,
            expected_bounds=checkpoint["bounds"],
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
        )
        self.assertIsNotNone(
            resumed_coordinator.next_uploads_request()
        )
        self.assertEqual(3, resumed_coordinator.quota.consumed_units)
        resumed_coordinator.record_uploads_retry(
            "temporary_unavailable"
        )
        self.assertIsNone(
            resumed_coordinator.next_uploads_request()
        )
        self.assertEqual(
            ("blocked", "quota_exhausted"),
            resumed_coordinator.uploads_state(),
        )
        with self.assertRaises(AttributeError):
            resumed_coordinator.quota.consumed_units = 0
        with self.assertRaises(TypeError):
            resumed_coordinator.quota.method_counts["channels.list"] = 0

        stale_coordinator = youtube_coordinator(
            max_quota_units=3,
            quota_store=quota_store,
        )
        with self.assertRaises(AdapterConformanceError):
            stale_coordinator.resume_uploads(
                stale_coordinator.issued_channel_resolution(),
                REGISTRY,
                checkpoint,
                expected_bounds=checkpoint["bounds"],
                expected_checkpoint_sha256=checkpoint[
                    "checkpoint_sha256"
                ],
            )

    def test_forged_uploads_inventory_cannot_authorize_videos(self) -> None:
        coordinator = youtube_coordinator()
        payload = {
            "channel_lineage_sha256": hashlib.sha256(
                b"invented-channel"
            ).hexdigest(),
            "session_binding_sha256": (
                coordinator._session.binding_sha256
            ),
            "uploads_manifest_sha256": hashlib.sha256(
                b"invented-manifest"
            ).hexdigest(),
            "video_ids": ("foreign001",),
        }
        inventory = object.__new__(UploadsInventory)
        for field, value in (
            *payload.items(),
            (
                "lineage_sha256",
                hashlib.sha256(
                    json.dumps(
                        payload,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            ),
        ):
            object.__setattr__(inventory, field, value)
        with self.assertRaisesRegex(ValueError, "not issued"):
            coordinator.videos_adapter(
                inventory,
                ("foreign001",),
            )

    def test_quota_authority_is_shared_across_coordinators(self) -> None:
        quota_store = YouTubeQuotaStore(sqlite3.connect(":memory:"))
        first = youtube_coordinator(
            max_quota_units=1,
            quota_store=quota_store,
        )
        second = youtube_coordinator(
            max_quota_units=1,
            quota_store=quota_store,
        )
        self.assertIsNotNone(first.begin_channel(REGISTRY))
        self.assertIsNone(second.begin_channel(REGISTRY))
        self.assertEqual(
            ("blocked", "quota_exhausted"),
            second.channel_state(),
        )
        self.assertEqual(
            first.quota.authority_id,
            second.quota.authority_id,
        )
        self.assertEqual(1, second.quota.consumed_units)

    def test_quota_authority_survives_reopened_sqlite_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "youtube-quota.sqlite3"
            first_connection = sqlite3.connect(database)
            first = youtube_coordinator(
                max_quota_units=1,
                quota_store=YouTubeQuotaStore(first_connection),
            )
            self.assertIsNotNone(first.begin_channel(REGISTRY))
            authority_id = first.quota.authority_id
            first_connection.close()

            reopened_connection = sqlite3.connect(database)
            reopened = youtube_coordinator(
                max_quota_units=1,
                quota_store=YouTubeQuotaStore(reopened_connection),
            )
            self.assertIsNone(reopened.begin_channel(REGISTRY))
            self.assertEqual(
                ("blocked", "quota_exhausted"),
                reopened.channel_state(),
            )
            self.assertEqual(authority_id, reopened.quota.authority_id)
            self.assertEqual(1, reopened.quota.consumed_units)
            reopened_connection.close()

    def test_issued_stage_artifacts_survive_reopened_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "youtube-run.sqlite3"
            first_connection = sqlite3.connect(database)
            first = youtube_coordinator(
                max_quota_units=5,
                quota_store=YouTubeQuotaStore(first_connection),
            )
            inventory = uploads_inventory(first, ("video001",))
            first_connection.close()

            reopened_connection = sqlite3.connect(database)
            reopened = youtube_coordinator(
                max_quota_units=5,
                quota_store=YouTubeQuotaStore(reopened_connection),
            )
            self.assertEqual(
                "UCinventedChannel001",
                reopened.issued_channel_resolution().channel_id,
            )
            restored_inventory = (
                reopened.issued_uploads_inventory()
            )
            self.assertEqual(inventory, restored_inventory)
            harness = OfflineConformanceHarness(
                reopened.videos_adapter(
                    restored_inventory,
                    ("video001",),
                ),
                REGISTRY,
            )
            self.assertIsNotNone(harness.next_request())
            self.assertEqual(3, reopened.quota.consumed_units)
            reopened_connection.close()

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

    def test_documented_403_throttles_are_rate_limited(self) -> None:
        coordinator = youtube_coordinator()
        for reason in (
            "rateLimitExceeded",
            "userRateLimitExceeded",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    "rate_limited",
                    coordinator.classify_error(
                        status=403,
                        reason=reason,
                    ),
                )
        self.assertEqual(
            "access_forbidden",
            coordinator.classify_error(
                status=403,
                reason="forbidden",
            ),
        )

    def test_stage_checkpoint_cannot_resume_under_other_inventory_lineage(
        self,
    ) -> None:
        first = youtube_coordinator(max_quota_units=10)
        first_resolution = channel_resolution(first)
        first_inventory = complete_uploads(
            first,
            first_resolution,
            ("video001",),
            published_at="2026-01-02T03:04:05Z",
        )
        first_videos = OfflineConformanceHarness(
            first.videos_adapter(first_inventory, ("video001",)),
            REGISTRY,
        )
        first_videos.next_request()
        first_videos.record_retry("temporary_unavailable")
        checkpoint = first_videos.checkpoint()

        second = youtube_coordinator(max_quota_units=10)
        second_resolution = channel_resolution(second)
        second_inventory = complete_uploads(
            second,
            second_resolution,
            ("video001",),
            published_at="2026-02-03T04:05:06Z",
        )
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness.resume(
                second.videos_adapter(
                    second_inventory,
                    ("video001",),
                ),
                REGISTRY,
                checkpoint,
                expected_bounds=first_videos.bounds,
                expected_checkpoint_sha256=checkpoint[
                    "checkpoint_sha256"
                ],
            )

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
        self.assertEqual(
            "availability_public",
            by_id[adapter.stable_record_id({"id": "public001"})][
                "availability"
            ],
        )
        self.assertEqual(
            "availability_region_blocked",
            by_id[adapter.stable_record_id({"id": "region001"})][
                "availability"
            ],
        )
        self.assertEqual(
            "availability_age_gated",
            by_id[adapter.stable_record_id({"id": "age001"})][
                "availability"
            ],
        )
        self.assertEqual(
            "availability_unavailable",
            by_id[adapter.stable_record_id({"id": "missing001"})][
                "availability"
            ],
        )

    def test_changed_restriction_shapes_fail_closed(self) -> None:
        coordinator = youtube_coordinator()
        adapter = coordinator.videos_adapter(
            uploads_inventory(coordinator, ("invalid001",)),
            ("invalid001",),
        )
        invalid_details = (
            {"duration": "PT1M", "regionRestriction": []},
            {
                "duration": "PT1M",
                "regionRestriction": {"blocked": []},
            },
            {
                "duration": "PT1M",
                "regionRestriction": {"blocked": "KR"},
            },
            {
                "duration": "PT1M",
                "regionRestriction": {"unknown": ["KR"]},
            },
            {"contentRating": "age", "duration": "PT1M"},
            {
                "contentRating": {"ytRating": "invented"},
                "duration": "PT1M",
            },
        )
        for details in invalid_details:
            with self.subTest(details=details), self.assertRaises(
                ValueError
            ):
                adapter.parse_page(
                    video_page(
                        [
                            {
                                "id": "invalid001",
                                "contentDetails": details,
                                "status": {
                                    "privacyStatus": "public"
                                },
                            }
                        ]
                    ),
                    cursor=None,
                )

    def test_live_lifecycle_is_explicit_and_foreign_ids_cannot_be_enriched(
        self,
    ) -> None:
        coordinator = youtube_coordinator()
        resolution = channel_resolution(coordinator)
        caller_harness = OfflineConformanceHarness(
            coordinator._new_uploads_adapter(resolution),
            REGISTRY,
        )
        caller_request = caller_harness.next_request()
        caller_harness.ingest(
            MetadataResponse(
                status=200,
                mime_type="application/json",
                body=upload_page(
                    [upload_item("item001", video_id="foreign001")],
                    expected_total=1,
                ),
                final_url=caller_request.url,
            )
        )
        with self.assertRaises(ValueError):
            coordinator.finalize_uploads()
        inventory = complete_uploads(
            coordinator,
            resolution,
            ("live001",),
        )
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
            request = youtube_coordinator()._new_channel_adapter().build_request(
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
