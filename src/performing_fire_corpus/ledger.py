"""Durable, privacy-safe SQLite ledger and capability queue."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
RECORD_TYPES = ("source", "asset", "rights", "job", "lease", "object", "evidence")
ID_FIELDS = {record_type: f"{record_type}_id" for record_type in RECORD_TYPES}
ASSET_STATES = (
    "discovered",
    "metadata_verified",
    "approved_for_ingest",
    "transfer_pending",
    "raw_in_object_store",
    "extraction_pending",
    "extracting",
    "derived_in_object_store",
    "indexed",
)
FAILURE_STATES = ("blocked", "failed_retryable", "failed_final")
ALL_STATES = ASSET_STATES + FAILURE_STATES
CROSSING_CAPABILITIES = {"trusted-vm", "trusted-laptop", "object-storage"}
_LOCAL_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/]|file://)|(?:/home/|/Users/|/tmp/)")
_OBJECT_KEY = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*\\)[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$"
)


class LedgerError(RuntimeError):
    """Base exception for ledger contract violations."""


class InvalidTransition(LedgerError):
    """Raised when an asset state change bypasses a required gate."""


class LeaseError(LedgerError):
    """Raised when a lease is absent, expired, or owned by another worker."""


class CapabilityError(LedgerError):
    """Raised when a worker cannot satisfy a job's bounded capabilities."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | str | None = None) -> str:
    if value is None:
        value = utc_now()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _schema_resource(record_type: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{record_type}.json"
    )
    if packaged.is_file():
        return packaged
    # Source checkouts retain the public contracts at repository root.
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{record_type}.json"


def validate_record(record: Mapping[str, Any]) -> None:
    record_type = record.get("record_type")
    if record_type not in RECORD_TYPES:
        raise LedgerError(f"unsupported record_type: {record_type!r}")
    schema_resource = _schema_resource(str(record_type))
    if not schema_resource.is_file():
        raise LedgerError(f"schema is unavailable for {record_type}")
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(record))


