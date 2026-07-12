"""Tests for ApplyPipeline — apply_one is exercised via test_apply_command.py
(CLI level); this file covers apply_batch and the construction guards."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository
from magicapply.pipelines.apply import ApplyPipeline


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url("sqlite:///:memory:")
    create_db(eng)
    yield eng
    eng.dispose()


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.url = "https://example.com"

    def goto(self, url: str) -> None:
        self.url = url
        self.calls.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    def content(self) -> str:
        return "<html>ok</html>"

    def set_input_files(self, selector: str, files: str) -> None:
        self.calls.append(("set_input_files", selector, files))

    def select_option(self, selector: str, value: str) -> None:
        self.calls.append(("select_option", selector, value))

    def check(self, selector: str) -> None:
        self.calls.append(("check", selector))


class _FakeSession:
    """new_page() returns a fresh FakePage each time, tracked on .pages."""

    def __init__(self) -> None:
        self.pages: list[_FakePage] = []

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page


def _static_answers() -> StaticAnswers:
    return StaticAnswers(full_name="Test Person", email="t@example.com", phone="555-0100")


def _data_builder(app: Application, job: Job, *, dry_run: bool) -> ApplicationData:
    return ApplicationData(
        job_url=job.url,
        static_answers=_static_answers(),
        tailored_resume=TailoredResume(base_name="R", job_id=job.id, name="Test Person"),
        resume_docx_path=Path(app.tailored_path or "") / "resume.docx",
        cover_letter="Cover.",
        dry_run=dry_run,
    )


def _seed_tailored(
    engine: Engine,
    tmp_path: Path,
    *,
    profile: str = "swe",
    url: str,
) -> tuple[Job, Application]:
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)
    job = Job.new(source_name="s", url=url, title="T", company="Acme")
    jobs.upsert(job)

    tailored_dir = tmp_path / "tailored" / "placeholder"
    tailored_dir.mkdir(parents=True, exist_ok=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump({"base_name": "R", "job_id": job.id, "name": "Test Person"})
    )
    (tailored_dir / "cover_letter.md").write_text("Cover.")

    a = Application(job_id=job.id, profile_name=profile, tailored_path=str(tailored_dir))
    a.transition_to(ApplicationState.SCORED)
    a.transition_to(ApplicationState.TAILORED)
    apps.add(a)
    return job, a


class TestConstructionGuards:
    def test_apply_batch_without_deps_raises(self, engine: Engine) -> None:
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(applications_repo=apps)
        session = _FakeSession()
        with pytest.raises(RuntimeError, match="apply_batch requires"):
            pipeline.apply_batch(session=session, profile_name="swe", dry_run=True)


class TestRetry:
    def test_retry_on_failed_transitions_to_applied(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _, app = _seed_tailored(
            engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1"
        )
        apps = SqlApplicationsRepository(engine)
        # Move the app through TAILORED -> APPLYING -> FAILED (simulate a
        # first attempt that hit an ATS handler exception).
        app.transition_to(ApplicationState.APPLYING)
        app.transition_to(ApplicationState.FAILED, reason="first attempt failed")
        apps.save(app)

        pipeline = ApplyPipeline(applications_repo=apps)
        page = _FakePage()
        report = pipeline.apply_one(
            page=page,
            application=app,
            job=Job.new(
                source_name="s",
                url="https://boards.greenhouse.io/acme/jobs/1",
                title="X",
                company="Acme",
            ),
            application_data=_data_builder(app, _job_stub(app), dry_run=False),
            retry=True,
        )
        assert report.final_state is ApplicationState.APPLIED

    def test_retry_false_still_rejects_failed_state(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _, app = _seed_tailored(
            engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1"
        )
        apps = SqlApplicationsRepository(engine)
        app.transition_to(ApplicationState.APPLYING)
        app.transition_to(ApplicationState.FAILED)
        apps.save(app)

        pipeline = ApplyPipeline(applications_repo=apps)
        with pytest.raises(ValueError, match="tailored"):
            pipeline.apply_one(
                page=_FakePage(),
                application=app,
                job=_job_stub(app),
                application_data=_data_builder(app, _job_stub(app), dry_run=False),
                retry=False,
            )


def _job_stub(app: Application) -> Job:
    return Job.new(
        source_name="s",
        url="https://boards.greenhouse.io/acme/jobs/1",
        title="X",
        company="Acme",
    )


class TestApplyBatchHappyPath:
    def test_empty_queue_returns_empty_reports(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        reports = pipeline.apply_batch(
            session=_FakeSession(), profile_name="swe", dry_run=True
        )
        assert reports == []

    def test_multiple_tailored_apps_each_get_a_page(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        # Two TAILORED apps under the same profile, distinct Greenhouse URLs.
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1")
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/2")

        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        session = _FakeSession()

        reports = pipeline.apply_batch(
            session=session, profile_name="swe", dry_run=True
        )

        assert len(reports) == 2
        assert len(session.pages) == 2  # one new_page per application
        # Both landed in APPLIED (dry-run terminal state).
        assert all(r.final_state is ApplicationState.APPLIED for r in reports)
        # Neither page saw a real submit click (dry_run=True short-circuits).
        for page in session.pages:
            assert ("click", "input[type='submit']") not in page.calls

    def test_dry_run_flag_reaches_the_row(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1")
        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        pipeline.apply_batch(session=_FakeSession(), profile_name="swe", dry_run=True)

        applied = apps.list_by_state(ApplicationState.APPLIED)
        assert len(applied) == 1
        assert applied[0].dry_run is True

    def test_yes_submit_actually_clicks(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1")
        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        session = _FakeSession()

        pipeline.apply_batch(session=session, profile_name="swe", dry_run=False)

        # Real submit → click happened.
        assert ("click", "input[type='submit']") in session.pages[0].calls
        applied = apps.list_by_state(ApplicationState.APPLIED)
        assert applied[0].dry_run is False


class TestApplyBatchProfileIsolation:
    def test_other_profile_untouched(self, engine: Engine, tmp_path: Path) -> None:
        _seed_tailored(
            engine, tmp_path, profile="swe",
            url="https://boards.greenhouse.io/acme/jobs/1",
        )
        _, other_app = _seed_tailored(
            engine, tmp_path, profile="pm",
            url="https://boards.greenhouse.io/acme/jobs/2",
        )

        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        pipeline.apply_batch(session=_FakeSession(), profile_name="swe", dry_run=True)

        # The pm app is still TAILORED, untouched.
        other_reloaded = apps.get(other_app.id)
        assert other_reloaded is not None
        assert other_reloaded.state is ApplicationState.TAILORED


class TestApplyBatchMissingJob:
    def test_missing_job_is_skipped(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        from sqlmodel import Session, delete

        from magicapply.infrastructure.persistence.tables import JobRow

        _, good_app = _seed_tailored(
            engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1"
        )
        _, ghost = _seed_tailored(
            engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/gone"
        )
        # Delete the second job to simulate corruption.
        with Session(engine) as session:
            session.exec(delete(JobRow).where(JobRow.id == ghost.job_id))
            session.commit()

        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )

        reports = pipeline.apply_batch(
            session=_FakeSession(), profile_name="swe", dry_run=True
        )

        assert len(reports) == 1
        assert reports[0].application_id == good_app.id


class TestApplyBatchPaced:
    def test_max_outcomes_stops_after_n(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1")
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/2")
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/3")
        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        sleeps: list[float] = []
        reports = pipeline.apply_batch(
            session=_FakeSession(),
            profile_name="swe",
            dry_run=True,
            max_outcomes=2,
            pace_seconds=1.0,
            sleep=sleeps.append,
        )
        assert len(reports) == 2
        assert all(r.final_state is ApplicationState.APPLIED for r in reports)
        assert sleeps == [1.0]  # pace after first outcome; second hits max

    def test_throttle_deny_skips_and_tries_next(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """Quota deny must not abort the wave — try next-best allowed dest."""
        from magicapply.domain.apply.throttle import ApplyThrottle, ThrottleDecision
        from magicapply.config.models import ApplyThrottleConfig, ThrottleCaps

        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/1")
        _seed_tailored(engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/2")
        jobs = SqlJobsRepository(engine)
        apps = SqlApplicationsRepository(engine)

        class _AlwaysDeny(ApplyThrottle):
            def check(self, *, ats: str) -> ThrottleDecision:  # type: ignore[override]
                return ThrottleDecision.deny("test deny")

        throttle = _AlwaysDeny(
            config=ApplyThrottleConfig(
                ats_default=ThrottleCaps(hourly=4, daily=55),
                global_cap=ThrottleCaps(hourly=4, daily=55),
            ),
            ats_hourly_count=lambda *a: 0,
            ats_daily_count=lambda *a: 0,
            global_hourly_count=lambda *a: 0,
            global_daily_count=lambda *a: 0,
        )
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
            throttle=throttle,
        )
        sleeps: list[float] = []
        reports = pipeline.apply_batch(
            session=_FakeSession(),
            profile_name="swe",
            dry_run=True,
            max_outcomes=5,
            pace_seconds=30.0,
            sleep=sleeps.append,
        )
        # All denied → reports are throttle skips; still TAILORED; sleep then re-list
        assert reports
        assert all(
            r.error and r.error.startswith("throttle:") for r in reports
        )
        assert sleeps  # slept when nothing allowed this pass
        assert apps.list_by_state(ApplicationState.APPLIED) == []

    def test_include_retry_states_applies_failed(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        _, app = _seed_tailored(
            engine, tmp_path, url="https://boards.greenhouse.io/acme/jobs/99"
        )
        apps = SqlApplicationsRepository(engine)
        app.transition_to(ApplicationState.APPLYING)
        app.transition_to(ApplicationState.FAILED, reason="boom")
        apps.save(app)
        jobs = SqlJobsRepository(engine)
        pipeline = ApplyPipeline(
            applications_repo=apps,
            jobs_repo=jobs,
            data_builder=_data_builder,
        )
        reports = pipeline.apply_batch(
            session=_FakeSession(),
            profile_name="swe",
            dry_run=True,
            include_retry_states=True,
            max_outcomes=1,
        )
        assert len(reports) == 1
        assert reports[0].final_state is ApplicationState.APPLIED


class TestScoreFirstOrder:
    """Candidates ordered by score; TAILORED before retries."""

    def test_higher_score_first(self) -> None:
        from magicapply.pipelines.apply import _order_candidates_by_score

        low = Application(job_id="a", profile_name="swe", score=40)
        low.transition_to(ApplicationState.SCORED)
        low.transition_to(ApplicationState.TAILORED)
        high = Application(job_id="b", profile_name="swe", score=95)
        high.transition_to(ApplicationState.SCORED)
        high.transition_to(ApplicationState.TAILORED)
        mid = Application(job_id="c", profile_name="swe", score=70)
        mid.transition_to(ApplicationState.SCORED)
        mid.transition_to(ApplicationState.TAILORED)
        ordered = _order_candidates_by_score([low, high, mid])
        assert [a.job_id for a in ordered] == ["b", "c", "a"]

    def test_tailored_before_failed_at_same_score(self) -> None:
        from magicapply.pipelines.apply import _order_candidates_by_score

        failed = Application(job_id="f", profile_name="swe", score=99)
        failed.transition_to(ApplicationState.SCORED)
        failed.transition_to(ApplicationState.TAILORED)
        failed.transition_to(ApplicationState.APPLYING)
        failed.transition_to(ApplicationState.FAILED, reason="x")
        ready = Application(job_id="t", profile_name="swe", score=50)
        ready.transition_to(ApplicationState.SCORED)
        ready.transition_to(ApplicationState.TAILORED)
        ordered = _order_candidates_by_score([failed, ready])
        assert [a.job_id for a in ordered] == ["t", "f"]


class TestScoreFirstThrottleSkip:
    def test_after_bucket_cap_applies_next_best_other_ats(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """Score 90 Lever, 80 GH, 70 Lever: after Lever cap 1, apply GH not 2nd Lever."""
        from magicapply.domain.apply.throttle import ApplyThrottle, ThrottleDecision
        from magicapply.config.models import ApplyThrottleConfig, ThrottleCaps

        jobs_repo = SqlJobsRepository(engine)
        apps_repo = SqlApplicationsRepository(engine)
        profile = "score-throttle"
        lever_applied = {"n": 0}

        def _seed(url: str, *, score: int, source: str) -> Application:
            job = Job.new(source_name=source, url=url, title="T", company="C")
            jobs_repo.upsert(job)
            d = tmp_path / "t" / job.id
            d.mkdir(parents=True, exist_ok=True)
            (d / "resume.yaml").write_text(
                yaml.safe_dump(
                    {"base_name": "R", "job_id": job.id, "name": "Test Person"}
                )
            )
            (d / "resume.docx").write_bytes(b"PK\x03\x04")
            a = Application(
                job_id=job.id,
                profile_name=profile,
                score=score,
                tailored_path=str(d),
            )
            a.transition_to(ApplicationState.SCORED)
            a.transition_to(ApplicationState.TAILORED)
            apps_repo.add(a)
            return a

        lev_hi = _seed(
            "https://jobs.lever.co/acme/high",
            score=90,
            source="linkedin-search",
        )
        gh_mid = _seed(
            "https://boards.greenhouse.io/acme/jobs/mid",
            score=80,
            source="greenhouse-boards",
        )
        lev_lo = _seed(
            "https://jobs.lever.co/acme/low",
            score=70,
            source="linkedin-search",
        )

        class _LeverCapOne(ApplyThrottle):
            def check(self, *, ats: str) -> ThrottleDecision:  # type: ignore[override]
                if ats == "lever" and lever_applied["n"] >= 1:
                    return ThrottleDecision.deny("lever hourly cap")
                return ThrottleDecision.ok()

        throttle = _LeverCapOne(
            config=ApplyThrottleConfig(
                ats_default=ThrottleCaps(hourly=4, daily=55),
                global_cap=ThrottleCaps(hourly=20, daily=100),
            ),
            ats_hourly_count=lambda *a: 0,
            ats_daily_count=lambda *a: 0,
            global_hourly_count=lambda *a: 0,
            global_daily_count=lambda *a: 0,
        )

        pipeline = ApplyPipeline(
            applications_repo=apps_repo,
            jobs_repo=jobs_repo,
            data_builder=_data_builder,
            throttle=throttle,
        )
        _orig = pipeline.apply_one

        def _wrap(**kwargs):  # type: ignore[no-untyped-def]
            report = _orig(**kwargs)
            if (
                report.final_state is ApplicationState.APPLIED
                and kwargs["job"].url
                and "lever.co" in kwargs["job"].url
            ):
                lever_applied["n"] += 1
            return report

        pipeline.apply_one = _wrap  # type: ignore[method-assign]

        reports = pipeline.apply_batch(
            session=_FakeSession(),
            profile_name=profile,
            dry_run=True,
            max_outcomes=2,
            pace_seconds=1.0,
            sleep=lambda _s: None,
        )
        applied_ids = [
            r.application_id
            for r in reports
            if r.final_state is ApplicationState.APPLIED
        ]
        assert applied_ids == [lev_hi.id, gh_mid.id]
        assert lev_lo.id not in applied_ids

    def test_ats_key_buckets_board_hosts(self) -> None:
        from magicapply.pipelines.apply import ats_key_for_url

        assert ats_key_for_url("https://www.indeed.com/viewjob?jk=1") == "indeed"
        assert ats_key_for_url("https://www.linkedin.com/jobs/view/1") == "linkedin"
        assert (
            ats_key_for_url(
                "https://job-boards.greenhouse.io/embed/job_app?for=x&token=1"
            )
            == "greenhouse"
        )
