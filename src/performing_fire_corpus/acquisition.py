"""Bounded public metadata inventory for one reviewed source."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from jsonschema.exceptions import ValidationError

from performing_fire_corpus.ledger import Ledger, LedgerError, utc_text
from performing_fire_corpus.policy import (
    AcquisitionPolicyError,
    validate_public_url,
    validate_redirect,
)
from performing_fire_corpus.rate_limit import HostRateLimiter
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.retry import RetryPolicy, RetryState, plan_retry


SOURCE_NAME = "antiegg-fluxus"
SOURCE_ID = "source_antiegg_fluxus"
ASSET_ID = "asset_antiegg_fluxus_25502"
JOB_ID = "job_antiegg_fluxus_25502_inventory"
ROBOTS_URL = "https://antiegg.kr/robots.txt"
ARTICLE_URL = "https://antiegg.kr/25502/"
USER_AGENT = "performing-fire-corpus/0.1"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class AcquisitionError(RuntimeError):
    """Raised for invalid local acquisition configuration."""


@dataclass(frozen=True)
class HTTPResponse:
    """The minimal in-memory response shape accepted from a transport."""

    url: str
    status: int
    mime_type: str
    body: bytes
    declared_bytes: int | None = None
    retry_after: str | None = None
    location: str | None = None
    oversized: bool = False


class HTTPTransport(Protocol):
    """Injectable GET-only transport used by portable tests and the live CLI."""

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse: ...


class SourceAdapter(Protocol):
    """Convert one reviewed public metadata response into an asset record."""

    name: str
    robots_url: str
    catalogue_url: str

    def parse_asset(self, body: bytes, response_url: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class AcquisitionConfig:
    source: str
    max_requests: int
    timeout_seconds: float
    rate_limit_seconds: float
    max_retries: int
    max_elapsed_seconds: float
    max_response_bytes: int
    ledger_path: str | Path
    manifest_path: str | Path

    def __post_init__(self) -> None:
        numeric_bounds = (
            self.timeout_seconds,
            self.rate_limit_seconds,
            self.max_elapsed_seconds,
        )
        if self.source != SOURCE_NAME:
            raise AcquisitionError("exactly one reviewed source adapter is available")
        if (
            isinstance(self.max_requests, bool)
            or not isinstance(self.max_requests, int)
            or self.max_requests < 1
            or isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
            or isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes < 1
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in numeric_bounds
            )
            or self.timeout_seconds <= 0
            or self.rate_limit_seconds < 0
            or self.max_elapsed_seconds <= 0
        ):
            raise AcquisitionError("request, timeout, rate, retry, and size bounds are required")
        if not str(self.ledger_path).strip() or not str(self.manifest_path).strip():
            raise AcquisitionError("explicit ledger and manifest paths are required")
        if Path(self.ledger_path).resolve() == Path(self.manifest_path).resolve():
            raise AcquisitionError("ledger and manifest paths must be different")


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urlrequest.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class UrllibGETTransport:
    """Unauthenticated stdlib transport that never persists response bodies."""

    def __init__(self) -> None:
        self._opener = urlrequest.build_opener(_NoRedirect)

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse:
        request = urlrequest.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"},
            method="GET",
        )
        try:
            opened = self._opener.open(request, timeout=timeout_seconds)
        except urlerror.HTTPError as error:
            opened = error
        except (OSError, urlerror.URLError, TimeoutError) as error:
            raise AcquisitionError(f"public metadata request failed: {type(error).__name__}") from None
        with opened:
            headers = opened.headers
            content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].lower()
            declared: int | None = None
            try:
                if headers.get("Content-Length") is not None:
                    declared = int(headers["Content-Length"])
            except (TypeError, ValueError):
                declared = None
            oversized = declared is not None and declared > max_response_bytes
            body = b"" if oversized else opened.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                body = b""
                oversized = True
            return HTTPResponse(
                url=opened.geturl(),
                status=int(opened.status),
                mime_type=content_type,
                body=body,
                declared_bytes=declared,
                retry_after=headers.get("Retry-After"),
                location=headers.get("Location"),
                oversized=oversized,
            )


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.canonical: str | None = None

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        values = {key.lower(): value for key, value in attributes if value is not None}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content")
            if key in {"og:title", "og:type", "article:published_time"} and content:
                self.metadata[key] = content.strip()
        elif (
            tag.lower() == "link"
            and values.get("rel", "").lower() == "canonical"
            and values.get("href")
        ):
            self.canonical = values["href"]


class AntieggFluxusAdapter:
    """Adapter for the one checked-in ANTIEGG Fluxus article URL."""

    name = SOURCE_NAME
    robots_url = ROBOTS_URL
    catalogue_url = ARTICLE_URL

    def parse_asset(self, body: bytes, response_url: str) -> dict[str, object]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise AcquisitionError("response_structure_changed") from None
        parser = _MetadataParser()
        try:
            parser.feed(text)
        except Exception:
            raise AcquisitionError("response_structure_changed") from None
        title = parser.metadata.get("og:title")
        canonical = parser.canonical
        if not title or not canonical or parser.metadata.get("og:type") != "article":
            raise AcquisitionError("response_structure_changed")
        if (
            len(title) > 512
            or "\r" in title
            or "\n" in title
            or sanitize(title, environ={}) != title
        ):
            raise AcquisitionError("response_structure_changed")
        if validate_public_url(canonical).url != ARTICLE_URL:
            raise AcquisitionError("response_structure_changed")
        metadata = {"title": title}
        published = parser.metadata.get("article:published_time")
        if published:
            if (
                len(published) > 512
                or "\r" in published
                or "\n" in published
                or sanitize(published, environ={}) != published
            ):
                raise AcquisitionError("response_structure_changed")
            metadata["published_at"] = published
        return {
            "schema_version": 1,
            "record_type": "asset",
            "asset_id": ASSET_ID,
            "source_id": SOURCE_ID,
            "public_url": validate_public_url(response_url).url,
            "media_type": "text/html",
            "metadata": metadata,
        }


def _source_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "source",
        "source_id": SOURCE_ID,
        "public_url": ARTICLE_URL,
        "source_kind": "article",
        "metadata": {"adapter": SOURCE_NAME},
    }


def _blocked_asset() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "asset",
        "asset_id": ASSET_ID,
        "source_id": SOURCE_ID,
        "public_url": ARTICLE_URL,
        "media_type": "text/html",
        "metadata": {"inventory_status": "blocked"},
    }


def _job_record(result: str, max_attempts: int) -> dict[str, object]:
    blocked = result == "blocked"
    return {
        "schema_version": 1,
        "record_type": "job",
        "job_id": JOB_ID,
        "asset_id": ASSET_ID,
        "operation": "public_metadata_inventory",
        "status": "blocked" if blocked else "completed",
        "required_capabilities": ["network-acquisition"],
        "retry_state": "exhausted",
        "attempt_count": 1,
        "max_attempts": max(1, max_attempts),
        "checkpoint": {
            "sequence": 1,
            "summary": (
                "Public metadata inventory stopped at a durable blocker."
                if blocked
                else "Public metadata inventory completed within configured bounds."
            ),
        },
    }


def _public_reference(url: str) -> str:
    parsed = urlsplit(validate_public_url(url).url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _json_summary(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class _Runner:
    def __init__(
        self,
        config: AcquisitionConfig,
        ledger: Ledger,
        transport: HTTPTransport,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.config = config
        self.ledger = ledger
        self.transport = transport
        self.clock = clock
        self.sleep = sleep
        self.started = clock()
        self.requests = _request_facts(ledger)
        self.run_request_count = 0
        self.rate_limiter = HostRateLimiter(
            {"antiegg.kr": config.rate_limit_seconds},
            clock=clock,
            sleep=self._bounded_sleep,
        )
        self.retry_policy = RetryPolicy(
            max_attempts=config.max_retries + 1,
            max_elapsed_backoff=config.max_elapsed_seconds,
            base_delay=min(1.0, config.timeout_seconds),
            max_retry_after=config.timeout_seconds,
            transient_outcomes=frozenset({"http_429", "http_5xx", "network_error"}),
        )

    def _remaining(self) -> float:
        return self.config.max_elapsed_seconds - (self.clock() - self.started)

    def _wait(self, delay: float) -> bool:
        if delay <= 0:
            return True
        if delay > self._remaining():
            return False
        self.sleep(delay)
        return self._remaining() > 0

    def _bounded_sleep(self, delay: float) -> None:
        if not self._wait(delay):
            raise AcquisitionError("elapsed_time_exhausted")

    def _record(
        self, response: HTTPResponse, retry_outcome: str
    ) -> dict[str, object]:
        body_hash = (
            None
            if response.oversized
            else hashlib.sha256(response.body).hexdigest()
        )
        fact: dict[str, object] = {
            "public_url": _public_reference(response.url),
            "status": response.status,
            "mime_type": response.mime_type or "unknown",
            "byte_count": (
                response.declared_bytes
                if response.declared_bytes is not None
                else len(response.body)
            ),
            "recorded_at": utc_text(),
            "retry_outcome": retry_outcome,
            "response_sha256": body_hash,
        }
        evidence = {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"evidence_antiegg_fluxus_request_{len(self.requests) + 1:03d}",
            "subject_id": SOURCE_ID,
            "evidence_kind": "sanitized_public_request",
            "recorded_at": fact["recorded_at"],
            "summary": _json_summary(
                {key: value for key, value in fact.items() if key != "public_url"}
            ),
            "public_references": [fact["public_url"]],
        }
        self.ledger.upsert(evidence)
        self.requests.append(fact)
        return fact

    def get(self, url: str) -> tuple[HTTPResponse | None, str | None]:
        current = validate_public_url(url).url
        retry_state = RetryState()
        redirects = 0
        while True:
            if self.run_request_count >= self.config.max_requests:
                return None, "request_budget_exhausted"
            if self._remaining() <= 0:
                return None, "elapsed_time_exhausted"
            validated = validate_public_url(current)
            try:
                self.rate_limiter.acquire(validated.hostname)
            except AcquisitionError:
                return None, "elapsed_time_exhausted"
            timeout = min(self.config.timeout_seconds, self._remaining())
            if timeout <= 0:
                return None, "elapsed_time_exhausted"
            try:
                response = self.transport.get(
                    current,
                    timeout_seconds=timeout,
                    max_response_bytes=self.config.max_response_bytes,
                )
            except AcquisitionError:
                self.run_request_count += 1
                decision = plan_retry(
                    self.retry_policy, retry_state, "network_error"
                )
                can_retry = (
                    decision.retry
                    and self.run_request_count < self.config.max_requests
                )
                self._record(
                    HTTPResponse(
                        url=current,
                        status=0,
                        mime_type="unknown",
                        body=b"",
                    ),
                    "retry_scheduled" if can_retry else "retry_exhausted",
                )
                if not can_retry:
                    return None, "request_failed"
                retry_state = decision.state
                if not self._wait(decision.delay):
                    return None, "elapsed_time_exhausted"
                continue
            self.run_request_count += 1
            if (
                response.oversized
                or len(response.body) > self.config.max_response_bytes
                or (
                    response.declared_bytes is not None
                    and response.declared_bytes > self.config.max_response_bytes
                )
            ):
                response = HTTPResponse(
                    url=response.url,
                    status=response.status,
                    mime_type=response.mime_type,
                    body=b"",
                    declared_bytes=response.declared_bytes,
                    retry_after=response.retry_after,
                    location=response.location,
                    oversized=True,
                )
            if response.status in _REDIRECT_STATUSES:
                self._record(response, "redirect")
                if not response.location or redirects >= 3:
                    return None, "redirect_blocked"
                try:
                    target = validate_redirect(current, response.location)
                except AcquisitionPolicyError:
                    return None, "redirect_blocked"
                if target.hostname != "antiegg.kr":
                    return None, "redirect_blocked"
                current = target.url
                redirects += 1
                continue
            if response.status == 429 or 500 <= response.status <= 599:
                outcome = "http_429" if response.status == 429 else "http_5xx"
                decision = plan_retry(
                    self.retry_policy,
                    retry_state,
                    outcome,
                    retry_after=response.retry_after,
                    now=datetime.now(timezone.utc),
                )
                can_retry = (
                    decision.retry
                    and self.run_request_count < self.config.max_requests
                )
                self._record(response, "retry_scheduled" if can_retry else "retry_exhausted")
                if not can_retry:
                    return response, (
                        "rate_limit_exhausted"
                        if response.status == 429
                        else "retry_exhausted"
                    )
                retry_state = decision.state
                if not self._wait(decision.delay):
                    return response, "elapsed_time_exhausted"
                continue
            self._record(response, "not_retried")
            return response, None


def _write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    if not path.parent.is_dir():
        raise AcquisitionError("manifest parent directory must already exist")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(manifest, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _request_facts(ledger: Ledger) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for index in range(1, 1000):
        record = ledger.get_record(
            "evidence", f"evidence_antiegg_fluxus_request_{index:03d}"
        )
        if record is None:
            break
        fact = json.loads(record["summary"])
        fact["public_url"] = record["public_references"][0]
        facts.append(fact)
    return facts


def _blocker(ledger: Ledger) -> dict[str, str] | None:
    record = ledger.get_record("evidence", "evidence_antiegg_fluxus_blocker")
    return None if record is None else json.loads(record["summary"])


def _manifest(ledger: Ledger) -> dict[str, object]:
    source = ledger.get_record("source", SOURCE_ID)
    asset = ledger.get_record("asset", ASSET_ID)
    state = ledger.asset_state(ASSET_ID)
    blocker = _blocker(ledger)
    value: dict[str, object] = {
        "schema_version": 1,
        "manifest_type": "public_metadata_inventory",
        "source": source,
        "assets": [] if asset is None else [asset],
        "requests": _request_facts(ledger),
        "result": "blocked" if blocker is not None else "completed",
    }
    if blocker is not None:
        value["blocker"] = blocker
    value["state_counts"] = {} if state is None else {state: 1}
    return value


def _finish_blocked(
    ledger: Ledger,
    code: str,
    next_safe_action: str,
    config: AcquisitionConfig,
) -> dict[str, object]:
    asset = ledger.get_record("asset", ASSET_ID)
    if asset is None:
        ledger.upsert(_blocked_asset())
    if ledger.asset_state(ASSET_ID) != "blocked":
        ledger.transition_asset(
            ASSET_ID,
            "blocked",
            blocker=f"{code}: {next_safe_action}",
        )
    blocker = {"code": code, "next_safe_action": next_safe_action}
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": "evidence_antiegg_fluxus_blocker",
            "subject_id": ASSET_ID,
            "evidence_kind": "public_inventory_blocker",
            "recorded_at": utc_text(),
            "summary": _json_summary(blocker),
            "public_references": [ARTICLE_URL],
        }
    )
    ledger.create_job(_job_record("blocked", config.max_retries + 1))
    return _manifest(ledger)


def inventory_public_source(
    config: AcquisitionConfig,
    *,
    transport: HTTPTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Inventory one public article within explicit request and elapsed bounds."""

    adapter = AntieggFluxusAdapter()
    ledger_path = Path(config.ledger_path).resolve()
    manifest_path = Path(config.manifest_path).resolve()
    if not ledger_path.parent.is_dir():
        raise AcquisitionError("ledger parent directory must already exist")
    try:
        with Ledger(ledger_path) as ledger:
            ledger.upsert(_source_record())
            state = ledger.asset_state(ASSET_ID)
            if state in {"metadata_verified", "blocked"}:
                manifest = _manifest(ledger)
                _write_manifest(manifest_path, manifest)
                return manifest

            runner = _Runner(
                config,
                ledger,
                transport or UrllibGETTransport(),
                clock=clock,
                sleep=sleep,
            )
            robots, failure = runner.get(adapter.robots_url)
            if failure is not None:
                manifest = _finish_blocked(
                    ledger, failure, "review the bounded request failure", config
                )
            elif robots is None:
                manifest = _finish_blocked(
                    ledger,
                    "request_failed",
                    "review the bounded request failure",
                    config,
                )
            elif robots.status in {401, 403}:
                manifest = _finish_blocked(
                    ledger,
                    "login_required" if robots.status == 401 else "access_forbidden",
                    "use a different unauthenticated public metadata source",
                    config,
                )
            elif robots.oversized:
                manifest = _finish_blocked(
                    ledger,
                    "response_oversized",
                    "reduce the response bound only after source review",
                    config,
                )
            elif robots.status == 404:
                manifest = {}
            elif robots.status != 200 or robots.mime_type not in {
                "text/plain",
                "text/plain;charset=utf-8",
            }:
                manifest = _finish_blocked(
                    ledger,
                    "unexpected_mime_type",
                    "review the public robots metadata format",
                    config,
                )
            else:
                parser = RobotFileParser()
                try:
                    parser.parse(robots.body.decode("utf-8").splitlines())
                    allowed = parser.can_fetch(USER_AGENT, adapter.catalogue_url)
                except UnicodeDecodeError:
                    allowed = False
                if not allowed:
                    manifest = _finish_blocked(
                        ledger,
                        "robots_denied",
                        "review robots policy or select another public source",
                        config,
                    )
                else:
                    manifest = {}

            if not manifest:
                page, failure = runner.get(adapter.catalogue_url)
                if failure is not None:
                    manifest = _finish_blocked(
                        ledger,
                        failure,
                        "use a different public metadata source or lower request pressure",
                        config,
                    )
                elif page is None:
                    manifest = _finish_blocked(
                        ledger,
                        "request_failed",
                        "review the bounded request failure",
                        config,
                    )
                elif page.status in {401, 403}:
                    manifest = _finish_blocked(
                        ledger,
                        "login_required" if page.status == 401 else "access_forbidden",
                        "use a different unauthenticated public metadata source",
                        config,
                    )
                elif page.oversized or (
                    page.declared_bytes is not None
                    and page.declared_bytes > config.max_response_bytes
                ):
                    manifest = _finish_blocked(
                        ledger,
                        "response_oversized",
                        "keep the metadata response within the configured byte bound",
                        config,
                    )
                elif page.status != 200:
                    manifest = _finish_blocked(
                        ledger,
                        "http_status_blocked",
                        "review the public source status without bypassing access controls",
                        config,
                    )
                elif page.mime_type not in {"text/html", "application/xhtml+xml"}:
                    manifest = _finish_blocked(
                        ledger,
                        "unexpected_mime_type",
                        "select a metadata-only public response",
                        config,
                    )
                else:
                    try:
                        asset = adapter.parse_asset(page.body, page.url)
                    except (AcquisitionError, AcquisitionPolicyError):
                        manifest = _finish_blocked(
                            ledger,
                            "response_structure_changed",
                            "review the adapter against current public metadata structure",
                            config,
                        )
                    else:
                        try:
                            ledger.upsert(asset)
                            ledger.transition_asset(ASSET_ID, "metadata_verified")
                            ledger.create_job(
                                _job_record("completed", config.max_retries + 1)
                            )
                        except (LedgerError, ValidationError):
                            manifest = _finish_blocked(
                                ledger,
                                "response_structure_changed",
                                "review extracted metadata against the public record schema",
                                config,
                            )
                        else:
                            manifest = _manifest(ledger)
            _write_manifest(manifest_path, manifest)
            return manifest
    except (LedgerError, OSError) as error:
        raise AcquisitionError(
            f"public metadata inventory failed: {type(error).__name__}"
        ) from None