def _assert_sanitized(value: Any, *, field: str = "payload") -> None:
    if sanitize(value, environ={}) != value:
        raise LedgerError(f"{field} contains private or secret-like data")
    if isinstance(value, bytes):
        raise LedgerError(f"{field} may not contain binary media")
    if isinstance(value, str):
        if "\r" in value or "\n" in value or _LOCAL_PATH.search(value):
            raise LedgerError(f"{field} contains a local path or unsafe text")
    elif isinstance(value, Mapping):
        forbidden = {"credential", "credentials", "cookie", "media", "private_text", "secret", "token"}
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise LedgerError(f"{field} contains forbidden field {key!r}")
            _assert_sanitized(child, field=f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_sanitized(child, field=f"{field}[{index}]")


class Ledger:
    """A caller-selected SQLite ledger.

    One connection is owned by each instance. SQLite WAL plus ``BEGIN
    IMMEDIATE`` provides the compare-and-set boundary used by job claims.
    """

    def __init__(self, database: str | Path, *, timeout: float = 5.0) -> None:
        if database is None or str(database).strip() == "":
            raise ValueError("an explicit SQLite database path is required")
        self.database = str(database)
        self._connection = sqlite3.connect(
            self.database,
            timeout=timeout,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        if self.database != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        sql = (
            files("performing_fire_corpus.migrations")
            .joinpath("001_initial.sql")
            .read_text(encoding="utf-8")
        )
        with self._lock:
            self._connection.executescript(sql)
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (utc_text(),),
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _operation_result(
        self,
        operation_id: str | None,
        kind: str,
        subject_id: str,
        request: Any,
    ) -> dict[str, Any] | None:
        if operation_id is None:
            return None
        row = self._connection.execute(
            """SELECT operation_kind, subject_id, result
               FROM operations WHERE operation_id = ?""",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        stored = json.loads(row["result"])
        if (
            row["operation_kind"] != kind
            or row["subject_id"] != subject_id
            or not isinstance(stored, dict)
            or stored.get("version") != 1
            or stored.get("request") != _canonical(request)
            or "result" not in stored
        ):
            raise LedgerError(
                f"operation_id {operation_id!r} is already bound to a different request"
            )
        return stored["result"]

    def _record_operation(
        self,
        operation_id: str | None,
        kind: str,
        subject_id: str,
        request: Any,
        result: Any,
        now: str,
    ) -> None:
        if operation_id is not None:
            stored = {
                "version": 1,
                "request": _canonical(request),
                "result": result,
            }
            self._connection.execute(
                "INSERT INTO operations VALUES(?, ?, ?, ?, ?)",
                (operation_id, kind, subject_id, _canonical(stored), now),
            )

    def upsert(self, record: Mapping[str, Any], *, operation_id: str | None = None) -> dict[str, Any]:
        """Validate and idempotently store one public record."""
        validate_record(record)
        value = dict(record)
        _assert_sanitized(value)
        record_type = str(value["record_type"])
        record_id = str(value[ID_FIELDS[record_type]])
        operation_subject = f"{record_type}:{record_id}"
        now = utc_text()
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "upsert", operation_subject, value
                )
                if prior is not None:
                    self._connection.commit()
                    return prior
                existing = self.get_record(record_type, record_id)
                if existing is not None and existing != value:
                    raise LedgerError(f"conflicting upsert for stable identifier {record_id}")
                if record_type == "object":
                    self._require_approved_rights(str(value["asset_id"]))
                    for row in self._connection.execute(
                        "SELECT record_id, body FROM records WHERE record_type='object'"
                    ):
                        other = json.loads(row["body"])
                        if (
                            other["object_key"] == value["object_key"]
                            and row["record_id"] != record_id
                        ):
                            raise LedgerError(
                                f"object key already has receipt {row['record_id']}"
                            )
                self._connection.execute(
                    """INSERT INTO records(record_type, record_id, body, updated_at)
                       VALUES(?, ?, ?, ?)
                       ON CONFLICT(record_type, record_id)
                       DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at""",
                    (record_type, record_id, _canonical(value), now),
                )
                if record_type == "asset":
                    self._connection.execute(
                        "INSERT OR IGNORE INTO asset_states VALUES(?, 'discovered', NULL, NULL, ?)",
                        (record_id, now),
                    )
                self._record_operation(
                    operation_id, "upsert", operation_subject, value, value, now
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    upsert_record = upsert

    def get_record(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT body FROM records WHERE record_type=? AND record_id=?",
            (record_type, record_id),
        ).fetchone()
        return None if row is None else json.loads(row["body"])

    def _require_approved_rights(self, asset_id: str) -> None:
        rows = self._connection.execute(
            "SELECT body FROM records WHERE record_type='rights'"
        ).fetchall()
        if not any(
            (record := json.loads(row["body"])).get("asset_id") == asset_id
            and record.get("state") == "approved"
            for row in rows
        ):
            raise InvalidTransition(f"{asset_id} has no approved rights record")

    def asset_state(self, asset_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT state FROM asset_states WHERE asset_id=?", (asset_id,)
        ).fetchone()
        return None if row is None else str(row["state"])

    def transition_asset(
        self,
        asset_id: str,
        new_state: str,
        *,
        operation_id: str | None = None,
        blocker: str | None = None,
    ) -> str:
        if new_state not in ALL_STATES:
            raise InvalidTransition(f"unknown asset state {new_state!r}")
        _assert_sanitized(blocker)
        request = {"new_state": new_state, "blocker": blocker}
        now = utc_text()
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "transition", asset_id, request
                )
                if prior is not None:
                    self._connection.commit()
                    return str(prior["state"])
                row = self._connection.execute(
                    "SELECT state, resume_state FROM asset_states WHERE asset_id=?", (asset_id,)
                ).fetchone()
                if row is None:
                    raise InvalidTransition(f"unknown asset {asset_id}")
                current = str(row["state"])
                if current == new_state:
                    result = {"asset_id": asset_id, "state": current}
                    self._record_operation(
                        operation_id, "transition", asset_id, request, result, now
                    )
                    self._connection.commit()
                    return current
                resume_state: str | None = None
                if new_state in FAILURE_STATES:
                    if current in ("indexed", "failed_final"):
                        raise InvalidTransition(f"terminal state {current} cannot transition")
                    if new_state == "blocked" and not blocker:
                        raise InvalidTransition("blocked transitions require a sanitized reason")
                    resume_state = current if new_state == "failed_retryable" else None
                elif current == "failed_retryable" and new_state == row["resume_state"]:
                    resume_state = None
                elif current in FAILURE_STATES:
                    raise InvalidTransition(f"{current} cannot transition to {new_state}")
                else:
                    expected = ASSET_STATES[ASSET_STATES.index(current) + 1]
                    if new_state != expected:
                        raise InvalidTransition(f"expected {expected}, not {new_state}")
                if new_state in ASSET_STATES and ASSET_STATES.index(
                    new_state
                ) >= ASSET_STATES.index("approved_for_ingest"):
                    self._require_approved_rights(asset_id)
                if new_state == "raw_in_object_store":
                    self._require_object(asset_id, prefix="raw/")
                if new_state == "derived_in_object_store":
                    self._require_object(asset_id, prefix="derived/")
                self._connection.execute(
                    """UPDATE asset_states
                       SET state=?, resume_state=?, blocker=?, updated_at=? WHERE asset_id=?""",
                    (new_state, resume_state, blocker, now, asset_id),
                )
                result = {"asset_id": asset_id, "state": new_state}
                self._record_operation(
                    operation_id, "transition", asset_id, request, result, now
                )
                self._connection.commit()
                return new_state
            except Exception:
                self._connection.rollback()
                raise

    set_asset_state = transition_asset

    def _require_object(self, asset_id: str, *, prefix: str) -> None:
        rows = self._connection.execute(
            "SELECT body FROM records WHERE record_type='object'"
        ).fetchall()
        if not any(
            (record := json.loads(row["body"])).get("asset_id") == asset_id
            and str(record.get("object_key", "")).startswith(prefix)
            for row in rows
        ):
            raise InvalidTransition(f"{asset_id} has no verified {prefix} object receipt")

    def create_job(
        self, record: Mapping[str, Any], *, operation_id: str | None = None
    ) -> dict[str, Any]:
        validate_record(record)
        if record.get("record_type") != "job":
            raise LedgerError("create_job requires a job record")
        value = dict(record)
        _assert_sanitized(value)
        capabilities = set(value["required_capabilities"])
        if capabilities & CROSSING_CAPABILITIES and not (
            value.get("input_object_key") or value.get("output_object_key")
        ):
            raise LedgerError("cross-lane jobs require an object key")
        for field in ("input_object_key", "output_object_key"):
            if field in value and not _OBJECT_KEY.fullmatch(str(value[field])):
                raise LedgerError(f"{field} must be an object key")
        now = utc_text()
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "create_job", str(value["job_id"]), value
                )
                if prior is not None:
                    self._connection.commit()
                    return prior
                existing = self._connection.execute(
                    "SELECT body FROM jobs WHERE asset_id=? AND operation=?",
                    (value["asset_id"], value["operation"]),
                ).fetchone()
                if existing is not None:
                    result = json.loads(existing["body"])
                    self._record_operation(
                        operation_id,
                        "create_job",
                        str(value["job_id"]),
                        value,
                        result,
                        now,
                    )
                    self._connection.commit()
                    return result
                self._connection.execute(
                    """INSERT INTO jobs VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                    (
                        value["job_id"],
                        value["asset_id"],
                        value["operation"],
                        _canonical(value),
                        value["status"],
                        value["retry_state"],
                        value["attempt_count"],
                        value["max_attempts"],
                        _canonical(sorted(value["required_capabilities"])),
                        _canonical(value["checkpoint"]),
                        now,
                    ),
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO records VALUES('job', ?, ?, ?)",
                    (value["job_id"], _canonical(value), now),
                )
                self._record_operation(
                    operation_id,
                    "create_job",
                    str(value["job_id"]),
                    value,
                    value,
                    now,
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """SELECT body, status, retry_state, attempt_count, checkpoint, active_lease_id
               FROM jobs WHERE job_id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row["body"])
        result.update(
            status=row["status"],
            retry_state=row["retry_state"],
            attempt_count=row["attempt_count"],
            checkpoint=json.loads(row["checkpoint"]),
            active_lease_id=row["active_lease_id"],
        )
        return result

    def claim_job(
        self,
        holder_id: str,
        capabilities: Iterable[str],
        *,
        lease_seconds: int = 300,
        now: datetime | str | None = None,
        lease_id: str | None = None,
    ) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        available = set(capabilities)
        now_text = utc_text(now)
        expires = utc_text(_parse_time(now_text) + timedelta(seconds=lease_seconds))
        with self._lock:
            self._begin()
            try:
                self._recover_expired_locked(now_text)
                rows = self._connection.execute(
                    """SELECT * FROM jobs
                       WHERE status='queued' AND retry_state IN ('ready','retry_scheduled')
                         AND attempt_count < max_attempts
                       ORDER BY job_id"""
                ).fetchall()
                job = next(
                    (
                        row
                        for row in rows
                        if set(json.loads(row["required_capabilities"])) <= available
                    ),
                    None,
                )
                if job is None:
                    self._connection.commit()
                    return None
                selected_lease = lease_id or f"lease_{uuid.uuid4().hex}"
                checkpoint = json.loads(job["checkpoint"])
                self._connection.execute(
                    """INSERT INTO leases
                       VALUES(?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
                    (
                        selected_lease,
                        job["job_id"],
                        holder_id,
                        _canonical(sorted(available)),
                        now_text,
                        expires,
                        _canonical(checkpoint),
                    ),
                )
                updated = self._connection.execute(
                    """UPDATE jobs SET status='leased', active_lease_id=?, updated_at=?
                       WHERE job_id=? AND status='queued' AND active_lease_id IS NULL""",
                    (selected_lease, now_text, job["job_id"]),
                )
                if updated.rowcount != 1:
                    raise LeaseError("job was claimed concurrently")
                result = {
                    "lease_id": selected_lease,
                    "job_id": job["job_id"],
                    "holder_id": holder_id,
                    "capabilities": sorted(available),
                    "acquired_at": now_text,
                    "expires_at": expires,
                    "checkpoint": checkpoint,
                }
                self._connection.commit()
                return result
            except Exception:
                self._connection.rollback()
                raise

    claim = claim_job

    def _active_lease(
        self, lease_id: str, holder_id: str, now: datetime | str | None
    ) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        now_text = utc_text(now)
        if row is None or row["released_at"] is not None:
            raise LeaseError("lease is not active")
        if row["holder_id"] != holder_id:
            raise LeaseError("lease belongs to a different worker")
        if _parse_time(row["expires_at"]) <= _parse_time(now_text):
            raise LeaseError("lease has expired")
        return row

    def heartbeat(
        self,
        lease_id: str,
        holder_id: str,
        *,
        lease_seconds: int = 300,
        now: datetime | str | None = None,
    ) -> str:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_text = utc_text(now)
        expires = utc_text(_parse_time(now_text) + timedelta(seconds=lease_seconds))
        with self._lock:
            self._begin()
            try:
                self._active_lease(lease_id, holder_id, now_text)
                self._connection.execute(
                    "UPDATE leases SET expires_at=? WHERE lease_id=?", (expires, lease_id)
                )
                self._connection.commit()
                return expires
            except Exception:
                self._connection.rollback()
                raise

    def write_checkpoint(
        self,
        lease_id: str,
        holder_id: str,
        checkpoint: Mapping[str, Any],
        *,
        operation_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        value = dict(checkpoint)
        _assert_sanitized(value)
        if not isinstance(value.get("sequence"), int) or value["sequence"] < 0:
            raise LedgerError("checkpoint requires a non-negative sequence")
        if not value.get("summary"):
            raise LedgerError("checkpoint requires a summary")
        if "object_key" in value and not _OBJECT_KEY.fullmatch(str(value["object_key"])):
            raise LedgerError("checkpoint object_key is invalid")
        now_text = utc_text(now)
        request = {"holder_id": holder_id, "checkpoint": value}
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "checkpoint", lease_id, request
                )
                if prior is not None:
                    self._connection.commit()
                    return prior
                lease = self._active_lease(lease_id, holder_id, now_text)
                current = json.loads(lease["checkpoint"])
                if value["sequence"] < current["sequence"]:
                    raise LedgerError("checkpoint sequence cannot move backwards")
                if value["sequence"] == current["sequence"] and value != current:
                    raise LedgerError("checkpoint sequence conflicts with stored checkpoint")
                self._connection.execute(
                    "UPDATE leases SET checkpoint=? WHERE lease_id=?",
                    (_canonical(value), lease_id),
                )
                self._connection.execute(
                    "UPDATE jobs SET checkpoint=?, status='running', updated_at=? WHERE job_id=?",
                    (_canonical(value), now_text, lease["job_id"]),
                )
                self._record_operation(
                    operation_id,
                    "checkpoint",
                    lease_id,
                    request,
                    value,
                    now_text,
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    checkpoint = write_checkpoint

    def complete_job(
        self,
        lease_id: str,
        holder_id: str,
        *,
        operation_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        now_text = utc_text(now)
        request = {"holder_id": holder_id}
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "complete_job", lease_id, request
                )
                if prior is not None:
                    self._connection.commit()
                    return prior
                lease = self._active_lease(lease_id, holder_id, now_text)
                self._connection.execute(
                    "UPDATE leases SET released_at=?, release_reason='completed' WHERE lease_id=?",
                    (now_text, lease_id),
                )
                self._connection.execute(
                    """UPDATE jobs SET status='completed', retry_state='ready',
                       active_lease_id=NULL, updated_at=? WHERE job_id=?""",
                    (now_text, lease["job_id"]),
                )
                result = {"job_id": lease["job_id"], "status": "completed"}
                self._record_operation(
                    operation_id,
                    "complete_job",
                    lease_id,
                    request,
                    result,
                    now_text,
                )
                self._connection.commit()
                return result
            except Exception:
                self._connection.rollback()
                raise

    complete = complete_job

    def fail_job(
        self,
        lease_id: str,
        holder_id: str,
        *,
        retryable: bool = True,
        reason: str = "worker failure",
        operation_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        _assert_sanitized(reason)
        now_text = utc_text(now)
        request = {
            "holder_id": holder_id,
            "retryable": retryable,
            "reason": reason,
        }
        with self._lock:
            self._begin()
            try:
                prior = self._operation_result(
                    operation_id, "fail_job", lease_id, request
                )
                if prior is not None:
                    self._connection.commit()
                    return prior
                lease = self._active_lease(lease_id, holder_id, now_text)
                job = self._connection.execute(
                    "SELECT attempt_count, max_attempts FROM jobs WHERE job_id=?",
                    (lease["job_id"],),
                ).fetchone()
                attempts = int(job["attempt_count"]) + 1
                exhausted = not retryable or attempts >= int(job["max_attempts"])
                status = "failed" if exhausted else "queued"
                retry_state = "exhausted" if exhausted else "retry_scheduled"
                self._connection.execute(
                    "UPDATE leases SET released_at=?, release_reason=? WHERE lease_id=?",
                    (now_text, reason, lease_id),
                )
                self._connection.execute(
                    """UPDATE jobs SET status=?, retry_state=?, attempt_count=?,
                       active_lease_id=NULL, updated_at=? WHERE job_id=?""",
                    (status, retry_state, attempts, now_text, lease["job_id"]),
                )
                result = {
                    "job_id": lease["job_id"],
                    "status": status,
                    "retry_state": retry_state,
                    "attempt_count": attempts,
                }
                self._record_operation(
                    operation_id,
                    "fail_job",
                    lease_id,
                    request,
                    result,
                    now_text,
                )
                self._connection.commit()
                return result
            except Exception:
                self._connection.rollback()
                raise

    def release_lease(
        self,
        lease_id: str,
        holder_id: str,
        *,
        reason: str = "disconnected",
        now: datetime | str | None = None,
    ) -> None:
        _assert_sanitized(reason)
        now_text = utc_text(now)
        with self._lock:
            self._begin()
            try:
                lease = self._active_lease(lease_id, holder_id, now_text)
                self._connection.execute(
                    "UPDATE leases SET released_at=?, release_reason=? WHERE lease_id=?",
                    (now_text, reason, lease_id),
                )
                self._connection.execute(
                    """UPDATE jobs SET status='queued', active_lease_id=NULL, updated_at=?
                       WHERE job_id=?""",
                    (now_text, lease["job_id"]),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    release = release_lease

    def _recover_expired_locked(self, now_text: str) -> int:
        rows = self._connection.execute(
            """SELECT lease_id, job_id FROM leases
               WHERE released_at IS NULL AND expires_at <= ?""",
            (now_text,),
        ).fetchall()
        for row in rows:
            self._connection.execute(
                """UPDATE leases SET released_at=?, release_reason='expired'
                   WHERE lease_id=?""",
                (now_text, row["lease_id"]),
            )
            self._connection.execute(
                """UPDATE jobs SET status='queued', active_lease_id=NULL, updated_at=?
                   WHERE job_id=? AND active_lease_id=?""",
                (now_text, row["job_id"], row["lease_id"]),
            )
        return len(rows)

    def recover_expired(self, *, now: datetime | str | None = None) -> int:
        now_text = utc_text(now)
        with self._lock:
            self._begin()
            try:
                count = self._recover_expired_locked(now_text)
                self._connection.commit()
                return count
            except Exception:
                self._connection.rollback()
                raise

    def add_link(self, subject_id: str, url: str, *, kind: str = "issue") -> None:
        if kind not in {"issue", "pr"} or not url.startswith("https://"):
            raise LedgerError("links must be HTTPS issue or PR references")
        _assert_sanitized(url)
        self._connection.execute(
            "INSERT OR IGNORE INTO links VALUES(?, ?, ?)", (subject_id, kind, url)
        )

    def progress(self, *, now: datetime | str | None = None) -> dict[str, Any]:
        now_text = utc_text(now)
        states = Counter(
            row["state"] for row in self._connection.execute("SELECT state FROM asset_states")
        )
        retries = Counter(
            row["retry_state"] for row in self._connection.execute("SELECT retry_state FROM jobs")
        )
        blockers = [
            {"asset_id": row["asset_id"], "reason": row["blocker"]}
            for row in self._connection.execute(
                "SELECT asset_id, blocker FROM asset_states WHERE state='blocked' ORDER BY asset_id"
            )
        ]
        leases = Counter()
        for row in self._connection.execute(
            "SELECT expires_at, released_at, release_reason FROM leases"
        ):
            if row["released_at"] is None:
                leases[
                    "expired"
                    if _parse_time(row["expires_at"]) <= _parse_time(now_text)
                    else "active"
                ] += 1
            elif row["release_reason"] == "expired":
                leases["expired"] += 1
        evidence_links: list[str] = []
        for row in self._connection.execute(
            "SELECT body FROM records WHERE record_type='evidence' ORDER BY record_id"
        ):
            evidence_links.extend(json.loads(row["body"])["public_references"])
        work_links = [
            {"subject_id": row["subject_id"], "kind": row["link_kind"], "url": row["url"]}
            for row in self._connection.execute(
                "SELECT * FROM links ORDER BY subject_id, link_kind, url"
            )
        ]
        if blockers:
            next_action = "resolve current blockers"
        elif leases["active"]:
            next_action = "allow active leases to finish or checkpoint"
        elif any(value for key, value in retries.items() if key != "exhausted"):
            next_action = "claim the next capability-compatible job"
        elif states.get("indexed", 0) == sum(states.values()) and states:
            next_action = "no action; all assets are indexed"
        elif states:
            next_action = "advance the earliest asset through its next verified gate"
        else:
            next_action = "discover a source and create the first asset"
        return {
            "states": dict(sorted(states.items())),
            "retry_status": dict(sorted(retries.items())),
            "blockers": blockers,
            "leases": {"active": leases["active"], "expired": leases["expired"]},
            "evidence_links": sorted(set(evidence_links)),
            "work_links": work_links,
            "next_safe_action": next_action,
        }


DurableLedger = Ledger
