"""Deterministic per-host request spacing."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping


def _canonical_hostname(hostname: str) -> str:
    if not isinstance(hostname, str) or hostname != hostname.strip():
        raise ValueError("hostname must be a reviewed canonical hostname")
    try:
        canonical = hostname.encode("ascii").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("hostname must be ASCII") from error
    if not canonical or canonical.endswith("."):
        raise ValueError("hostname must be a reviewed canonical hostname")
    return canonical


class HostRateLimiter:
    """Space requests independently using an injectable monotonic clock."""

    def __init__(
        self,
        intervals: Mapping[str, float],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._intervals: dict[str, float] = {}
        for hostname, interval in intervals.items():
            canonical = _canonical_hostname(hostname)
            if canonical != hostname.lower() or interval < 0:
                raise ValueError("rate-limit configuration is invalid")
            self._intervals[canonical] = float(interval)
        if not self._intervals:
            raise ValueError("at least one host interval is required")
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, hostname: str) -> float:
        canonical = _canonical_hostname(hostname)
        if canonical not in self._intervals:
            raise ValueError("hostname has no reviewed request interval")
        with self._lock:
            now = self._clock()
            previous = self._last_request.get(canonical)
            delay = (
                0.0
                if previous is None
                else max(0.0, previous + self._intervals[canonical] - now)
            )
            if delay:
                self._sleep(delay)
                now = self._clock()
            self._last_request[canonical] = now
            return delay
