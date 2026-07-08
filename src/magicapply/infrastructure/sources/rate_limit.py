"""Simple per-source rate limiter.

Blocks the calling thread so requests-per-minute stays under configured cap.
Deliberately naive — no burst allowance, and sources run serially in MVP.

**Jitter (Phase B):** the raw min-interval is rhythmically identical which
is a bot-detection signal. Passing ``jitter_ratio`` (a fraction 0.0–1.0)
scales the sleep by ``[1 - jitter_ratio, 1 + jitter_ratio]`` per call.
Default 0.0 keeps backwards compat; discovery adapters bump it to ~0.3.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class RateLimiter:
    """Blocking rate limiter enforcing a minimum interval between calls."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        jitter_ratio: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if not (0.0 <= jitter_ratio <= 1.0):
            raise ValueError("jitter_ratio must be in [0.0, 1.0]")
        self._min_interval = 60.0 / requests_per_minute
        self._jitter_ratio = jitter_ratio
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._next_allowed_at: float = 0.0

    def wait(self) -> None:
        """Block until the next request slot, then reserve it."""
        now = self._clock()
        delay = self._next_allowed_at - now
        if delay > 0:
            self._sleep(delay)
            now = self._clock()
        interval = self._min_interval
        if self._jitter_ratio > 0.0:
            # Scale by uniform(1 - r, 1 + r) so the average request rate
            # matches `requests_per_minute` while individual gaps vary.
            factor = 1.0 + self._rng.uniform(-self._jitter_ratio, self._jitter_ratio)
            interval = self._min_interval * factor
        self._next_allowed_at = now + interval
