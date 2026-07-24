from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

from .adapter_conformance import MetadataRequest


_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_MIME = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*"
)
_YEAR = re.compile(r"[0-9]{4}")
_DURATION = re.compile(
    r"P(?=.+)(?:[0-9]+D)?"
    r"(?:T(?=.+)(?:[0-9]+H)?(?:[0-9]+M)?(?:[0-9]+S)?)?"
)
_LANGUAGES = (
    "language_bilingual",
    "language_en",
    "language_ko",
    "language_unknown",
)
_RECORD_CLASSES = (
    "record_class_archive_image",
    "record_class_broadcast",
    "record_class_institutional_record",
    "record_class_research_document",
    "record_class_video_work",
)
_ASSET_KINDS = frozenset(
    {
        "asset_kind_caption",
        "asset_kind_document",
        "asset_kind_image",
        "asset_kind_thumbnail",
        "asset_kind_video",
    }
)
_BLOCKERS = (
    "access_forbidden",
    "login_required",
    "rate_limited",
    "subscription_required",
)


class SourceShapeUnreviewed(RuntimeError):
    """Raised before production use when no current bounded shape exists."""


@dataclass(frozen=True)
class VideoLibraryAssetCandidate:
    source_id: str
    relationship_record_id: str
    asset_kind: str
    public_url: str
    claimed_mime_type: str
    rights_state: str = "pending"
    acquisition_eligible: bool = False
    access_blocker: str | None = None
    retry_allowed: bool = False


class _MetadataHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.records: list[dict[str, str]] = []
        self.assets: list[dict[str, str]] = []
        self.stack: list[str] = []
        self.seen_doctype = False
        self.seen_html = False
        self.seen_head = False
        self.seen_body = False

    def handle_decl(self, decl: str) -> None:
        self.seen_doctype = decl.lower() == "doctype html"

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attribute_names = [key for key, _ in attrs]
        if len(attribute_names) != len(set(attribute_names)):
            raise ValueError("metadata HTML has duplicate attributes")
        values = {
            key: value for key, value in attrs if value is not None
        }
        if tag in {"html", "head", "body", "article", "a"}:
            self.stack.append(tag)
        self.seen_html = self.seen_html or tag == "html"
        self.seen_head = self.seen_head or tag == "head"
        self.seen_body = self.seen_body or tag == "body"
        if tag == "meta" and values.get("name", "").startswith(
            (
                "access-",
                "expected-",
                "next-",
                "rejected-",
                "terminal",
            )
        ):
            name = values.get("name")
            content = values.get("content")
            if name and content is not None:
                if name in self.meta:
                    raise ValueError(
                        "metadata HTML has duplicate control markers"
                    )
                self.meta[name] = content
        elif tag == "article":
            self.records.append(values)
        elif tag == "a" and any(
            key.startswith("data-asset-") for key in values
        ):
            self.assets.append(values)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"html", "head", "body", "article", "a"}:
            if not self.stack or self.stack.pop() != tag:
                raise ValueError("metadata HTML structure changed")


def _parsed(body: bytes) -> _MetadataHTMLParser:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("metadata page is not UTF-8") from error
    parser = _MetadataHTMLParser()
    parser.feed(text)
    parser.close()
    if (
        not parser.seen_doctype
        or not parser.seen_html
        or not parser.seen_head
        or not parser.seen_body
        or parser.stack
    ):
        raise ValueError("metadata HTML is incomplete")
    return parser


def _canonical_record_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("record lacks a stable public identifier")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "njpvideo.ggcf.kr"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path in {"", "/"}
        or not _path_is_unambiguous(parsed.path)
        or value != f"https://njpvideo.ggcf.kr{parsed.path}"
    ):
        raise ValueError("record canonical URL is outside the reviewed host")
    return value


def _path_is_unambiguous(path: str) -> bool:
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "//" not in path
        and "\\" not in path
        and "%" not in path
        and all(
            segment not in {".", ".."}
            for segment in path.split("/")
        )
    )


def _page_number(cursor: object) -> int:
    if not isinstance(cursor, str):
        raise ValueError("pagination cursor is not canonical")
    match = re.fullmatch(r"page-([0-9]{1,18})", cursor)
    if match is None:
        raise ValueError("pagination cursor is not canonical")
    page = int(match.group(1))
    canonical = f"{page:03d}" if page < 1000 else str(page)
    if page < 2 or cursor != f"page-{canonical}":
        raise ValueError("pagination cursor is not canonical")
    return page


