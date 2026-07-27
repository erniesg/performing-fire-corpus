from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

from .adapter_conformance import MetadataRequest


_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_BLOCKERS = (
    "access_forbidden",
    "login_required",
    "rate_limited",
    "subscription_required",
)
_MEDIA_OBJECT_PATH = re.compile(r"/mediaObjects/([1-9][0-9]{0,17})")
_PAGE_CURSOR = re.compile(r"page-([1-9][0-9]{0,3})")
_VIDEO_ARCHIVE_PDF_COUNT = 8


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


class SourceShapeUnreviewed(RuntimeError):
    pass


class _MetadataHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.records: list[dict[str, str]] = []
        self.attachments: list[dict[str, str]] = []
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
        values = {key: value for key, value in attrs if value is not None}
        if tag in {"html", "head", "body", "article", "a"}:
            self.stack.append(tag)
        self.seen_html = self.seen_html or tag == "html"
        self.seen_head = self.seen_head or tag == "head"
        self.seen_body = self.seen_body or tag == "body"
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

    def handle_endtag(self, tag: str) -> None:
        if tag in {"html", "head", "body", "article", "a"}:
            if not self.stack or self.stack.pop() != tag:
                raise ValueError("metadata HTML structure changed")


class _MediaObjectsFragmentParser(HTMLParser):
    """Extract only the reviewed factual projection from one live fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, str]] = []
        self._active: dict[str, str] | None = None
        self._title_parts: list[str] = []
        self._saw_wrapper = False
        self._saw_item_container = False
        self._nonblank_text = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key: value for key, value in attrs if value is not None}
        self._saw_wrapper = self._saw_wrapper or tag == "ul"
        self._saw_item_container = self._saw_item_container or tag == "li"
        if self._active is not None:
            raise ValueError("mediaObjects item anchor shape changed")
        if tag != "a":
            return
        href = values.get("href", "")
        if href.startswith("/mediaObjects/"):
            match = _MEDIA_OBJECT_PATH.fullmatch(href)
            if match is None:
                raise ValueError("mediaObjects item identifier changed")
            self._active = {"id": match.group(1), "href": href}
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._nonblank_text = True
        if self._active is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._active is None or tag != "a":
            return
        title = " ".join("".join(self._title_parts).split())
        if (
            not title
            or len(title) > 512
            or any(ord(character) < 32 for character in title)
        ):
            raise ValueError("mediaObjects item title changed")
        record = dict(self._active)
        record["title"] = title
        self.records.append(record)
        self._active = None
        self._title_parts = []

    def finish(self) -> None:
        if self._active is not None or not self._saw_wrapper:
            raise ValueError("mediaObjects fragment structure changed")
        if not self.records and (self._saw_item_container or self._nonblank_text):
            raise ValueError("mediaObjects anchor pattern is absent")
        if len(self.records) > 8:
            raise ValueError("mediaObjects page exceeds the reviewed bound")
        identifiers = [record["id"] for record in self.records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("mediaObjects page repeats an identifier")


def _video_archive_document_url(value: str) -> str | None:
    try:
        absolute = urlsplit(
            urljoin(NJPCenterVideoArchiveAdapter.public_url, value)
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Video Archive document URL is invalid") from error
    decoded_path = unquote(absolute.path)
    if not absolute.path.startswith("/storage/upload/"):
        return None
    try:
        unsafe = (
            absolute.scheme != "https"
            or absolute.hostname != "njp.ggcf.kr"
            or absolute.port not in {None, 443}
            or absolute.username is not None
            or absolute.password is not None
            or absolute.query
            or absolute.fragment
            or "//" in absolute.path
            or "//" in decoded_path
            or "\\" in decoded_path
            or "%" in decoded_path
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in decoded_path
            )
            or not decoded_path.startswith("/storage/upload/")
            or any(
                segment in {".", ".."}
                for segment in decoded_path.split("/")
            )
            or not decoded_path.lower().endswith(".pdf")
        )
    except ValueError as error:
        raise ValueError("Video Archive document URL is invalid") from error
    if unsafe:
        raise ValueError("Video Archive document URL left the reviewed shape")
    canonical_path = quote(
        decoded_path,
        safe="/!$&'()*+,-.:;=@_~",
    )
    return f"https://njp.ggcf.kr{canonical_path}"


class _VideoArchivePageParser(HTMLParser):
    """Extract the eight reviewed public PDF catalogue entries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, str]] = []
        self.seen_doctype = False
        self.seen_html = False
        self.seen_head = False
        self.seen_body = False
        self._active_url: str | None = None
        self._title_parts: list[str] = []
        self._paragraph_depth = 0

    def handle_decl(self, decl: str) -> None:
        self.seen_doctype = decl.lower() == "doctype html"

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.seen_html = self.seen_html or tag == "html"
        self.seen_head = self.seen_head or tag == "head"
        self.seen_body = self.seen_body or tag == "body"
        if tag == "p":
            self._paragraph_depth += 1
        if self._active_url is not None and tag != "a":
            raise ValueError("Video Archive document title markup changed")
        if tag != "a":
            return
        if self._active_url is not None:
            raise ValueError("Video Archive document anchors are nested")
        names = [name.lower() for name, _value in attrs]
        values = {
            name.lower(): value
            for name, value in attrs
        }
        if len(names) != len(set(names)):
            raise ValueError("Video Archive document attributes repeat")
        href = values.get("href")
        if not isinstance(href, str):
            return
        document_url = _video_archive_document_url(href)
        if document_url is not None:
            if (
                self._paragraph_depth < 1
                or len(names) != 4
                or not {"class", "href", "rel"}.issubset(names)
                or not all(
                    isinstance(values.get(name), str)
                    for name in ("class", "href", "rel")
                )
            ):
                raise ValueError(
                    "Video Archive document anchor shape changed"
                )
            self._active_url = document_url
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_url is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._paragraph_depth = max(0, self._paragraph_depth - 1)
        if tag != "a" or self._active_url is None:
            return
        title = " ".join("".join(self._title_parts).split())
        if (
            not title
            or len(title) > 512
            or any(ord(character) < 32 for character in title)
        ):
            raise ValueError("Video Archive document title changed")
        self.records.append(
            {
                "canonical_detail_url": self._active_url,
                "title": title,
            }
        )
        self._active_url = None
        self._title_parts = []

    def finish(self) -> None:
        if (
            not self.seen_doctype
            or not self.seen_html
            or not self.seen_head
            or not self.seen_body
            or self._active_url is not None
            or len(self.records) != _VIDEO_ARCHIVE_PDF_COUNT
        ):
            raise ValueError("Video Archive page structure changed")
        urls = [record["canonical_detail_url"] for record in self.records]
        if len(urls) != len(set(urls)):
            raise ValueError("Video Archive page repeats a document")


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
            "allowed_values": [
                "language_bilingual",
                "language_en",
                "language_ko",
                "language_unknown",
            ],
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

    def _require_reviewed_shape(self) -> None:
        raise SourceShapeUnreviewed(
            "current source shape is unreviewed; metadata adapter is held"
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        self._require_reviewed_shape()
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
        self._require_reviewed_shape()
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
        self._require_reviewed_shape()
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
            metadata = {
                "classification": item["data-classification"],
                "language": item["data-language"],
                "record_type": item["data-record-type"],
            }
            if "data-year" in item:
                metadata["year"] = item["data-year"]
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": hashlib.sha256(
                        f"{self.source_id}\0{item['data-record-id']}".encode()
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
        self._require_reviewed_shape()
        page = _parsed(body)
        record_ids = {
            item.get("data-record-id", "")
            for item in page.records
        }
        candidates: list[AttachmentCandidate] = []
        for item in page.attachments:
            record_id = item.get("data-attachment-record", "")
            mime_type = item.get("data-attachment-mime", "")
            locator = item.get("href", "")
            if (
                not _SAFE_ID.fullmatch(record_id)
                or record_id not in record_ids
                or not _MIME.fullmatch(mime_type)
            ):
                raise ValueError("attachment candidate shape changed")
            public_url = urljoin(self.public_url, locator)
            parsed = urlsplit(public_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "njp.ggcf.kr"
                or parsed.port not in {None, 443}
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
        self._require_reviewed_shape()
        parsed = urlsplit(candidate.public_url)
        if (
            candidate.source_id != self.source_id
            or status != 403
            or parsed.scheme != "https"
            or parsed.hostname != "njp.ggcf.kr"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/storage/upload/")
        ):
            raise ValueError("only an exact 403 attachment observation is supported")
        return replace(
            candidate,
            rights_state="blocked",
            acquisition_eligible=False,
            access_blocker="access_forbidden",
            retry_allowed=False,
        )


class NJPCenterMainAdapter(_BaseNJPCenterAdapter):
    adapter_id = "njp-center-mediaobjects-fragment-html"
    adapter_version = "2.0.0"
    source_id = "njp-center-main"
    endpoint_id = "njp-center-main-home"
    public_url = "https://njp.ggcf.kr/mediaObjects/more"
    reviewed_shape_sha256 = (
        "c5ddca73ddd4d4e2710320794e5c120ab32d3d7ad77916f1d1c7743481a384b5"
    )
    query_parameter_contracts = {
        "page": {
            "cursor_prefix": "page-",
            "first_value": "1",
            "value_type": "cursor_integer",
        }
    }
    approved_metadata_fields = (
        "canonical_detail_url",
        "public_identifier",
        "title",
    )
    required_metadata_fields = approved_metadata_fields
    metadata_field_contracts = {
        "canonical_detail_url": {"value_type": "public_url"},
        "public_identifier": {"value_type": "positive_integer_string"},
        "title": {"max_length": 512, "value_type": "bounded_text"},
    }

    def _require_reviewed_shape(self) -> None:
        return None

    @staticmethod
    def _page_number(cursor: str | None) -> int:
        if cursor is None:
            return 1
        match = _PAGE_CURSOR.fullmatch(cursor)
        if match is None:
            raise ValueError("invalid mediaObjects page cursor")
        return int(match.group(1))

    def build_request(self, cursor: str | None) -> MetadataRequest:
        page = self._page_number(cursor)
        url = f"{self.public_url}?{urlencode({'page': page})}"
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "njp.ggcf.kr"
            or parsed.path != "/mediaObjects/more"
            or parsed.query != f"page={page}"
            or parsed.fragment
        ):
            raise ValueError("mediaObjects request is outside the reviewed boundary")
        return MetadataRequest(self.endpoint_id, "GET", url)

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        identifier = item.get("id") or item.get("public_identifier")
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[1-9][0-9]{0,17}", identifier) is None
        ):
            raise ValueError("record lacks a stable mediaObjects identifier")
        digest = hashlib.sha256(
            f"{self.source_id}\0mediaObjects:{identifier}".encode()
        ).hexdigest()[:24]
        return f"{self.source_id}-{digest}"

    def detect_access_blocker(self, body: bytes) -> str | None:
        del body
        return None

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
    ) -> dict[str, Any]:
        current_page = self._page_number(cursor)
        try:
            text = body.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise ValueError("mediaObjects fragment is not UTF-8") from error
        parser = _MediaObjectsFragmentParser()
        parser.feed(text)
        parser.close()
        parser.finish()
        records = []
        for item in parser.records:
            identifier = item["id"]
            detail_url = f"https://njp.ggcf.kr/mediaObjects/{identifier}"
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": hashlib.sha256(
                        f"{self.source_id}\0mediaObjects:{identifier}".encode()
                    ).hexdigest(),
                    "metadata": {
                        "canonical_detail_url": detail_url,
                        "public_identifier": identifier,
                        "title": item["title"],
                    },
                }
            )
        terminal = not records
        return {
            "records": records,
            "next_cursor": None if terminal else f"page-{current_page + 1}",
            "next_ordinal": None if terminal else current_page,
            "terminal": terminal,
            "expected_total": None,
            "rejected_count": 0,
        }


