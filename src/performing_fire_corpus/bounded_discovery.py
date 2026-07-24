"""Source-neutral bounded metadata discovery with atomic resume checkpoints."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.governance import (
    CANONICAL_ENDPOINT_IDS,
    GovernanceError,
    evaluate_source_operation,
    validate_source_governance,
)
from performing_fire_corpus.ledger import Ledger
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
LIMIT_KEYS = frozenset(
    {
        "aggregate_bytes",
        "elapsed_seconds",
        "max_pages",
        "max_requests",
        "max_response_bytes",
        "max_retries",
        "max_retry_after_seconds",
        "per_host_interval_seconds",
        "timeout_seconds",
    }
)
_CURSOR = re.compile(r"^(?:page|offset)-[0-9]{1,18}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_MIME_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$"
)
_FORBIDDEN_METADATA_FIELDS = frozenset(
    {
        "body",
        "caption",
        "description",
        "excerpt",
        "html",
        "lyrics",
        "notes",
        "prose",
        "summary",
        "text",
        "transcript",
    }
)


class DiscoveryError(ValueError):
    """Raised when a discovery contract cannot be safely evaluated."""


class RetryableDiscoveryError(RuntimeError):
    """A content-free retry signal from a bounded transport."""

    def __init__(
        self, code: str, *, retry_after_seconds: float | None = None
    ) -> None:
        if (
            not isinstance(code, str)
            or len(code) > 64
            or not _ERROR_CODE.fullmatch(code)
            or any(
                sensitive in code
                for sensitive in ("account", "cookie", "credential", "secret", "token")
            )
        ):
            raise ValueError("retry code must be a stable content-free identifier")
        if retry_after_seconds is not None and (
            not isinstance(retry_after_seconds, (int, float))
            or isinstance(retry_after_seconds, bool)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("Retry-After must be a finite nonnegative number")
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class PageResponse:
    """One bounded in-memory response; only sanitized facts become durable."""

    status: int
    mime_type: str
    body: bytes
    observed_at: datetime


class DiscoveryAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    approved_metadata_fields: tuple[str, ...]
    limit_contract: Mapping[str, int | float]

    def parse_page(
        self, body: bytes, *, cursor: str | None
    ) -> Mapping[str, Any]: ...


class DiscoveryTransport(Protocol):
    def fetch(
        self,
        endpoint_id: str,
        cursor: str | None,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> PageResponse: ...


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(dict(value))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
        raise DiscoveryError(f"{name} does not match the strict contract") from error
    if sanitize(value, environ={}) != value:
        raise DiscoveryError(f"{name} contains private or secret-like data")
    return dict(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _time(value: str | datetime) -> datetime:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise DiscoveryError("discovery timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise DiscoveryError("discovery timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _time_text(value: datetime) -> str:
    return _time(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cursor_hash(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    return hashlib.sha256(cursor.encode("utf-8")).hexdigest()


def _safe_cursor(value: Any) -> str:
    if not isinstance(value, str) or not _CURSOR.fullmatch(value):
        raise DiscoveryError("unsafe_cursor")
    lowered = value.lower()
    if any(part in lowered for part in ("token", "secret", "cookie", "signature")):
        raise DiscoveryError("unsafe_cursor")
    return value


def _validate_response(value: PageResponse) -> None:
    if (
        not isinstance(value.status, int)
        or isinstance(value.status, bool)
        or value.status < 100
        or value.status > 599
        or not isinstance(value.mime_type, str)
        or not _MIME_TYPE.fullmatch(value.mime_type)
        or not isinstance(value.body, bytes)
    ):
        raise DiscoveryError("transport returned an invalid response")
    _time(value.observed_at)


class _DiscoveryStore:
    def __init__(self, database: str | Path) -> None:
        with Ledger(database):
            pass
        self.connection = sqlite3.connect(str(database), isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> _DiscoveryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(
        self,
        plan: Mapping[str, Any],
        fingerprint: str,
        checkpoint: Mapping[str, Any],
        now: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        row = self.connection.execute(
            """
            SELECT plan_fingerprint, checkpoint_body, report_body
            FROM discovery_runs WHERE run_id = ?
            """,
            (plan["run_id"],),
        ).fetchone()
        if row is not None:
            return (
                json.loads(row["checkpoint_body"]),
                None if row["report_body"] is None else json.loads(row["report_body"]),
                str(row["plan_fingerprint"]),
            )
        self.connection.execute(
            """
            INSERT INTO discovery_runs(
                run_id, plan_fingerprint, plan_body, checkpoint_body,
                report_body, status, updated_at
            ) VALUES(?, ?, ?, ?, NULL, 'running', ?)
            """,
            (
                plan["run_id"],
                fingerprint,
                _canonical(plan),
                _canonical(checkpoint),
                now,
            ),
        )
        return dict(checkpoint), None, fingerprint

    def finish(self, report: Mapping[str, Any], now: str) -> dict[str, Any]:
        value = _validate("completeness-report", report)
        self.connection.execute(
            """
            UPDATE discovery_runs
            SET report_body = ?, status = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (_canonical(value), value["state"], now, value["run_id"]),
        )
        return value

    def commit_fact(
        self,
        fact: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        now: str,
    ) -> dict[str, Any]:
        fact_value = _validate("request-fact", fact)
        checkpoint_value = _validate("page-checkpoint", checkpoint)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO discovery_request_facts(
                    request_fact_id, run_id, request_sequence, attempt,
                    body, committed_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_value["request_fact_id"],
                    fact_value["run_id"],
                    fact_value["request_sequence"],
                    fact_value["attempt"],
                    _canonical(fact_value),
                    now,
                ),
            )
            self.connection.execute(
                """
                UPDATE discovery_runs
                SET checkpoint_body = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (_canonical(checkpoint_value), now, fact_value["run_id"]),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return checkpoint_value

    def commit_page(
        self,
        fact: Mapping[str, Any],
        observations: list[Mapping[str, Any]],
        checkpoint: Mapping[str, Any],
        now: str,
        commit_hook: Callable[[str, int], None] | None,
    ) -> dict[str, Any]:
        fact_value = _validate("request-fact", fact)
        observation_values = [
            _validate("discovery-observation", item) for item in observations
        ]
        checkpoint_value = dict(checkpoint)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO discovery_request_facts(
                    request_fact_id, run_id, request_sequence, attempt,
                    body, committed_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_value["request_fact_id"],
                    fact_value["run_id"],
                    fact_value["request_sequence"],
                    fact_value["attempt"],
                    _canonical(fact_value),
                    now,
                ),
            )
            duplicate_increment = 0
            duplicate_occurrences: dict[str, int] = {}
            for observation in observation_values:
                existing = self.connection.execute(
                    """
                    SELECT body FROM discovery_observations
                    WHERE run_id = ? AND stable_record_id = ?
                    """,
                    (observation["run_id"], observation["stable_record_id"]),
                ).fetchone()
                if existing is None:
                    self.connection.execute(
                        """
                        INSERT INTO discovery_observations(
                            observation_id, run_id, stable_record_id,
                            page_sequence, body, committed_at
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observation["observation_id"],
                            observation["run_id"],
                            observation["stable_record_id"],
                            observation["page_sequence"],
                            _canonical(observation),
                            now,
                        ),
                    )
                else:
                    duplicate_increment += 1
                    occurrence = duplicate_occurrences.get(
                        observation["stable_record_id"], 0
                    ) + 1
                    duplicate_occurrences[observation["stable_record_id"]] = occurrence
                    self.connection.execute(
                        """
                        INSERT INTO discovery_duplicate_events(
                            run_id, request_fact_id, stable_record_id,
                            occurrence_index, committed_at
                        ) VALUES(?, ?, ?, ?, ?)
                        """,
                        (
                            observation["run_id"],
                            fact_value["request_fact_id"],
                            observation["stable_record_id"],
                            occurrence,
                            now,
                        ),
                    )
            checkpoint_value["duplicate_records"] += duplicate_increment
            checkpoint_value = _validate("page-checkpoint", checkpoint_value)
            if commit_hook is not None:
                commit_hook("before_commit", int(checkpoint_value["page_sequence"]))
            self.connection.execute(
                """
                UPDATE discovery_runs
                SET checkpoint_body = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (_canonical(checkpoint_value), now, fact_value["run_id"]),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        if commit_hook is not None:
            commit_hook("after_commit", int(checkpoint_value["page_sequence"]))
        return checkpoint_value

    def observation_count(self, run_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM discovery_observations WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )

    def observation_conflict(
        self, observations: list[Mapping[str, Any]]
    ) -> bool:
        page_values: dict[str, dict[str, Any]] = {}
        for observation in observations:
            stable_record_id = str(observation["stable_record_id"])
            comparable = {
                "source_id": observation["source_id"],
                "endpoint_id": observation["endpoint_id"],
                "adapter_version": observation["adapter_version"],
                "metadata": observation["metadata"],
            }
            prior = page_values.get(stable_record_id)
            if prior is not None and prior != comparable:
                return True
            page_values[stable_record_id] = comparable
            existing = self.connection.execute(
                """
                SELECT body FROM discovery_observations
                WHERE run_id = ? AND stable_record_id = ?
                """,
                (observation["run_id"], stable_record_id),
            ).fetchone()
            if existing is None:
                continue
            stored = json.loads(existing["body"])
            stored_comparable = {
                "source_id": stored["source_id"],
                "endpoint_id": stored["endpoint_id"],
                "adapter_version": stored["adapter_version"],
                "metadata": stored["metadata"],
            }
            if stored_comparable != comparable:
                return True
        return False


def _initial_checkpoint(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "page_checkpoint",
        "run_id": plan["run_id"],
        "source_id": plan["source_id"],
        "endpoint_id": plan["endpoint_id"],
        "adapter_version": plan["adapter_version"],
        "policy_snapshot_id": plan["policy_snapshot_id"],
        "page_sequence": 0,
        "next_cursor": None,
        "next_ordinal": 0,
        "committed_request_fact_id": None,
        "seen_cursor_hashes": [],
        "aggregate_bytes": 0,
        "requests_attempted": 0,
        "elapsed_seconds": 0.0,
        "rejected_records": 0,
        "duplicate_records": 0,
        "expected_total": None,
        "current_page_retries": 0,
        "terminal": False,
        "terminal_pages": 0,
    }


def _request_fact(
    plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    attempt: int,
    response: PageResponse | None,
    outcome: str,
    failure_code: str | None,
    observed_at: datetime,
) -> dict[str, Any]:
    sequence = int(checkpoint["requests_attempted"])
    run_suffix = str(plan["run_id"]).removeprefix("discovery_run_")
    body = None if response is None else response.body
    return {
        "schema_version": 1,
        "record_type": "request_fact",
        "request_fact_id": (
            f"request_fact_{run_suffix}_{sequence:06d}_{attempt:03d}"
        ),
        "run_id": plan["run_id"],
        "source_id": plan["source_id"],
        "endpoint_id": plan["endpoint_id"],
        "request_sequence": sequence,
        "attempt": attempt,
        "cursor_hash": _cursor_hash(checkpoint["next_cursor"]),
        "status": None if response is None else response.status,
        "mime_type": None if response is None else response.mime_type,
        "observed_bytes": 0 if body is None else len(body),
        "response_sha256": (
            None if body is None else hashlib.sha256(body).hexdigest()
        ),
        "observed_at": _time_text(observed_at),
        "outcome": outcome,
        "failure_code": failure_code,
    }


def _observations(
    plan: Mapping[str, Any],
    page_sequence: int,
    records: list[Any],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for child in records:
        if not isinstance(child, Mapping) or set(child) != {"record_id", "metadata"}:
            raise DiscoveryError("shape_drift")
        record_id = child["record_id"]
        metadata = child["metadata"]
        if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
            raise DiscoveryError("shape_drift")
        if not isinstance(metadata, Mapping):
            raise DiscoveryError("shape_drift")
        if not set(metadata).issubset(plan["approved_metadata_fields"]):
            raise DiscoveryError("shape_drift")
        observation_hash = hashlib.sha256(
            f"{plan['run_id']}|{record_id}".encode("utf-8")
        ).hexdigest()[:32]
        values.append(
            _validate(
                "discovery-observation",
                {
                    "schema_version": 1,
                    "record_type": "discovery_observation",
                    "observation_id": (
                        f"discovery_observation_{observation_hash}"
                    ),
                    "run_id": plan["run_id"],
                    "source_id": plan["source_id"],
                    "endpoint_id": plan["endpoint_id"],
                    "stable_record_id": record_id,
                    "page_sequence": page_sequence,
                    "adapter_version": plan["adapter_version"],
                    "metadata": dict(metadata),
                },
            )
        )
    return values


def _normalize_page(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "records",
        "next_cursor",
        "next_ordinal",
        "terminal",
        "expected_total",
        "rejected_count",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise DiscoveryError("shape_drift")
    if not isinstance(value["records"], list):
        raise DiscoveryError("shape_drift")
    if not isinstance(value["terminal"], bool):
        raise DiscoveryError("shape_drift")
    if (
        value["expected_total"] is not None
        and (
            not isinstance(value["expected_total"], int)
            or isinstance(value["expected_total"], bool)
            or value["expected_total"] < 0
        )
    ):
        raise DiscoveryError("shape_drift")
    if (
        not isinstance(value["rejected_count"], int)
        or isinstance(value["rejected_count"], bool)
        or value["rejected_count"] < 0
    ):
        raise DiscoveryError("shape_drift")
    return dict(value)


def _report(
    plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    state: str,
    stop_reason: str,
    observed_unique_records: int,
    generated_at: datetime,
    blocked_pages: int = 0,
) -> dict[str, Any]:
    expected_total = checkpoint["expected_total"]
    remainder = (
        None
        if expected_total is None
        else max(int(expected_total) - observed_unique_records, 0)
    )
    run_suffix = str(plan["run_id"]).removeprefix("discovery_run_")
    return _validate(
        "completeness-report",
        {
            "schema_version": 1,
            "record_type": "completeness_report",
            "report_id": f"completeness_report_{run_suffix}",
            "run_id": plan["run_id"],
            "source_id": plan["source_id"],
            "endpoint_id": plan["endpoint_id"],
            "adapter_version": plan["adapter_version"],
            "policy_snapshot_id": plan["policy_snapshot_id"],
            "state": state,
            "stop_reason": stop_reason,
            "requests_attempted": checkpoint["requests_attempted"],
            "pages_committed": checkpoint["page_sequence"],
            "observed_unique_records": observed_unique_records,
            "duplicate_records": checkpoint["duplicate_records"],
            "rejected_records": checkpoint["rejected_records"],
            "blocked_pages": blocked_pages,
            "terminal_pages": checkpoint["terminal_pages"],
            "expected_total": expected_total,
            "unvisited_remainder": remainder,
            "generated_at": _time_text(generated_at),
        },
    )


def _validate_plan_and_adapter(
    value: Mapping[str, Any], adapter: DiscoveryAdapter
) -> dict[str, Any]:
    plan = _validate("discovery-run-plan", value)
    source_id = plan["source_id"]
    endpoint_id = plan["endpoint_id"]
    if (
        source_id not in CANONICAL_ENDPOINT_IDS
        or endpoint_id not in CANONICAL_ENDPOINT_IDS[source_id]
    ):
        raise DiscoveryError("run plan endpoint is not bound to its canonical source")
    if (
        adapter.adapter_id != plan["adapter_id"]
        or adapter.adapter_version != plan["adapter_version"]
    ):
        raise DiscoveryError("adapter identity does not match the run plan")
    approved_fields = plan["approved_metadata_fields"]
    if approved_fields != sorted(approved_fields):
        raise DiscoveryError("approved metadata fields must use canonical ordering")
    if set(approved_fields).intersection(_FORBIDDEN_METADATA_FIELDS):
        raise DiscoveryError("approved metadata fields include content-bearing prose")
    if tuple(adapter.approved_metadata_fields) != tuple(approved_fields):
        raise DiscoveryError(
            "adapter approved metadata projection does not match the run plan"
        )
    contract = dict(adapter.limit_contract)
    if set(contract) != LIMIT_KEYS:
        raise DiscoveryError("adapter omits a required run-plan limit")
    for key in LIMIT_KEYS:
        child = contract[key]
        if (
            not isinstance(child, (int, float))
            or isinstance(child, bool)
            or not math.isfinite(child)
            or child <= 0
            or child != plan["limits"][key]
        ):
            raise DiscoveryError("adapter limit contract must exactly match the run plan")
    return plan


def _validate_governance_binding(
    plan: Mapping[str, Any], governance_record: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        governance = validate_source_governance(governance_record)
    except GovernanceError as error:
        raise DiscoveryError("source governance does not match its strict contract") from error
    if (
        governance["source_id"] != plan["source_id"]
        or governance["endpoint_id"] != plan["endpoint_id"]
    ):
        raise DiscoveryError("source governance does not target the run-plan endpoint")

    expected_snapshot = f"policy_snapshot_{_fingerprint(governance)[:32]}"
    if plan["policy_snapshot_id"] != expected_snapshot:
        raise DiscoveryError("run plan is not bound to the supplied governance snapshot")

    operation_state = governance["operation_states"]["metadata_inventory"]
    if plan["policy_state"] != operation_state:
        raise DiscoveryError("run-plan policy state differs from source governance")

    robots = [
        item
        for item in governance["observations"]
        if item["dimension"] == "robots"
        and item["evidence_id"] == plan["robots_evidence_id"]
    ]
    if len(robots) != 1:
        raise DiscoveryError("run plan lacks its exact robots evidence binding")
    robots_observation = robots[0]
    if (
        plan["robots_state"] != robots_observation["state"]
        or _time(plan["robots_expires_at"])
        > _time(robots_observation["expires_at"])
    ):
        raise DiscoveryError("run plan overstates its reviewed robots evidence")

    authority_expiries = [
        _time(item["expires_at"]) for item in governance["observations"]
    ]
    authority_expiries.extend(
        _time(item["expires_at"])
        for item in governance["decisions"]
        if item["affected_operation"] == "metadata_inventory"
    )
    if not authority_expiries:
        raise DiscoveryError("source governance has no metadata authority horizon")
    if _time(plan["policy_expires_at"]) > min(authority_expiries):
        raise DiscoveryError("run plan outlives its reviewed policy evidence")
    return governance


def run_bounded_discovery(
    run_plan: Mapping[str, Any],
    database: str | Path,
    *,
    governance_record: Mapping[str, Any],
    adapter: DiscoveryAdapter,
    transport: DiscoveryTransport,
    wall_clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    commit_hook: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Run or resume one bounded metadata-only discovery plan."""

    plan = _validate_plan_and_adapter(run_plan, adapter)
    governance = _validate_governance_binding(plan, governance_record)
    plan_fingerprint = _fingerprint(plan)
    started = monotonic()
    with _DiscoveryStore(database) as store:
        checkpoint, existing_report, stored_fingerprint = store.start(
            plan,
            plan_fingerprint,
            _validate("page-checkpoint", _initial_checkpoint(plan)),
            _time_text(wall_clock()),
        )

        def finish(
            state: str, reason: str, *, blocked_pages: int = 0
        ) -> dict[str, Any]:
            report = _report(
                plan,
                checkpoint,
                state=state,
                stop_reason=reason,
                observed_unique_records=store.observation_count(plan["run_id"]),
                generated_at=wall_clock(),
                blocked_pages=blocked_pages,
            )
            return store.finish(report, _time_text(wall_clock()))

        if stored_fingerprint != plan_fingerprint:
            raise DiscoveryError("run id is already bound to a different plan")
        if existing_report is not None:
            return _validate("completeness-report", existing_report)

        now = _time(wall_clock())
        try:
            policy_evaluation = evaluate_source_operation(
                governance,
                "metadata_inventory",
                now=now,
            )
        except GovernanceError as error:
            raise DiscoveryError("source governance could not be evaluated") from error
        if not policy_evaluation["eligible"]:
            return finish("blocked", "policy_ineligible")
        if _time(plan["policy_expires_at"]) <= now:
            return finish("blocked", "policy_expired")
        if _time(plan["robots_expires_at"]) <= now:
            return finish("blocked", "robots_expired")

        carried_elapsed = float(checkpoint["elapsed_seconds"])
        last_request_at = monotonic() if checkpoint["requests_attempted"] else None

        def sync_elapsed() -> None:
            checkpoint["elapsed_seconds"] = carried_elapsed + max(
                monotonic() - started, 0.0
            )

        def wait_for_rate_limit() -> None:
            nonlocal last_request_at
            if last_request_at is None:
                return
            interval = float(plan["limits"]["per_host_interval_seconds"])
            remaining = interval - (monotonic() - last_request_at)
            if remaining > 0:
                sleeper(remaining)

        while True:
            sync_elapsed()
            now = _time(wall_clock())
            if checkpoint["terminal"]:
                return finish("complete_for_observed_endpoint", "terminal_page")
            if _time(plan["policy_expires_at"]) <= now:
                return finish("blocked", "policy_expired")
            if _time(plan["robots_expires_at"]) <= now:
                return finish("blocked", "robots_expired")
            if checkpoint["page_sequence"] >= plan["limits"]["max_pages"]:
                return finish("bounded_partial", "page_budget_exhausted")
            if checkpoint["requests_attempted"] >= plan["limits"]["max_requests"]:
                return finish("bounded_partial", "request_budget_exhausted")
            if checkpoint["elapsed_seconds"] >= plan["limits"]["elapsed_seconds"]:
                return finish("bounded_partial", "elapsed_budget_exhausted")
            if checkpoint["aggregate_bytes"] >= plan["limits"]["aggregate_bytes"]:
                return finish("bounded_partial", "aggregate_byte_budget_exhausted")

            response: PageResponse | None = None
            fact: dict[str, Any] | None = None
            while response is None:
                sync_elapsed()
                if checkpoint["requests_attempted"] >= plan["limits"]["max_requests"]:
                    return finish("bounded_partial", "request_budget_exhausted")
                if checkpoint["elapsed_seconds"] >= plan["limits"]["elapsed_seconds"]:
                    return finish("bounded_partial", "elapsed_budget_exhausted")
                wait_for_rate_limit()
                sync_elapsed()
                now = _time(wall_clock())
                if _time(plan["policy_expires_at"]) <= now:
                    return finish("blocked", "policy_expired")
                if _time(plan["robots_expires_at"]) <= now:
                    return finish("blocked", "robots_expired")
                if checkpoint["elapsed_seconds"] >= plan["limits"]["elapsed_seconds"]:
                    return finish("bounded_partial", "elapsed_budget_exhausted")
                remaining_elapsed = float(plan["limits"]["elapsed_seconds"]) - float(
                    checkpoint["elapsed_seconds"]
                )
                checkpoint["requests_attempted"] += 1
                attempt = int(checkpoint["current_page_retries"]) + 1
                last_request_at = monotonic()
                try:
                    response = transport.fetch(
                        plan["endpoint_id"],
                        checkpoint["next_cursor"],
                        timeout_seconds=min(
                            float(plan["limits"]["timeout_seconds"]),
                            remaining_elapsed,
                        ),
                        max_response_bytes=int(
                            plan["limits"]["max_response_bytes"]
                        ),
                    )
                    if not isinstance(response, PageResponse):
                        raise DiscoveryError("transport returned an invalid response")
                    _validate_response(response)
                except RetryableDiscoveryError as error:
                    sync_elapsed()
                    retry_limit_exhausted = (
                        checkpoint["current_page_retries"]
                        >= plan["limits"]["max_retries"]
                    )
                    retry_after_exceeded = (
                        error.retry_after_seconds is not None
                        and error.retry_after_seconds
                        > plan["limits"]["max_retry_after_seconds"]
                    )
                    checkpoint["current_page_retries"] += 1
                    fact = _request_fact(
                        plan,
                        checkpoint,
                        attempt=attempt,
                        response=None,
                        outcome="retryable_error",
                        failure_code=(
                            "retry_after_exceeded"
                            if retry_after_exceeded
                            else error.code
                        ),
                        observed_at=wall_clock(),
                    )
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    if retry_after_exceeded:
                        return finish(
                            "blocked",
                            "retry_after_exceeds_limit",
                            blocked_pages=1,
                        )
                    if retry_limit_exhausted:
                        return finish("blocked", "retry_exhausted", blocked_pages=1)
                    requested_delay = (
                        float(plan["limits"]["per_host_interval_seconds"])
                        if error.retry_after_seconds is None
                        else float(error.retry_after_seconds)
                    )
                    sleeper(max(requested_delay, 0.0))
                except Exception:
                    sync_elapsed()
                    fact = _request_fact(
                        plan,
                        checkpoint,
                        attempt=attempt,
                        response=None,
                        outcome="retryable_error",
                        failure_code="transport_error",
                        observed_at=wall_clock(),
                    )
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("blocked", "transport_error", blocked_pages=1)

            sync_elapsed()
            observed_at = _time(response.observed_at)
            if (
                checkpoint["elapsed_seconds"]
                > plan["limits"]["elapsed_seconds"]
            ):
                fact = _request_fact(
                    plan,
                    checkpoint,
                    attempt=attempt,
                    response=response,
                    outcome="elapsed_budget_exceeded",
                    failure_code="elapsed_budget_exceeded",
                    observed_at=observed_at,
                )
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish(
                    "bounded_partial",
                    "elapsed_budget_exhausted",
                    blocked_pages=1,
                )
            authority_now = _time(wall_clock())
            for authority_name in ("policy", "robots"):
                if _time(plan[f"{authority_name}_expires_at"]) <= authority_now:
                    failure_code = f"{authority_name}_expired"
                    fact = _request_fact(
                        plan,
                        checkpoint,
                        attempt=attempt,
                        response=response,
                        outcome="authority_expired",
                        failure_code=failure_code,
                        observed_at=observed_at,
                    )
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish(
                        "blocked",
                        failure_code,
                        blocked_pages=1,
                    )
            if observed_at > _time(wall_clock()):
                fact = _request_fact(
                    plan,
                    checkpoint,
                    attempt=attempt,
                    response=response,
                    outcome="invalid_response_time",
                    failure_code="invalid_response_time",
                    observed_at=observed_at,
                )
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("blocked", "invalid_response_time", blocked_pages=1)
            response_bytes = len(response.body)
            if response_bytes > plan["limits"]["max_response_bytes"]:
                fact = _request_fact(
                    plan,
                    checkpoint,
                    attempt=attempt,
                    response=response,
                    outcome="response_oversized",
                    failure_code="response_oversized",
                    observed_at=observed_at,
                )
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("blocked", "response_oversized", blocked_pages=1)
            if (
                checkpoint["aggregate_bytes"] + response_bytes
                > plan["limits"]["aggregate_bytes"]
            ):
                fact = _request_fact(
                    plan,
                    checkpoint,
                    attempt=attempt,
                    response=response,
                    outcome="aggregate_budget_exceeded",
                    failure_code="aggregate_budget_exceeded",
                    observed_at=observed_at,
                )
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish(
                    "bounded_partial",
                    "aggregate_byte_budget_exhausted",
                    blocked_pages=1,
                )
            if response.status != 200:
                fact = _request_fact(
                    plan,
                    checkpoint,
                    attempt=attempt,
                    response=response,
                    outcome="http_error",
                    failure_code="http_error",
                    observed_at=observed_at,
                )
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("blocked", "http_error", blocked_pages=1)

            fact = _request_fact(
                plan,
                checkpoint,
                attempt=attempt,
                response=response,
                outcome="success",
                failure_code=None,
                observed_at=observed_at,
            )
            checkpoint["aggregate_bytes"] += response_bytes
            try:
                parsed = _normalize_page(
                    adapter.parse_page(response.body, cursor=checkpoint["next_cursor"])
                )
                page_sequence = int(checkpoint["page_sequence"]) + 1
                observations = _observations(
                    plan, page_sequence, parsed["records"]
                )
            except Exception:
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("changed", "shape_drift", blocked_pages=1)

            if store.observation_conflict(observations):
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("changed", "record_changed", blocked_pages=1)

            expected_total = parsed["expected_total"]
            if (
                expected_total is not None
                and checkpoint["expected_total"] is not None
                and expected_total != checkpoint["expected_total"]
            ):
                checkpoint = store.commit_fact(
                    fact, checkpoint, _time_text(wall_clock())
                )
                return finish("changed", "expected_total_changed", blocked_pages=1)
            if expected_total is not None:
                checkpoint["expected_total"] = expected_total

            if parsed["terminal"]:
                if (
                    parsed["next_cursor"] is not None
                    or parsed["next_ordinal"] is not None
                ):
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("changed", "shape_drift", blocked_pages=1)
                checkpoint["next_cursor"] = None
                checkpoint["next_ordinal"] = None
                checkpoint["terminal"] = True
                checkpoint["terminal_pages"] += 1
            else:
                if parsed["next_cursor"] is None:
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("changed", "ambiguous_terminal", blocked_pages=1)
                try:
                    next_cursor = _safe_cursor(parsed["next_cursor"])
                except DiscoveryError:
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("blocked", "unsafe_cursor", blocked_pages=1)
                expected_ordinal = int(checkpoint["next_ordinal"]) + 1
                if (
                    not isinstance(parsed["next_ordinal"], int)
                    or isinstance(parsed["next_ordinal"], bool)
                    or parsed["next_ordinal"] != expected_ordinal
                ):
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("changed", "pagination_loop", blocked_pages=1)
                next_hash = _cursor_hash(next_cursor)
                if next_hash in checkpoint["seen_cursor_hashes"]:
                    checkpoint = store.commit_fact(
                        fact, checkpoint, _time_text(wall_clock())
                    )
                    return finish("changed", "pagination_loop", blocked_pages=1)
                checkpoint["seen_cursor_hashes"].append(next_hash)
                checkpoint["next_cursor"] = next_cursor
                checkpoint["next_ordinal"] = parsed["next_ordinal"]

            checkpoint["page_sequence"] = page_sequence
            checkpoint["current_page_retries"] = 0
            checkpoint["rejected_records"] += parsed["rejected_count"]
            checkpoint["committed_request_fact_id"] = fact["request_fact_id"]
            checkpoint = store.commit_page(
                fact,
                observations,
                checkpoint,
                _time_text(wall_clock()),
                commit_hook,
            )
            if (
                checkpoint["expected_total"] is not None
                and store.observation_count(plan["run_id"])
                > checkpoint["expected_total"]
            ):
                return finish("changed", "expected_total_changed", blocked_pages=1)
