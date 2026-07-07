"""The Application aggregate — its state machine is the heart of the tracker.

Terminal states are `REJECTED`, `APPLIED`, `SKIPPED`. `FAILED` allows retry.
`NEEDS_INTERVENTION` covers the CAPTCHA / manual-review fallback path.

The transition table lives here and is authoritative — the DB layer (Phase 4)
persists whatever state we store, but only these transitions are ever legal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ApplicationState(StrEnum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    REJECTED = "rejected"
    TAILORED = "tailored"
    APPLYING = "applying"
    APPLIED = "applied"
    NEEDS_INTERVENTION = "needs_intervention"
    FAILED = "failed"
    SKIPPED = "skipped"


ALLOWED_TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    ApplicationState.DISCOVERED: frozenset({ApplicationState.SCORED, ApplicationState.SKIPPED}),
    ApplicationState.SCORED: frozenset(
        {ApplicationState.REJECTED, ApplicationState.TAILORED, ApplicationState.SKIPPED}
    ),
    ApplicationState.REJECTED: frozenset(),
    ApplicationState.TAILORED: frozenset({ApplicationState.APPLYING, ApplicationState.SKIPPED}),
    ApplicationState.APPLYING: frozenset(
        {
            ApplicationState.APPLIED,
            ApplicationState.NEEDS_INTERVENTION,
            ApplicationState.FAILED,
            ApplicationState.APPLYING,  # orphaned mid-run retry (--retry)
            ApplicationState.SKIPPED,
        }
    ),
    ApplicationState.NEEDS_INTERVENTION: frozenset(
        {
            ApplicationState.APPLYING,  # --retry after CAPTCHA / manual bail-out
            ApplicationState.APPLIED,
            ApplicationState.FAILED,
            ApplicationState.SKIPPED,
        }
    ),
    ApplicationState.APPLIED: frozenset(
        {ApplicationState.APPLYING}
    ),  # dry-run re-verify (--retry on a prior dry-run row)
    ApplicationState.FAILED: frozenset({ApplicationState.APPLYING, ApplicationState.SKIPPED}),
    ApplicationState.SKIPPED: frozenset(),
}


TERMINAL_STATES: frozenset[ApplicationState] = frozenset(
    s for s, allowed in ALLOWED_TRANSITIONS.items() if not allowed
)


class InvalidTransition(ValueError):
    """Raised when calling `Application.transition_to` with a disallowed target."""


class StateTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_state: ApplicationState
    to_state: ApplicationState
    at: datetime
    reason: str | None = None


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class Application(BaseModel):
    """Tracks one attempt at applying to one job under one profile."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    job_id: str
    profile_name: str
    state: ApplicationState = ApplicationState.DISCOVERED
    score: int | None = Field(default=None, ge=0, le=100)
    score_rationale: str | None = None
    error: str | None = None
    attempts: int = 0
    tailored_path: str | None = None
    """Directory on disk holding the tailored resume + cover letter for review.

    Populated by the tailoring pipeline. Files live at
    `<data_dir>/tailored/<application_id>/{resume.yaml,cover_letter.md}`.
    """
    dry_run: bool = False
    """True if the apply flow stopped at the pre-submit guard.

    The row still lands in `APPLIED` (reused terminal state) so callers can
    treat both real and dry-run applications with one query and split on
    this flag for reporting.
    """
    history: list[StateTransition] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)

    def transition_to(
        self,
        new_state: ApplicationState,
        *,
        reason: str | None = None,
    ) -> None:
        """Move the application to `new_state`, recording history.

        Raises `InvalidTransition` if the target is not allowed from the current
        state. Mutates the model in place.
        """
        allowed = ALLOWED_TRANSITIONS.get(self.state, frozenset())
        if new_state not in allowed:
            raise InvalidTransition(
                f"illegal transition {self.state} -> {new_state} "
                f"(allowed: {sorted(a.value for a in allowed)})"
            )
        now = _now_utc()
        self.history.append(
            StateTransition(from_state=self.state, to_state=new_state, at=now, reason=reason)
        )
        self.state = new_state
        self.updated_at = now

    def is_terminal(self) -> bool:
        # APPLIED stays terminal for status reporting; --retry is an explicit
        # CLI path that re-opens the row via APPLIED → APPLYING.
        if self.state is ApplicationState.APPLIED:
            return True
        return self.state in TERMINAL_STATES
