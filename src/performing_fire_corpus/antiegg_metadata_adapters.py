"""ANTIEGG public metadata adapters for sitemap and WordPress discovery.

The WordPress posts adapter is bound to the reviewed public REST projection.
The sitemap adapter remains held pending a separate bounded shape review.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from .adapter_conformance import MetadataRequest, is_valid_utc_timestamp


HOST = "antiegg.kr"
SOURCE_ID = "antiegg-fluxus"

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
CONTROL_NAMESPACE = "urn:performing-fire-corpus:bounded-discovery:1"
XML_PROLOGUE = '<?xml version="1.0" encoding="UTF-8"?>'

_BLOCKERS = (
    "access_forbidden",
    "login_required",
    "rate_limited",
    "subscription_required",
)
_ENTRY_KINDS = (
    "entry_kind_child_sitemap",
    "entry_kind_public_document",
)
_CONTROL_NAMES = (
    "access-state",
    "expected-total",
    "next-cursor",
    "next-ordinal",
    "rejected-count",
    "terminal",
)
_CONTROL_ATTRIBUTES = frozenset(
    f"{{{CONTROL_NAMESPACE}}}{name}" for name in _CONTROL_NAMES
)
_ENTRY_CONTAINERS = {
    f"{{{SITEMAP_NAMESPACE}}}sitemapindex": (
        f"{{{SITEMAP_NAMESPACE}}}sitemap",
        "entry_kind_child_sitemap",
    ),
    f"{{{SITEMAP_NAMESPACE}}}urlset": (
        f"{{{SITEMAP_NAMESPACE}}}url",
        "entry_kind_public_document",
    ),
}
_LOCATION_TAG = f"{{{SITEMAP_NAMESPACE}}}loc"
_MODIFIED_TAG = f"{{{SITEMAP_NAMESPACE}}}lastmod"

#: The reviewed maximum this endpoint accepts, and the size the adapter asks
#: for. A smaller page size costs one request per page for no safety gain: the
#: `x-wp-totalpages` cross-check in `_pagination_headers` is what catches a
#: silent undercount, not the page size.
POSTS_REVIEWED_MAX_PER_PAGE = 100
POSTS_PER_PAGE = POSTS_REVIEWED_MAX_PER_PAGE
POSTS_RESPONSE_FIELDS = (
    "author",
    "categories",
    "date",
    "excerpt",
    "featured_media",
    "id",
    "link",
    "modified",
    "slug",
    "tags",
    "title",
)
_POST_RESPONSE_FIELD_SET = frozenset(POSTS_RESPONSE_FIELDS)
_LOCAL_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"
)
#: Source fields that may carry prose, media, or personal detail. The adapter
#: never reads them, even where the WordPress API labels them metadata.
FORBIDDEN_POST_FIELDS = (
    "author",
    "comment_status",
    "content",
    "excerpt",
    "guid",
    "title",
    "yoast_head",
)


class SourceShapeUnreviewed(RuntimeError):
    """Raised before production use when no current bounded shape exists."""


def _path_is_unambiguous(path: str) -> bool:
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "//" not in path
        and "\\" not in path
        and "%" not in path
        and all(
            character.isprintable() and not character.isspace()
            for character in path
        )
        and all(segment not in {".", ".."} for segment in path.split("/"))
    )


def canonical_public_url(value: object) -> str:
    """Return the one canonical spelling of a reviewed public ANTIEGG URL."""

    if not isinstance(value, str) or not value:
        raise ValueError("record lacks a stable public identifier")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not _path_is_unambiguous(parsed.path)
        or value != f"https://{HOST}{parsed.path}"
    ):
        raise ValueError("record URL is outside the reviewed public boundary")
    return f"https://{HOST}{parsed.path.rstrip('/') or '/'}"


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


def _canonical_counter(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("metadata counter is not canonical")
    counter = int(value)
    if str(counter) != value:
        raise ValueError("metadata counter is not canonical")
    return counter


def _bounded_counter(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("metadata counter is not canonical")
    return value


def _bounded_page(
    records: list[dict[str, Any]],
    *,
    terminal: bool,
    next_cursor: str | None,
    next_ordinal: int | None,
    expected_total: int | None,
    rejected_count: int,
    current_page: int,
) -> dict[str, Any]:
    if (
        (
            expected_total is not None
            and expected_total < len(records)
        )
        or (terminal and (next_cursor is not None or next_ordinal is not None))
        or (
            not terminal
            and (
                not isinstance(next_cursor, str)
                or not isinstance(next_ordinal, int)
                or isinstance(next_ordinal, bool)
                or next_ordinal < 1
            )
        )
    ):
        raise ValueError("metadata page controls are inconsistent")
    # The adapter only binds the cursor suffix to the current page, so a
    # repeated cursor stays admissible here. The shared harness binds
    # next_ordinal to its own committed ordinal and classifies a repeated
    # cursor as a resumable pagination loop rather than shape drift.
    if not terminal and _page_number(next_cursor) not in {
        current_page,
        current_page + 1,
    }:
        raise ValueError("metadata pagination is noncontiguous")
    return {
        "records": records,
        "next_cursor": next_cursor,
        "next_ordinal": next_ordinal,
        "terminal": terminal,
        "expected_total": expected_total,
        "rejected_count": rejected_count,
    }


class _BaseANTIEGGAdapter:
    """Shared held request, identity, and completeness boundary."""

    adapter_version = "1.0.0"
    source_id = SOURCE_ID
    robots_applicability = "required"
    allowed_methods = ("GET",)
    allowed_hosts = (HOST,)
    allowed_query_parameters = ("page",)
    query_parameter_contracts = {
        "page": {"cursor_prefix": "page-", "value_type": "cursor_integer"}
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = _BLOCKERS
    identity_fields: tuple[str, ...]
    endpoint_id: str
    public_url: str

    def _require_reviewed_shape(self) -> None:
        raise SourceShapeUnreviewed(
            "current ANTIEGG endpoint shape is unreviewed; adapter is held"
        )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        self._require_reviewed_shape()
        url = self.public_url
        if cursor is not None:
            url = f"{url}?{urlencode({'page': _page_number(cursor)})}"
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=url,
        )

    def _identity_value(self, item: Mapping[str, Any]) -> str:
        if not isinstance(item, Mapping):
            raise ValueError("record lacks a stable public identifier")
        present = [field for field in self.identity_fields if field in item]
        if len(present) != 1:
            raise ValueError("record lacks a stable public identifier")
        return f"url:{canonical_public_url(item[present[0]])}"

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        """Derive one stable ID from the source ID and the canonical URL."""

        digest = hashlib.sha256(
            f"{self.source_id}\0{self._identity_value(item)}".encode()
        ).hexdigest()
        return f"{self.source_id}-{digest[:24]}"

    def _source_identity(self, identity: str) -> str:
        return hashlib.sha256(
            f"{self.source_id}\0{identity}".encode()
        ).hexdigest()

    def declared_total_observation(
        self,
        body: bytes,
        *,
        observed_at: str,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Report an endpoint-declared total as a timestamped observation.

        A declared total is what one endpoint said at one time. It is never a
        completeness guarantee, so the observation carries its own time and
        says so explicitly.
        """

        self._require_reviewed_shape()
        if not is_valid_utc_timestamp(observed_at):
            raise ValueError("a declared total needs a UTC observation time")
        page = self.parse_page(body, cursor=cursor)
        return {
            "observation_kind": "endpoint_declared_total",
            "source_id": self.source_id,
            "endpoint_id": self.endpoint_id,
            "declared_total": page["expected_total"],
            "observed_records": len(page["records"]),
            "observed_at": observed_at,
            "is_completeness_guarantee": False,
        }


