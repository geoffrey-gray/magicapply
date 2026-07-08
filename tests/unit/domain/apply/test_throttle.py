"""Tests for the ApplyThrottle domain policy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from magicapply.config.models import (
    ApplyThrottleConfig,
    ThrottleCaps,
)
from magicapply.domain.apply.throttle import ApplyThrottle, ThrottleDecision


class _FakeCounters:
    """Records queries + returns pre-scripted counts by ATS."""

    def __init__(
        self,
        *,
        ats_counts: dict[str, int] | None = None,
        global_count: int = 0,
    ) -> None:
        self._ats_counts = ats_counts or {}
        self._global_count = global_count
        self.ats_calls: list[tuple[str, datetime]] = []
        self.global_calls: list[datetime] = []

    def ats(self, ats: str, since: datetime) -> int:
        self.ats_calls.append((ats, since))
        return self._ats_counts.get(ats, 0)

    def global_(self, since: datetime) -> int:
        self.global_calls.append(since)
        return self._global_count


_NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)


def _throttle(
    counters: _FakeCounters,
    *,
    config: ApplyThrottleConfig | None = None,
) -> ApplyThrottle:
    return ApplyThrottle(
        config=config or ApplyThrottleConfig(),
        ats_hourly_count=counters.ats,
        ats_daily_count=counters.ats,
        global_hourly_count=counters.global_,
        global_daily_count=counters.global_,
        clock=lambda: _NOW,
    )


class TestAllowedPath:
    def test_zero_counts_is_ok(self) -> None:
        t = _throttle(_FakeCounters())
        assert t.check(ats="greenhouse") == ThrottleDecision.ok()

    def test_under_default_cap_allowed(self) -> None:
        # Default hourly=6, daily=25.
        counters = _FakeCounters(ats_counts={"greenhouse": 5})
        assert _throttle(counters).check(ats="greenhouse").allowed


class TestPerAtsCap:
    def test_hourly_cap_hit_denies(self) -> None:
        counters = _FakeCounters(ats_counts={"greenhouse": 6})
        d = _throttle(counters).check(ats="greenhouse")
        assert d.allowed is False
        assert "hourly cap" in d.reason
        assert "greenhouse" in d.reason

    def test_daily_cap_hit_denies(self) -> None:
        # Push hourly below cap but daily >= cap.
        counters = _FakeCounters(ats_counts={"greenhouse": 25})
        cfg = ApplyThrottleConfig(
            ats_default=ThrottleCaps(hourly=100, daily=25),
        )
        d = _throttle(counters, config=cfg).check(ats="greenhouse")
        assert d.allowed is False
        assert "daily cap" in d.reason

    def test_override_beats_default(self) -> None:
        # Workday override: hourly=2, daily=10. Default: hourly=6.
        # Workday hit at 2 → denied even though default would allow.
        counters = _FakeCounters(ats_counts={"workday": 2})
        cfg = ApplyThrottleConfig(
            ats_overrides={"workday": ThrottleCaps(hourly=2, daily=10)},
        )
        d = _throttle(counters, config=cfg).check(ats="workday")
        assert d.allowed is False
        assert "workday" in d.reason

    def test_different_ats_untouched(self) -> None:
        counters = _FakeCounters(ats_counts={"greenhouse": 100})
        # Workday count is 0 → allowed even when Greenhouse is over cap.
        assert _throttle(counters).check(ats="workday").allowed


class TestGlobalCap:
    def test_global_hourly_hit_denies_regardless_of_per_ats(self) -> None:
        # Global hourly cap = 15 by default.
        counters = _FakeCounters(ats_counts={"greenhouse": 0}, global_count=15)
        d = _throttle(counters).check(ats="greenhouse")
        assert d.allowed is False
        assert "global hourly" in d.reason

    def test_global_checked_before_per_ats(self) -> None:
        """Efficiency check: if the global cap denies, per-ATS counters
        shouldn't be consulted (cheaper query)."""
        counters = _FakeCounters(ats_counts={"greenhouse": 5}, global_count=15)
        _throttle(counters).check(ats="greenhouse")
        assert counters.ats_calls == []
        assert len(counters.global_calls) == 1


class TestWindowBoundaries:
    def test_hour_window_uses_hour_ago(self) -> None:
        counters = _FakeCounters()
        _throttle(counters).check(ats="greenhouse")
        # ats_hourly_count called with (ats, hour_ago)
        assert counters.ats_calls, "expected an hourly ATS query"
        _, since = counters.ats_calls[0]
        delta = _NOW - since
        assert delta.total_seconds() == pytest.approx(3600, abs=1)

    def test_day_window_uses_day_ago(self) -> None:
        # Force the hourly check to pass by setting per-ats hourly small
        # but daily large. Then observe that a daily query fired.
        counters = _FakeCounters()
        _throttle(counters).check(ats="greenhouse")
        # Both hourly + daily fired (with different since values).
        assert len(counters.ats_calls) == 2
        (_, hour_since), (_, day_since) = counters.ats_calls
        assert (_NOW - hour_since).total_seconds() == pytest.approx(3600, abs=1)
        assert (_NOW - day_since).total_seconds() == pytest.approx(86400, abs=1)


class TestDecisionShape:
    def test_ok_helper(self) -> None:
        d = ThrottleDecision.ok()
        assert d.allowed is True
        assert d.reason == "ok"

    def test_deny_helper_carries_reason(self) -> None:
        d = ThrottleDecision.deny("nope")
        assert d.allowed is False
        assert d.reason == "nope"
