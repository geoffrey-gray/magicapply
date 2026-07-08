"""Application throttle — per-ATS + global caps on APPLIED transitions.

`ApplyPipeline.apply_one` asks the throttle for permission just before it
calls the ATS handler. When the cap is hit for either the specific ATS or
the global budget, the throttle returns a decision with `allowed=False`
and the pipeline leaves the Application in TAILORED so the next batch
picks it up naturally when the window rolls.

**Why in domain and not in the pipeline?** The policy — hourly + daily
window, per-ATS override, global cap — is a domain concern (how the
operator wants to treat their platforms), separate from the pipeline
mechanics (state transitions, browser session). Same convention as
`AnswerRouter`: policy in `domain/`, injected into the pipeline. See
``docs/GOF_PATTERNS.md`` §Extend-don't-multiply — the pipeline is not a
sibling of the throttle; the throttle is a small collaborator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from magicapply.config.models import ApplyThrottleConfig, ThrottleCaps


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> ThrottleDecision:
        return cls(allowed=True, reason="ok")

    @classmethod
    def deny(cls, reason: str) -> ThrottleDecision:
        return cls(allowed=False, reason=reason)


class ApplyThrottle:
    """Query recent APPLIED rows and decide whether one more may fire."""

    def __init__(
        self,
        *,
        config: ApplyThrottleConfig,
        ats_hourly_count: Callable[[str, datetime], int],
        ats_daily_count: Callable[[str, datetime], int],
        global_hourly_count: Callable[[datetime], int],
        global_daily_count: Callable[[datetime], int],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._ats_hourly = ats_hourly_count
        self._ats_daily = ats_daily_count
        self._global_hourly = global_hourly_count
        self._global_daily = global_daily_count
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    def check(self, *, ats: str) -> ThrottleDecision:
        now = self._clock()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        # Global first — cheaper (no join), and if it's tripped every
        # per-ATS check is also academic.
        gcaps = self._config.global_cap
        if self._global_hourly(hour_ago) >= gcaps.hourly:
            return ThrottleDecision.deny(
                f"global hourly cap reached ({gcaps.hourly})"
            )
        if self._global_daily(day_ago) >= gcaps.daily:
            return ThrottleDecision.deny(
                f"global daily cap reached ({gcaps.daily})"
            )

        caps = self._caps_for(ats)
        if self._ats_hourly(ats, hour_ago) >= caps.hourly:
            return ThrottleDecision.deny(
                f"hourly cap reached (ats={ats}, cap={caps.hourly})"
            )
        if self._ats_daily(ats, day_ago) >= caps.daily:
            return ThrottleDecision.deny(
                f"daily cap reached (ats={ats}, cap={caps.daily})"
            )
        return ThrottleDecision.ok()

    def _caps_for(self, ats: str) -> ThrottleCaps:
        return self._config.ats_overrides.get(ats, self._config.ats_default)