class NJPCenterVideoArchiveAdapter(_BaseNJPCenterAdapter):
    adapter_id = "njp-center-video-archive-html"
    adapter_version = "2.0.0"
    source_id = "njp-center-video-archive"
    endpoint_id = "njp-center-video-archive-page"
    public_url = "https://njp.ggcf.kr/pages/videoarchive"
    reviewed_shape_sha256 = (
        "e6f9a2911a325fb321202b5994b257ec50ae48bf91a60553f64e38cc33e8851b"
    )
    allowed_query_parameters = ()
    query_parameter_contracts: Mapping[str, Mapping[str, Any]] = {}
    approved_metadata_fields = ("canonical_detail_url", "title")
    required_metadata_fields = approved_metadata_fields
    metadata_field_contracts = {
        "canonical_detail_url": {"value_type": "public_url"},
        "title": {"max_length": 512, "value_type": "bounded_text"},
    }

    def _require_reviewed_shape(self) -> None:
        return None

    def build_request(self, cursor: str | None) -> MetadataRequest:
        if cursor is not None:
            raise ValueError("Video Archive page has no pagination cursor")
        return MetadataRequest(self.endpoint_id, "GET", self.public_url)

    def detect_access_blocker(self, body: bytes) -> str | None:
        del body
        return None

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        value = item.get("canonical_detail_url") or item.get("href")
        if not isinstance(value, str):
            raise ValueError("Video Archive record lacks its public URL")
        canonical = _video_archive_document_url(value)
        if canonical is None:
            raise ValueError("Video Archive record URL is outside the reviewed shape")
        digest = hashlib.sha256(
            f"{self.source_id}\0document:{canonical}".encode()
        ).hexdigest()
        return f"{self.source_id}-{digest[:24]}"

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
    ) -> dict[str, Any]:
        if cursor is not None:
            raise ValueError("Video Archive page has no pagination cursor")
        try:
            text = body.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise ValueError("Video Archive page is not UTF-8") from error
        parser = _VideoArchivePageParser()
        parser.feed(text)
        parser.close()
        parser.finish()
        records = []
        for item in parser.records:
            canonical = item["canonical_detail_url"]
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": hashlib.sha256(
                        f"{self.source_id}\0document:{canonical}".encode()
                    ).hexdigest(),
                    "metadata": dict(item),
                }
            )
        return {
            "records": records,
            "next_cursor": None,
            "next_ordinal": None,
            "terminal": True,
            "expected_total": _VIDEO_ARCHIVE_PDF_COUNT,
            "rejected_count": 0,
        }

    def attachment_candidates(self, body: bytes) -> tuple[AttachmentCandidate, ...]:
        del body
        return ()

    def record_attachment_status(
        self,
        candidate: AttachmentCandidate,
        status: int,
    ) -> AttachmentCandidate:
        del candidate, status
        raise ValueError("Video Archive attachment requests are not reviewed")
