"""Tests for throttle race condition fix — APPLYING state counted."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository


def _make_job_and_app(
    engine: Engine, profile: str = "p1", source: str = "s", url_suffix: str = ""
) -> tuple[Job, Application]:
    jobs = SqlJobsRepository(engine)
    # Use unique URL per call to avoid upsert collision
    import uuid
    unique_id = url_suffix or uuid.uuid4().hex[:8]
    job = Job.new(
        source_name=source,
        url=f"https://acme.com/j/{unique_id}",
        title="SWE",
        company="Acme",
    )
    jobs.upsert(job)
    app = Application(job_id=job.id, profile_name=profile)
    return job, app


class TestCountAppliedAllInWindow:
    """count_applied_all_in_window counts APPLIED + APPLYING."""

    def test_counts_applied_state(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        app.transition_to(ApplicationState.SCORED)
        app.transition_to(ApplicationState.TAILORED)
        app.transition_to(ApplicationState.APPLYING)
        app.transition_to(ApplicationState.APPLIED)
        repo.save(app)

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_all_in_window(since)
        assert count == 1

    def test_counts_applying_state(self, engine: Engine) -> None:
        """CRITICAL: APPLYING must count to prevent throttle race."""
        _, app = _make_job_and_app(engine)
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        app.transition_to(ApplicationState.SCORED)
        app.transition_to(ApplicationState.TAILORED)
        app.transition_to(ApplicationState.APPLYING)
        repo.save(app)

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_all_in_window(since)
        assert count == 1, "APPLYING must count to prevent race condition"

    def test_ignores_other_states(self, engine: Engine) -> None:
        _, app1 = _make_job_and_app(engine, profile="p1")
        _, app2 = _make_job_and_app(engine, profile="p2")
        _, app3 = _make_job_and_app(engine, profile="p3")
        repo = SqlApplicationsRepository(engine)
        repo.add(app1)
        repo.add(app2)
        repo.add(app3)

        # app1 → SCORED (not counted)
        app1.transition_to(ApplicationState.SCORED)
        # app2 → TAILORED (not counted)
        app2.transition_to(ApplicationState.SCORED)
        app2.transition_to(ApplicationState.TAILORED)
        # app3 → FAILED (not counted)
        app3.transition_to(ApplicationState.SCORED)
        app3.transition_to(ApplicationState.TAILORED)
        app3.transition_to(ApplicationState.APPLYING)
        app3.transition_to(ApplicationState.FAILED)
        repo.save(app1)
        repo.save(app2)
        repo.save(app3)

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_all_in_window(since)
        assert count == 0

    def test_window_filtering(self, engine: Engine) -> None:
        _, old_app = _make_job_and_app(engine, profile="p1")
        _, new_app = _make_job_and_app(engine, profile="p2")
        repo = SqlApplicationsRepository(engine)
        repo.add(old_app)

        # Simulate old application (2 hours ago)
        old_app.transition_to(ApplicationState.SCORED)
        old_app.transition_to(ApplicationState.TAILORED)
        old_app.transition_to(ApplicationState.APPLYING)
        old_app.transition_to(ApplicationState.APPLIED)
        old_app.updated_at = datetime.now(UTC) - timedelta(hours=2)
        repo.save(old_app)

        # New application (just now)
        repo.add(new_app)
        new_app.transition_to(ApplicationState.SCORED)
        new_app.transition_to(ApplicationState.TAILORED)
        new_app.transition_to(ApplicationState.APPLYING)
        repo.save(new_app)

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_all_in_window(since)
        assert count == 1, "Only new_app should count (APPLYING state)"


class TestCountAppliedBySourceInWindow:
    """count_applied_by_source_in_window counts APPLIED + APPLYING per source."""

    def test_counts_applying_for_source(self, engine: Engine) -> None:
        _, app = _make_job_and_app(engine, source="indeed")
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        app.transition_to(ApplicationState.SCORED)
        app.transition_to(ApplicationState.TAILORED)
        app.transition_to(ApplicationState.APPLYING)
        repo.save(app)

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_by_source_in_window("indeed", since)
        assert count == 1

    def test_filters_by_source(self, engine: Engine) -> None:
        _, indeed_app = _make_job_and_app(engine, source="indeed")
        _, linkedin_app = _make_job_and_app(engine, source="linkedin")
        repo = SqlApplicationsRepository(engine)
        repo.add(indeed_app)
        repo.add(linkedin_app)

        # indeed app → APPLYING
        indeed_app.transition_to(ApplicationState.SCORED)
        indeed_app.transition_to(ApplicationState.TAILORED)
        indeed_app.transition_to(ApplicationState.APPLYING)
        # linkedin app → APPLIED
        linkedin_app.transition_to(ApplicationState.SCORED)
        linkedin_app.transition_to(ApplicationState.TAILORED)
        linkedin_app.transition_to(ApplicationState.APPLYING)
        linkedin_app.transition_to(ApplicationState.APPLIED)
        repo.save(indeed_app)
        repo.save(linkedin_app)

        since = datetime.now(UTC) - timedelta(hours=1)
        indeed_count = repo.count_applied_by_source_in_window("indeed", since)
        linkedin_count = repo.count_applied_by_source_in_window("linkedin", since)
        assert indeed_count == 1
        assert linkedin_count == 1


class TestCountAppliedInWindow:
    """count_applied_in_window counts APPLIED + APPLYING per ATS."""

    def test_counts_applying_for_ats(self, engine: Engine) -> None:
        jobs = SqlJobsRepository(engine)
        job = Job.new(
            source_name="s",
            url="https://boards.greenhouse.io/company/jobs/123",
            title="SWE",
            company="Acme",
        )
        jobs.upsert(job)
        app = Application(job_id=job.id, profile_name="p1")
        repo = SqlApplicationsRepository(engine)
        repo.add(app)
        app.transition_to(ApplicationState.SCORED)
        app.transition_to(ApplicationState.TAILORED)
        app.transition_to(ApplicationState.APPLYING)
        repo.save(app)

        from magicapply.pipelines.apply import ats_key_for_url

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_in_window(ats_key_for_url, "greenhouse", since)
        assert count == 1

    def test_filters_by_ats(self, engine: Engine) -> None:
        jobs = SqlJobsRepository(engine)
        gh_job = Job.new(
            source_name="s",
            url="https://boards.greenhouse.io/company/jobs/1",
            title="SWE",
            company="Acme",
        )
        lever_job = Job.new(
            source_name="s",
            url="https://jobs.lever.co/company/abc",
            title="PM",
            company="Beta",
        )
        jobs.upsert(gh_job)
        jobs.upsert(lever_job)

        gh_app = Application(job_id=gh_job.id, profile_name="p1")
        lever_app = Application(job_id=lever_job.id, profile_name="p1")
        repo = SqlApplicationsRepository(engine)
        repo.add(gh_app)
        repo.add(lever_app)

        # greenhouse app → APPLYING
        gh_app.transition_to(ApplicationState.SCORED)
        gh_app.transition_to(ApplicationState.TAILORED)
        gh_app.transition_to(ApplicationState.APPLYING)
        # lever app → APPLIED
        lever_app.transition_to(ApplicationState.SCORED)
        lever_app.transition_to(ApplicationState.TAILORED)
        lever_app.transition_to(ApplicationState.APPLYING)
        lever_app.transition_to(ApplicationState.APPLIED)
        repo.save(gh_app)
        repo.save(lever_app)

        from magicapply.pipelines.apply import ats_key_for_url

        since = datetime.now(UTC) - timedelta(hours=1)
        gh_count = repo.count_applied_in_window(ats_key_for_url, "greenhouse", since)
        lever_count = repo.count_applied_in_window(ats_key_for_url, "lever", since)
        assert gh_count == 1
        assert lever_count == 1

    def test_both_applied_and_applying_counted(self, engine: Engine) -> None:
        """Verify both APPLIED and APPLYING states are counted."""
        jobs = SqlJobsRepository(engine)
        job1 = Job.new(
            source_name="s",
            url="https://boards.greenhouse.io/company/jobs/1",
            title="SWE1",
            company="Acme",
        )
        job2 = Job.new(
            source_name="s",
            url="https://boards.greenhouse.io/company/jobs/2",
            title="SWE2",
            company="Acme",
        )
        jobs.upsert(job1)
        jobs.upsert(job2)

        app1 = Application(job_id=job1.id, profile_name="p1")
        app2 = Application(job_id=job2.id, profile_name="p1")
        repo = SqlApplicationsRepository(engine)
        repo.add(app1)
        repo.add(app2)

        # app1 → APPLYING
        app1.transition_to(ApplicationState.SCORED)
        app1.transition_to(ApplicationState.TAILORED)
        app1.transition_to(ApplicationState.APPLYING)
        repo.save(app1)

        # app2 → APPLIED
        app2.transition_to(ApplicationState.SCORED)
        app2.transition_to(ApplicationState.TAILORED)
        app2.transition_to(ApplicationState.APPLYING)
        app2.transition_to(ApplicationState.APPLIED)
        repo.save(app2)

        from magicapply.pipelines.apply import ats_key_for_url

        since = datetime.now(UTC) - timedelta(hours=1)
        count = repo.count_applied_in_window(ats_key_for_url, "greenhouse", since)
        assert count == 2, "Should count both APPLYING and APPLIED"
