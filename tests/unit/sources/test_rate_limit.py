"""Tests for the RateLimiter (deterministic — no real sleeps)."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.sources.rate_limit import RateLimiter


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class TestRateLimiter:
    def test_first_call_does_not_sleep(self) -> None:
        f = _FakeClock()
        rl = RateLimiter(60, clock=f.clock, sleep=f.sleep)
        rl.wait()
        assert f.sleeps == []

    def test_second_call_within_interval_sleeps(self) -> None:
        f = _FakeClock()
        rl = RateLimiter(60, clock=f.clock, sleep=f.sleep)  # 1 req/sec
        rl.wait()
        rl.wait()
        assert f.sleeps == pytest.approx([1.0])

    def test_spaced_calls_do_not_sleep(self) -> None:
        f = _FakeClock()
        rl = RateLimiter(60, clock=f.clock, sleep=f.sleep)  # 1 req/sec
        rl.wait()
        f.now += 5.0
        rl.wait()
        assert f.sleeps == []

    def test_zero_or_negative_rpm_rejected(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(0)
        with pytest.raises(ValueError):
            RateLimiter(-1)
