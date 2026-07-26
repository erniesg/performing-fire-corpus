"""Bounded trusted-VM preflight for the two NJP Center site sources."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.robotparser import RobotFileParser
from urllib.parse import parse_qs, urlsplit

from performing_fire_corpus.njp_center_adapters import (
    NJPCenterMainAdapter,
    NJPCenterVideoArchiveAdapter,
)
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
USER_AGENT = "performing-fire-corpus/0.1"
ROBOTS_URL = "https://njp.ggcf.kr/robots.txt"
SOURCE_ADAPTERS = {
    "njp-center-main": NJPCenterMainAdapter,
    "njp-center-video-archive": NJPCenterVideoArchiveAdapter,
}
SOURCE_MECHANISMS = {
    "njp-center-main": "reviewed_mediaobjects_fragment_html",
    "njp-center-video-archive": "registered_archive_page_html",
}
_RUN_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class NJPInventoryError(RuntimeError):
    """A content-free failure at the live proof boundary."""


@dataclass(frozen=True)
class InventoryLimits:
    max_requests: int = 6
    max_pages: int = 5
    max_response_bytes: int = 65536
    aggregate_bytes: int = 131072
    max_retries: int = 1
    retry_after_seconds: float = 2.0
    per_host_interval_seconds: float = 1.0
    timeout_seconds: float = 10.0
    elapsed_seconds: float = 30.0

    def __post_init__(self) -> None:
        positive_integer_values = (
            self.max_requests,
            self.max_pages,
            self.max_response_bytes,
            self.aggregate_bytes,
        )
        float_values = (
            self.retry_after_seconds,
            self.per_host_interval_seconds,
            self.timeout_seconds,
            self.elapsed_seconds,
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                for value in positive_integer_values
            )
            or isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                for value in float_values
            )
            or self.max_response_bytes > self.aggregate_bytes
        ):
            raise NJPInventoryError("invalid_inventory_limits")

    def as_dict(self) -> dict[str, int | float]:
        return {
            "aggregate_bytes": self.aggregate_bytes,
            "elapsed_seconds": self.elapsed_seconds,
            "max_pages": self.max_pages,
            "max_requests": self.max_requests,
            "max_response_bytes": self.max_response_bytes,
            "max_retries": self.max_retries,
            "retry_after_seconds": self.retry_after_seconds,
            "per_host_interval_seconds": self.per_host_interval_seconds,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class MetadataSafeResponse:
    url: str
    status: int
    mime_type: str
    body: bytes
    declared_bytes: int | None = None
    retry_after_seconds: float | None = None
    location: str | None = None
    oversized: bool = False
    failure_code: str | None = None


class PreflightTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MetadataSafeResponse: ...


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


class UrllibPreflightTransport:
    """Unauthenticated GET/HEAD transport with redirects disabled."""

    def __init__(self) -> None:
        self._opener = urlrequest.build_opener(_NoRedirect)

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MetadataSafeResponse:
        if not _reviewed_request(method, url):
            raise NJPInventoryError("request_outside_reviewed_boundary")
        request = urlrequest.Request(
            url,
            headers={
                "Accept": "text/plain" if url == ROBOTS_URL else "text/html",
                "User-Agent": USER_AGENT,
            },
            method=method,
        )
        try:
            opened = self._opener.open(request, timeout=timeout_seconds)
        except urlerror.HTTPError as error:
            opened = error
        except (OSError, TimeoutError, urlerror.URLError) as error:
            raise NJPInventoryError(
                f"transport_{type(error).__name__.lower()}"
            ) from None
        with opened:
            headers = opened.headers
            mime_type = str(headers.get("Content-Type", "")).split(";", 1)[0].lower()
            declared_bytes: int | None = None
            try:
                if headers.get("Content-Length") is not None:
                    declared_bytes = int(headers["Content-Length"])
            except (TypeError, ValueError):
                declared_bytes = None
            oversized = (
                declared_bytes is not None
                and declared_bytes > max_response_bytes
                and method == "GET"
            )
            body = b""
            if method == "GET" and not oversized:
                body = opened.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    body = b""
                    oversized = True
            retry_after: float | None = None
            try:
                if headers.get("Retry-After") is not None:
                    retry_after = float(headers["Retry-After"])
            except (TypeError, ValueError):
                retry_after = None
            return MetadataSafeResponse(
                url=opened.geturl(),
                status=int(opened.status),
                mime_type=mime_type,
                body=body,
                declared_bytes=declared_bytes,
                retry_after_seconds=retry_after,
                location=headers.get("Location"),
                oversized=oversized,
            )


def _reviewed_request(method: str, url: str) -> bool:
    if method == "GET" and url == ROBOTS_URL:
        return True
    if method == "HEAD" and url == NJPCenterVideoArchiveAdapter.public_url:
        return True
    if method != "GET":
        return False
    try:
        parsed = urlsplit(url)
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
        return (
            parsed.scheme == "https"
            and parsed.netloc == "njp.ggcf.kr"
            and parsed.path == "/mediaObjects/more"
            and not parsed.fragment
            and set(query) == {"page"}
            and len(query["page"]) == 1
            and re.fullmatch(r"[1-9][0-9]{0,3}", query["page"][0]) is not None
        )
    except (TypeError, ValueError):
        return False


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise NJPInventoryError("naive_inventory_time")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if sanitize(value, environ={}) != value:
        raise NJPInventoryError("unsafe_inventory_artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _policy_snapshot(
    source_id: str,
    governance: Mapping[str, Any],
) -> dict[str, Any]:
    adapter = SOURCE_ADAPTERS[source_id]
    records = [
        record
        for record in governance.get("records", [])
        if isinstance(record, Mapping)
        and record.get("source_id") == source_id
        and record.get("endpoint_id") == adapter.endpoint_id
    ]
    if len(records) != 1:
        raise NJPInventoryError("missing_source_governance")
    record = records[0]
    return {
        "record_type": "njp_inventory_policy_snapshot",
        "schema_version": 1,
        "source_id": source_id,
        "endpoint_id": adapter.endpoint_id,
        "page_mechanism": SOURCE_MECHANISMS[source_id],
        "fact_states": dict(record["fact_states"]),
        "operation_states": {
            "metadata_inventory": record["operation_states"]["metadata_inventory"],
            "retention": record["operation_states"]["retention"],
        },
        "source_shape_state": "unreviewed",
        "attachment_policy": {
            "discovery_state": "candidate_only",
            "rights_state": "pending",
            "allowed_probe": "head_only_after_explicit_policy_approval",
            "retry_after_403": False,
        },
    }


class _RunLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS njp_inventory_run (
                run_id TEXT PRIMARY KEY,
                plan_json TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL,
                report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS njp_inventory_request (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                attempt INTEGER NOT NULL,
                fact_json TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence, attempt)
            );
            CREATE TABLE IF NOT EXISTS njp_inventory_blocker (
                run_id TEXT NOT NULL,
                blocker_id TEXT NOT NULL,
                blocker_json TEXT NOT NULL,
                PRIMARY KEY (run_id, blocker_id)
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def start(
        self,
        run_id: str,
        plan: Mapping[str, Any],
        policy: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        row = self.connection.execute(
            "SELECT * FROM njp_inventory_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO njp_inventory_run VALUES (?, ?, ?, ?, NULL)",
                (run_id, _canonical(plan), _canonical(policy), _canonical(checkpoint)),
            )
            self.connection.commit()
            return dict(checkpoint), None
        if row["plan_json"] != _canonical(plan) or row["policy_json"] != _canonical(policy):
            raise NJPInventoryError("run_plan_changed")
        return (
            json.loads(row["checkpoint_json"]),
            None if row["report_json"] is None else json.loads(row["report_json"]),
        )

    def commit_request(
        self,
        run_id: str,
        fact: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
    ) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO njp_inventory_request VALUES (?, ?, ?, ?)",
            (
                run_id,
                fact["sequence"],
                fact["attempt"],
                _canonical(fact),
            ),
        )
        stored = self.connection.execute(
            """
            SELECT fact_json FROM njp_inventory_request
            WHERE run_id = ? AND sequence = ? AND attempt = ?
            """,
            (run_id, fact["sequence"], fact["attempt"]),
        ).fetchone()
        if stored is None or stored["fact_json"] != _canonical(fact):
            raise NJPInventoryError("request_identity_changed")
        self.connection.execute(
            "UPDATE njp_inventory_run SET checkpoint_json = ? WHERE run_id = ?",
            (_canonical(checkpoint), run_id),
        )
        self.connection.commit()

    def finish(
        self,
        run_id: str,
        blockers: list[Mapping[str, Any]],
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        for blocker in blockers:
            self.connection.execute(
                "INSERT OR IGNORE INTO njp_inventory_blocker VALUES (?, ?, ?)",
                (run_id, blocker["blocker_id"], _canonical(blocker)),
            )
        row = self.connection.execute(
            "SELECT report_json FROM njp_inventory_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise NJPInventoryError("missing_inventory_run")
        if row["report_json"] is not None:
            existing = json.loads(row["report_json"])
            if existing != report:
                raise NJPInventoryError("terminal_report_changed")
            return existing
        self.connection.execute(
            "UPDATE njp_inventory_run SET report_json = ? WHERE run_id = ?",
            (_canonical(report), run_id),
        )
        self.connection.commit()
        return dict(report)


def _request_fact(
    response: MetadataSafeResponse,
    *,
    method: str,
    sequence: int,
    attempt: int,
    observed_at: str,
    outcome: str,
) -> dict[str, Any]:
    return {
        "record_type": "njp_inventory_request_fact",
        "sequence": sequence,
        "attempt": attempt,
        "method": method,
        "status": response.status,
        "mime_type": response.mime_type or "unknown",
        "observed_bytes": len(response.body),
        "declared_bytes": response.declared_bytes,
        "response_sha256": (
            hashlib.sha256(response.body).hexdigest() if response.body else None
        ),
        "observed_at": observed_at,
        "outcome": outcome,
        "failure_code": response.failure_code,
    }


def _blocker(code: str, next_safe_action: str) -> dict[str, str]:
    return {
        "blocker_id": f"blocker_{code}",
        "code": code,
        "next_safe_action": next_safe_action,
    }


def _classify_robots(response: MetadataSafeResponse, endpoint_url: str) -> str:
    if response.failure_code is not None:
        return "transport_error"
    if response.status == 429:
        return "rate_limited"
    if response.status in {401, 403}:
        return "robots_access_blocked"
    if response.url != ROBOTS_URL or response.status != 200:
        return "robots_access_blocked"
    if response.oversized or response.mime_type not in {"text/plain", "text/robots"}:
        return "robots_ambiguous"
    try:
        text = response.body.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return "robots_ambiguous"
    if "user-agent:" not in text.lower():
        return "robots_ambiguous"
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)
    parser.parse(text.splitlines())
    return "robots_allowed" if parser.can_fetch(USER_AGENT, endpoint_url) else "robots_denied"


def _perform_request(
    transport: PreflightTransport,
    method: str,
    url: str,
    limits: InventoryLimits,
    *,
    sequence: int,
    now: Callable[[], datetime],
    sleeper: Callable[[float], None],
    requests_used: int,
) -> tuple[MetadataSafeResponse, int, int, list[tuple[MetadataSafeResponse, int]]]:
    attempt = 0
    attempts: list[tuple[MetadataSafeResponse, int]] = []
    while True:
        if requests_used >= limits.max_requests:
            raise NJPInventoryError("request_budget_exhausted")
        attempt += 1
        requests_used += 1
        try:
            response = transport.request(
                method,
                url,
                timeout_seconds=limits.timeout_seconds,
                max_response_bytes=limits.max_response_bytes,
            )
        except NJPInventoryError as error:
            failed_response = MetadataSafeResponse(
                url=url,
                status=599,
                mime_type="unknown",
                body=b"",
                failure_code=str(error),
            )
            attempts.append((failed_response, attempt))
            if attempt <= limits.max_retries:
                sleeper(limits.per_host_interval_seconds)
                continue
            return (
                failed_response,
                attempt,
                requests_used,
                attempts,
            )
        attempts.append((response, attempt))
        if (
            response.status != 429
            or attempt > limits.max_retries
            or response.retry_after_seconds is None
            or response.retry_after_seconds > limits.retry_after_seconds
        ):
            return response, attempt, requests_used, attempts
        sleeper(response.retry_after_seconds)


def run_source_preflight(
    source_id: str,
    *,
    run_label: str,
    state_root: Path,
    governance: Mapping[str, Any],
    limits: InventoryLimits,
    transport: PreflightTransport,
    now: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    if source_id not in SOURCE_ADAPTERS or not _RUN_LABEL.fullmatch(run_label):
        raise NJPInventoryError("invalid_inventory_identity")
    adapter = SOURCE_ADAPTERS[source_id]
    shape_bound = source_id == "njp-center-main"
    endpoint_url = (
        adapter().build_request(None).url
        if shape_bound
        else adapter.public_url
    )
    observed_at = _utc_text(now())
    source_root = state_root / source_id
    run_id = f"njp_inventory_{run_label}_{source_id.replace('-', '_')}"
    policy = _policy_snapshot(source_id, governance)
    plan = {
        "record_type": "njp_inventory_run_plan",
        "schema_version": 1,
        "run_id": run_id,
        "source_id": source_id,
        "endpoint_id": adapter.endpoint_id,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "robots_url": ROBOTS_URL,
        "endpoint_url": endpoint_url,
        "limits": limits.as_dict(),
        "allowed_methods": (
            ["GET robots.txt", "GET mediaObjects fragments"]
            if shape_bound
            else ["GET robots.txt", "HEAD registered endpoint"]
        ),
        "attachment_requests_allowed": False,
        "catalogue_body_requests_allowed": shape_bound,
    }
    checkpoint = {
        "record_type": "njp_inventory_checkpoint",
        "run_id": run_id,
        "phase": "initial",
        "requests_attempted": 0,
        "aggregate_bytes": 0,
        "pages_committed": 0,
        "next_cursor": None,
        "records": [],
    }
    _write_json(source_root / "run-plan.json", plan)
    _write_json(source_root / "policy-snapshot.json", policy)
    ledger = _RunLedger(source_root / "ledger.sqlite3")
    started = monotonic()
    try:
        checkpoint, existing = ledger.start(run_id, plan, policy, checkpoint)
        if existing is not None:
            _write_json(source_root / "checkpoint.json", checkpoint)
            _write_json(source_root / "completeness-report.json", existing)
            return existing
        blockers: list[dict[str, str]] = []
        requests_used = int(checkpoint["requests_attempted"])
        if checkpoint["phase"] == "initial":
            response, attempt, requests_used, attempts = _perform_request(
                transport,
                "GET",
                ROBOTS_URL,
                limits,
                sequence=1,
                now=now,
                sleeper=sleeper,
                requests_used=requests_used,
            )
            robots_outcome = _classify_robots(response, endpoint_url)
            checkpoint.update(
                {
                    "phase": "robots_checked",
                    "requests_attempted": requests_used,
                    "aggregate_bytes": len(response.body),
                    "robots_outcome": robots_outcome,
                }
            )
            for attempted_response, attempted_number in attempts:
                ledger.commit_request(
                    run_id,
                    _request_fact(
                        attempted_response,
                        method="GET",
                        sequence=1,
                        attempt=attempted_number,
                        observed_at=_utc_text(now()),
                        outcome=(
                            robots_outcome
                            if attempted_number == attempt
                            else "retry_scheduled"
                        ),
                    ),
                    checkpoint,
                )
            _write_json(source_root / "checkpoint.json", checkpoint)
        if monotonic() - started > limits.elapsed_seconds:
            blockers.append(
                _blocker("elapsed_bound", "Start a new reviewed bounded run.")
            )
        robots_outcome = checkpoint.get("robots_outcome")
        if not blockers and robots_outcome != "robots_allowed":
            blockers.append(
                _blocker(
                    str(robots_outcome),
                    "Review current robots behavior; do not request the source page.",
                )
            )
        if shape_bound and not blockers and checkpoint["phase"] == "robots_checked":
            live_adapter = adapter()
            while checkpoint["phase"] == "robots_checked":
                if checkpoint["pages_committed"] >= limits.max_pages:
                    blockers.append(
                        _blocker(
                            "page_budget_exhausted",
                            "Start a new reviewed run with a sufficient page bound.",
                        )
                    )
                    break
                if requests_used >= limits.max_requests:
                    blockers.append(
                        _blocker(
                            "request_budget_exhausted",
                            "Start a new reviewed run with a sufficient request bound.",
                        )
                    )
                    break
                request = live_adapter.build_request(checkpoint["next_cursor"])
                sleeper(limits.per_host_interval_seconds)
                response, attempt, requests_used, attempts = _perform_request(
                    transport,
                    request.method,
                    request.url,
                    limits,
                    sequence=2 + checkpoint["pages_committed"],
                    now=now,
                    sleeper=sleeper,
                    requests_used=requests_used,
                )
                sequence = 2 + checkpoint["pages_committed"]
                if response.failure_code is not None:
                    outcome = "transport_error"
                elif response.url != request.url or response.status in _REDIRECTS:
                    outcome = "disallowed_redirect"
                elif response.status in {401, 403}:
                    outcome = "access_forbidden"
                elif response.status == 429:
                    outcome = "rate_limited"
                elif response.status != 200:
                    outcome = "public_access_unconfirmed"
                elif response.oversized:
                    outcome = "response_oversized"
                elif response.mime_type != "text/html":
                    outcome = "mime_mismatch"
                elif (
                    checkpoint["aggregate_bytes"] + len(response.body)
                    > limits.aggregate_bytes
                ):
                    outcome = "aggregate_byte_bound"
                else:
                    outcome = "public_get_available"
                for attempted_response, attempted_number in attempts:
                    ledger.commit_request(
                        run_id,
                        _request_fact(
                            attempted_response,
                            method=request.method,
                            sequence=sequence,
                            attempt=attempted_number,
                            observed_at=_utc_text(now()),
                            outcome=(
                                outcome
                                if attempted_number == attempt
                                else "retry_scheduled"
                            ),
                        ),
                        checkpoint,
                    )
                checkpoint["requests_attempted"] = requests_used
                if outcome != "public_get_available":
                    checkpoint["access_outcome"] = outcome
                    blockers.append(
                        _blocker(
                            outcome,
                            "Keep the source blocked and review the sanitized request fact.",
                        )
                    )
                    break
                try:
                    page = live_adapter.parse_page(
                        response.body,
                        cursor=checkpoint["next_cursor"],
                    )
                except ValueError:
                    checkpoint["access_outcome"] = "public_get_available"
                    blockers.append(
                        _blocker(
                            "source_shape_changed",
                            "Review the current fragment before another inventory.",
                        )
                    )
                    break
                existing_ids = {
                    record["record_id"] for record in checkpoint["records"]
                }
                new_ids = [record["record_id"] for record in page["records"]]
                if existing_ids.intersection(new_ids):
                    blockers.append(
                        _blocker(
                            "duplicate_record",
                            "Review pagination before claiming completeness.",
                        )
                    )
                    break
                checkpoint["records"].extend(page["records"])
                checkpoint["pages_committed"] += 1
                checkpoint["aggregate_bytes"] += len(response.body)
                checkpoint["access_outcome"] = "public_get_available"
                checkpoint["next_cursor"] = page["next_cursor"]
                if page["terminal"]:
                    checkpoint["phase"] = "shape_bound_terminal"
                _write_json(source_root / "checkpoint.json", checkpoint)
        if not shape_bound and not blockers and checkpoint["phase"] == "robots_checked":
            sleeper(limits.per_host_interval_seconds)
            response, attempt, requests_used, attempts = _perform_request(
                transport,
                "HEAD",
                adapter.public_url,
                limits,
                sequence=2,
                now=now,
                sleeper=sleeper,
                requests_used=requests_used,
            )
            if response.failure_code is not None:
                access_outcome = "transport_error"
            elif response.url != adapter.public_url or response.status in _REDIRECTS:
                access_outcome = "disallowed_redirect"
            elif response.status in {401, 403}:
                access_outcome = "access_forbidden"
            elif response.status == 429:
                access_outcome = "rate_limited"
            elif response.status == 200:
                access_outcome = "public_head_available"
            else:
                access_outcome = "public_access_unconfirmed"
            checkpoint.update(
                {
                    "phase": "access_checked",
                    "requests_attempted": requests_used,
                    "access_outcome": access_outcome,
                }
            )
            for attempted_response, attempted_number in attempts:
                ledger.commit_request(
                    run_id,
                    _request_fact(
                        attempted_response,
                        method="HEAD",
                        sequence=2,
                        attempt=attempted_number,
                        observed_at=_utc_text(now()),
                        outcome=(
                            access_outcome
                            if attempted_number == attempt
                            else "retry_scheduled"
                        ),
                    ),
                    checkpoint,
                )
            _write_json(source_root / "checkpoint.json", checkpoint)
        expected_access = (
            "public_get_available" if shape_bound else "public_head_available"
        )
        if not blockers and checkpoint.get("access_outcome") != expected_access:
            access_code = str(
                checkpoint.get("access_outcome", "public_access_unconfirmed")
            )
            blockers.append(
                _blocker(
                    access_code,
                    (
                        "Keep this source blocked; do not retry or bypass access controls."
                        if access_code == "access_forbidden"
                        else "Keep this source blocked and review the sanitized access fact."
                    ),
                )
            )
        # Each policy blocker is derived from the governance record this run
        # already loaded. Appending them unconditionally would report a source
        # as rights-pending even after the operator recorded the decision, so a
        # recorded approval could never take effect.
        for code, action, satisfied in (
            (
                "platform_terms_pending",
                "Review and time-bound the terms decision for metadata inventory.",
                policy["fact_states"]["platform_terms"] == "permitted",
            ),
            (
                "copyright_rights_pending",
                "Record the lawful-basis and attachment-rights decision.",
                policy["fact_states"]["copyright_lawful_basis"] == "permitted",
            ),
            (
                "retention_pending",
                "Approve the narrow factual metadata retention projection.",
                policy["operation_states"]["retention"] == "approved",
            ),
            (
                # The adapter shape gate is not governance-derived: it stays
                # until an adapter is bound to a reviewed live page shape.
                "source_shape_unreviewed",
                "Bind the adapter to a current bounded factual page shape.",
                shape_bound,
            ),
        ):
            if not satisfied:
                blockers.append(_blocker(code, action))
        reached_terminal = checkpoint["phase"] == "shape_bound_terminal"
        checkpoint["phase"] = "terminal"
        _write_json(source_root / "checkpoint.json", checkpoint)
        report = {
            "record_type": "njp_inventory_completeness_report",
            "schema_version": 1,
            "run_id": run_id,
            "source_id": source_id,
            "endpoint_id": adapter.endpoint_id,
            "generated_at": observed_at,
            "state": (
                "complete_for_observed_endpoint"
                if shape_bound and reached_terminal and not blockers
                else "blocked"
            ),
            "shape_state": "shape_bound" if shape_bound else "source_shape_unreviewed",
            "robots_state": checkpoint.get("robots_outcome", "not_checked"),
            "access_state": checkpoint.get("access_outcome", "not_checked"),
            "requests_attempted": checkpoint["requests_attempted"],
            "pages_committed": checkpoint["pages_committed"],
            "observed_unique_records": len(checkpoint["records"]),
            "duplicate_records": 0,
            "alias_records": 0,
            "attachment_candidates": 0,
            "records": checkpoint["records"],
            "unvisited_remainder": None,
            "page_mechanism": policy["page_mechanism"],
            "policy_states": {
                "access_control": (
                    (
                        "current_public_metadata_observation"
                        if shape_bound
                        else "current_public_head_observation"
                    )
                    if checkpoint.get("access_outcome") == expected_access
                    else "blocked_or_unconfirmed"
                ),
                "platform_terms": policy["fact_states"]["platform_terms"],
                "copyright_lawful_basis": policy["fact_states"][
                    "copyright_lawful_basis"
                ],
                "retention": policy["operation_states"]["retention"],
                "metadata_inventory": policy["operation_states"][
                    "metadata_inventory"
                ],
            },
            "blockers": blockers,
        }
        report = ledger.finish(run_id, blockers, report)
        _write_json(source_root / "completeness-report.json", report)
        return report
    finally:
        ledger.close()


def run_njp_site_inventories(
    *,
    run_label: str,
    state_root: str | Path,
    aggregate_report: str | Path,
    governance_path: str | Path,
    limits: InventoryLimits | None = None,
    transport: PreflightTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    selected_limits = limits or InventoryLimits()
    selected_transport = transport or UrllibPreflightTransport()
    selected_now = now or (lambda: datetime.now(UTC))
    selected_monotonic = monotonic or time.monotonic
    selected_sleeper = sleeper or time.sleep
    try:
        governance = json.loads(Path(governance_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise NJPInventoryError("governance_unavailable") from error
    reports = [
        run_source_preflight(
            source_id,
            run_label=run_label,
            state_root=Path(state_root),
            governance=governance,
            limits=selected_limits,
            transport=selected_transport,
            now=selected_now,
            monotonic=selected_monotonic,
            sleeper=selected_sleeper,
        )
        for source_id in SOURCE_ADAPTERS
    ]
    aggregate = {
        "record_type": "njp_center_source_universe_gap_report",
        "schema_version": 1,
        "run_label": run_label,
        "generated_at": max(report["generated_at"] for report in reports),
        "source_scope": list(SOURCE_ADAPTERS),
        "whole_njp_center_universe_state": "unknown",
        "counts_are_additive": False,
        "duplicate_scope_semantics": "unknown_across_sources",
        "alias_scope_semantics": "source_local_only",
        "sources": [
            {
                "source_id": report["source_id"],
                "endpoint_id": report["endpoint_id"],
                "state": report["state"],
                "shape_state": report["shape_state"],
                "robots_state": report["robots_state"],
                "access_state": report["access_state"],
                "observed_unique_records": report["observed_unique_records"],
                "unvisited_remainder": report["unvisited_remainder"],
                "page_mechanism": report["page_mechanism"],
                "policy_states": report["policy_states"],
                "blocker_codes": [
                    blocker["code"] for blocker in report["blockers"]
                ],
            }
            for report in reports
        ],
        "safe_scope_statement": (
            "These are two independent endpoint proofs; their counts do not "
            "measure the whole NJP Center universe."
        ),
        "attachment_bytes_requested": False,
    }
    _write_json(Path(aggregate_report), aggregate)
    return aggregate
