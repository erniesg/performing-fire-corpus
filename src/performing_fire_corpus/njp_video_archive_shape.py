"""Bounded, content-neutral review of the NJP Video Archive page shape."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from performing_fire_corpus.governance import (
    GovernanceError,
    evaluate_source_operation,
    validate_source_governance_registry,
)
from performing_fire_corpus.njp_center_adapters import (
    NJPCenterVideoArchiveAdapter,
)
from performing_fire_corpus.njp_site_inventory import (
    ROBOTS_URL,
    USER_AGENT,
    MetadataSafeResponse,
    NJPInventoryError,
    PreflightTransport,
    UrllibPreflightTransport,
    _classify_robots,
)
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_GOVERNANCE_PATH = "config/source-governance.v1.json"
_REQUIRED_OPERATIONS = (
    "metadata_inventory",
    "public_retrieval",
    "retention",
)
_MAX_SIGNATURES = 512
_MAX_JSON_NODES = 2048
_MAX_JSON_DEPTH = 10
_HTML_TAGS = frozenset(
    {
        "a",
        "article",
        "aside",
        "body",
        "button",
        "div",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "html",
        "img",
        "input",
        "label",
        "li",
        "link",
        "main",
        "meta",
        "nav",
        "ol",
        "option",
        "p",
        "script",
        "section",
        "select",
        "source",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "template",
        "textarea",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
        "video",
    }
)
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_ATTRIBUTE_CATEGORIES = frozenset(
    {
        "action",
        "aria",
        "class",
        "data",
        "href",
        "id",
        "name",
        "other",
        "rel",
        "role",
        "src",
        "type",
    }
)
_URL_ATTRIBUTES = frozenset({"action", "href", "src"})
_MIME_CATEGORIES = frozenset(
    {"application/json", "application/ld+json", "text/html", "text/plain"}
)
_REPORT_KEYS = frozenset(
    {
        "access_state",
        "adapter_id",
        "adapter_version",
        "attachment_requests_allowed",
        "attributes",
        "authorized",
        "blocker_codes",
        "category",
        "child_count",
        "commit_sha",
        "count",
        "declared_bytes",
        "depth",
        "elapsed_seconds",
        "endpoint_id",
        "exact_head_verified",
        "failure_code",
        "fragment_present",
        "governance",
        "host_scope",
        "html_recovery_events",
        "json_shapes",
        "json_unreadable",
        "key_shape_sha256",
        "lane",
        "max_response_bytes",
        "method",
        "mime_type",
        "nonblank_text_nodes",
        "observed_at",
        "observed_bytes",
        "outcome",
        "page_limit",
        "parent",
        "path_segments",
        "per_host_interval_seconds",
        "plan",
        "prose_retained",
        "query_count",
        "raw_body_retained",
        "record_type",
        "redirects_followed",
        "request_limit",
        "requests",
        "required_operations",
        "response_sha256",
        "retry_limit",
        "robots_state",
        "schema_version",
        "signatures",
        "snapshot_sha256",
        "source_id",
        "state",
        "status",
        "structure",
        "structure_sha256",
        "summary_truncated",
        "tag",
        "timeout_seconds",
        "type",
        "url_shape",
        "user_agent",
    }
)
_REPORT_LITERALS = (
    _HTML_TAGS
    | _ATTRIBUTE_CATEGORIES
    | _MIME_CATEGORIES
    | {
        "access_forbidden",
        "application/other",
        "array",
        "blocked",
        "boolean",
        "disallowed_redirect",
        "elapsed_bound",
        "external",
        "file",
        "GET",
        "governance_not_authorized",
        "mime_mismatch",
        "not_requested",
        "njp-center-video-archive",
        "njp-center-video-archive-html",
        "njp-center-video-archive-page",
        "njp_video_archive_shape_review",
        "null",
        "number",
        "numeric",
        "object",
        "other",
        "public_access_unconfirmed",
        "public_get_available",
        "rate_limited",
        "response_oversized",
        "robots_access_blocked",
        "robots_allowed",
        "robots_ambiguous",
        "robots_denied",
        "same-host",
        "shape_observed",
        "shape_summary_bound",
        "slug",
        "source_shape_unreadable",
        "string",
        "text/other",
        "transport_error",
        "trusted-vm-first",
        "unknown",
        "1.0.0",
        USER_AGENT,
    }
)


class VideoArchiveShapeError(RuntimeError):
    """A fail-closed error at the one-page shape-review boundary."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise VideoArchiveShapeError("naive_observation_time")
    return (
        value.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _assert_safe_report(value: Any) -> None:
    if isinstance(value, Mapping):
        if any(key not in _REPORT_KEYS for key in value):
            raise VideoArchiveShapeError("unsafe_shape_report")
        for child in value.values():
            _assert_safe_report(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_safe_report(child)
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise VideoArchiveShapeError("unsafe_shape_report")
    if isinstance(value, str) and (
        value in _REPORT_LITERALS
        or _COMMIT_SHA.fullmatch(value)
        or _SHA256.fullmatch(value)
        or _UTC_TIMESTAMP.fullmatch(value)
    ):
        return
    raise VideoArchiveShapeError("unsafe_shape_report")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _assert_safe_report(value)
    if sanitize(value, environ={}) != value:
        raise VideoArchiveShapeError("unsafe_shape_report")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _verify_exact_clean_head(repo_root: Path, expected_commit_sha: str) -> None:
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise VideoArchiveShapeError("exact_head_not_verified") from error
    if actual != expected_commit_sha or dirty:
        raise VideoArchiveShapeError("exact_head_not_verified")


def _governance_status(
    repo_root: Path,
    governance_path: str | Path,
    observed_at: datetime,
    required_horizon_seconds: float,
) -> dict[str, Any]:
    candidate = Path(governance_path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != _GOVERNANCE_PATH
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return {
            "snapshot_sha256": None,
            "required_operations": len(_REQUIRED_OPERATIONS),
            "authorized": False,
        }
    selected = (repo_root / candidate).resolve()
    if not selected.is_relative_to(repo_root):
        return {
            "snapshot_sha256": None,
            "required_operations": len(_REQUIRED_OPERATIONS),
            "authorized": False,
        }
    try:
        raw = selected.read_bytes()
        registry = validate_source_governance_registry(json.loads(raw))
        records = [
            record
            for record in registry["records"]
            if record["source_id"] == NJPCenterVideoArchiveAdapter.source_id
            and record["endpoint_id"] == NJPCenterVideoArchiveAdapter.endpoint_id
            and record.get("asset_id") is None
        ]
        if len(records) != 1:
            raise GovernanceError("endpoint governance is not unique")
        record = records[0]
        required_horizon = observed_at + timedelta(
            seconds=required_horizon_seconds
        )
        authorized = all(
            evaluate_source_operation(
                record,
                operation,
                now=required_horizon,
            )["eligible"]
            for operation in _REQUIRED_OPERATIONS
        )
    except (
        GovernanceError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raw = locals().get("raw", b"")
        authorized = False
    return {
        "snapshot_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "required_operations": len(_REQUIRED_OPERATIONS),
        "authorized": authorized,
    }


def _tag_category(tag: str) -> str:
    return tag if tag in _HTML_TAGS else "other"


def _attribute_category(name: str) -> str:
    if name in _ATTRIBUTE_CATEGORIES:
        return name
    if name.startswith("data-"):
        return "data"
    if name.startswith("aria-"):
        return "aria"
    return "other"


def _segment_category(segment: str) -> str:
    if segment.isdecimal():
        return "numeric"
    if "." in segment and segment.rsplit(".", 1)[-1].isalnum():
        return "file"
    if re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        return "slug"
    return "other"


def _url_shape(value: str) -> dict[str, Any] | None:
    if len(value) > 2048 or any(ord(character) < 32 for character in value):
        return None
    try:
        absolute = urlsplit(
            urljoin(NJPCenterVideoArchiveAdapter.public_url, value)
        )
        reviewed = urlsplit(NJPCenterVideoArchiveAdapter.public_url)
    except (TypeError, ValueError):
        return None
    if absolute.scheme not in {"http", "https"} or not absolute.hostname:
        return None
    segments = [
        _segment_category(segment)
        for segment in absolute.path.split("/")
        if segment
    ]
    query_count = (
        0 if not absolute.query else min(256, absolute.query.count("&") + 1)
    )
    return {
        "host_scope": (
            "same-host"
            if absolute.hostname.lower() == (reviewed.hostname or "").lower()
            else "external"
        ),
        "path_segments": segments,
        "query_count": query_count,
        "fragment_present": bool(absolute.fragment),
    }


def _mime_category(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.lower()
    if normalized in _MIME_CATEGORIES:
        return normalized
    if normalized.startswith("text/"):
        return "text/other"
    if normalized.startswith("application/"):
        return "application/other"
    return "other"


class _ShapeParser(HTMLParser):
    """Summarize structure without retaining source-derived strings."""

    def __init__(self, check_deadline: Callable[[], None]) -> None:
        super().__init__(convert_charrefs=True)
        self.seen_doctype = False
        self.seen_html = False
        self.seen_head = False
        self.seen_body = False
        self.stack: list[str] = []
        self.signatures: Counter[str] = Counter()
        self.json_shapes: Counter[str] = Counter()
        self.nonblank_text_nodes = 0
        self.json_unreadable = False
        self._json_script_depth: int | None = None
        self._json_script_parts: list[str] = []
        self._json_script_type: str | None = None
        self._json_nodes = 0
        self._summary_truncated = False
        self._html_recovery_events = 0
        self._check_deadline = check_deadline

    def handle_decl(self, decl: str) -> None:
        self._check_deadline()
        self.seen_doctype = decl.lower() == "doctype html"

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._check_deadline()
        parent = _tag_category(self.stack[-1]) if self.stack else None
        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)
        self.seen_html = self.seen_html or tag == "html"
        self.seen_head = self.seen_head or tag == "head"
        self.seen_body = self.seen_body or tag == "body"

        selected: list[dict[str, Any]] = []
        script_type: str | None = None
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            category = _attribute_category(name)
            attribute: dict[str, Any] = {"category": category}
            if name in _URL_ATTRIBUTES and raw_value is not None:
                shaped = _url_shape(raw_value)
                if shaped is not None:
                    attribute["url_shape"] = shaped
            selected.append(attribute)
            if (
                name == "type"
                and raw_value is not None
                and raw_value.lower()
                in {"application/json", "application/ld+json"}
            ):
                script_type = raw_value.lower()
        signature = _canonical(
            {
                "tag": _tag_category(tag),
                "parent": parent,
                "attributes": sorted(selected, key=_canonical),
            }
        )
        if (
            signature not in self.signatures
            and len(self.signatures) >= _MAX_SIGNATURES
        ):
            self._summary_truncated = True
        else:
            self.signatures[signature] += 1

        if tag == "script" and script_type is not None:
            if self._json_script_depth is not None:
                self.json_unreadable = True
            self._json_script_depth = len(self.stack)
            self._json_script_parts = []
            self._json_script_type = script_type

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self._check_deadline()
        if data.strip():
            self.nonblank_text_nodes += 1
        if self._json_script_depth is not None:
            self._json_script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        self._check_deadline()
        if (
            tag == "script"
            and self._json_script_depth is not None
            and self._json_script_depth == len(self.stack)
        ):
            self._summarize_json_script()
            self._json_script_depth = None
            self._json_script_parts = []
            self._json_script_type = None
        if not self.stack or self.stack[-1] != tag:
            self._html_recovery_events += 1
            if tag in self.stack:
                while self.stack and self.stack[-1] != tag:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()
            return
        self.stack.pop()

    def _summarize_json_script(self) -> None:
        raw = "".join(self._json_script_parts)
        if not raw.strip():
            self.json_unreadable = True
            return

        def reject_nonstandard_constant(_: str) -> None:
            raise ValueError("nonstandard JSON constant")

        try:
            value = json.loads(raw, parse_constant=reject_nonstandard_constant)
        except (RecursionError, TypeError, ValueError, json.JSONDecodeError):
            self.json_unreadable = True
            return

        def visit(item: Any, depth: int) -> None:
            self._check_deadline()
            if depth > _MAX_JSON_DEPTH or self._json_nodes >= _MAX_JSON_NODES:
                self._summary_truncated = True
                return
            self._json_nodes += 1
            if isinstance(item, Mapping):
                shape = "object"
                children = list(item.values())
                key_shape_sha256 = hashlib.sha256(
                    _canonical(sorted(item)).encode()
                ).hexdigest()
            elif isinstance(item, list):
                shape = "array"
                children = item
                key_shape_sha256 = None
            elif item is None:
                shape = "null"
                children = []
                key_shape_sha256 = None
            elif isinstance(item, bool):
                shape = "boolean"
                children = []
                key_shape_sha256 = None
            elif isinstance(item, (int, float)):
                shape = "number"
                children = []
                key_shape_sha256 = None
            else:
                shape = "string"
                children = []
                key_shape_sha256 = None
            self.json_shapes[
                _canonical(
                    {
                        "depth": depth,
                        "type": shape,
                        "child_count": len(children),
                        "mime_type": self._json_script_type,
                        "key_shape_sha256": key_shape_sha256,
                    }
                )
            ] += 1
            for child in children:
                visit(child, depth + 1)

        visit(value, 0)

    def finish(self) -> dict[str, Any]:
        self._check_deadline()
        if (
            not self.seen_doctype
            or not self.seen_html
            or not self.seen_head
            or not self.seen_body
            or self._json_script_depth is not None
        ):
            raise VideoArchiveShapeError("html_structure_incomplete")
        if self.stack:
            self._html_recovery_events += len(self.stack)
            self.stack.clear()
        signatures = [
            {**json.loads(serialized), "count": count}
            for serialized, count in sorted(self.signatures.items())
        ]
        json_shapes = [
            {**json.loads(serialized), "count": count}
            for serialized, count in sorted(self.json_shapes.items())
        ]
        summary = {
            "signatures": signatures,
            "json_shapes": json_shapes,
            "nonblank_text_nodes": self.nonblank_text_nodes,
            "json_unreadable": self.json_unreadable,
            "html_recovery_events": self._html_recovery_events,
            "summary_truncated": self._summary_truncated,
        }
        return {
            **summary,
            "structure_sha256": hashlib.sha256(
                _canonical(summary).encode()
            ).hexdigest(),
        }


def review_video_archive_shape(
    *,
    commit_sha: str,
    repo_root: str | Path,
    governance_path: str | Path,
    output_path: str | Path,
    max_response_bytes: int = 131072,
    timeout_seconds: float = 10.0,
    per_host_interval_seconds: float = 1.0,
    elapsed_seconds: float = 30.0,
    transport: PreflightTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Run one governance- and robots-aware GET shape review."""

    if (
        not _COMMIT_SHA.fullmatch(commit_sha)
        or isinstance(max_response_bytes, bool)
        or not isinstance(max_response_bytes, int)
        or not 1024 <= max_response_bytes <= 524288
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in (
                timeout_seconds,
                per_host_interval_seconds,
                elapsed_seconds,
            )
        )
        or timeout_seconds > 60
        or per_host_interval_seconds > 60
        or elapsed_seconds > 300
    ):
        raise VideoArchiveShapeError("invalid_shape_review_plan")

    selected_root = Path(repo_root).resolve()
    _verify_exact_clean_head(selected_root, commit_sha)
    selected_transport = transport or UrllibPreflightTransport()
    selected_now = now or (lambda: datetime.now(UTC))
    selected_monotonic = monotonic or time.monotonic
    selected_sleeper = sleeper or time.sleep
    observed_time = selected_now()
    observed_at = _utc_text(observed_time)
    started = selected_monotonic()
    deadline = started + elapsed_seconds
    requests: list[dict[str, Any]] = []
    blocker_codes: list[str] = []
    structure: dict[str, Any] | None = None
    robots_state = "not_requested"
    access_state = "not_requested"
    governance = _governance_status(
        selected_root,
        governance_path,
        observed_time,
        elapsed_seconds,
    )

    def remaining() -> float:
        return deadline - selected_monotonic()

    def check_deadline() -> None:
        if remaining() <= 0:
            raise VideoArchiveShapeError("elapsed_bound")

    def request_timeout() -> float | None:
        available = remaining()
        if available <= 0:
            return None
        return min(timeout_seconds, available)

    def request_fact(
        response: MetadataSafeResponse,
        *,
        outcome: str,
    ) -> dict[str, Any]:
        status = (
            response.status
            if isinstance(response.status, int)
            and not isinstance(response.status, bool)
            and 0 <= response.status <= 999
            else 0
        )
        declared = (
            response.declared_bytes
            if isinstance(response.declared_bytes, int)
            and not isinstance(response.declared_bytes, bool)
            and response.declared_bytes >= 0
            else None
        )
        return {
            "method": "GET",
            "status": status,
            "mime_type": _mime_category(response.mime_type),
            "declared_bytes": declared,
            "observed_bytes": len(response.body),
            "response_sha256": (
                hashlib.sha256(response.body).hexdigest()
                if response.body
                else None
            ),
            "outcome": outcome,
            "failure_code": (
                "transport_error"
                if response.failure_code is not None
                else None
            ),
        }

    def exceeds_byte_limit(
        response: MetadataSafeResponse,
        limit: int,
    ) -> bool:
        return (
            response.oversized
            or len(response.body) > limit
            or (
                isinstance(response.declared_bytes, int)
                and not isinstance(response.declared_bytes, bool)
                and response.declared_bytes > limit
            )
        )

    if not governance["authorized"]:
        blocker_codes.append("governance_not_authorized")
    elif remaining() <= 0:
        blocker_codes.append("elapsed_bound")
    else:
        timeout = request_timeout()
        if timeout is None:
            blocker_codes.append("elapsed_bound")
        else:
            robots_byte_limit = min(max_response_bytes, 65536)
            try:
                robots = selected_transport.request(
                    "GET",
                    ROBOTS_URL,
                    timeout_seconds=timeout,
                    max_response_bytes=robots_byte_limit,
                )
            except NJPInventoryError:
                robots = MetadataSafeResponse(
                    url=ROBOTS_URL,
                    status=599,
                    mime_type="unknown",
                    body=b"",
                    failure_code="transport_error",
                )
            robots_state = (
                "robots_ambiguous"
                if exceeds_byte_limit(robots, robots_byte_limit)
                else _classify_robots(
                    robots,
                    NJPCenterVideoArchiveAdapter.public_url,
                )
            )
            requests.append(request_fact(robots, outcome=robots_state))
            if remaining() <= 0:
                blocker_codes.append("elapsed_bound")
            elif robots_state != "robots_allowed":
                blocker_codes.append(robots_state)
            elif remaining() <= per_host_interval_seconds:
                blocker_codes.append("elapsed_bound")
            else:
                selected_sleeper(per_host_interval_seconds)
                timeout = request_timeout()
                if timeout is None:
                    blocker_codes.append("elapsed_bound")
                else:
                    try:
                        page = selected_transport.request(
                            "GET",
                            NJPCenterVideoArchiveAdapter.public_url,
                            timeout_seconds=timeout,
                            max_response_bytes=max_response_bytes,
                        )
                    except NJPInventoryError:
                        page = MetadataSafeResponse(
                            url=NJPCenterVideoArchiveAdapter.public_url,
                            status=599,
                            mime_type="unknown",
                            body=b"",
                            failure_code="transport_error",
                        )
                    if page.failure_code is not None:
                        access_state = "transport_error"
                    elif (
                        page.url != NJPCenterVideoArchiveAdapter.public_url
                        or page.status in _REDIRECTS
                    ):
                        access_state = "disallowed_redirect"
                    elif page.status in {401, 403}:
                        access_state = "access_forbidden"
                    elif page.status == 429:
                        access_state = "rate_limited"
                    elif page.status != 200:
                        access_state = "public_access_unconfirmed"
                    elif exceeds_byte_limit(page, max_response_bytes):
                        access_state = "response_oversized"
                    elif page.mime_type != "text/html":
                        access_state = "mime_mismatch"
                    else:
                        access_state = "public_get_available"
                    requests.append(request_fact(page, outcome=access_state))
                    if remaining() <= 0:
                        blocker_codes.append("elapsed_bound")
                    elif access_state != "public_get_available":
                        blocker_codes.append(access_state)
                    else:
                        try:
                            parser = _ShapeParser(check_deadline)
                            parser.feed(page.body.decode("utf-8", "strict"))
                            parser.close()
                            structure = parser.finish()
                        except UnicodeDecodeError:
                            blocker_codes.append("source_shape_unreadable")
                        except VideoArchiveShapeError as error:
                            blocker_codes.append(
                                "elapsed_bound"
                                if str(error) == "elapsed_bound"
                                else "source_shape_unreadable"
                            )
                        else:
                            if structure["json_unreadable"]:
                                blocker_codes.append(
                                    "source_shape_unreadable"
                                )
                            if structure["summary_truncated"]:
                                blocker_codes.append("shape_summary_bound")
                            if remaining() <= 0:
                                blocker_codes.append("elapsed_bound")

    report = {
        "record_type": "njp_video_archive_shape_review",
        "schema_version": 1,
        "source_id": "njp-center-video-archive",
        "endpoint_id": NJPCenterVideoArchiveAdapter.endpoint_id,
        "adapter_id": NJPCenterVideoArchiveAdapter.adapter_id,
        "adapter_version": NJPCenterVideoArchiveAdapter.adapter_version,
        "commit_sha": commit_sha,
        "exact_head_verified": True,
        "observed_at": observed_at,
        "lane": "trusted-vm-first",
        "user_agent": USER_AGENT,
        "plan": {
            "request_limit": 2,
            "page_limit": 1,
            "max_response_bytes": max_response_bytes,
            "retry_limit": 0,
            "per_host_interval_seconds": per_host_interval_seconds,
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": elapsed_seconds,
            "redirects_followed": False,
            "raw_body_retained": False,
            "prose_retained": False,
            "attachment_requests_allowed": False,
        },
        "governance": governance,
        "robots_state": robots_state,
        "access_state": access_state,
        "state": (
            "shape_observed"
            if structure is not None and not blocker_codes
            else "blocked"
        ),
        "blocker_codes": sorted(set(blocker_codes)),
        "requests": requests,
        "structure": structure,
    }
    _write_json(Path(output_path), report)
    return report
