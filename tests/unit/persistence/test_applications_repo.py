"""Tests for SqlApplicationsRepository."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.repositories.applications import (
    DuplicateApplication,
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository


def _make_job_and_app(engine: Engine, profile: str = "p1") -> tuple[Job, Application]:
    jobs = SqlJobsRepository(engine)
    job = Job.new(
        source_name="s",
        url="https://acme.com/j/1",
        title="SWE",
        company="Acme",
    )
    jobs.upsert(job)
    app = Application(job_id=job.id, profile_name=profile)
    return job, app


class TestAdd:
    def test_add_new(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        stored = repo.add(app)
        assert stored.id == app.id

    def test_duplicate_id_raises(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        with pytest.raises(DuplicateApplication):
            repo.add(app)


class TestSave:
    def test_save_persists_state_and_history(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        repo.add(app)

        app.transition_to(ApplicationState.SCORED, reason="ready")
        app.score = 82
        app.score_rationale = "strong python signals"
        repo.save(app)

        reloaded = repo.get(app.id)
        assert reloaded is not None
        assert reloaded.state is ApplicationState.SCORED
        assert reloaded.score == 82
        assert reloaded.score_rationale == "strong python signals"
        assert len(reloaded.history) == 1
        assert reloaded.history[0].reason == "ready"

    def test_save_unknown_raises(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        with pytest.raises(KeyError):
            repo.save(app)


class TestQueries:
    def test_by_job_and_profile(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine, profile="senior-swe")
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        found = repo.by_job_and_profile(app.job_id, "senior-swe")
        assert found is not None
        assert found.id == app.id
        assert repo.by_job_and_profile(app.job_id, "other-profile") is None

    def test_list_by_state(self, engine: Engine) -> None:
        _, app1 = _make_job_and_app(engine, profile="p1")
        _, app2 = _make_job_and_app(engine, profile="p2")
        repo = SqlApplicationsRepository(engine)
        repo.add(app1)
        repo.add(app2)

        app1.transition_to(ApplicationState.SCORED)
        repo.save(app1)

        scored = repo.list_by_state(ApplicationState.SCORED)
        discovered = repo.list_by_state(ApplicationState.DISCOVERED)
        assert {a.id for a in scored} == {app1.id}
        assert {a.id for a in discovered} == {app2.id}