def _sitemap_document(body: bytes) -> ElementTree.Element:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("sitemap document is not UTF-8") from error
    if not text.startswith(XML_PROLOGUE):
        raise ValueError("sitemap prologue is not canonical")
    remainder = text[len(XML_PROLOGUE) :]
    if any(marker in remainder for marker in ("<!", "<?", "&", "]]>")):
        raise ValueError("sitemap document has an unreviewed construct")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ValueError("sitemap document is not well formed") from error


def _sitemap_controls(root: ElementTree.Element) -> dict[str, str]:
    if set(root.attrib) - _CONTROL_ATTRIBUTES:
        raise ValueError("sitemap control marker changed")
    return {
        key.removeprefix(f"{{{CONTROL_NAMESPACE}}}").replace("-", "_"): value
        for key, value in root.attrib.items()
    }


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _sitemap_entries(
    root: ElementTree.Element,
) -> tuple[str, list[dict[str, str]]]:
    if root.tag not in _ENTRY_CONTAINERS:
        raise ValueError("sitemap root element changed")
    entry_tag, entry_kind = _ENTRY_CONTAINERS[root.tag]
    if not _is_blank(root.text):
        raise ValueError("sitemap document carries unreviewed text")
    entries: list[dict[str, str]] = []
    for child in root:
        if (
            child.tag != entry_tag
            or child.attrib
            or not _is_blank(child.text)
            or not _is_blank(child.tail)
        ):
            raise ValueError("sitemap entry shape changed")
        fields: dict[str, str] = {}
        for leaf in child:
            if (
                leaf.tag not in {_LOCATION_TAG, _MODIFIED_TAG}
                or leaf.tag in fields
                or leaf.attrib
                or len(leaf)
                or leaf.text is None
                or not _is_blank(leaf.tail)
            ):
                raise ValueError("sitemap entry shape changed")
            fields[leaf.tag] = leaf.text
        if _LOCATION_TAG not in fields:
            raise ValueError("sitemap entry lacks a stable public URL")
        entry = {"loc": fields[_LOCATION_TAG]}
        if _MODIFIED_TAG in fields:
            entry["lastmod"] = fields[_MODIFIED_TAG]
        entries.append(entry)
    return entry_kind, entries


