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
from datetime import datetime, timedelta, timezone
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
ROBOTS_OBSERVATION_TTL = timedelta(hours=24)
_ROBOTS_OBSERVATION_ID = "evidence_antiegg_fluxus_robots_observation"
REQUEST_EVIDENCE_PREFIX = "evidence_antiegg_fluxus_request"
#: Ceiling on one ledger's request-evidence trail. Writer and reader share the
#: `{index:03d}` spelling, so they agree past 999; this bound only keeps the
#: reader's gap scan finite.
MAX_REQUEST_EVIDENCE = 100_000
_RECORDED_BOUNDS_ID = "evidence_antiegg_fluxus_recorded_bounds"
#: Response headers this lane is allowed to keep. Everything else — cookies,
#: server banners, cache keys — is dropped inside the transport and never
#: reaches a record.
CAPTURED_RESPONSE_HEADERS = ("x-wp-total", "x-wp-totalpages")
#: Bounds that decide whether a stored terminal result still describes the run
#: an operator is asking for. A stored blocker recorded under different bounds
#: is a replay, not an answer, and the manifest has to say so.
BOUND_FIELDS = (
    "max_elapsed_seconds",
    "max_requests",
    "max_response_bytes",
    "max_retries",
    "rate_limit_seconds",
    "timeout_seconds",
)


