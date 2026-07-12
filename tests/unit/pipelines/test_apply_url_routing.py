"""Apply routing uses Job.apply_url when enriched (PR1 custom ATS)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.generic import GenericHandler
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.ats.indeed import IndeedHandler
from magicapply.infrastructure.browser.ats.linkedin import LinkedInHandler
from magicapply.infrastructure.browser.ats.workday import WorkdayHandler
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository
from magicapply.pipelines.apply import ApplyPipeline


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url("sqlite:///:memory:")
    create_db(eng)
    yield eng
    eng.dispose()


def test_effective_apply_url_selects_workday_handler() -> None:
    job = Job.new(
        source_name="linkedin-search",
        url="https://www.linkedin.com/jobs/view/4432714211",
        apply_url=(
            "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/STORE-SUPPORT"
        ),
        title="Data Science Manager",
        company="The Home Depot",
    )
    assert isinstance(ATSHandlerFactory.for_url(job.url), LinkedInHandler)
    handler = ATSHandlerFactory.for_url(job.effective_apply_url)
    assert isinstance(handler, WorkdayHandler)


def test_apply_pipeline_routes_via_apply_url_not_listing(
    engine: Engine, tmp_path: Path
) -> None:
    job = Job.new(
        source_name="linkedin-search",
        url="https://www.linkedin.com/jobs/view/999",
        apply_url="https://boards.greenhouse.io/acme/jobs/42",
        title="Senior Data Scientist",
        company="Acme",
    )
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)
    jobs.upsert(job)

    tailored_dir = tmp_path / "tailored" / "app1"
    tailored_dir.mkdir(parents=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump({"base_name": "R", "job_id": job.id, "name": "Test Person"})
    )
    (tailored_dir / "resume.docx").write_bytes(b"PK\x03\x04")

    app = Application(job_id=job.id, profile_name="staff-ds", tailored_path=str(tailored_dir))
    app.transition_to(ApplicationState.SCORED)
    app.transition_to(ApplicationState.TAILORED)
    apps.add(app)

    class _Page:
        url = job.url
        calls: list[tuple[str, ...]] = []

        def goto(self, url: str) -> None:
            self.url = url
            self.calls.append(("goto", url))

        def fill(self, selector: str, value: str) -> None:
            self.calls.append(("fill", selector, value))

        def click(self, selector: str) -> None:
            self.calls.append(("click", selector))

        def content(self) -> str:
            return (
                "<html><body><form>"
                "<input id='first_name' /><input id='last_name' />"
                "<input id='email' /><input type='submit' />"
                "</form></body></html>"
            )

        def set_input_files(self, selector: str, files: str) -> None:
            self.calls.append(("set_input_files", selector, files))

        def select_option(self, selector: str, value: str) -> None:
            self.calls.append(("select_option", selector, value))

        def check(self, selector: str) -> None:
            self.calls.append(("check", selector))

    page = _Page()
    data = ApplicationData(
        job_url=job.effective_apply_url,
        static_answers=StaticAnswers(full_name="Test Person", email="t@example.com"),
        tailored_resume=TailoredResume(base_name="R", job_id=job.id, name="Test Person"),
        resume_docx_path=tailored_dir / "resume.docx",
        dry_run=True,
    )

    pipeline = ApplyPipeline(applications_repo=apps)
    report = pipeline.apply_one(
        page=page,
        application=app,
        job=job,
        application_data=data,
    )

    assert isinstance(ATSHandlerFactory.for_url(job.url), LinkedInHandler)
    assert isinstance(
        ATSHandlerFactory.for_url(job.effective_apply_url), GreenhouseHandler
    )
    assert report.error != "unsupported ATS"
    assert report.final_state is ApplicationState.APPLIED
    # Board job URLs are rewritten to the Greenhouse embed form.
    assert (
        "goto",
        "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=42",
    ) in page.calls


def test_apply_pipeline_attempts_indeed_listing_via_indeed_handler(
    engine: Engine, tmp_path: Path
) -> None:
    """Indeed listing URLs apply via IndeedHandler (not skipped).

    Rate is controlled by apply throttle (bucket indeed) — not by
    refusing board hosts.
    """
    job = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=abc123",
        title="Data Scientist",
        company="Acme",
        # Already resolved once so apply_one does not re-open the listing
        # before the handler (keeps this unit test hermetic).
        raw={"board_resolve": "done", "indeedApplyable": True},
    )
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)
    jobs.upsert(job)

    tailored_dir = tmp_path / "tailored" / "board1"
    tailored_dir.mkdir(parents=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump({"base_name": "R", "job_id": job.id, "name": "Test Person"})
    )
    (tailored_dir / "resume.docx").write_bytes(b"PK\x03\x04")

    app = Application(job_id=job.id, profile_name="staff-ds", tailored_path=str(tailored_dir))
    app.transition_to(ApplicationState.SCORED)
    app.transition_to(ApplicationState.TAILORED)
    apps.add(app)

    class _Page:
        url = job.url
        calls: list[tuple[str, ...]] = []

        def goto(self, url: str) -> None:
            self.url = url
            self.calls.append(("goto", url))

        def fill(self, selector: str, value: str) -> None:
            pass

        def click(self, selector: str) -> None:
            pass

        def content(self) -> str:
            return (
                "<html><body><form>"
                "<input id='first_name' /><input type='submit' />"
                "</form></body></html>"
            )

        def set_input_files(self, selector: str, files: str) -> None:
            pass

        def select_option(self, selector: str, value: str) -> None:
            pass

        def check(self, selector: str) -> None:
            pass

    page = _Page()
    data = ApplicationData(
        job_url=job.effective_apply_url,
        static_answers=StaticAnswers(full_name="Test Person", email="t@example.com"),
        tailored_resume=TailoredResume(base_name="R", job_id=job.id, name="Test Person"),
        resume_docx_path=tailored_dir / "resume.docx",
        dry_run=True,
    )

    pipeline = ApplyPipeline(applications_repo=apps, jobs_repo=jobs)
    report = pipeline.apply_one(
        page=page,
        application=app,
        job=job,
        application_data=data,
    )

    assert isinstance(ATSHandlerFactory.for_url(job.url), IndeedHandler)
    assert ("goto", job.url) in page.calls
    assert report.final_state is not ApplicationState.SKIPPED
    saved = apps.get(app.id)
    assert saved is not None
    assert saved.state is not ApplicationState.SKIPPED


def test_apply_pipeline_resolves_stripe_raw_to_greenhouse(
    engine: Engine, tmp_path: Path
) -> None:
    job = Job.new(
        source_name="greenhouse-boards",
        url="https://stripe.com/jobs/search?gh_jid=8044460",
        title="AI Engineer",
        company="Stripe",
        raw={
            "id": 8044460,
            "absolute_url": "https://stripe.com/jobs/search?gh_jid=8044460",
            "greenhouse_board": "stripe",
        },
    )
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)
    jobs.upsert(job)

    tailored_dir = tmp_path / "tailored" / "stripe1"
    tailored_dir.mkdir(parents=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump({"base_name": "R", "job_id": job.id, "name": "Test Person"})
    )
    (tailored_dir / "resume.docx").write_bytes(b"PK\x03\x04")

    app = Application(job_id=job.id, profile_name="staff-ds", tailored_path=str(tailored_dir))
    app.transition_to(ApplicationState.SCORED)
    app.transition_to(ApplicationState.TAILORED)
    apps.add(app)

    class _Page:
        url = job.url
        calls: list[tuple[str, ...]] = []

        def goto(self, url: str) -> None:
            self.url = url
            self.calls.append(("goto", url))

        def fill(self, selector: str, value: str) -> None:
            self.calls.append(("fill", selector, value))

        def click(self, selector: str) -> None:
            self.calls.append(("click", selector))

        def content(self) -> str:
            return (
                "<html><body><form>"
                "<input id='first_name' /><input id='last_name' />"
                "<input id='email' /><input type='submit' />"
                "</form></body></html>"
            )

        def set_input_files(self, selector: str, files: str) -> None:
            self.calls.append(("set_input_files", selector, files))

        def select_option(self, selector: str, value: str) -> None:
            pass

        def check(self, selector: str) -> None:
            pass

    page = _Page()
    data = ApplicationData(
        job_url=job.effective_apply_url,
        static_answers=StaticAnswers(full_name="Test Person", email="t@example.com"),
        tailored_resume=TailoredResume(base_name="R", job_id=job.id, name="Test Person"),
        resume_docx_path=tailored_dir / "resume.docx",
        dry_run=True,
    )

    pipeline = ApplyPipeline(applications_repo=apps)
    report = pipeline.apply_one(
        page=page,
        application=app,
        job=job,
        application_data=data,
    )

    expected = (
        "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8044460"
    )
    assert isinstance(ATSHandlerFactory.for_url(expected), GreenhouseHandler)
    assert report.final_state is ApplicationState.APPLIED
    assert ("goto", expected) in page.calls