class NJPVideoLibraryAdapter:
    """Held production adapter plus an invented-fixture conformance seam."""

    adapter_id = "njp-video-library-html"
    adapter_version = "1.0.0"
    source_id = "njp-video-library"
    endpoint_id = "njp-video-library-home"
    public_url = "https://njpvideo.ggcf.kr/"
    robots_applicability = "required"
    allowed_methods = ("GET",)
    allowed_hosts = ("njpvideo.ggcf.kr",)
    allowed_query_parameters = ("page",)
    query_parameter_contracts = {
        "page": {
            "cursor_prefix": "page-",
            "value_type": "cursor_integer",
        }
    }
    expected_mime_types = ("text/html",)
    approved_metadata_fields = (
        "duration",
        "language",
        "record_class",
        "year",
    )
    required_metadata_fields = ("language", "record_class")
    metadata_field_contracts = {
        "duration": {"value_type": "duration_iso8601"},
        "language": {
            "allowed_values": list(_LANGUAGES),
            "value_type": "enum",
        },
        "record_class": {
            "allowed_values": list(_RECORD_CLASSES),
            "value_type": "enum",
        },
        "year": {"value_type": "year"},
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    reviewed_asset_path_prefixes: tuple[str, ...] = ()

    def _require_reviewed_shape(self) -> None:
        raise SourceShapeUnreviewed(
            "current Video Library shape is unreviewed; adapter is held"
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        self._require_reviewed_shape()
        url = self.public_url
        if cursor is not None:
            page = _page_number(cursor)
            url = f"{url}?{urlencode({'page': page})}"
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=url,
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        self._require_reviewed_shape()
        state = _parsed(body).meta.get("access-state")
        if state is None:
            return None
        if state not in self.blocker_states:
            raise ValueError("unknown access state")
        return state

    def _identity_value(self, item: Mapping[str, Any]) -> str:
        item_id = item.get("id") or item.get("data-catalogue-id")
        if item_id is not None:
            if (
                not isinstance(item_id, str)
                or not _SAFE_ID.fullmatch(item_id)
            ):
                raise ValueError("record lacks a stable public identifier")
            return f"id:{item_id}"
        canonical_url = item.get("canonical_url") or item.get(
            "data-canonical-url"
        )
        return f"url:{_canonical_record_url(canonical_url)}"

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        identity = self._identity_value(item)
        digest = hashlib.sha256(
            f"{self.source_id}\0{identity}".encode()
        ).hexdigest()[:24]
        return f"{self.source_id}-{digest}"

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
    ) -> dict[str, Any]:
        self._require_reviewed_shape()
        del cursor
        page = _parsed(body)
        if page.meta.get("terminal") not in {"true", "false"}:
            raise ValueError("metadata page has no bounded terminal marker")

        records: list[dict[str, Any]] = []
        for item in page.records:
            identifiers = {
                key
                for key in (
                    "data-canonical-url",
                    "data-catalogue-id",
                )
                if key in item
            }
            if (
                len(identifiers) != 1
                or not {
                    "data-language",
                    "data-record-class",
                }.issubset(item)
                or item["data-language"] not in _LANGUAGES
                or item["data-record-class"] not in _RECORD_CLASSES
                or (
                    "data-duration" in item
                    and _DURATION.fullmatch(item["data-duration"]) is None
                )
                or (
                    "data-year" in item
                    and _YEAR.fullmatch(item["data-year"]) is None
                )
            ):
                raise ValueError("metadata record shape changed")
            identity = self._identity_value(item)
            metadata = {
                "language": item["data-language"],
                "record_class": item["data-record-class"],
            }
            if "data-duration" in item:
                metadata["duration"] = item["data-duration"]
            if "data-year" in item:
                metadata["year"] = item["data-year"]
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": hashlib.sha256(
                        f"{self.source_id}\0{identity}".encode()
                    ).hexdigest(),
                    "metadata": metadata,
                }
            )
        try:
            next_ordinal = (
                int(page.meta["next-ordinal"])
                if "next-ordinal" in page.meta
                else None
            )
            expected_total = (
                int(page.meta["expected-total"])
                if "expected-total" in page.meta
                else None
            )
            rejected_count = int(
                page.meta.get("rejected-count", "0")
            )
        except ValueError as error:
            raise ValueError("metadata counters are invalid") from error
        terminal = page.meta["terminal"] == "true"
        next_cursor = page.meta.get("next-cursor")
        if (
            rejected_count < 0
            or (
                expected_total is not None
                and (
                    expected_total < 0
                    or expected_total < len(records)
                )
            )
            or (
                terminal
                and (
                    next_cursor is not None
                    or next_ordinal is not None
                )
            )
            or (
                not terminal
                and (
                    not isinstance(next_cursor, str)
                    or not isinstance(next_ordinal, int)
                    or next_ordinal < 1
                )
            )
        ):
            raise ValueError("metadata page controls are inconsistent")
        if not terminal:
            _page_number(next_cursor)
        return {
            "records": records,
            "next_cursor": next_cursor,
            "next_ordinal": next_ordinal,
            "terminal": terminal,
            "expected_total": expected_total,
            "rejected_count": rejected_count,
        }

    def _validate_asset_url(self, locator: str) -> str:
        if not isinstance(locator, str) or not locator:
            raise ValueError(
                "asset locator is outside the reviewed boundary"
            )
        raw = urlsplit(locator)
        if raw.scheme or raw.netloc:
            if (
                raw.scheme != "https"
                or raw.netloc != "njpvideo.ggcf.kr"
                or _canonical_record_url(locator) != locator
            ):
                raise ValueError(
                    "asset locator is outside the reviewed boundary"
                )
        elif (
            locator != raw.path
            or not raw.path.startswith("/")
            or not _path_is_unambiguous(raw.path)
        ):
            raise ValueError(
                "asset locator is outside the reviewed boundary"
            )
        public_url = urljoin(self.public_url, locator)
        parsed = urlsplit(public_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "njpvideo.ggcf.kr"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not _path_is_unambiguous(parsed.path)
            or public_url != f"https://njpvideo.ggcf.kr{parsed.path}"
            or not self.reviewed_asset_path_prefixes
            or any(
                not _path_is_unambiguous(prefix)
                or not prefix.endswith("/")
                for prefix in self.reviewed_asset_path_prefixes
            )
            or not any(
                parsed.path.startswith(prefix)
                for prefix in self.reviewed_asset_path_prefixes
            )
        ):
            raise ValueError("asset locator is outside the reviewed boundary")
        return public_url

    def asset_candidates(
        self,
        body: bytes,
    ) -> tuple[VideoLibraryAssetCandidate, ...]:
        self._require_reviewed_shape()
        admitted = self.parse_page(body, cursor=None)
        admitted_record_ids = {
            item["record_id"] for item in admitted["records"]
        }
        page = _parsed(body)
        candidates: list[VideoLibraryAssetCandidate] = []
        for item in page.assets:
            asset_kind = item.get("data-asset-kind", "")
            mime_type = item.get("data-asset-mime", "")
            locator = item.get("href", "")
            relationship_keys = {
                key
                for key in (
                    "data-asset-for",
                    "data-asset-for-url",
                )
                if key in item
            }
            if len(relationship_keys) != 1:
                raise ValueError("asset candidate shape changed")
            if "data-asset-for" in item:
                record_id = item["data-asset-for"]
                if not _SAFE_ID.fullmatch(record_id):
                    raise ValueError("asset candidate shape changed")
                relationship_record_id = self.stable_record_id(
                    {"id": record_id}
                )
            else:
                relationship_record_id = self.stable_record_id(
                    {
                        "canonical_url": item[
                            "data-asset-for-url"
                        ]
                    }
                )
            if (
                relationship_record_id not in admitted_record_ids
                or asset_kind not in _ASSET_KINDS
                or not _MIME.fullmatch(mime_type)
            ):
                raise ValueError("asset candidate shape changed")
            candidates.append(
                VideoLibraryAssetCandidate(
                    source_id=self.source_id,
                    relationship_record_id=relationship_record_id,
                    asset_kind=asset_kind,
                    public_url=self._validate_asset_url(locator),
                    claimed_mime_type=mime_type,
                )
            )
        return tuple(candidates)

    def record_asset_status(
        self,
        candidate: VideoLibraryAssetCandidate,
        status: int,
    ) -> VideoLibraryAssetCandidate:
        self._require_reviewed_shape()
        blockers = {
            401: "login_required",
            403: "access_forbidden",
            429: "rate_limited",
        }
        blocker = blockers.get(status)
        if (
            not isinstance(candidate, VideoLibraryAssetCandidate)
            or candidate.source_id != self.source_id
            or candidate.asset_kind not in _ASSET_KINDS
            or not re.fullmatch(
                r"njp-video-library-[0-9a-f]{24}",
                candidate.relationship_record_id,
            )
            or not _MIME.fullmatch(candidate.claimed_mime_type)
            or candidate.rights_state != "pending"
            or candidate.acquisition_eligible
            or candidate.retry_allowed
            or blocker is None
        ):
            raise ValueError(
                "only an exact denied candidate observation is supported"
            )
        self._validate_asset_url(candidate.public_url)
        return replace(
            candidate,
            rights_state="blocked",
            acquisition_eligible=False,
            access_blocker=blocker,
            retry_allowed=False,
        )
