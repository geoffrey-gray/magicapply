"""Unit tests for FormComposer and RulesBasedDriver."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.router_dispatch import apply_router_to_form
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_rules_registry


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "canned narrative"


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


def _build_data(
    static: StaticAnswers,
    *,
    html_router: bool = True,
) -> tuple[_FakePage, ApplicationData]:
    html = """
    <div data-automation-id="applyFlowPage">
      <label for="fn">First name</label>
      <input id="fn" name="first_name" type="text">
      <fieldset>
        <legend>Have you previously worked for Acme?</legend>
        <input id="yes" name="prev" type="radio" value="true">
        <input id="no" name="prev" type="radio" value="false">
      </fieldset>
      <input id="agree" name="agree" type="checkbox">
      <label for="agree">I consent and confirm</label>
      <input id="resume" name="resume" type="file">
    </div>
    """
    page = _FakePage(f"<html><body>{html}</body></html>")
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
        answer_router=router if html_router else None,
        job=job,
    )
    registry = build_rules_registry(router)
    data = data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )
    return page, data


def test_radio_select_clicks_matching_value() -> None:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        previously_employed=False,
    )
    page, data = _build_data(static)
    composer = data.form_composer
    assert isinstance(composer, FormComposer)
    report = composer.fill_scanned(
        page,
        "[data-automation-id='applyFlowPage']",
        ats="workday",
    )
    assert ("click", "input[type='radio'][name='prev'][value='false']") in page.actions
    assert report.unhandled == []


def test_delegation_via_apply_router_to_form() -> None:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        previously_employed=False,
    )
    page, data = _build_data(static)
    apply_router_to_form(
        page,
        data,
        handler_name="Workday",
        form_selector="[data-automation-id='applyFlowPage']",
    )
    assert ("click", "input[type='radio'][name='prev'][value='false']") in page.actions


def test_scanned_fields_have_variant() -> None:
    static = StaticAnswers(full_name="Jane Doe", email="jane@example.com")
    page, data = _build_data(static)
    composer = data.form_composer
    assert isinstance(composer, FormComposer)
    composer.fill_scanned(
        page,
        "[data-automation-id='applyFlowPage']",
        ats="greenhouse",
    )
    assert data.resolutions_log
    first = data.resolutions_log[0].field
    assert first.variant == "text"


def test_file_strategy_recorded_without_fill() -> None:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        previously_employed=False,
    )
    page, data = _build_data(static)
    composer = data.form_composer
    assert isinstance(composer, FormComposer)
    report = composer.fill_scanned(
        page,
        "[data-automation-id='applyFlowPage']",
        ats="greenhouse",
    )
    file_fills = [a for a in page.actions if a[0] == "fill" and "resume" in a[1]]
    assert file_fills == []
    assert "#resume" in [r.field.selector for r in data.resolutions_log]
    assert report.unhandled == []