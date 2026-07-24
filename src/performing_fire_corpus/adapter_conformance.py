"""Strict, network-free conformance helpers for metadata source adapters."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import re
import socket
import urllib.request
import webbrowser
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.registry import require_source


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_CURSOR = re.compile(r"^(?:page|offset)-[0-9]{1,18}$")
_MIME_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$"
)
_YEAR = re.compile(r"^[0-9]{4}$")
_FORBIDDEN_FIELD_PARTS = frozenset(
    {
        "body",
        "caption",
        "content",
        "description",
        "excerpt",
        "html",
        "lyrics",
        "media_url",
        "notes",
        "personal",
        "prose",
        "signed",
        "summary",
        "text",
        "title",
        "token",
        "transcript",
    }
)
_SENSITIVE_QUERY_PARTS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "key",
        "secret",
        "session",
        "signature",
        "sig",
        "token",
    }
)
_STANDARD_BLOCKERS = frozenset(
    {
        "access_forbidden",
        "login_required",
        "rate_limited",
        "subscription_required",
    }
)


class AdapterConformanceError(ValueError):
    """Raised when an adapter bypasses a portable conformance boundary."""


@dataclass(frozen=True)
class MetadataRequest:
    """A content-free metadata request with no credential or body surface."""

    endpoint_id: str
    method: str
    url: str


@dataclass(frozen=True)
class MetadataResponse:
    """One synthetic response supplied to the offline harness."""

    status: int
    mime_type: str
    body: bytes
    final_url: str


class ConformantMetadataAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    source_id: str
    endpoint_id: str
    robots_applicability: str
    allowed_methods: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    allowed_query_parameters: tuple[str, ...]
    expected_mime_types: tuple[str, ...]
    approved_metadata_fields: tuple[str, ...]
    required_metadata_fields: tuple[str, ...]
    metadata_field_contracts: Mapping[str, Mapping[str, Any]]
    terminal_states: tuple[str, ...]
    blocker_states: tuple[str, ...]

    def build_request(self, cursor: str | None) -> MetadataRequest: ...

    def detect_access_blocker(self, body: bytes) -> str | None: ...

    def stable_record_id(self, item: Mapping[str, Any]) -> str: ...

    def parse_page(
        self, body: bytes, *, cursor: str | None
    ) -> Mapping[str, Any]: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _canonical_tuple(
    value: Any,
    field: str,
    *,
    pattern: re.Pattern[str] | None = None,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple)
        or (not value and not allow_empty)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
        or tuple(sorted(value)) != value
        or (pattern is not None and any(not pattern.fullmatch(item) for item in value))
    ):
        raise AdapterConformanceError(f"{field} must be a sorted unique tuple")
    return value


def _endpoint(
    adapter: ConformantMetadataAdapter,
    registry: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        source = require_source(registry, adapter.source_id)
    except Exception as error:
        raise AdapterConformanceError(
            "adapter source is not in the canonical registry"
        ) from error
    matches = [
        item
        for item in source["endpoints"]
        if item["endpoint_id"] == adapter.endpoint_id
    ]
    if len(matches) != 1:
        raise AdapterConformanceError(
            "adapter endpoint is not bound to its canonical source"
        )
    return matches[0]


def validate_adapter_declaration(
    adapter: ConformantMetadataAdapter,
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a source adapter's complete, source-bound declaration."""

    for field in ("adapter_id", "source_id", "endpoint_id"):
        value = getattr(adapter, field, None)
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise AdapterConformanceError(f"{field} is not a stable identifier")
    if (
        not isinstance(adapter.adapter_version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", adapter.adapter_version)
    ):
        raise AdapterConformanceError("adapter_version must be semantic")
    endpoint = _endpoint(adapter, registry)

    methods = _canonical_tuple(adapter.allowed_methods, "allowed_methods")
    if set(methods) - {"GET", "HEAD"} or "GET" not in methods:
        raise AdapterConformanceError("metadata adapters may only declare GET or HEAD")
    hosts = _canonical_tuple(adapter.allowed_hosts, "allowed_hosts")
    endpoint_host = (urlsplit(endpoint["public_url"]).hostname or "").lower()
    if hosts != (endpoint_host,):
        raise AdapterConformanceError(
            "allowed_hosts must exactly match the canonical endpoint host"
        )
    query_parameters = _canonical_tuple(
        adapter.allowed_query_parameters,
        "allowed_query_parameters",
        pattern=_IDENTIFIER,
        allow_empty=True,
    )
    if any(
        part in parameter.lower()
        for parameter in query_parameters
        for part in _SENSITIVE_QUERY_PARTS
    ):
        raise AdapterConformanceError("signed or credential query parameters are forbidden")
    mime_types = _canonical_tuple(
        adapter.expected_mime_types,
        "expected_mime_types",
        pattern=_MIME_TYPE,
    )
    approved = _canonical_tuple(
        adapter.approved_metadata_fields,
        "approved_metadata_fields",
        pattern=_IDENTIFIER,
    )
    required = _canonical_tuple(
        adapter.required_metadata_fields,
        "required_metadata_fields",
        pattern=_IDENTIFIER,
    )
    if not set(required).issubset(approved):
        raise AdapterConformanceError(
            "required metadata fields must be in the approved projection"
        )
    if any(
        part in field for field in approved for part in _FORBIDDEN_FIELD_PARTS
    ):
        raise AdapterConformanceError(
            "approved metadata projection contains content-bearing fields"
        )
    contracts = adapter.metadata_field_contracts
    if not isinstance(contracts, Mapping) or set(contracts) != set(approved):
        raise AdapterConformanceError(
            "metadata contracts must exactly cover the approved projection"
        )
    for field, contract in contracts.items():
        if not isinstance(contract, Mapping):
            raise AdapterConformanceError(f"{field} has an invalid metadata contract")
        if contract.get("value_type") == "year" and set(contract) == {"value_type"}:
            continue
        if (
            contract.get("value_type") == "enum"
            and set(contract) == {"value_type", "allowed_values"}
            and isinstance(contract["allowed_values"], list)
            and contract["allowed_values"]
            and contract["allowed_values"] == sorted(set(contract["allowed_values"]))
            and all(isinstance(item, str) and item for item in contract["allowed_values"])
            and all(
                "://" not in item and sanitize(item, environ={}) == item
                for item in contract["allowed_values"]
            )
        ):
            continue
        raise AdapterConformanceError(f"{field} has an invalid metadata contract")

    if adapter.robots_applicability not in {"required", "not_applicable"}:
        raise AdapterConformanceError("robots applicability must be explicit")
    terminal_states = _canonical_tuple(
        adapter.terminal_states,
        "terminal_states",
        pattern=_IDENTIFIER,
    )
    if "complete_for_observed_endpoint" not in terminal_states:
        raise AdapterConformanceError("adapter omits its bounded terminal state")
    blocker_states = _canonical_tuple(
        adapter.blocker_states,
        "blocker_states",
        pattern=_IDENTIFIER,
    )
    if not _STANDARD_BLOCKERS.issubset(blocker_states):
        raise AdapterConformanceError("adapter omits common access blocker states")

    return {
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "source_id": adapter.source_id,
        "endpoint_id": adapter.endpoint_id,
        "robots_applicability": adapter.robots_applicability,
        "allowed_methods": list(methods),
        "allowed_hosts": list(hosts),
        "allowed_query_parameters": list(query_parameters),
        "expected_mime_types": list(mime_types),
        "approved_metadata_fields": list(approved),
        "required_metadata_fields": list(required),
        "metadata_field_contracts": copy.deepcopy(dict(contracts)),
        "terminal_states": list(terminal_states),
        "blocker_states": list(blocker_states),
        "canonical_endpoint_url": endpoint["public_url"],
    }


def _validate_request(
    declaration: Mapping[str, Any],
    request: MetadataRequest,
) -> MetadataRequest:
    if not isinstance(request, MetadataRequest):
        raise AdapterConformanceError("adapter returned an invalid request")
    if (
        request.endpoint_id != declaration["endpoint_id"]
        or request.method not in declaration["allowed_methods"]
    ):
        raise AdapterConformanceError("request escapes the declared endpoint or method")
    try:
        parsed = urlsplit(request.url)
        canonical = urlsplit(declaration["canonical_endpoint_url"])
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as error:
        raise AdapterConformanceError("request URL is invalid") from error
    try:
        outside_boundary = (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.hostname or "").lower() not in declaration["allowed_hosts"]
            or parsed.port not in (None, 443)
            or parsed.path.rstrip("/") != canonical.path.rstrip("/")
        )
    except ValueError as error:
        raise AdapterConformanceError("request URL is invalid") from error
    if outside_boundary:
        raise AdapterConformanceError("request URL escapes the canonical endpoint")
    keys = [key for key, _ in query]
    if (
        len(keys) != len(set(keys))
        or not set(keys).issubset(declaration["allowed_query_parameters"])
        or any(
            part in key.lower()
            for key in keys
            for part in _SENSITIVE_QUERY_PARTS
        )
        or any(sanitize(value, environ={}) != value for _, value in query)
    ):
        raise AdapterConformanceError("request query is outside the approved projection")
    return request


