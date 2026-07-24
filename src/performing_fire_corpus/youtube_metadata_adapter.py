from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

from .adapter_conformance import MetadataRequest


_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_PAGE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")
_METHOD_COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
}
_BLOCKERS = (
    "access_forbidden",
    "login_required",
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


@dataclass(frozen=True)
class ChannelResolution:
    handle: str
    channel_id: str
    uploads_playlist_id: str


@dataclass(frozen=True)
class YouTubeAssetCandidate:
    video_id: str
    asset_kind: str
    rights_state: str = "pending"
    acquisition_eligible: bool = False


class YouTubeQuotaLedger:
    def __init__(self, *, max_units: int) -> None:
        if not isinstance(max_units, int) or isinstance(max_units, bool) or max_units < 1:
            raise ValueError("quota budget must be positive")
        self.max_units = max_units
        self.consumed_units = 0
        self.method_counts = {method: 0 for method in sorted(_METHOD_COSTS)}

    def reserve(self, method: str) -> None:
        cost = _METHOD_COSTS.get(method)
        if cost is None or self.consumed_units + cost > self.max_units:
            raise ValueError("YouTube quota budget exhausted or method unreviewed")
        self.consumed_units += cost
        self.method_counts[method] += 1

    def checkpoint(self) -> dict[str, Any]:
        unsigned = {
            "schema_version": 1,
            "max_units": self.max_units,
            "consumed_units": self.consumed_units,
            "method_counts": copy.deepcopy(self.method_counts),
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
                "schema_version",
            }
            or checkpoint.get("schema_version") != 1
            or checkpoint.get("max_units") != expected_max_units
            or checkpoint.get("checkpoint_sha256") != expected_sha256
        ):
            raise ValueError("quota checkpoint binding is invalid")
        unsigned = {
            key: copy.deepcopy(checkpoint[key])
            for key in (
                "schema_version",
                "max_units",
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
        ledger = cls(max_units=expected_max_units)
        ledger.consumed_units = consumed
        ledger.method_counts = dict(counts)
        return ledger


class YouTubeChannelResolverAdapter:
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

    def build_request(self, cursor: str | None) -> MetadataRequest:
        if cursor is not None:
            raise ValueError("channel resolution is not paginated")
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
        return ChannelResolution(self.handle, channel_id, uploads_id)

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


class YouTubeUploadsAdapter:
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

    def __init__(self, resolution: ChannelResolution) -> None:
        if (
            resolution.handle != "@NamJunePaikArtCenter"
            or not _PUBLIC_ID.fullmatch(resolution.channel_id)
            or not _PUBLIC_ID.fullmatch(resolution.uploads_playlist_id)
        ):
            raise ValueError("channel resolution is invalid")
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
                "value_type": "literal",
            },
        }

    def build_request(self, cursor: str | None) -> MetadataRequest:
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
        if not isinstance(video_id, str) or not _PUBLIC_ID.fullmatch(video_id):
            raise ValueError("video identifier is invalid")
        return f"youtube-video-{video_id}"

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
                    "source_identity": video_id,
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


class YouTubeVideosAdapter:
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
        "duration_iso8601": {"value_type": "duration_iso8601"},
        "resource_type": {
            "allowed_values": ["resource_type_video"],
            "value_type": "enum",
        },
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    public_url = "https://www.googleapis.com/youtube/v3/videos"

    def __init__(self, video_ids: tuple[str, ...]) -> None:
        if (
            not video_ids
            or len(video_ids) > 50
            or tuple(sorted(set(video_ids))) != video_ids
            or any(not _PUBLIC_ID.fullmatch(item) for item in video_ids)
        ):
            raise ValueError("video batch must be sorted, unique, and bounded")
        self.video_ids = video_ids
        self.query_parameter_contracts = {
            "id": {
                "exact_value": ",".join(video_ids),
                "value_type": "literal",
            },
            "part": {
                "allowed_values": ["contentDetails", "status"],
                "value_type": "metadata_parts",
            },
        }

    def build_request(self, cursor: str | None) -> MetadataRequest:
        if cursor is not None:
            raise ValueError("video ID batches are not paginated")
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=f"{self.public_url}?{urlencode({'id': ','.join(self.video_ids), 'part': 'contentDetails,status'})}",
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
        return f"youtube-video-{video_id}"

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
                if details.get("contentRating"):
                    availability = "availability_age_gated"
                metadata["availability"] = availability
                duration = details.get("duration")
                if duration is not None:
                    metadata["duration_iso8601"] = duration
            records.append(
                {
                    "record_id": f"youtube-video-{video_id}",
                    "source_identity": video_id,
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
    def asset_candidates(
        self,
        video_id: str,
    ) -> tuple[YouTubeAssetCandidate, ...]:
        if not _PUBLIC_ID.fullmatch(video_id):
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
