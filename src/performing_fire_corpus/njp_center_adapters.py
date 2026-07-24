from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

from .adapter_conformance import MetadataRequest


_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_BLOCKERS = (
    "access_forbidden",
    "login_required",
    "rate_limited",
    "subscription_required",
)


@dataclass(frozen=True)
class AttachmentCandidate:
    source_id: str
    relationship_record_id: str
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
        self.attachments: list[dict[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key: value for key, value in attrs if value is not None}
        if tag == "meta" and values.get("name", "").startswith(
            ("terminal", "next-", "expected-", "rejected-", "access-")
        ):
            name = values.get("name")
            content = values.get("content")
            if name and content is not None:
                self.meta[name] = content
        elif tag == "article" and "data-record-id" in values:
            self.records.append(values)
        elif tag == "a" and "data-attachment-record" in values:
            self.attachments.append(values)


def _parsed(body: bytes) -> _MetadataHTMLParser:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("metadata page is not UTF-8") from error
    parser = _MetadataHTMLParser()
    parser.feed(text)
    parser.close()
    return parser


class _BaseNJPCenterAdapter:
    adapter_version = "1.0.0"
    robots_applicability = "required"
    allowed_methods = ("GET",)
    allowed_hosts = ("njp.ggcf.kr",)
    allowed_query_parameters = ("page",)
    query_parameter_contracts = {
        "page": {"cursor_prefix": "page-", "value_type": "cursor_integer"}
    }
    expected_mime_types = ("text/html",)
    approved_metadata_fields = (
        "classification",
        "language",
        "record_type",
        "year",
    )
    required_metadata_fields = (
        "classification",
        "language",
        "record_type",
    )
    metadata_field_contracts = {
        "classification": {
            "allowed_values": [
                "classification_archive",
                "classification_collection",
                "classification_learning",
                "classification_public_programme",
            ],
            "value_type": "enum",
        },
        "language": {
            "allowed_values": ["language_en", "language_ko", "language_unknown"],
            "value_type": "enum",
        },
        "record_type": {
            "allowed_values": [
                "record_type_archive_record",
                "record_type_exhibition",
                "record_type_programme",
                "record_type_publication",
            ],
            "value_type": "enum",
        },
        "year": {"value_type": "year"},
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    public_url: str

    def build_request(self, cursor: str | None) -> MetadataRequest:
        url = self.public_url
        if cursor is not None:
            page = int(cursor.removeprefix("page-"))
            url = f"{url}?{urlencode({'page': page})}"
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=url,
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        state = _parsed(body).meta.get("access-state")
        if state is None:
            return None
        if state not in self.blocker_states:
            raise ValueError("unknown access state")
        return state

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        item_id = item.get("id") or item.get("data-record-id")
        if not isinstance(item_id, str) or not _SAFE_ID.fullmatch(item_id):
            raise ValueError("record lacks a stable public identifier")
        digest = hashlib.sha256(
            f"{self.source_id}\0{item_id}".encode()
        ).hexdigest()[:24]
        return f"{self.source_id}-{digest}"

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
    ) -> dict[str, Any]:
        del cursor
        page = _parsed(body)
        if page.meta.get("terminal") not in {"true", "false"}:
            raise ValueError("metadata page has no bounded terminal marker")
        records: list[dict[str, Any]] = []
        required_attributes = {
            "data-classification",
            "data-language",
            "data-record-id",
            "data-record-type",
        }
        for item in page.records:
            if not required_attributes.issubset(item):
                raise ValueError("metadata record shape changed")
            year = item.get("data-year", "unknown")
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": (
                        f"{self.source_id}-record-{item['data-record-id']}"
                    ),
                    "metadata": {
                        "classification": item["data-classification"],
                        "language": item["data-language"],
                        "record_type": item["data-record-type"],
                        "year": year,
                    },
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
            rejected_count = int(page.meta.get("rejected-count", "0"))
        except ValueError as error:
            raise ValueError("metadata counters are invalid") from error
        return {
            "records": records,
            "next_cursor": page.meta.get("next-cursor"),
            "next_ordinal": next_ordinal,
            "terminal": page.meta["terminal"] == "true",
            "expected_total": expected_total,
            "rejected_count": rejected_count,
        }

    def attachment_candidates(self, body: bytes) -> tuple[AttachmentCandidate, ...]:
        candidates: list[AttachmentCandidate] = []
        for item in _parsed(body).attachments:
            record_id = item.get("data-attachment-record", "")
            mime_type = item.get("data-attachment-mime", "")
            locator = item.get("href", "")
            if not _SAFE_ID.fullmatch(record_id) or not _MIME.fullmatch(mime_type):
                raise ValueError("attachment candidate shape changed")
            public_url = urljoin(self.public_url, locator)
            parsed = urlsplit(public_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "njp.ggcf.kr"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/storage/upload/")
            ):
                raise ValueError("attachment locator is outside the reviewed boundary")
            candidates.append(
                AttachmentCandidate(
                    source_id=self.source_id,
                    relationship_record_id=self.stable_record_id(
                        {"id": record_id}
                    ),
                    public_url=public_url,
                    claimed_mime_type=mime_type,
                )
            )
        return tuple(candidates)

    def record_attachment_status(
        self,
        candidate: AttachmentCandidate,
        status: int,
    ) -> AttachmentCandidate:
        if candidate.source_id != self.source_id or status != 403:
            raise ValueError("only an exact 403 attachment observation is supported")
        return replace(candidate, access_blocker="access_forbidden")


class NJPCenterMainAdapter(_BaseNJPCenterAdapter):
    adapter_id = "njp-center-main-html"
    source_id = "njp-center-main"
    endpoint_id = "njp-center-main-home"
    public_url = "https://njp.ggcf.kr/"


class NJPCenterVideoArchiveAdapter(_BaseNJPCenterAdapter):
    adapter_id = "njp-center-video-archive-html"
    source_id = "njp-center-video-archive"
    endpoint_id = "njp-center-video-archive-page"
    public_url = "https://njp.ggcf.kr/pages/videoarchive"