def _utc_wall_clock() -> datetime:
    return datetime.now(timezone.utc)


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
    observed_bytes: int | None = None
    retry_after: str | None = None
    location: str | None = None
    oversized: bool = False
    #: Only `CAPTURED_RESPONSE_HEADERS`. Pagination totals are the one header
    #: fact this lane needs, and they never enter a durable record.
    headers: Mapping[str, str] | None = None


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

    def __init__(self, *, accept: str = "text/html,text/plain") -> None:
        self._opener = urlrequest.build_opener(_NoRedirect)
        self._accept = accept

    def get(
        self, url: str, *, timeout_seconds: float, max_response_bytes: int
    ) -> HTTPResponse:
        request = urlrequest.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": self._accept},
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
                    if declared < 0:
                        declared = None
            except (TypeError, ValueError):
                declared = None
            oversized = declared is not None and declared > max_response_bytes
            body = b"" if oversized else opened.read(max_response_bytes + 1)
            observed = len(body)
            if len(body) > max_response_bytes:
                body = b""
                oversized = True
            captured = {
                name: str(headers[name])
                for name in CAPTURED_RESPONSE_HEADERS
                if headers.get(name) is not None
            }
            return HTTPResponse(
                url=opened.geturl(),
                status=int(opened.status),
                mime_type=content_type,
                body=body,
                declared_bytes=declared,
                observed_bytes=observed,
                retry_after=headers.get("Retry-After"),
                location=headers.get("Location"),
                oversized=oversized,
                headers=captured,
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


def json_summary(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class BoundedRequestRunner:
    def __init__(
        self,
        config: AcquisitionConfig,
        ledger: Ledger,
        transport: HTTPTransport,
        *,
        clock: Callable[[], float],
        wall_clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        source_id: str = SOURCE_ID,
        evidence_prefix: str = REQUEST_EVIDENCE_PREFIX,
    ) -> None:
        self.config = config
        self.ledger = ledger
        self.transport = transport
        self.clock = clock
        self.wall_clock = wall_clock
        self.sleep = sleep
        self.source_id = source_id
        self.evidence_prefix = evidence_prefix
        self.started = clock()
        self.requests = _request_facts(ledger, prefix=evidence_prefix)
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
                and response.declared_bytes >= 0
                else (
                    response.observed_bytes
                    if response.observed_bytes is not None
                    and response.observed_bytes >= 0
                    else len(response.body)
                )
            ),
            "recorded_at": utc_text(self.wall_clock()),
            "retry_outcome": retry_outcome,
            "response_sha256": body_hash,
        }
        sequence = len(self.requests) + 1
        if sequence > MAX_REQUEST_EVIDENCE:
            raise AcquisitionError("request evidence trail is full; use a fresh ledger")
        evidence = {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"{self.evidence_prefix}_{sequence:03d}",
            "subject_id": self.source_id,
            "evidence_kind": "sanitized_public_request",
            "recorded_at": fact["recorded_at"],
            "summary": json_summary(
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
                observed_bytes = (
                    response.observed_bytes
                    if response.observed_bytes is not None
                    else len(response.body)
                )
                observed_bytes = min(
                    observed_bytes, self.config.max_response_bytes + 1
                )
                response = HTTPResponse(
                    url=response.url,
                    status=response.status,
                    mime_type=response.mime_type,
                    body=b"",
                    declared_bytes=response.declared_bytes,
                    observed_bytes=observed_bytes,
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
                    now=self.wall_clock(),
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


def write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
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


def _request_facts(
    ledger: Ledger, *, prefix: str = REQUEST_EVIDENCE_PREFIX
) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for index in range(1, MAX_REQUEST_EVIDENCE + 1):
        record = ledger.get_record("evidence", f"{prefix}_{index:03d}")
        if record is None:
            break
        fact = json.loads(record["summary"])
        fact["public_url"] = record["public_references"][0]
        facts.append(fact)
    return facts


def _blocker(ledger: Ledger) -> dict[str, str] | None:
    record = ledger.get_record("evidence", "evidence_antiegg_fluxus_blocker")
    return None if record is None else json.loads(record["summary"])


def bounds_of(config: object) -> dict[str, object]:
    """The bounds a terminal result was produced under."""

    return {name: getattr(config, name) for name in BOUND_FIELDS}


def record_bounds(
    ledger: Ledger,
    config: object,
    *,
    subject_id: str,
    evidence_id: str,
) -> None:
    """Pin the bounds a terminal result was produced under, for replay.

    The first terminal result wins: these are the bounds that produced the
    stored answer, not the bounds of whoever resumed the ledger later.
    """

    if ledger.get_record("evidence", evidence_id) is not None:
        return
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": evidence_id,
            "subject_id": subject_id,
            "evidence_kind": "sanitized_run_bounds",
            "recorded_at": utc_text(),
            "summary": json_summary(bounds_of(config)),
            "public_references": [],
        }
    )


def recorded_bounds(
    ledger: Ledger, *, evidence_id: str
) -> dict[str, object] | None:
    record = ledger.get_record("evidence", evidence_id)
    if record is None:
        return None
    try:
        value = json.loads(str(record["summary"]))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def stored_result_replay(
    ledger: Ledger, config: object, *, evidence_id: str
) -> dict[str, object]:
    """Say plainly that a stored result is being replayed, and under which bounds.

    A resumed run that silently reprints its stored blocker teaches an operator
    that the flag they just raised does nothing. The bounds go in the manifest
    so the two runs can be told apart.
    """

    current = bounds_of(config)
    recorded = recorded_bounds(ledger, evidence_id=evidence_id)
    if recorded is None:
        next_safe_action = (
            "this stored result predates bound recording; re-run with a fresh "
            "ledger to re-attempt under the current bounds"
        )
    elif recorded != current:
        next_safe_action = (
            "this stored result was recorded under different bounds; re-run "
            "with a fresh ledger to re-attempt under the current bounds"
        )
    else:
        next_safe_action = (
            "this stored result was recorded under these same bounds; no new "
            "public request was made"
        )
    return {
        "replayed_stored_result": True,
        "recorded_bounds": recorded,
        "current_bounds": current,
        "bounds_changed": recorded is not None and recorded != current,
        "next_safe_action": next_safe_action,
    }


def _robots_observation_records(
    ledger: Ledger,
) -> list[tuple[str, dict[str, object]]]:
    records: list[tuple[str, dict[str, object]]] = []
    legacy = ledger.get_record("evidence", _ROBOTS_OBSERVATION_ID)
    if legacy is not None:
        records.append((_ROBOTS_OBSERVATION_ID, legacy))
    for index in range(1, len(_request_facts(ledger)) + 1):
        evidence_id = f"{_ROBOTS_OBSERVATION_ID}_{index:03d}"
        record = ledger.get_record("evidence", evidence_id)
        if record is not None:
            records.append((evidence_id, record))
    return records


def _parse_robots_observation(
    record: Mapping[str, object],
) -> dict[str, object] | None:
    try:
        observation = json.loads(str(record["summary"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        record.get("subject_id") != SOURCE_ID
        or record.get("evidence_kind") != "sanitized_robots_observation"
        or record.get("public_references") != [ROBOTS_URL]
        or not isinstance(observation, dict)
        or set(observation) != {"catalogue_allowed", "outcome", "status"}
        or (
            observation.get("catalogue_allowed"),
            observation.get("outcome"),
            observation.get("status"),
        )
        not in {
            (True, "allowed", 200),
            (True, "not_found", 404),
            (False, "denied", 200),
        }
    ):
        return None
    return observation


def _robots_observation(ledger: Ledger) -> dict[str, object] | None:
    records = _robots_observation_records(ledger)
    if not records:
        return None
    return _parse_robots_observation(records[-1][1])


def _request_supports_robots_observation(
    record: Mapping[str, object],
    observation_record: Mapping[str, object],
    observation: Mapping[str, object],
) -> bool:
    try:
        fact = json.loads(str(record["summary"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        record.get("subject_id") == SOURCE_ID
        and record.get("evidence_kind") == "sanitized_public_request"
        and record.get("public_references") == [ROBOTS_URL]
        and record.get("recorded_at") == observation_record.get("recorded_at")
        and isinstance(fact, dict)
        and fact.get("recorded_at") == observation_record.get("recorded_at")
        and fact.get("status") == observation.get("status")
        and fact.get("retry_outcome") == "not_retried"
    )


def _fresh_robots_observation(
    ledger: Ledger, now: datetime
) -> dict[str, object] | None:
    records = _robots_observation_records(ledger)
    if not records:
        return None
    evidence_id, record = records[-1]
    observation = _parse_robots_observation(record)
    try:
        checked_at = datetime.fromisoformat(
            str(record["recorded_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if now.tzinfo is None:
            return None
        current = now.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    if (
        observation is None
        or observation.get("catalogue_allowed") is not True
        or checked_at > current
        or current - checked_at > ROBOTS_OBSERVATION_TTL
    ):
        return None
    request_records: list[dict[str, object]] = []
    suffix = evidence_id.removeprefix(f"{_ROBOTS_OBSERVATION_ID}_")
    if evidence_id != _ROBOTS_OBSERVATION_ID and suffix.isdigit():
        request = ledger.get_record(
            "evidence", f"evidence_antiegg_fluxus_request_{suffix}"
        )
        if request is not None:
            request_records.append(request)
    else:
        for index in range(1, MAX_REQUEST_EVIDENCE + 1):
            request = ledger.get_record(
                "evidence", f"{REQUEST_EVIDENCE_PREFIX}_{index:03d}"
            )
            if request is None:
                break
            request_records.append(request)
    if not any(
        _request_supports_robots_observation(request, record, observation)
        for request in request_records
    ):
        return None
    return observation


def _record_robots_observation(
    ledger: Ledger,
    *,
    outcome: str,
    status: int,
    request_index: int,
    recorded_at: str,
) -> dict[str, object]:
    observation: dict[str, object] = {
        "catalogue_allowed": outcome != "denied",
        "outcome": outcome,
        "status": status,
    }
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"{_ROBOTS_OBSERVATION_ID}_{request_index:03d}",
            "subject_id": SOURCE_ID,
            "evidence_kind": "sanitized_robots_observation",
            "recorded_at": recorded_at,
            "summary": json_summary(observation),
            "public_references": [ROBOTS_URL],
        }
    )
    return observation


def _manifest(ledger: Ledger) -> dict[str, object]:
    source = ledger.get_record("source", SOURCE_ID)
    asset = ledger.get_record("asset", ASSET_ID)
    state = ledger.asset_state(ASSET_ID)
    blocker = _blocker(ledger)
    robots_observation = _robots_observation(ledger)
    requests = _request_facts(ledger)
    value: dict[str, object] = {
        "schema_version": 1,
        "manifest_type": "public_metadata_inventory",
        "source": source,
        "assets": [] if asset is None else [asset],
        "requests": requests,
        "result": "blocked" if blocker is not None else "completed",
        "record_counts": {
            "assets": 0 if asset is None else 1,
            "blockers": 0 if blocker is None else 1,
            "jobs": (
                0
                if ledger.get_record("job", JOB_ID) is None
                else 1
            ),
            "requests": len(requests),
        },
    }
    if robots_observation is not None:
        value["robots_observation"] = robots_observation
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
            "summary": json_summary(blocker),
            "public_references": [ARTICLE_URL],
        }
    )
    ledger.create_job(_job_record("blocked", config.max_retries + 1))
    record_bounds(
        ledger,
        config,
        subject_id=ASSET_ID,
        evidence_id=_RECORDED_BOUNDS_ID,
    )
    return _manifest(ledger)


def inventory_public_source(
    config: AcquisitionConfig,
    *,
    transport: HTTPTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = _utc_wall_clock,
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
                manifest["stored_result_replay"] = stored_result_replay(
                    ledger, config, evidence_id=_RECORDED_BOUNDS_ID
                )
                write_manifest(manifest_path, manifest)
                return manifest

            runner = BoundedRequestRunner(
                config,
                ledger,
                transport or UrllibGETTransport(),
                clock=clock,
                wall_clock=wall_clock,
                sleep=sleep,
            )
            robots_observation = _fresh_robots_observation(ledger, wall_clock())
            if robots_observation is not None:
                manifest = {}
            else:
                robots, failure = runner.get(adapter.robots_url)
                if failure is not None:
                    manifest = _finish_blocked(
                        ledger,
                        failure,
                        "review the bounded request failure",
                        config,
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
                        (
                            "login_required"
                            if robots.status == 401
                            else "access_forbidden"
                        ),
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
                    _record_robots_observation(
                        ledger,
                        outcome="not_found",
                        status=robots.status,
                        request_index=len(runner.requests),
                        recorded_at=str(runner.requests[-1]["recorded_at"]),
                    )
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
                        allowed = parser.can_fetch(
                            USER_AGENT, adapter.catalogue_url
                        )
                    except UnicodeDecodeError:
                        allowed = False
                    if not allowed:
                        _record_robots_observation(
                            ledger,
                            outcome="denied",
                            status=robots.status,
                            request_index=len(runner.requests),
                            recorded_at=str(runner.requests[-1]["recorded_at"]),
                        )
                        manifest = _finish_blocked(
                            ledger,
                            "robots_denied",
                            "review robots policy or select another public source",
                            config,
                        )
                    else:
                        _record_robots_observation(
                            ledger,
                            outcome="allowed",
                            status=robots.status,
                            request_index=len(runner.requests),
                            recorded_at=str(runner.requests[-1]["recorded_at"]),
                        )
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
                            record_bounds(
                                ledger,
                                config,
                                subject_id=ASSET_ID,
                                evidence_id=_RECORDED_BOUNDS_ID,
                            )
                            manifest = _manifest(ledger)
            write_manifest(manifest_path, manifest)
            return manifest
    except (LedgerError, OSError) as error:
        raise AcquisitionError(
            f"public metadata inventory failed: {type(error).__name__}"
        ) from None
