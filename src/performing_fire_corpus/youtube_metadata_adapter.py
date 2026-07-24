from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlencode

from .adapter_conformance import (
    AdapterRequestBlocked,
    MetadataRequest,
    MetadataResponse,
    OfflineConformanceHarness,
)


_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,55}$")
_PAGE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{6,128}$")
_RUN_ID = re.compile(r"^youtube_metadata_run_[a-z0-9][a-z0-9._-]{0,96}$")
_METHOD_COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
}
_BLOCKERS = (
    "access_forbidden",
    "login_required",
    "quota_exhausted",
    "rate_limited",
    "subscription_required",
)


def _canonical(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_object(body: bytes, expected_kind: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("YouTube metadata response is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("kind") != expected_kind
        or not isinstance(value.get("items"), list)
    ):
        raise ValueError("YouTube metadata response shape changed")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _video_source_identity(video_id: str) -> str:
    return f"youtube-id-{hashlib.sha256(video_id.encode()).hexdigest()}"


def _video_record_id(video_id: str) -> str:
    if not _VIDEO_ID.fullmatch(video_id):
        raise ValueError("video identifier is invalid")
    return f"youtube-video-id-{video_id.encode('ascii').hex()}"


def _video_id_from_record_id(record_id: str) -> str:
    prefix = "youtube-video-id-"
    if not isinstance(record_id, str) or not record_id.startswith(prefix):
        raise ValueError("video record identifier is invalid")
    encoded = record_id.removeprefix(prefix)
    try:
        video_id = bytes.fromhex(encoded).decode("ascii")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("video record identifier is invalid") from error
    if (
        not _VIDEO_ID.fullmatch(video_id)
        or _video_record_id(video_id) != record_id
    ):
        raise ValueError("video record identifier is invalid")
    return video_id


@dataclass(frozen=True, init=False)
class ChannelResolution:
    handle: str
    channel_id: str
    uploads_playlist_id: str
    session_binding_sha256: str
    lineage_sha256: str

    def validate(self, *, expected_session_binding_sha256: str) -> None:
        payload = {
            "channel_id": self.channel_id,
            "handle": self.handle,
            "session_binding_sha256": self.session_binding_sha256,
            "uploads_playlist_id": self.uploads_playlist_id,
        }
        if (
            self.handle != "@NamJunePaikArtCenter"
            or not _PUBLIC_ID.fullmatch(self.channel_id)
            or not _PUBLIC_ID.fullmatch(self.uploads_playlist_id)
            or self.session_binding_sha256
            != expected_session_binding_sha256
            or self.lineage_sha256 != _digest(payload)
        ):
            raise ValueError("channel resolution lineage is invalid")


@dataclass(frozen=True, init=False)
class UploadsInventory:
    channel_lineage_sha256: str
    session_binding_sha256: str
    video_ids: tuple[str, ...]
    uploads_manifest_sha256: str
    lineage_sha256: str

    def validate(self, *, expected_session_binding_sha256: str) -> None:
        payload = {
            "channel_lineage_sha256": self.channel_lineage_sha256,
            "session_binding_sha256": self.session_binding_sha256,
            "uploads_manifest_sha256": self.uploads_manifest_sha256,
            "video_ids": self.video_ids,
        }
        if (
            self.session_binding_sha256
            != expected_session_binding_sha256
            or not self.video_ids
            or tuple(sorted(set(self.video_ids))) != self.video_ids
            or any(not _VIDEO_ID.fullmatch(item) for item in self.video_ids)
            or self.lineage_sha256 != _digest(payload)
        ):
            raise ValueError("uploads inventory lineage is invalid")


@dataclass(frozen=True)
class YouTubeAssetCandidate:
    video_id: str
    asset_kind: str
    rights_state: str = "pending"
    acquisition_eligible: bool = False


class YouTubeQuotaExhausted(ValueError):
    """The local run-wide quota bound is exhausted."""


@dataclass(frozen=True)
class YouTubeQuotaSnapshot:
    max_units: int
    run_id: str
    consumed_units: int
    method_counts: Mapping[str, int]


class YouTubeQuotaStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise ValueError("quota store requires an SQLite connection")
        self._connection = connection
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_quota_run (
                run_id TEXT PRIMARY KEY,
                max_units INTEGER NOT NULL,
                consumed_units INTEGER NOT NULL,
                channels_list INTEGER NOT NULL,
                playlist_items_list INTEGER NOT NULL,
                videos_list INTEGER NOT NULL
            )
            """
        )
        self._connection.commit()

    def ensure_run(self, *, run_id: str, max_units: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT max_units FROM youtube_quota_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO youtube_quota_run (
                        run_id,
                        max_units,
                        consumed_units,
                        channels_list,
                        playlist_items_list,
                        videos_list
                    ) VALUES (?, ?, 0, 0, 0, 0)
                    """,
                    (run_id, max_units),
                )
            elif row[0] != max_units:
                raise ValueError(
                    "quota run maximum conflicts with durable state"
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def snapshot(self, *, run_id: str) -> YouTubeQuotaSnapshot:
        row = self._connection.execute(
            """
            SELECT
                max_units,
                consumed_units,
                channels_list,
                playlist_items_list,
                videos_list
            FROM youtube_quota_run
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("quota run is absent from durable state")
        return YouTubeQuotaSnapshot(
            max_units=row[0],
            run_id=run_id,
            consumed_units=row[1],
            method_counts=MappingProxyType(
                {
                    "channels.list": row[2],
                    "playlistItems.list": row[3],
                    "videos.list": row[4],
                }
            ),
        )

    def reserve(self, *, run_id: str, method: str, cost: int) -> None:
        column = {
            "channels.list": "channels_list",
            "playlistItems.list": "playlist_items_list",
            "videos.list": "videos_list",
        }.get(method)
        if column is None or cost != _METHOD_COSTS.get(method):
            raise ValueError("YouTube quota method is unreviewed")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            snapshot = self.snapshot(run_id=run_id)
            if snapshot.consumed_units + cost > snapshot.max_units:
                raise YouTubeQuotaExhausted(
                    "YouTube quota budget exhausted"
                )
            self._connection.execute(
                f"""
                UPDATE youtube_quota_run
                SET consumed_units = consumed_units + ?,
                    {column} = {column} + 1
                WHERE run_id = ?
                """,
                (cost, run_id),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def merge_checkpoint(
        self,
        *,
        run_id: str,
        consumed_units: int,
        method_counts: Mapping[str, int],
    ) -> None:
        if (
            not isinstance(consumed_units, int)
            or isinstance(consumed_units, bool)
            or consumed_units < 0
            or not isinstance(method_counts, Mapping)
            or set(method_counts) != set(_METHOD_COSTS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in method_counts.values()
            )
            or consumed_units
            != sum(
                method_counts[method] * _METHOD_COSTS[method]
                for method in _METHOD_COSTS
            )
        ):
            raise ValueError("YouTube checkpoint counters are invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.snapshot(run_id=run_id)
            if (
                consumed_units > current.max_units
                or current.consumed_units > consumed_units
                or any(
                    current.method_counts[method]
                    > method_counts[method]
                    for method in current.method_counts
                )
            ):
                raise ValueError("YouTube runtime checkpoint is stale")
            self._connection.execute(
                """
                UPDATE youtube_quota_run
                SET consumed_units = ?,
                    channels_list = ?,
                    playlist_items_list = ?,
                    videos_list = ?
                WHERE run_id = ?
                """,
                (
                    consumed_units,
                    method_counts["channels.list"],
                    method_counts["playlistItems.list"],
                    method_counts["videos.list"],
                    run_id,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise


class YouTubeQuotaLedger:
    def __init__(
        self,
        *,
        max_units: int,
        run_id: str,
        store: YouTubeQuotaStore | None = None,
    ) -> None:
        if not isinstance(max_units, int) or isinstance(max_units, bool) or max_units < 1:
            raise ValueError("quota budget must be positive")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("quota run identifier is invalid")
        self._store = store or YouTubeQuotaStore(
            sqlite3.connect(":memory:")
        )
        self._run_id = run_id
        self._store.ensure_run(run_id=run_id, max_units=max_units)

    @property
    def snapshot(self) -> YouTubeQuotaSnapshot:
        return self._store.snapshot(run_id=self._run_id)

    @property
    def max_units(self) -> int:
        return self.snapshot.max_units

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def consumed_units(self) -> int:
        return self.snapshot.consumed_units

    @property
    def method_counts(self) -> Mapping[str, int]:
        return self.snapshot.method_counts

    def reserve(self, method: str) -> None:
        cost = _METHOD_COSTS.get(method)
        if cost is None:
            raise ValueError("YouTube quota method is unreviewed")
        self._store.reserve(
            run_id=self.run_id,
            method=method,
            cost=cost,
        )

    def checkpoint(self) -> dict[str, Any]:
        snapshot = self.snapshot
        unsigned = {
            "schema_version": 1,
            "max_units": snapshot.max_units,
            "run_id": snapshot.run_id,
            "consumed_units": snapshot.consumed_units,
            "method_counts": dict(snapshot.method_counts),
        }
        return {
            **unsigned,
            "checkpoint_sha256": hashlib.sha256(
                _canonical(unsigned).encode()
            ).hexdigest(),
        }

    @classmethod
    def resume(
        cls,
        checkpoint: Mapping[str, Any],
        *,
        expected_max_units: int,
        expected_run_id: str,
        expected_sha256: str,
    ) -> YouTubeQuotaLedger:
        if (
            not isinstance(checkpoint, Mapping)
            or set(checkpoint)
            != {
                "checkpoint_sha256",
                "consumed_units",
                "max_units",
                "method_counts",
                "run_id",
                "schema_version",
            }
            or checkpoint.get("schema_version") != 1
            or checkpoint.get("max_units") != expected_max_units
            or checkpoint.get("run_id") != expected_run_id
            or checkpoint.get("checkpoint_sha256") != expected_sha256
        ):
            raise ValueError("quota checkpoint binding is invalid")
        unsigned = {
            key: copy.deepcopy(checkpoint[key])
            for key in (
                "schema_version",
                "max_units",
                "run_id",
                "consumed_units",
                "method_counts",
            )
        }
        if hashlib.sha256(_canonical(unsigned).encode()).hexdigest() != expected_sha256:
            raise ValueError("quota checkpoint integrity failed")
        counts = checkpoint["method_counts"]
        consumed = checkpoint["consumed_units"]
        if (
            not isinstance(counts, Mapping)
            or set(counts) != set(_METHOD_COSTS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in counts.values()
            )
            or not isinstance(consumed, int)
            or isinstance(consumed, bool)
            or consumed < 0
            or consumed > expected_max_units
            or consumed
            != sum(counts[method] * _METHOD_COSTS[method] for method in counts)
        ):
            raise ValueError("quota checkpoint counters are invalid")
        ledger = cls(
            max_units=expected_max_units,
            run_id=expected_run_id,
        )
        ledger._store.merge_checkpoint(
            run_id=expected_run_id,
            consumed_units=consumed,
            method_counts=counts,
        )
        return ledger


@dataclass(frozen=True)
class _YouTubeSession:
    run_id: str
    binding_sha256: str
    ledger: YouTubeQuotaLedger


class _QuotaBoundAdapter:
    quota_method: str

    def __init__(self, session: _YouTubeSession) -> None:
        if (
            not isinstance(session, _YouTubeSession)
            or session.run_id != session.ledger.run_id
            or session.binding_sha256
            != _digest(
                {
                    "max_units": session.ledger.max_units,
                    "run_id": session.run_id,
                    "source_id": "njp-youtube-official",
                }
            )
        ):
            raise ValueError("YouTube metadata session is invalid")
        self._session = session

    def _reserve_quota(self) -> None:
        try:
            self._session.ledger.reserve(self.quota_method)
        except YouTubeQuotaExhausted as error:
            raise AdapterRequestBlocked("quota_exhausted") from error

    def runtime_checkpoint(self) -> dict[str, Any]:
        return {
            "runtime_type": "youtube_quota",
            "run_id": self._session.run_id,
            "quota": self._session.ledger.checkpoint(),
        }

    def restore_runtime_checkpoint(self, value: Mapping[str, Any]) -> None:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"quota", "run_id", "runtime_type"}
            or value.get("runtime_type") != "youtube_quota"
            or value.get("run_id") != self._session.run_id
            or not isinstance(value.get("quota"), Mapping)
        ):
            raise ValueError("YouTube runtime checkpoint is invalid")
        quota = value["quota"]
        restored = YouTubeQuotaLedger.resume(
            quota,
            expected_max_units=self._session.ledger.max_units,
            expected_run_id=self._session.run_id,
            expected_sha256=quota.get("checkpoint_sha256"),
        )
        restored_snapshot = restored.snapshot
        self._session.ledger._store.merge_checkpoint(
            run_id=self._session.run_id,
            consumed_units=restored_snapshot.consumed_units,
            method_counts=restored_snapshot.method_counts,
        )


class YouTubeChannelResolverAdapter(_QuotaBoundAdapter):
    adapter_id = "youtube-channel-resolver"
    adapter_version = "1.0.0"
    source_id = "njp-youtube-official"
    endpoint_id = "njp-youtube-channels-api"
    robots_applicability = "not_applicable"
    allowed_methods = ("GET",)
    allowed_hosts = ("www.googleapis.com",)
    allowed_query_parameters = ("forHandle", "part")
    query_parameter_contracts = {
        "forHandle": {
            "exact_value": "@NamJunePaikArtCenter",
            "value_type": "literal",
        },
        "part": {
            "allowed_values": ["contentDetails"],
            "value_type": "metadata_parts",
        },
    }
    expected_mime_types = ("application/json",)
    approved_metadata_fields = ("resource_type",)
    required_metadata_fields = ("resource_type",)
    metadata_field_contracts = {
        "resource_type": {
            "allowed_values": ["resource_type_channel"],
            "value_type": "enum",
        }
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    public_url = "https://www.googleapis.com/youtube/v3/channels"
    handle = "@NamJunePaikArtCenter"
    quota_method = "channels.list"

    def adapter_lineage_sha256(self) -> str:
        return _digest(
            {
                "adapter_id": self.adapter_id,
                "handle": self.handle,
                "session_binding_sha256": self._session.binding_sha256,
            }
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        if cursor is not None:
            raise ValueError("channel resolution is not paginated")
        self._reserve_quota()
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=f"{self.public_url}?{urlencode({'forHandle': self.handle, 'part': 'contentDetails'})}",
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        value = _json_object(body, "youtube#channelListResponse")
        state = value.get("accessState")
        if state is not None and state not in self.blocker_states:
            raise ValueError("unknown YouTube access state")
        return state

    def resolve_channel(self, body: bytes) -> ChannelResolution:
        value = _json_object(body, "youtube#channelListResponse")
        if len(value["items"]) != 1:
            raise ValueError("YouTube handle resolution is ambiguous")
        item = value["items"][0]
        try:
            channel_id = item["id"]
            uploads_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
        except (KeyError, TypeError) as error:
            raise ValueError("YouTube channel response shape changed") from error
        if (
            not isinstance(channel_id, str)
            or not _PUBLIC_ID.fullmatch(channel_id)
            or not isinstance(uploads_id, str)
            or not _PUBLIC_ID.fullmatch(uploads_id)
        ):
            raise ValueError("YouTube channel identifiers are invalid")
        payload = {
            "channel_id": channel_id,
            "handle": self.handle,
            "session_binding_sha256": self._session.binding_sha256,
            "uploads_playlist_id": uploads_id,
        }
        resolution = object.__new__(ChannelResolution)
        for field, field_value in (
            ("handle", self.handle),
            ("channel_id", channel_id),
            ("uploads_playlist_id", uploads_id),
            ("session_binding_sha256", self._session.binding_sha256),
            ("lineage_sha256", _digest(payload)),
        ):
            object.__setattr__(resolution, field, field_value)
        return resolution

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        channel_id = item.get("id")
        if not isinstance(channel_id, str) or not _PUBLIC_ID.fullmatch(channel_id):
            raise ValueError("channel identifier is invalid")
        return f"youtube-channel-{channel_id}"

    def parse_page(self, body: bytes, *, cursor: str | None) -> dict[str, Any]:
        if cursor is not None:
            raise ValueError("channel resolution is not paginated")
        resolution = self.resolve_channel(body)
        return {
            "records": [
                {
                    "record_id": f"youtube-channel-{resolution.channel_id}",
                    "source_identity": resolution.channel_id,
                    "metadata": {"resource_type": "resource_type_channel"},
                }
            ],
            "next_cursor": None,
            "next_ordinal": None,
            "terminal": True,
            "expected_total": 1,
            "rejected_count": 0,
        }


class YouTubeUploadsAdapter(_QuotaBoundAdapter):
    adapter_id = "youtube-uploads-playlist"
    adapter_version = "1.0.0"
    source_id = "njp-youtube-official"
    endpoint_id = "njp-youtube-playlist-items-api"
    robots_applicability = "not_applicable"
    allowed_methods = ("GET",)
    allowed_hosts = ("www.googleapis.com",)
    expected_mime_types = ("application/json",)
    approved_metadata_fields = ("published_at", "resource_type")
    required_metadata_fields = ("resource_type",)
    metadata_field_contracts = {
        "published_at": {"value_type": "timestamp"},
        "resource_type": {
            "allowed_values": ["resource_type_video"],
            "value_type": "enum",
        },
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    public_url = "https://www.googleapis.com/youtube/v3/playlistItems"
    quota_method = "playlistItems.list"

    def __init__(
        self,
        session: _YouTubeSession,
        resolution: ChannelResolution,
    ) -> None:
        super().__init__(session)
        resolution.validate(
            expected_session_binding_sha256=session.binding_sha256
        )
        self.resolution = resolution
        self.allowed_query_parameters = (
            "maxResults",
            "pageToken",
            "part",
            "playlistId",
        )
        self.query_parameter_contracts = {
            "maxResults": {"exact_value": "50", "value_type": "literal"},
            "pageToken": {
                "checkpoint_ordinal": True,
                "cursor_prefix": "opaque-",
                "value_type": "cursor_opaque",
            },
            "part": {
                "allowed_values": ["contentDetails"],
                "value_type": "metadata_parts",
            },
            "playlistId": {
                "exact_value": resolution.uploads_playlist_id,
                "value_type": "opaque_identifier",
            },
        }

    def adapter_lineage_sha256(self) -> str:
        return _digest(
            {
                "adapter_id": self.adapter_id,
                "channel_lineage_sha256": self.resolution.lineage_sha256,
                "session_binding_sha256": self._session.binding_sha256,
            }
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        self._reserve_quota()
        query = {
            "maxResults": "50",
            "part": "contentDetails",
            "playlistId": self.resolution.uploads_playlist_id,
        }
        if cursor is not None:
            suffix = cursor.removeprefix("opaque-")
            _, token = suffix.split("~", 1)
            query["pageToken"] = token
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=f"{self.public_url}?{urlencode(query)}",
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        value = _json_object(body, "youtube#playlistItemListResponse")
        state = value.get("accessState")
        if state is not None and state not in self.blocker_states:
            raise ValueError("unknown YouTube access state")
        return state

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        try:
            video_id = item["contentDetails"]["videoId"]
        except (KeyError, TypeError) as error:
            raise ValueError("upload item has no public video identifier") from error
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
            raise ValueError("video identifier is invalid")
        return _video_record_id(video_id)

    def parse_page(self, body: bytes, *, cursor: str | None) -> dict[str, Any]:
        value = _json_object(body, "youtube#playlistItemListResponse")
        records: list[dict[str, Any]] = []
        rejected = 0
        for item in value["items"]:
            try:
                record_id = self.stable_record_id(item)
                video_id = item["contentDetails"]["videoId"]
            except (ValueError, KeyError, TypeError):
                rejected += 1
                continue
            metadata = {"resource_type": "resource_type_video"}
            published = item["contentDetails"].get("videoPublishedAt")
            if published is not None:
                metadata["published_at"] = published
            records.append(
                {
                    "record_id": record_id,
                    "source_identity": _video_source_identity(video_id),
                    "metadata": metadata,
                }
            )
        token = value.get("nextPageToken")
        if token is not None and (
            not isinstance(token, str) or not _PAGE_TOKEN.fullmatch(token)
        ):
            raise ValueError("YouTube page token is invalid")
        current_ordinal = (
            0
            if cursor is None
            else int(cursor.removeprefix("opaque-").split("~", 1)[0])
        )
        next_ordinal = current_ordinal + 1 if token is not None else None
        page_info = value.get("pageInfo")
        expected_total = (
            page_info.get("totalResults")
            if isinstance(page_info, Mapping)
            else None
        )
        return {
            "records": records,
            "next_cursor": (
                f"opaque-{next_ordinal}~{token}" if token is not None else None
            ),
            "next_ordinal": next_ordinal,
            "terminal": token is None,
            "expected_total": expected_total,
            "rejected_count": rejected,
        }


class YouTubeVideosAdapter(_QuotaBoundAdapter):
    adapter_id = "youtube-videos-metadata"
    adapter_version = "1.0.0"
    source_id = "njp-youtube-official"
    endpoint_id = "njp-youtube-videos-api"
    robots_applicability = "not_applicable"
    allowed_methods = ("GET",)
    allowed_hosts = ("www.googleapis.com",)
    allowed_query_parameters = ("id", "part")
    expected_mime_types = ("application/json",)
    approved_metadata_fields = (
        "availability",
        "broadcast_state",
        "duration_iso8601",
        "resource_type",
    )
    required_metadata_fields = ("availability", "resource_type")
    metadata_field_contracts = {
        "availability": {
            "allowed_values": [
                "availability_age_gated",
                "availability_private",
                "availability_public",
                "availability_region_blocked",
                "availability_unavailable",
                "availability_unlisted",
            ],
            "value_type": "enum",
        },
        "broadcast_state": {
            "allowed_values": [
                "broadcast_state_completed",
                "broadcast_state_live",
                "broadcast_state_not_live",
                "broadcast_state_upcoming",
            ],
            "value_type": "enum",
        },
        "duration_iso8601": {"value_type": "duration_iso8601"},
        "resource_type": {
            "allowed_values": ["resource_type_video"],
            "value_type": "enum",
        },
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    public_url = "https://www.googleapis.com/youtube/v3/videos"
    quota_method = "videos.list"

    def __init__(
        self,
        session: _YouTubeSession,
        inventory: UploadsInventory,
        video_ids: tuple[str, ...],
    ) -> None:
        super().__init__(session)
        inventory.validate(
            expected_session_binding_sha256=session.binding_sha256
        )
        if (
            not video_ids
            or len(video_ids) > 50
            or tuple(sorted(set(video_ids))) != video_ids
            or any(not _VIDEO_ID.fullmatch(item) for item in video_ids)
            or not set(video_ids).issubset(inventory.video_ids)
        ):
            raise ValueError(
                "video batch must be a sorted bounded uploads-inventory subset"
            )
        self.inventory = inventory
        self.video_ids = video_ids
        self.query_parameter_contracts = {
            "id": {
                "exact_value": ",".join(video_ids),
                "max_items": 50,
                "value_type": "opaque_identifier_list",
            },
            "part": {
                "allowed_values": [
                    "contentDetails",
                    "liveStreamingDetails",
                    "status",
                ],
                "value_type": "metadata_parts",
            },
        }

    def adapter_lineage_sha256(self) -> str:
        return _digest(
            {
                "adapter_id": self.adapter_id,
                "session_binding_sha256": self._session.binding_sha256,
                "uploads_lineage_sha256": self.inventory.lineage_sha256,
                "video_ids": self.video_ids,
            }
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        if cursor is not None:
            raise ValueError("video ID batches are not paginated")
        self._reserve_quota()
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=f"{self.public_url}?{urlencode({'id': ','.join(self.video_ids), 'part': 'contentDetails,liveStreamingDetails,status'})}",
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        value = _json_object(body, "youtube#videoListResponse")
        state = value.get("accessState")
        if state is not None and state not in self.blocker_states:
            raise ValueError("unknown YouTube access state")
        return state

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        video_id = item.get("id")
        if not isinstance(video_id, str) or video_id not in self.video_ids:
            raise ValueError("video response is outside the requested batch")
        return _video_record_id(video_id)

    def parse_page(self, body: bytes, *, cursor: str | None) -> dict[str, Any]:
        if cursor is not None:
            raise ValueError("video ID batches are not paginated")
        value = _json_object(body, "youtube#videoListResponse")
        returned: dict[str, Mapping[str, Any]] = {}
        for item in value["items"]:
            if not isinstance(item, Mapping):
                raise ValueError("video response item is invalid")
            video_id = item.get("id")
            if (
                not isinstance(video_id, str)
                or video_id not in self.video_ids
                or video_id in returned
            ):
                raise ValueError("video response batch is ambiguous")
            returned[video_id] = item
        records: list[dict[str, Any]] = []
        for video_id in self.video_ids:
            item = returned.get(video_id)
            metadata: dict[str, str] = {
                "availability": "availability_unavailable",
                "broadcast_state": "broadcast_state_not_live",
                "resource_type": "resource_type_video",
            }
            if item is not None:
                details = item.get("contentDetails")
                status = item.get("status")
                if not isinstance(details, Mapping) or not isinstance(status, Mapping):
                    raise ValueError("video metadata shape changed")
                privacy = status.get("privacyStatus")
                availability = {
                    "private": "availability_private",
                    "public": "availability_public",
                    "unlisted": "availability_unlisted",
                }.get(privacy)
                if availability is None:
                    raise ValueError("video privacy state is unknown")
                if details.get("regionRestriction"):
                    availability = "availability_region_blocked"
                rating = details.get("contentRating")
                if (
                    isinstance(rating, Mapping)
                    and rating.get("ytRating") == "ytAgeRestricted"
                ):
                    availability = "availability_age_gated"
                metadata["availability"] = availability
                live_details = item.get("liveStreamingDetails")
                if live_details is not None:
                    if not isinstance(live_details, Mapping):
                        raise ValueError("live metadata shape changed")
                    timestamps = {
                        key: live_details.get(key)
                        for key in (
                            "actualEndTime",
                            "actualStartTime",
                            "scheduledStartTime",
                        )
                    }
                    for timestamp in timestamps.values():
                        if timestamp is not None:
                            try:
                                datetime.strptime(
                                    timestamp,
                                    "%Y-%m-%dT%H:%M:%SZ",
                                )
                            except (TypeError, ValueError) as error:
                                raise ValueError(
                                    "live metadata timestamp is invalid"
                                ) from error
                    if timestamps["actualEndTime"] is not None:
                        metadata["broadcast_state"] = (
                            "broadcast_state_completed"
                        )
                    elif timestamps["actualStartTime"] is not None:
                        metadata["broadcast_state"] = "broadcast_state_live"
                    elif timestamps["scheduledStartTime"] is not None:
                        metadata["broadcast_state"] = (
                            "broadcast_state_upcoming"
                        )
                    else:
                        raise ValueError("live metadata lifecycle is unknown")
                duration = details.get("duration")
                if duration is not None:
                    metadata["duration_iso8601"] = duration
            records.append(
                {
                    "record_id": (
                        _video_record_id(video_id)
                        if item is None
                        else self.stable_record_id(item)
                    ),
                    "source_identity": _video_source_identity(video_id),
                    "metadata": metadata,
                }
            )
        return {
            "records": records,
            "next_cursor": None,
            "next_ordinal": None,
            "terminal": True,
            "expected_total": len(self.video_ids),
            "rejected_count": 0,
        }


class YouTubeMetadataCoordinator:
    def __init__(
        self,
        *,
        max_quota_units: int,
        run_id: str,
        quota_store: YouTubeQuotaStore,
    ) -> None:
        ledger = YouTubeQuotaLedger(
            max_units=max_quota_units,
            run_id=run_id,
            store=quota_store,
        )
        self._session = _YouTubeSession(
            run_id=run_id,
            binding_sha256=_digest(
                {
                    "max_units": max_quota_units,
                    "run_id": run_id,
                    "source_id": "njp-youtube-official",
                }
            ),
            ledger=ledger,
        )
        self._uploads_harness: OfflineConformanceHarness | None = None
        self._uploads_resolution: ChannelResolution | None = None

    @property
    def quota(self) -> YouTubeQuotaSnapshot:
        return self._session.ledger.snapshot

    def channel_adapter(self) -> YouTubeChannelResolverAdapter:
        return YouTubeChannelResolverAdapter(self._session)

    def _new_uploads_adapter(
        self,
        resolution: ChannelResolution,
    ) -> YouTubeUploadsAdapter:
        return YouTubeUploadsAdapter(self._session, resolution)

    def begin_uploads(
        self,
        resolution: ChannelResolution,
        registry: Mapping[str, Any],
        **bounds: Any,
    ) -> MetadataRequest | None:
        if self._uploads_harness is not None:
            raise ValueError("uploads stage is already active")
        resolution.validate(
            expected_session_binding_sha256=self._session.binding_sha256
        )
        self._uploads_resolution = resolution
        self._uploads_harness = OfflineConformanceHarness(
            self._new_uploads_adapter(resolution),
            registry,
            **bounds,
        )
        return self._uploads_harness.next_request()

    def resume_uploads(
        self,
        resolution: ChannelResolution,
        registry: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        *,
        expected_bounds: Mapping[str, Any],
        expected_checkpoint_sha256: str,
    ) -> None:
        if self._uploads_harness is not None:
            raise ValueError("uploads stage is already active")
        resolution.validate(
            expected_session_binding_sha256=self._session.binding_sha256
        )
        self._uploads_resolution = resolution
        self._uploads_harness = OfflineConformanceHarness.resume(
            self._new_uploads_adapter(resolution),
            registry,
            checkpoint,
            expected_bounds=expected_bounds,
            expected_checkpoint_sha256=expected_checkpoint_sha256,
        )

    def next_uploads_request(self) -> MetadataRequest | None:
        if self._uploads_harness is None:
            raise ValueError("uploads stage is not active")
        return self._uploads_harness.next_request()

    def ingest_uploads(
        self,
        response: MetadataResponse,
    ) -> dict[str, Any]:
        if self._uploads_harness is None:
            raise ValueError("uploads stage is not active")
        return self._uploads_harness.ingest(response)

    def record_uploads_retry(self, code: str) -> dict[str, Any]:
        if self._uploads_harness is None:
            raise ValueError("uploads stage is not active")
        return self._uploads_harness.record_retry(code)

    def uploads_checkpoint(self) -> dict[str, Any]:
        if self._uploads_harness is None:
            raise ValueError("uploads stage is not active")
        return self._uploads_harness.checkpoint()

    def uploads_state(self) -> tuple[str, str | None]:
        if self._uploads_harness is None:
            raise ValueError("uploads stage is not active")
        manifest = self._uploads_harness.manifest()
        return manifest["state"], manifest["stop_reason"]

    def finalize_uploads(self) -> UploadsInventory:
        harness = self._uploads_harness
        resolution = self._uploads_resolution
        if (
            not isinstance(harness, OfflineConformanceHarness)
            or not isinstance(resolution, ChannelResolution)
            or type(harness.adapter) is not YouTubeUploadsAdapter
            or harness.adapter._session is not self._session
            or harness.adapter.resolution.lineage_sha256
            != resolution.lineage_sha256
        ):
            raise ValueError("uploads harness is not owned by this run")
        manifest = harness.manifest()
        expected_adapter_lineage = (
            harness.adapter.adapter_lineage_sha256()
        )
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("schema_version") != 1
            or manifest.get("manifest_type")
            != "offline_adapter_conformance"
            or manifest.get("adapter_id") != "youtube-uploads-playlist"
            or manifest.get("adapter_version") != "1.0.0"
            or manifest.get("adapter_lineage_sha256")
            != expected_adapter_lineage
            or manifest.get("source_id") != "njp-youtube-official"
            or manifest.get("endpoint_id")
            != "njp-youtube-playlist-items-api"
            or manifest.get("state")
            != "complete_for_observed_endpoint"
            or manifest.get("stop_reason") != "terminal_page"
            or manifest.get("next_cursor_sha256") is not None
            or manifest.get("unvisited_remainder") not in {None, 0}
            or manifest.get("rejected_records") != 0
            or not isinstance(manifest.get("records"), list)
            or manifest.get("observed_unique_records")
            != len(manifest["records"])
        ):
            raise ValueError("uploads manifest is not a complete bound inventory")
        video_ids: list[str] = []
        for record in manifest["records"]:
            if (
                not isinstance(record, Mapping)
                or set(record)
                != {"metadata", "record_id", "source_identity_sha256"}
                or not isinstance(record.get("record_id"), str)
                or not record["record_id"].startswith("youtube-video-id-")
                or not isinstance(record.get("source_identity_sha256"), str)
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    record["source_identity_sha256"],
                )
                or not isinstance(record.get("metadata"), Mapping)
                or not set(record["metadata"]).issubset(
                    {"published_at", "resource_type"}
                )
                or record["metadata"].get("resource_type")
                != "resource_type_video"
            ):
                raise ValueError("uploads manifest record lineage is invalid")
            published_at = record["metadata"].get("published_at")
            if published_at is not None:
                try:
                    datetime.strptime(
                        published_at,
                        "%Y-%m-%dT%H:%M:%SZ",
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "uploads manifest publish time is invalid"
                    ) from error
            try:
                video_id = _video_id_from_record_id(record["record_id"])
            except ValueError as error:
                raise ValueError(
                    "uploads manifest video identifier is invalid"
                ) from error
            if (
                not _VIDEO_ID.fullmatch(video_id)
                or record["source_identity_sha256"]
                != hashlib.sha256(
                    _video_source_identity(video_id).encode()
                ).hexdigest()
            ):
                raise ValueError("uploads manifest video identifier is invalid")
            video_ids.append(video_id)
        if not video_ids or len(set(video_ids)) != len(video_ids):
            raise ValueError("uploads manifest video set is invalid")
        bound_video_ids = tuple(sorted(video_ids))
        manifest_sha256 = _digest(manifest)
        payload = {
            "channel_lineage_sha256": resolution.lineage_sha256,
            "session_binding_sha256": resolution.session_binding_sha256,
            "uploads_manifest_sha256": manifest_sha256,
            "video_ids": bound_video_ids,
        }
        inventory = object.__new__(UploadsInventory)
        for field, value in (
            ("channel_lineage_sha256", resolution.lineage_sha256),
            (
                "session_binding_sha256",
                resolution.session_binding_sha256,
            ),
            ("video_ids", bound_video_ids),
            ("uploads_manifest_sha256", manifest_sha256),
            ("lineage_sha256", _digest(payload)),
        ):
            object.__setattr__(inventory, field, value)
        return inventory

    def videos_adapter(
        self,
        inventory: UploadsInventory,
        video_ids: tuple[str, ...],
    ) -> YouTubeVideosAdapter:
        return YouTubeVideosAdapter(
            self._session,
            inventory,
            video_ids,
        )

    def asset_candidates(
        self,
        video_id: str,
    ) -> tuple[YouTubeAssetCandidate, ...]:
        if not _VIDEO_ID.fullmatch(video_id):
            raise ValueError("video identifier is invalid")
        return tuple(
            YouTubeAssetCandidate(video_id=video_id, asset_kind=kind)
            for kind in ("audio", "caption", "thumbnail", "video")
        )

    def classify_error(self, *, status: int, reason: str) -> str:
        if status == 403 and reason in {
            "dailyLimitExceeded",
            "quotaExceeded",
        }:
            return "quota_exhausted"
        if status == 403:
            return "access_forbidden"
        if status == 401:
            return "login_required"
        if status == 429:
            return "rate_limited"
        raise ValueError("unreviewed YouTube API error")
