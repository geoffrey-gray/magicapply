"""Simple per-source rate limiter.

Blocks the calling thread so requests-per-minute stays under configured cap.
Deliberately naive — no burst allowance, no jitter — because sources run
serially in MVP. Upgrade if concurrent discovery lands in Phase 2.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """Blocking rate limiter enforcing a minimum interval between calls."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._min_interval = 60.0 / requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._next_allowed_at: float = 0.0

    def wait(self) -> None:
        """Block until the next request slot, then reserve it."""
        now = self._clock()
        delay = self._next_allowed_at - now
        if delay > 0:
            self._sleep(delay)
            now = self._clock()
        self._next_allowed_at = now + self._min_interval
