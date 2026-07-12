"""Apply pipeline utilities - result classification and counting.

Helper functions for analyzing ApplyReport outcomes. These are shared between
the orchestrator (apply.py) and strategies (apply_strategies.py) to avoid code
duplication and maintain a single source of truth for outcome logic.
"""

from __future__ import annotations

from magicapply.domain.models.application import ApplicationState
from magicapply.pipelines.apply_types import ApplyReport


def is_throttle_defer(report: ApplyReport) -> bool:
    """Check if report indicates throttle deferral (quota limit hit).

    Throttle deferrals occur when apply_one() cannot proceed because the
    ApplyThrottle signals that the ATS/destination has hit its hourly or daily
    quota. The error message starts with "throttle:" to distinguish this from
    other failures.

    These are NOT counted as outcomes because the application remains in
    TAILORED state and will be retried in a future batch when the quota window
    rolls over.
    """
    return bool(report.error and report.error.startswith("throttle:"))


def is_counted_outcome(report: ApplyReport) -> bool:
    """Check if report represents a counted outcome.

    Counted outcomes are terminal states that represent real work done:
    - APPLIED: Successfully submitted
    - FAILED: Attempted but encountered unrecoverable error
    - NEEDS_INTERVENTION: Hit CAPTCHA or other human-required step

    Throttle deferrals are NOT counted because they represent skipped attempts,
    not completed work. The application stays TAILORED and will be retried.
    """
    if is_throttle_defer(report):
        return False
    return report.final_state in {
        ApplicationState.APPLIED,
        ApplicationState.FAILED,
        ApplicationState.NEEDS_INTERVENTION,
    }


def count_outcomes(reports: list[ApplyReport]) -> int:
    """Count outcomes in reports (excludes throttle deferrals).

    Used by paced strategies to determine when max_outcomes limit is reached.
    Only counts reports that represent completed work, not skipped attempts.
    """
    return sum(1 for r in reports if is_counted_outcome(r))