def _metadata_value_matches(value: Any, contract: Mapping[str, Any]) -> bool:
    if not isinstance(value, str):
        return False
    if (
        "://" in value
        or "<html" in value.lower()
        or sanitize(value, environ={}) != value
    ):
        return False
    if contract["value_type"] == "year":
        return _YEAR.fullmatch(value) is not None
    return value in contract["allowed_values"]


def _validate_normalized_record(
    declaration: Mapping[str, Any],
    record_id: Any,
    metadata: Any,
) -> dict[str, Any]:
    approved = set(declaration["approved_metadata_fields"])
    required = set(declaration["required_metadata_fields"])
    contracts = declaration["metadata_field_contracts"]
    if (
        not isinstance(record_id, str)
        or not _RECORD_ID.fullmatch(record_id)
        or not isinstance(metadata, Mapping)
        or not required.issubset(metadata)
        or not set(metadata).issubset(approved)
        or any(
            not _metadata_value_matches(value, contracts[field])
            for field, value in metadata.items()
        )
        or sanitize(metadata, environ={}) != metadata
    ):
        raise AdapterConformanceError("shape_drift")
    return {"record_id": record_id, "metadata": dict(metadata)}


def _normalize_page(
    adapter: ConformantMetadataAdapter,
    declaration: Mapping[str, Any],
    body: bytes,
    cursor: str | None,
) -> dict[str, Any]:
    try:
        page = adapter.parse_page(body, cursor=cursor)
    except Exception as error:
        raise AdapterConformanceError("shape_drift") from error
    expected = {
        "records",
        "next_cursor",
        "next_ordinal",
        "terminal",
        "expected_total",
        "rejected_count",
    }
    if not isinstance(page, Mapping) or set(page) != expected:
        raise AdapterConformanceError("shape_drift")
    if (
        not isinstance(page["records"], list)
        or not isinstance(page["terminal"], bool)
        or (
            page["expected_total"] is not None
            and (
                not isinstance(page["expected_total"], int)
                or isinstance(page["expected_total"], bool)
                or page["expected_total"] < 0
            )
        )
        or not isinstance(page["rejected_count"], int)
        or isinstance(page["rejected_count"], bool)
        or page["rejected_count"] < 0
    ):
        raise AdapterConformanceError("shape_drift")

    normalized_records: list[dict[str, Any]] = []
    for record in page["records"]:
        if not isinstance(record, Mapping) or set(record) != {"record_id", "metadata"}:
            raise AdapterConformanceError("shape_drift")
        normalized_records.append(
            _validate_normalized_record(
                declaration,
                record["record_id"],
                record["metadata"],
            )
        )
    value = dict(page)
    value["records"] = normalized_records
    return value


