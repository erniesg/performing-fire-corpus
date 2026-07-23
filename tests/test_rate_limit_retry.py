from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.rate_limit import HostRateLimiter
from performing_fire_corpus.retry import (
    RetryPolicy,
    RetryState,
    classify_outcome,
    plan_retry,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class RateLimitAndRetryTests(unittest.TestCase):
    def test_rate_limiter_spaces_each_normalized_host_deterministically(self) -> None:
        clock = FakeClock()
        limiter = HostRateLimiter(
            {"njp.ggcf.kr": 2.0, "njpvideo.ggcf.kr": 5.0},
            clock=clock.now,
            sleep=clock.sleep,
        )

        self.assertEqual(0.0, limiter.acquire("NJP.GGCF.KR"))
        self.assertEqual(2.0, limiter.acquire("njp.ggcf.kr"))
        self.assertEqual(0.0, limiter.acquire("njpvideo.ggcf.kr"))
        self.assertEqual([2.0], clock.sleeps)

    def test_rate_limiter_rejects_unconfigured_or_ambiguous_aliases(self) -> None:
        limiter = HostRateLimiter({"njp.ggcf.kr": 1.0})
        for hostname in ("njp.ggcf.kr.", "alias.njp.ggcf.kr", "unknown.invalid"):
            with self.subTest(hostname=hostname):
                with self.assertRaises(ValueError):
                    limiter.acquire(hostname)

    def test_durable_outcomes_are_never_retried(self) -> None:
        expected = {
            "http_403": "blocked",
            "robots_denied": "blocked",
            "login_required": "blocked",
            "unclear_rights": "blocked",
            "changed_structure": "failed",
        }
        for outcome, disposition in expected.items():
            with self.subTest(outcome=outcome):
                classification = classify_outcome(outcome)
                self.assertEqual(disposition, classification.disposition)
                self.assertFalse(classification.retryable)

        permissive_configuration = RetryPolicy(
            max_attempts=3,
            max_elapsed_backoff=10.0,
            base_delay=1.0,
            max_retry_after=5.0,
            transient_outcomes=frozenset(expected),
        )
        for outcome in expected:
            with self.subTest(configured_durable_outcome=outcome):
                decision = plan_retry(
                    permissive_configuration, RetryState(), outcome
                )
                self.assertFalse(decision.retry)
                self.assertEqual(classify_outcome(outcome).code, decision.code)

    def test_retry_after_is_bounded_and_attempts_are_resume_safe(self) -> None:
        policy = RetryPolicy(
            max_attempts=3,
            max_elapsed_backoff=12.0,
            base_delay=2.0,
            max_retry_after=8.0,
            transient_outcomes=frozenset({"http_429", "http_503", "timeout"}),
        )
        first = plan_retry(
            policy,
            RetryState(),
            "http_429",
            retry_after="7",
        )
        self.assertTrue(first.retry)
        self.assertEqual(7.0, first.delay)
        self.assertEqual(
            {"attempt_count": 1, "elapsed_backoff": 7.0},
            first.state.to_dict(),
        )

        resumed = RetryState.from_dict(first.state.to_dict())
        second = plan_retry(policy, resumed, "timeout")
        self.assertTrue(second.retry)
        self.assertEqual(4.0, second.delay)
        self.assertEqual(2, second.state.attempt_count)

        exhausted = plan_retry(policy, second.state, "http_503")
        self.assertFalse(exhausted.retry)
        self.assertEqual("retry_exhausted", exhausted.code)
        self.assertEqual(3, exhausted.state.attempt_count)

    def test_invalid_or_excessive_retry_after_fails_closed_to_local_backoff(self) -> None:
        policy = RetryPolicy(
            max_attempts=4,
            max_elapsed_backoff=20.0,
            base_delay=1.0,
            max_retry_after=5.0,
            transient_outcomes=frozenset({"http_429"}),
        )
        for retry_after in ("-1", "6", "not-a-number", None):
            with self.subTest(retry_after=retry_after):
                result = plan_retry(
                    policy, RetryState(), "http_429", retry_after=retry_after
                )
                self.assertEqual(1.0, result.delay)

    def test_bounded_http_date_retry_after_is_honored(self) -> None:
        policy = RetryPolicy(
            max_attempts=3,
            max_elapsed_backoff=20.0,
            base_delay=1.0,
            max_retry_after=8.0,
            transient_outcomes=frozenset({"http_503"}),
        )
        result = plan_retry(
            policy,
            RetryState(),
            "http_503",
            retry_after="Thu, 01 Jan 2026 00:00:05 GMT",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(result.retry)
        self.assertEqual(5.0, result.delay)

    def test_unconfigured_transient_and_elapsed_cap_are_not_retried(self) -> None:
        policy = RetryPolicy(
            max_attempts=4,
            max_elapsed_backoff=3.0,
            base_delay=2.0,
            max_retry_after=5.0,
            transient_outcomes=frozenset({"timeout"}),
        )
        permanent = plan_retry(policy, RetryState(), "http_500")
        self.assertFalse(permanent.retry)
        self.assertEqual("outcome_not_retryable", permanent.code)

        capped = plan_retry(
            policy,
            RetryState(attempt_count=1, elapsed_backoff=2.0),
            "timeout",
        )
        self.assertFalse(capped.retry)
        self.assertEqual("backoff_budget_exhausted", capped.code)


if __name__ == "__main__":
    unittest.main()
