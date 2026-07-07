"""Unit tests for apply_router_to_form dispatch."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_rules_registry
from magicapply.infrastructure.browser.ats.router_dispatch import (
    apply_router_to_form,
    fill_composable_scanned,
)


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "canned"


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html
        self.actions: list[tuple[str, str]] = []

    def content(self) -> str:
        return self._html

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector))

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector))

    def select_option(self, selector: str, value: str) -> None:
        self.actions.append(("select_option", selector))

    def check(self, selector: str) -> None:
        self.actions.append(("check", selector))


def test_radio_select_clicks_matching_value() -> None:
    html = """
    <div data-automation-id="applyFlowPage">
      <fieldset>
        <legend>Have you previously worked for Acme?</legend>
        <input id="yes" name="prev" type="radio" value="true">
        <input id="no" name="prev" type="radio" value="false">
      </fieldset>
    </div>
    """
    page = _FakePage(f"<html><body>{html}</body></html>")
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        previously_employed=False,
    )
    router = AnswerRouter(
        static_answers=static,
        narrative=_RecordingNarrative(),
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")
    data = ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        answer_router=router,
        job=job,
    )
    registry = build_rules_registry(router)
    data = data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )
    apply_router_to_form(
        page,
        data,
        handler_name="Workday",
        form_selector="[data-automation-id='applyFlowPage']",
    )
    assert ("click", "input[type='radio'][name='prev'][value='false']") in page.actions


def test_fill_composable_scanned_delegates_to_composer() -> None:
    html = """
    <form class="posting-form">
      <fieldset>
        <legend>Have you previously worked for Acme?</legend>
        <input id="yes" name="prev" type="radio" value="true">
        <input id="no" name="prev" type="radio" value="false">
      </fieldset>
    </form>
    """
    page = _FakePage(f"<html><body>{html}</body></html>")
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        previously_employed=False,
    )
    router = AnswerRouter(
        static_answers=static,
        narrative=_RecordingNarrative(),
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")
    data = ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        answer_router=router,
        job=job,
    )
    registry = build_rules_registry(router)
    data = data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )
    fill_composable_scanned(
        page,
        data,
        ats="lever",
        form_selectors=("form.posting-form", "form"),
        schema_id="lever_application",
        handler_label="Lever",
    )
    assert ("click", "input[type='radio'][name='prev'][value='false']") in page.actions