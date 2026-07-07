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
    assert isinstance(ATSHandlerFactory.for_url(job.url), GenericHandler)
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

    assert isinstance(ATSHandlerFactory.for_url(job.url), GenericHandler)
    assert isinstance(
        ATSHandlerFactory.for_url(job.effective_apply_url), GreenhouseHandler
    )
    assert report.error != "unsupported ATS"
    assert report.final_state is ApplicationState.APPLIED
    assert ("goto", job.apply_url) in page.calls