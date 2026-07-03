"""Unit tests for AnswerRouter."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.answer_router import (
    AnswerRouter,
    ResolvedAnswer,
)
from magicapply.infrastructure.browser.ats.form_scan import FormField


class _RecordingNarrative:
    """NarrativeEngine stand-in that records every answer(job, question) call."""

    def __init__(self, reply: str = "canned narrative answer") -> None:
        self._reply = reply
        self.calls: list[tuple[Job, str]] = []

    def answer(self, job: Job, question: str) -> str:
        self.calls.append((job, question))
        return self._reply

    # NarrativeEngine also exposes cover_letter but the router never calls it.


def _router(
    static: StaticAnswers | None = None,
    narrative: _RecordingNarrative | None = None,
    resume_docx_path: Path = Path("/tmp/resume.docx"),
) -> AnswerRouter:
    return AnswerRouter(
        static_answers=static or _default_static(),
        narrative=narrative or _RecordingNarrative(),
        resume_docx_path=resume_docx_path,
    )


def _default_static() -> StaticAnswers:
    return StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-0100",
        linkedin_url="https://linkedin.com/in/jane",
        location="Boston, MA",
        authorized_to_work_us=True,
        needs_sponsorship_us=False,
        hispanic_latino=False,
        years_of_experience=8,
        desired_salary="$180k-$210k",
    )


def _job() -> Job:
    return Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")


class TestFile:
    def test_file_field_routes_to_resume_path(self) -> None:
        r = _router(resume_docx_path=Path("/tmp/my-resume.docx"))
        field = FormField(selector="input", label="Resume", kind="file")
        assert r.resolve(field, _job()) == ResolvedAnswer("file", "/tmp/my-resume.docx")


class TestIdentityText:
    def test_first_name(self) -> None:
        field = FormField(selector="#first_name", label="First Name", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "Jane")

    def test_last_name(self) -> None:
        field = FormField(selector="#last_name", label="Last Name", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "Doe")

    def test_email(self) -> None:
        field = FormField(selector="#email", label="Email Address", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "jane@example.com")

    def test_phone(self) -> None:
        field = FormField(selector="#phone", label="Phone Number", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "555-0100")

    def test_linkedin(self) -> None:
        field = FormField(
            selector="input[name='linkedin_url']", label="LinkedIn URL", kind="text"
        )
        assert _router().resolve(field, _job()).value.startswith("https://linkedin.com")

    def test_years_of_experience(self) -> None:
        field = FormField(selector="#yoe", label="Years of Experience", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "8")


class TestYesNoSelect:
    def test_authorized_yes(self) -> None:
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["", "Yes", "No"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "Yes")

    def test_needs_sponsorship_no(self) -> None:
        field = FormField(
            selector="#sp",
            label="Will you require sponsorship?",
            kind="select",
            options=["Yes", "No"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "No")

    def test_hispanic_no(self) -> None:
        field = FormField(
            selector="#h",
            label="Are you Hispanic or Latino?",
            kind="select",
            options=["Yes", "No", "Decline to state"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "No")

    def test_unset_bool_is_unhandled(self) -> None:
        answers = _default_static().model_copy(update={"authorized_to_work_us": None})
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["Yes", "No"],
        )
        assert _router(static=answers).resolve(field, _job()).strategy == "unhandled"

    def test_no_matching_option_is_unhandled(self) -> None:
        # Bank has bool but options are exotic — router bails rather than guess.
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["Citizen", "Green card", "H1B"],
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"


class TestNarrative:
    def test_textarea_dispatched_to_narrative(self) -> None:
        narrative = _RecordingNarrative(reply="Because I love it")
        field = FormField(
            selector="textarea[name='why']", label="Why Acme?", kind="textarea"
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result == ResolvedAnswer("narrative", "Because I love it")
        assert narrative.calls[0][1] == "Why Acme?"  # question preserved

    def test_question_mark_text_input_is_narrative(self) -> None:
        # A text input that reads like a question falls through to narrative.
        narrative = _RecordingNarrative(reply="Ex-alumnus referral.")
        field = FormField(
            selector="#howheard", label="How did you hear about us?", kind="text"
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result.strategy == "narrative"


class TestUnhandled:
    def test_opaque_text_input_is_unhandled(self) -> None:
        field = FormField(
            selector="input[name='favourite_colour']",
            label="Favourite colour",
            kind="text",
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_checkbox_is_unhandled(self) -> None:
        field = FormField(
            selector="input[name='newsletter']", label="Newsletter", kind="checkbox"
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"