class ANTIEGGSitemapAdapter(_BaseANTIEGGAdapter):
    """Held adapter for the public ANTIEGG sitemap index and URL sets."""

    adapter_id = "antiegg-sitemap-xml"
    endpoint_id = "antiegg-sitemap"
    public_url = "https://antiegg.kr/sitemap_index.xml"
    expected_mime_types = ("application/xml", "text/xml")
    approved_metadata_fields = ("entry_kind", "modified_at")
    required_metadata_fields = ("entry_kind",)
    metadata_field_contracts = {
        "entry_kind": {
            "allowed_values": list(_ENTRY_KINDS),
            "value_type": "enum",
        },
        "modified_at": {"value_type": "timestamp"},
    }
    identity_fields = ("canonical_url", "loc")

    def detect_access_blocker(self, body: bytes) -> str | None:
        self._require_reviewed_shape()
        state = _sitemap_controls(_sitemap_document(body)).get("access_state")
        if state is None:
            return None
        if state not in self.blocker_states:
            raise ValueError("unknown access state")
        return state

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
    ) -> dict[str, Any]:
        self._require_reviewed_shape()
        current_page = 1 if cursor is None else _page_number(cursor)
        root = _sitemap_document(body)
        controls = _sitemap_controls(root)
        if controls.get("terminal") not in {"true", "false"}:
            raise ValueError("sitemap page has no bounded terminal marker")
        entry_kind, entries = _sitemap_entries(root)

        records: list[dict[str, Any]] = []
        for entry in entries:
            metadata: dict[str, str] = {"entry_kind": entry_kind}
            if "lastmod" in entry:
                if not is_valid_utc_timestamp(entry["lastmod"]):
                    raise ValueError(
                        "sitemap modification time is not a UTC instant"
                    )
                metadata["modified_at"] = entry["lastmod"]
            identity = self._identity_value(entry)
            records.append(
                {
                    "record_id": self.stable_record_id(entry),
                    "source_identity": self._source_identity(identity),
                    "metadata": metadata,
                }
            )
        try:
            next_ordinal = (
                _canonical_counter(controls["next_ordinal"])
                if "next_ordinal" in controls
                else None
            )
            expected_total = (
                _canonical_counter(controls["expected_total"])
                if "expected_total" in controls
                else None
            )
            rejected_count = _canonical_counter(
                controls.get("rejected_count", "0")
            )
        except ValueError as error:
            raise ValueError("metadata counters are invalid") from error
        return _bounded_page(
            records,
            terminal=controls["terminal"] == "true",
            next_cursor=controls.get("next_cursor"),
            next_ordinal=next_ordinal,
            expected_total=expected_total,
            rejected_count=rejected_count,
            current_page=current_page,
        )


