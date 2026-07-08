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


class TestJitter:
    def test_jitter_scales_the_next_slot(self) -> None:
        """Jitter must vary the interval around the mean while keeping the
        long-run rate close to the configured rpm. Uses a seeded RNG so
        the assertion doesn't chase probability."""
        import random

        f = _FakeClock()
        rng = random.Random(42)
        rl = RateLimiter(60, jitter_ratio=0.5, clock=f.clock, sleep=f.sleep, rng=rng)
        rl.wait()  # first call: no sleep
        rl.wait()  # second: sleep for min_interval * uniform(0.5, 1.5)
        assert f.sleeps, "expected a sleep on the second call"
        actual = f.sleeps[0]
        # For rpm=60, min_interval=1.0; jitter=0.5 → range [0.5, 1.5].
        assert 0.5 <= actual <= 1.5

    def test_jitter_zero_matches_pre_jitter_behavior(self) -> None:
        f = _FakeClock()
        rl = RateLimiter(60, jitter_ratio=0.0, clock=f.clock, sleep=f.sleep)
        rl.wait()
        rl.wait()
        assert f.sleeps == pytest.approx([1.0])

    def test_jitter_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(60, jitter_ratio=-0.1)
        with pytest.raises(ValueError):
            RateLimiter(60, jitter_ratio=1.1)