def assert_stable_identity(
    adapter: ConformantMetadataAdapter,
    variants: Sequence[Mapping[str, Any]],
) -> str:
    """Assert that mutable source presentation variants keep one stable ID."""

    if not variants:
        raise AdapterConformanceError("at least one synthetic identity input is required")
    try:
        identifiers = [adapter.stable_record_id(item) for item in variants]
    except Exception as error:
        raise AdapterConformanceError("stable identity could not be derived") from error
    if (
        any(not isinstance(item, str) or not _RECORD_ID.fullmatch(item) for item in identifiers)
        or len(set(identifiers)) != 1
    ):
        raise AdapterConformanceError(
            "stable identity changed with mutable presentation metadata"
        )
    return identifiers[0]


class OfflineConformanceHarness:
    """Stateful, deterministic adapter conformance runner with no I/O."""

    def __init__(
        self,
        adapter: ConformantMetadataAdapter,
        registry: Mapping[str, Any],
        *,
        request_budget: int = 4,
        max_pages: int = 4,
        max_response_bytes: int = 8192,
        max_retries: int = 2,
        robots_allowed: bool = True,
        _checkpoint: Mapping[str, Any] | None = None,
    ) -> None:
        self.adapter = adapter
        self.declaration = validate_adapter_declaration(adapter, registry)
        for value, field, allow_zero in (
            (request_budget, "request_budget", True),
            (max_pages, "max_pages", False),
            (max_response_bytes, "max_response_bytes", False),
            (max_retries, "max_retries", True),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < (0 if allow_zero else 1)
            ):
                raise AdapterConformanceError(f"{field} is invalid")
        if not isinstance(robots_allowed, bool):
            raise AdapterConformanceError("robots_allowed must be boolean")
        self.bounds = {
            "request_budget": request_budget,
            "max_pages": max_pages,
            "max_response_bytes": max_response_bytes,
            "max_retries": max_retries,
            "robots_allowed": robots_allowed,
        }
        self._active_request: MetadataRequest | None = None
        self._state = self._new_state()
        if _checkpoint is not None:
            self._restore(_checkpoint)

    def _new_state(self) -> dict[str, Any]:
        return {
            "state": "ready",
            "stop_reason": None,
            "next_cursor": None,
            "next_ordinal": 0,
            "seen_cursors": [],
            "pages_committed": 0,
            "requests_attempted": 0,
            "current_retries": 0,
            "duplicate_records": 0,
            "rejected_records": 0,
            "expected_total": None,
            "records": {},
        }

    @classmethod
    def resume(
        cls,
        adapter: ConformantMetadataAdapter,
        registry: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
    ) -> OfflineConformanceHarness:
        if not isinstance(checkpoint, Mapping):
            raise AdapterConformanceError("checkpoint is invalid")
        bounds = checkpoint.get("bounds")
        if (
            not isinstance(bounds, Mapping)
            or set(bounds)
            != {
                "request_budget",
                "max_pages",
                "max_response_bytes",
                "max_retries",
                "robots_allowed",
            }
        ):
            raise AdapterConformanceError("checkpoint bounds are invalid")
        return cls(
            adapter,
            registry,
            request_budget=bounds.get("request_budget"),
            max_pages=bounds.get("max_pages"),
            max_response_bytes=bounds.get("max_response_bytes"),
            max_retries=bounds.get("max_retries"),
            robots_allowed=bounds.get("robots_allowed"),
            _checkpoint=checkpoint,
        )

    def _restore(self, checkpoint: Mapping[str, Any]) -> None:
        expected_keys = {
            "bounds",
            "checkpoint_sha256",
            "declaration_sha256",
            "state",
        }
        if set(checkpoint) != expected_keys:
            raise AdapterConformanceError("checkpoint is invalid")
        unsigned = {
            key: copy.deepcopy(checkpoint[key])
            for key in ("bounds", "declaration_sha256", "state")
        }
        if checkpoint["checkpoint_sha256"] != hashlib.sha256(
            _canonical(unsigned).encode("utf-8")
        ).hexdigest():
            raise AdapterConformanceError("checkpoint integrity check failed")
        fingerprint = hashlib.sha256(
            _canonical(self.declaration).encode("utf-8")
        ).hexdigest()
        if checkpoint["declaration_sha256"] != fingerprint:
            raise AdapterConformanceError("checkpoint adapter declaration changed")
        state = checkpoint["state"]
        if not isinstance(state, Mapping) or set(state) != set(self._new_state()):
            raise AdapterConformanceError("checkpoint state is invalid")
        restored = copy.deepcopy(dict(state))
        if (
            restored["state"] != "ready"
            or restored["stop_reason"] not in {None, "retry_pending"}
            or not isinstance(restored["records"], dict)
            or sanitize(restored, environ={}) != restored
        ):
            raise AdapterConformanceError("checkpoint is not resumable")
        integer_fields = (
            "next_ordinal",
            "pages_committed",
            "requests_attempted",
            "current_retries",
            "duplicate_records",
            "rejected_records",
        )
        if any(
            not isinstance(restored[field], int)
            or isinstance(restored[field], bool)
            or restored[field] < 0
            for field in integer_fields
        ):
            raise AdapterConformanceError("checkpoint counters are invalid")
        cursor = restored["next_cursor"]
        seen = restored["seen_cursors"]
        if (
            not isinstance(seen, list)
            or any(not isinstance(item, str) or not _CURSOR.fullmatch(item) for item in seen)
            or len(set(seen)) != len(seen)
            or (
                cursor is not None
                and (
                    not isinstance(cursor, str)
                    or not _CURSOR.fullmatch(cursor)
                    or cursor not in seen
                    or restored["next_ordinal"] < 1
                )
            )
            or (cursor is None and restored["next_ordinal"] != 0)
        ):
            raise AdapterConformanceError("checkpoint pagination state is invalid")
        if (
            restored["requests_attempted"] > self.bounds["request_budget"]
            or restored["pages_committed"] > self.bounds["max_pages"]
            or restored["current_retries"] > self.bounds["max_retries"]
        ):
            raise AdapterConformanceError("checkpoint exceeds its declared bounds")
        for record_id, metadata in restored["records"].items():
            _validate_normalized_record(self.declaration, record_id, metadata)
        expected_total = restored["expected_total"]
        if (
            expected_total is not None
            and (
                not isinstance(expected_total, int)
                or isinstance(expected_total, bool)
                or expected_total < len(restored["records"])
            )
        ):
            raise AdapterConformanceError("checkpoint completeness state is invalid")
        self._state = restored

    def _stop(self, state: str, reason: str) -> dict[str, Any]:
        self._active_request = None
        self._state["state"] = state
        self._state["stop_reason"] = reason
        return self.manifest()

    def next_request(self) -> MetadataRequest | None:
        if self._state["state"] != "ready":
            return None
        if self._active_request is not None:
            raise AdapterConformanceError("a synthetic request is already active")
        if self.bounds["request_budget"] == 0:
            self._stop("bounded_partial", "zero_request_budget")
            return None
        if (
            self.declaration["robots_applicability"] == "required"
            and not self.bounds["robots_allowed"]
        ):
            self._stop("blocked", "robots_denied")
            return None
        if self._state["requests_attempted"] >= self.bounds["request_budget"]:
            self._stop("bounded_partial", "request_budget_exhausted")
            return None
        if self._state["pages_committed"] >= self.bounds["max_pages"]:
            self._stop("bounded_partial", "page_budget_exhausted")
            return None
        request = _validate_request(
            self.declaration,
            self.adapter.build_request(self._state["next_cursor"]),
        )
        self._state["requests_attempted"] += 1
        self._state["stop_reason"] = None
        self._active_request = request
        return request

    def record_retry(self, code: str) -> dict[str, Any]:
        if self._active_request is None:
            raise AdapterConformanceError("retry has no active synthetic request")
        if not isinstance(code, str) or not _IDENTIFIER.fullmatch(code):
            raise AdapterConformanceError("retry code is invalid")
        self._active_request = None
        self._state["current_retries"] += 1
        if self._state["current_retries"] > self.bounds["max_retries"]:
            return self._stop("blocked", "retry_exhausted")
        self._state["stop_reason"] = "retry_pending"
        return self.manifest()

    def ingest(self, response: MetadataResponse) -> dict[str, Any]:
        request = self._active_request
        if request is None:
            raise AdapterConformanceError("response has no active synthetic request")
        self._active_request = None
        if (
            not isinstance(response, MetadataResponse)
            or not isinstance(response.status, int)
            or isinstance(response.status, bool)
            or not 100 <= response.status <= 599
            or not isinstance(response.mime_type, str)
            or not _MIME_TYPE.fullmatch(response.mime_type)
            or not isinstance(response.body, bytes)
            or not isinstance(response.final_url, str)
        ):
            return self._stop("changed", "invalid_response")
        if response.final_url != request.url:
            return self._stop("changed", "redirect_mismatch")
        status_reason = {
            401: "login_required",
            403: "access_forbidden",
            429: "rate_limited",
        }.get(response.status)
        if status_reason is not None:
            return self._stop("blocked", status_reason)
        if response.status != 200:
            return self._stop("blocked", "http_error")
        if response.mime_type not in self.declaration["expected_mime_types"]:
            return self._stop("changed", "mime_mismatch")
        if len(response.body) > self.bounds["max_response_bytes"]:
            return self._stop("changed", "response_oversized")
        try:
            access_blocker = self.adapter.detect_access_blocker(response.body)
        except Exception:
            return self._stop("changed", "shape_drift")
        if access_blocker is not None:
            if access_blocker not in self.declaration["blocker_states"]:
                return self._stop("changed", "shape_drift")
            return self._stop("blocked", access_blocker)
        try:
            page = _normalize_page(
                self.adapter,
                self.declaration,
                response.body,
                self._state["next_cursor"],
            )
        except AdapterConformanceError:
            return self._stop("changed", "shape_drift")

        terminal = page["terminal"]
        next_cursor = page["next_cursor"]
        next_ordinal = page["next_ordinal"]
        if terminal:
            if next_cursor is not None or next_ordinal is not None:
                return self._stop("changed", "shape_drift")
        else:
            if (
                not isinstance(next_cursor, str)
                or not _CURSOR.fullmatch(next_cursor)
                or not isinstance(next_ordinal, int)
                or isinstance(next_ordinal, bool)
                or next_ordinal != self._state["next_ordinal"] + 1
                or next_cursor in self._state["seen_cursors"]
            ):
                return self._stop("changed", "pagination_loop")

        expected_total = page["expected_total"]
        if (
            expected_total is not None
            and self._state["expected_total"] is not None
            and expected_total != self._state["expected_total"]
        ):
            return self._stop("changed", "expected_total_changed")

        candidate = copy.deepcopy(self._state["records"])
        duplicates = 0
        for record in page["records"]:
            record_id = record["record_id"]
            prior = candidate.get(record_id)
            if prior is not None:
                if prior != record["metadata"]:
                    return self._stop("changed", "stable_id_collision")
                duplicates += 1
            else:
                candidate[record_id] = record["metadata"]
        projected_total = (
            self._state["expected_total"]
            if expected_total is None
            else expected_total
        )
        if projected_total is not None and len(candidate) > projected_total:
            return self._stop("changed", "expected_total_changed")

        self._state["records"] = candidate
        self._state["duplicate_records"] += duplicates
        self._state["rejected_records"] += page["rejected_count"]
        self._state["expected_total"] = projected_total
        self._state["pages_committed"] += 1
        self._state["current_retries"] = 0
        self._state["next_cursor"] = next_cursor
        self._state["next_ordinal"] = next_ordinal
        if not terminal:
            self._state["seen_cursors"].append(next_cursor)
            self._state["stop_reason"] = None
            return self.manifest()
        self._state["next_cursor"] = None
        return self._stop("complete_for_observed_endpoint", "terminal_page")

    def checkpoint(self) -> dict[str, Any]:
        if self._active_request is not None:
            raise AdapterConformanceError("active synthetic request cannot be checkpointed")
        unsigned = {
            "bounds": copy.deepcopy(self.bounds),
            "declaration_sha256": hashlib.sha256(
                _canonical(self.declaration).encode("utf-8")
            ).hexdigest(),
            "state": copy.deepcopy(self._state),
        }
        return {
            **unsigned,
            "checkpoint_sha256": hashlib.sha256(
                _canonical(unsigned).encode("utf-8")
            ).hexdigest(),
        }

    def manifest(self) -> dict[str, Any]:
        records = [
            {"record_id": record_id, "metadata": copy.deepcopy(metadata)}
            for record_id, metadata in sorted(self._state["records"].items())
        ]
        observed_unique_records = len(records)
        expected_total = self._state["expected_total"]
        return {
            "schema_version": 1,
            "manifest_type": "offline_adapter_conformance",
            "adapter_id": self.declaration["adapter_id"],
            "adapter_version": self.declaration["adapter_version"],
            "source_id": self.declaration["source_id"],
            "endpoint_id": self.declaration["endpoint_id"],
            "state": self._state["state"],
            "stop_reason": self._state["stop_reason"],
            "requests_attempted": self._state["requests_attempted"],
            "pages_committed": self._state["pages_committed"],
            "duplicate_records": self._state["duplicate_records"],
            "rejected_records": self._state["rejected_records"],
            "observed_unique_records": observed_unique_records,
            "expected_total": expected_total,
            "unvisited_remainder": (
                None
                if expected_total is None
                else max(expected_total - observed_unique_records, 0)
            ),
            "next_cursor": self._state["next_cursor"],
            "records": records,
        }


def _deny_network(*_: Any, **__: Any) -> Any:
    raise AdapterConformanceError("live network access is forbidden in adapter tests")


@contextmanager
def deny_live_network(
    *,
    additional_entry_points: Sequence[tuple[object, str]] = (),
) -> Iterator[None]:
    """Patch common DNS, socket, HTTP, browser, and supplied SDK entry points."""

    targets: tuple[tuple[object, str], ...] = (
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (urllib.request, "urlopen"),
        (urllib.request, "build_opener"),
        (http.client.HTTPConnection, "connect"),
        (http.client.HTTPSConnection, "connect"),
        (webbrowser, "open"),
        *tuple(additional_entry_points),
    )
    with ExitStack() as stack:
        for owner, attribute in targets:
            if not hasattr(owner, attribute):
                raise AdapterConformanceError(
                    "declared network entry point does not exist"
                )
            stack.enter_context(patch.object(owner, attribute, _deny_network))
        yield
