"""Test repository score ordering - applications should be returned best-first."""

from sqlalchemy import Engine

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)


def _create_repo(engine: Engine) -> SqlApplicationsRepository:
    """Helper to create repository from engine."""
    return SqlApplicationsRepository(engine)


def test_list_by_state_orders_by_score_desc(engine: Engine):
    """Applications returned highest score first."""
    repo = _create_repo(engine)

    # Insert 3 apps with different scores (in non-score order)
    app1 = Application(
        id="app1",
        job_id="job1",
        profile_name="test_profile",
        state=ApplicationState.TAILORED,
        score=70,
    )
    app2 = Application(
        id="app2",
        job_id="job2",
        profile_name="test_profile",
        state=ApplicationState.TAILORED,
        score=90,
    )
    app3 = Application(
        id="app3",
        job_id="job3",
        profile_name="test_profile",
        state=ApplicationState.TAILORED,
        score=50,
    )

    repo.add(app1)
    repo.add(app2)
    repo.add(app3)

    # Query should return in score DESC order (best first)
    results = repo.list_by_state(ApplicationState.TAILORED)
    assert len(results) == 3
    assert [r.score for r in results] == [90, 70, 50]
    assert [r.id for r in results] == ["app2", "app1", "app3"]


def test_list_by_state_nulls_last(engine: Engine):
    """NULL scores appear after scored applications."""
    repo = _create_repo(engine)

    app1 = Application(
        id="app1",
        job_id="job1",
        profile_name="test_profile",
        state=ApplicationState.FAILED,
        score=None,  # NULL score
    )
    app2 = Application(
        id="app2",
        job_id="job2",
        profile_name="test_profile",
        state=ApplicationState.FAILED,
        score=60,
    )
    app3 = Application(
        id="app3",
        job_id="job3",
        profile_name="test_profile",
        state=ApplicationState.FAILED,
        score=80,
    )

    repo.add(app1)
    repo.add(app2)
    repo.add(app3)

    results = repo.list_by_state(ApplicationState.FAILED)
    assert len(results) == 3
    # Scored first (DESC), then NULL
    assert results[0].score == 80
    assert results[1].score == 60
    assert results[2].score is None


def test_list_by_state_and_profile_orders_by_score_desc(engine: Engine):
    """Profile-filtered list also returns highest score first."""
    repo = _create_repo(engine)

    # Add apps for different profiles
    app1 = Application(
        id="app1",
        job_id="job1",
        profile_name="profile_a",
        state=ApplicationState.TAILORED,
        score=40,
    )
    app2 = Application(
        id="app2",
        job_id="job2",
        profile_name="profile_b",
        state=ApplicationState.TAILORED,
        score=90,
    )
    app3 = Application(
        id="app3",
        job_id="job3",
        profile_name="profile_a",
        state=ApplicationState.TAILORED,
        score=70,
    )

    repo.add(app1)
    repo.add(app2)
    repo.add(app3)

    # Query profile_a - should return in score DESC order
    results = repo.list_by_state_and_profile(
        ApplicationState.TAILORED, "profile_a"
    )
    assert len(results) == 2
    assert [r.score for r in results] == [70, 40]
    assert [r.id for r in results] == ["app3", "app1"]


def test_list_by_state_and_profile_nulls_last(engine: Engine):
    """NULL scores appear last in profile-filtered query."""
    repo = _create_repo(engine)

    app1 = Application(
        id="app1",
        job_id="job1",
        profile_name="profile_a",
        state=ApplicationState.TAILORED,
        score=None,
    )
    app2 = Application(
        id="app2",
        job_id="job2",
        profile_name="profile_a",
        state=ApplicationState.TAILORED,
        score=50,
    )

    repo.add(app1)
    repo.add(app2)

    results = repo.list_by_state_and_profile(
        ApplicationState.TAILORED, "profile_a"
    )
    assert len(results) == 2
    assert results[0].score == 50  # Scored first
    assert results[1].score is None  # NULL last
