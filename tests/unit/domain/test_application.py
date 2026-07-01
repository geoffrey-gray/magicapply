"""Tests for the Application state machine."""

from __future__ import annotations

import pytest

from magicapply.domain.models.application import (
    ALLOWED_TRANSITIONS,
    Application,
    ApplicationState,
    InvalidTransition,
)


def _make(state: ApplicationState = ApplicationState.DISCOVERED) -> Application:
    app = Application(job_id="j1", profile_name="p1")
    if state is not ApplicationState.DISCOVERED:
        # Walk a legal path to the desired starting state.
        _walk_to(app, state)
    return app


def _walk_to(app: Application, target: ApplicationState) -> None:
    """Move `app` through legal transitions until it reaches `target`."""
    paths: dict[ApplicationState, list[ApplicationState]] = {
        ApplicationState.SCORED: [ApplicationState.SCORED],
        ApplicationState.REJECTED: [ApplicationState.SCORED, ApplicationState.REJECTED],
        ApplicationState.TAILORED: [ApplicationState.SCORED, ApplicationState.TAILORED],
        ApplicationState.APPLYING: [
            ApplicationState.SCORED,
            ApplicationState.TAILORED,
            ApplicationState.APPLYING,
        ],
        ApplicationState.APPLIED: [
            ApplicationState.SCORED,
            ApplicationState.TAILORED,
            ApplicationState.APPLYING,
            ApplicationState.APPLIED,
        ],
        ApplicationState.NEEDS_INTERVENTION: [
            ApplicationState.SCORED,
            ApplicationState.TAILORED,
            ApplicationState.APPLYING,
            ApplicationState.NEEDS_INTERVENTION,
        ],
        ApplicationState.FAILED: [
            ApplicationState.SCORED,
            ApplicationState.TAILORED,
            ApplicationState.APPLYING,
            ApplicationState.FAILED,
        ],
        ApplicationState.SKIPPED: [ApplicationState.SKIPPED],
    }
    for step in paths[target]:
        app.transition_to(step)


class TestConstruction:
    def test_defaults(self) -> None:
        app = Application(job_id="j1", profile_name="p1")
        assert app.state is ApplicationState.DISCOVERED
        assert app.score is None
        assert app.attempts == 0
        assert app.history == []
        assert app.id  # UUID hex

    def test_score_bounds(self) -> None:
        with pytest.raises(ValueError):
            Application(job_id="j", profile_name="p", score=-1)
        with pytest.raises(ValueError):
            Application(job_id="j", profile_name="p", score=101)


class TestTransitions:
    def test_valid_discovered_to_scored(self) -> None:
        app = _make()
        app.transition_to(ApplicationState.SCORED, reason="ready to score")
        assert app.state is ApplicationState.SCORED
        assert len(app.history) == 1
        assert app.history[0].from_state is ApplicationState.DISCOVERED
        assert app.history[0].reason == "ready to score"

    def test_invalid_direct_discovered_to_applied(self) -> None:
        app = _make()
        with pytest.raises(InvalidTransition, match="illegal transition"):
            app.transition_to(ApplicationState.APPLIED)

    def test_terminal_applied_rejects_further(self) -> None:
        app = _make(ApplicationState.APPLIED)
        for target in ApplicationState:
            with pytest.raises(InvalidTransition):
                app.transition_to(target)

    def test_terminal_rejected_rejects_further(self) -> None:
        app = _make(ApplicationState.REJECTED)
        with pytest.raises(InvalidTransition):
            app.transition_to(ApplicationState.APPLYING)

    def test_failed_can_retry(self) -> None:
        app = _make(ApplicationState.FAILED)
        app.transition_to(ApplicationState.APPLYING)
        assert app.state is ApplicationState.APPLYING

    def test_needs_intervention_recovery_paths(self) -> None:
        app = _make(ApplicationState.NEEDS_INTERVENTION)
        # NEEDS_INTERVENTION -> APPLIED is allowed (user finishes manually).
        app.transition_to(ApplicationState.APPLIED)
        assert app.state is ApplicationState.APPLIED

    def test_is_terminal(self) -> None:
        assert not _make().is_terminal()
        assert _make(ApplicationState.APPLIED).is_terminal()
        assert _make(ApplicationState.REJECTED).is_terminal()
        assert _make(ApplicationState.SKIPPED).is_terminal()

    def test_history_grows_and_updated_at_advances(self) -> None:
        app = _make()
        before = app.updated_at
        app.transition_to(ApplicationState.SCORED)
        app.transition_to(ApplicationState.TAILORED)
        assert len(app.history) == 2
        assert app.updated_at >= before


class TestTransitionTable:
    def test_all_states_present_in_table(self) -> None:
        for s in ApplicationState:
            assert s in ALLOWED_TRANSITIONS, f"missing transition entry for {s}"

    def test_targets_are_valid_states(self) -> None:
        for _from, targets in ALLOWED_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, ApplicationState)