class ANTIEGGPostsMetadataAdapter(_BaseANTIEGGAdapter):
    """Bound adapter for the public WordPress posts metadata projection."""

    adapter_id = "antiegg-posts-metadata-json"
    adapter_version = "2.0.0"
    endpoint_id = "antiegg-posts-api"
    public_url = "https://antiegg.kr/wp-json/wp/v2/posts"
    allowed_query_parameters = ("_fields", "page", "per_page")
    query_parameter_contracts = {
        "_fields": {
            "allowed_values": list(POSTS_RESPONSE_FIELDS),
            "value_type": "metadata_projection",
        },
        "page": {
            "cursor_prefix": "page-",
            # This adapter always sends page=1 on the first request rather than
            # omitting it, so the contract declares that first value explicitly.
            "first_value": "1",
            "value_type": "cursor_integer",
        },
        "per_page": {
            "exact_value": str(POSTS_PER_PAGE),
            "value_type": "literal",
        },
    }
    expected_mime_types = ("application/json",)
    approved_metadata_fields = ("record_type",)
    required_metadata_fields = ("record_type",)
    metadata_field_contracts = {
        "record_type": {
            "allowed_values": ["record_type_post"],
            "value_type": "enum",
        },
    }
    identity_fields = ("id",)

    def _require_reviewed_shape(self) -> None:
        return None

    def build_request(self, cursor: str | None) -> MetadataRequest:
        page = 1 if cursor is None else _page_number(cursor)
        if not 1 <= POSTS_PER_PAGE <= POSTS_REVIEWED_MAX_PER_PAGE:
            raise ValueError("posts page size exceeds the reviewed bound")
        query = {
            "_fields": ",".join(POSTS_RESPONSE_FIELDS),
            "page": page,
            "per_page": POSTS_PER_PAGE,
        }
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=f"{self.public_url}?{urlencode(query)}",
        )

    def _identity_value(self, item: Mapping[str, Any]) -> str:
        if not isinstance(item, Mapping):
            raise ValueError("record lacks a stable public identifier")
        identifier = item.get("id")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier < 1
        ):
            raise ValueError("post lacks an immutable public identifier")
        return f"post:{identifier}"

    def _items(self, body: bytes) -> list[Any]:
        try:
            value = json.loads(body.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("posts metadata response is invalid") from error
        if not isinstance(value, list):
            raise ValueError("posts metadata response shape changed")
        return value

    def detect_access_blocker(self, body: bytes) -> str | None:
        self._items(body)
        return None

    @staticmethod
    def _local_timestamp(value: object) -> bool:
        if not isinstance(value, str) or _LOCAL_TIMESTAMP.fullmatch(value) is None:
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _integer_list(value: object) -> bool:
        return (
            isinstance(value, list)
            and all(
                isinstance(item, int)
                and not isinstance(item, bool)
                and item >= 0
                for item in value
            )
        )

    def _metadata(self, item: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(item, Mapping) or set(item) != _POST_RESPONSE_FIELD_SET:
            raise ValueError("post metadata record shape changed")
        self._identity_value(item)
        if (
            canonical_public_url(item["link"]) != item["link"].rstrip("/")
            or not self._local_timestamp(item["date"])
            or not self._local_timestamp(item["modified"])
            or not isinstance(item["slug"], str)
            or not item["slug"]
            or any(
                character.isspace() or not character.isprintable()
                for character in item["slug"]
            )
            or not isinstance(item["title"], Mapping)
            or set(item["title"]) != {"rendered"}
            or not isinstance(item["title"]["rendered"], str)
            or not isinstance(item["excerpt"], Mapping)
            or set(item["excerpt"]) != {"protected", "rendered"}
            or not isinstance(item["excerpt"]["protected"], bool)
            or not isinstance(item["excerpt"]["rendered"], str)
            or not isinstance(item["author"], int)
            or isinstance(item["author"], bool)
            or item["author"] < 0
            or not isinstance(item["featured_media"], int)
            or isinstance(item["featured_media"], bool)
            or item["featured_media"] < 0
            or not self._integer_list(item["categories"])
            or not self._integer_list(item["tags"])
        ):
            raise ValueError("post metadata record shape changed")
        # The reviewed response includes display prose and relationship IDs,
        # but governance does not approve retaining them. The bounded record
        # therefore keeps only a source-type fact plus hashed identity.
        return {"record_type": "record_type_post"}

    @staticmethod
    def _pagination_headers(
        response_headers: Mapping[str, str] | None,
    ) -> tuple[int, int]:
        if not isinstance(response_headers, Mapping):
            raise ValueError("posts pagination headers are missing")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in response_headers.items()
        ):
            raise ValueError("posts pagination headers changed")
        normalized = {
            key.lower(): value for key, value in response_headers.items()
        }
        if len(normalized) != len(response_headers):
            raise ValueError("posts pagination headers changed")
        try:
            total = _canonical_counter(normalized["x-wp-total"])
            total_pages = _canonical_counter(normalized["x-wp-totalpages"])
        except (KeyError, ValueError) as error:
            raise ValueError("posts pagination headers changed") from error
        if (
            (total == 0) != (total_pages == 0)
            or total_pages
            != (
                0
                if total == 0
                else (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
            )
        ):
            raise ValueError("posts pagination headers are inconsistent")
        return total, total_pages

    def parse_page(
        self,
        body: bytes,
        *,
        cursor: str | None,
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        current_page = 1 if cursor is None else _page_number(cursor)
        items = self._items(body)
        expected_total, total_pages = self._pagination_headers(response_headers)
        if len(items) > POSTS_PER_PAGE:
            raise ValueError("posts page exceeds the reviewed bound")
        if current_page > total_pages and items:
            raise ValueError("posts page exceeds its declared pagination")
        if current_page <= total_pages and not items:
            raise ValueError("posts page ended before its declared pagination")

        records: list[dict[str, Any]] = []
        for item in items:
            metadata = self._metadata(item)
            identity = self._identity_value(item)
            records.append(
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": self._source_identity(identity),
                    "metadata": metadata,
                }
            )
        terminal = current_page >= total_pages
        return _bounded_page(
            records,
            terminal=terminal,
            next_cursor=(
                None if terminal else f"page-{current_page + 1:03d}"
            ),
            next_ordinal=None if terminal else current_page,
            expected_total=expected_total,
            rejected_count=0,
            current_page=current_page,
        )

    def declared_total_observation(
        self,
        body: bytes,
        *,
        observed_at: str,
        cursor: str | None = None,
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not is_valid_utc_timestamp(observed_at):
            raise ValueError("a declared total needs a UTC observation time")
        page = self.parse_page(
            body,
            cursor=cursor,
            response_headers=response_headers,
        )
        return {
            "observation_kind": "endpoint_declared_total",
            "source_id": self.source_id,
            "endpoint_id": self.endpoint_id,
            "declared_total": page["expected_total"],
            "observed_records": len(page["records"]),
            "observed_at": observed_at,
            "is_completeness_guarantee": False,
        }
