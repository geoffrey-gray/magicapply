"""Unit tests for resume-backed composable field resolution."""

from __future__ import annotations

from pathlib import Path

from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import EducationEntry, ExperienceEntry, TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.forms.fields import FormField
from magicapply.infrastructure.browser.forms.resume_resolve import resolve_from_resume
from magicapply.config.models import StaticAnswers


def _data() -> ApplicationData:
    static = StaticAnswers(full_name="Jane Doe", email="jane@example.com")
    job = Job.new(source_name="s", url="https://acme.wd1.myworkdayjobs.com/j", title="Eng", company="Acme")
    resume = TailoredResume(
        base_name="geoffrey",
        job_id="abc",
        name="Jane Doe",
        education=[
            EducationEntry(
                school="University of South Florida",
                degree="PhD",
                field="Computational Chemistry",
                graduated="2018",
            )
        ],
        experience=[
            ExperienceEntry(
                company="Intrinsic",
                title="Staff DS",
                start="2021-01",
                end="2025-01",
                bullets=[],
            )
        ],
    )
    return ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=resume,
        resume_docx_path=Path("/tmp/resume.docx"),
        job=job,
    )


def test_school_field_resolves_from_resume() -> None:
    field = FormField(
        selector="#education-4--school",
        label="School or University*",
        kind="text",
    )
    resolved = resolve_from_resume(field, _data())
    assert resolved == ResolvedAnswer("static", "University of South Florida")


def test_currently_work_here_unchecked_when_not_current() -> None:
    field = FormField(
        selector="#workExperience-5--currentlyWorkHere",
        label="I currently work here",
        kind="checkbox",
    )
    resolved = resolve_from_resume(field, _data())
    assert resolved == ResolvedAnswer("check", check=False)


def test_field_of_study_resolves_from_resume() -> None:
    field = FormField(
        selector="#education-4--fieldOfStudy",
        label="Field of Study",
        kind="text",
    )
    resolved = resolve_from_resume(field, _data())
    assert resolved == ResolvedAnswer("static", "Computational Chemistry")


def test_education_year_resolves_from_graduated() -> None:
    field = FormField(
        selector="#education-4--lastYearAttended-dateSectionYear-input",
        label="Year",
        kind="text",
        variant="workday_date_spin",
    )
    resolved = resolve_from_resume(field, _data())
    assert resolved == ResolvedAnswer("static", "2018")