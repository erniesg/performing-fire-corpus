"""Bounded, resume-safe retry decisions for acquisition workers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


_DURABLE_OUTCOMES = {
    "http_403": ("blocked", "access_forbidden"),
    "robots_denied": ("blocked", "robots_denied"),
    "login_required": ("blocked", "login_required"),
    "unclear_rights": ("blocked", "rights_unclear"),
    "changed_structure": ("failed", "response_structure_changed"),
}


@dataclass(frozen=True)
class OutcomeClassification:
    disposition: str
    code: str
    retryable: bool


@dataclass(frozen=True)
class RetryState:
    attempt_count: int = 0
    elapsed_backoff: float = 0.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or isinstance(self.elapsed_backoff, bool)
            or not isinstance(self.elapsed_backoff, (int, float))
            or self.attempt_count < 0
            or self.elapsed_backoff < 0
            or not math.isfinite(self.elapsed_backoff)
        ):
            raise ValueError("retry state cannot be negative")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "attempt_count": self.attempt_count,
            "elapsed_backoff": self.elapsed_backoff,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RetryState:
        if set(value) != {"attempt_count", "elapsed_backoff"}:
            raise ValueError("retry state has unknown or missing fields")
        attempt_count = value["attempt_count"]
        elapsed_backoff = value["elapsed_backoff"]
        if (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or isinstance(elapsed_backoff, bool)
            or not isinstance(elapsed_backoff, (int, float))
        ):
            raise ValueError("retry state has invalid field types")
        return cls(attempt_count, float(elapsed_backoff))


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    max_elapsed_backoff: float
    base_delay: float
    max_retry_after: float
    transient_outcomes: frozenset[str]

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
            or self.max_elapsed_backoff < 0
            or self.base_delay <= 0
            or self.max_retry_after < 0
            or not all(
                math.isfinite(value)
                for value in (
                    self.max_elapsed_backoff,
                    self.base_delay,
                    self.max_retry_after,
                )
            )
        ):
            raise ValueError("retry policy bounds are invalid")


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay: float
    code: str
    state: RetryState


def classify_outcome(outcome: str) -> OutcomeClassification:
    durable = _DURABLE_OUTCOMES.get(outcome)
    if durable is not None:
        disposition, code = durable
        return OutcomeClassification(disposition, code, False)
    return OutcomeClassification("failed", "outcome_not_retryable", False)


def _bounded_retry_after(
    value: str | int | float | None,
    maximum: float,
    now: datetime,
) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        if not isinstance(value, str):
            return None
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            return None
        parsed = (retry_at - now.astimezone(timezone.utc)).total_seconds()
    return parsed if math.isfinite(parsed) and 0 <= parsed <= maximum else None


def plan_retry(
    policy: RetryPolicy,
    state: RetryState,
    outcome: str,
    *,
    retry_after: str | int | float | None = None,
    now: datetime | None = None,
) -> RetryDecision:
    """Record this failed attempt and decide whether another is permitted."""

    next_state = RetryState(state.attempt_count + 1, state.elapsed_backoff)
    if outcome in _DURABLE_OUTCOMES:
        classification = classify_outcome(outcome)
        return RetryDecision(False, 0.0, classification.code, next_state)
    if outcome not in policy.transient_outcomes:
        classification = classify_outcome(outcome)
        return RetryDecision(False, 0.0, classification.code, next_state)
    if next_state.attempt_count >= policy.max_attempts:
        return RetryDecision(False, 0.0, "retry_exhausted", next_state)
    local_delay = policy.base_delay * (2 ** (next_state.attempt_count - 1))
    current_time = datetime.now(timezone.utc) if now is None else now
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    server_delay = _bounded_retry_after(
        retry_after, policy.max_retry_after, current_time
    )
    delay = local_delay if server_delay is None else server_delay
    if state.elapsed_backoff + delay > policy.max_elapsed_backoff:
        return RetryDecision(False, 0.0, "backoff_budget_exhausted", next_state)
    scheduled = RetryState(
        next_state.attempt_count, state.elapsed_backoff + delay
    )
    return RetryDecision(True, delay, "retry_scheduled", scheduled)
