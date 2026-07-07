"""Unit tests for GreenhouseHandler composable form path (CF.2)."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_driver_registry


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "Because I like the mission."


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html
        self.actions: list[tuple[str, ...]] = []

    def content(self) -> str:
        return self._html

    def goto(self, url: str) -> None:
        self.actions.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector))

    def set_input_files(self, selector: str, files: str) -> None:
        self.actions.append(("set_input_files", selector, files))

    def select_option(self, selector: str, value: str) -> None:
        self.actions.append(("select_option", selector, value))

    def check(self, selector: str) -> None:
        self.actions.append(("check", selector))


_FORM_HTML = """
<form id="application-form">
  <label for="fn">First name</label>
  <input id="fn" name="first_name" type="text">
  <label>Are you authorized to work in the US?
    <select name="authorized" id="authorized">
      <option value="">--</option>
      <option value="Yes">Yes</option>
      <option value="No">No</option>
    </select>
  </label>
  <label>Why do you want to work here?
    <textarea id="why" name="why"></textarea>
  </label>
  <input type="file" name="resume">
  <input type="submit" value="Apply">
</form>
"""


def _composable_data() -> tuple[_FakePage, ApplicationData]:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        authorized_to_work_us=True,
    )
    narrative = _RecordingNarrative()
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(
        source_name="gh",
        url="https://job-boards.greenhouse.io/reddit/jobs/7772274",
        title="ML Engineer",
        company="Reddit",
    )
    data = ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        answer_router=router,
        job=job,
    )
    registry = build_driver_registry(router, narrative)
    data = data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )
    page = _FakePage(f"<html><body>{_FORM_HTML}</body></html>")
    return page, data


def test_composable_fill_scanned_fills_custom_fields() -> None:
    page, data = _composable_data()
    handler = GreenhouseHandler()
    handler._fill_dynamic(page, data)
    assert any(a[0] == "select_option" for a in page.actions)
    assert any(a[0] == "fill" and "why" in str(a) for a in page.actions)
    assert data.resolutions_log


def test_composable_apply_happy_path() -> None:
    page, data = _composable_data()
    result = GreenhouseHandler().apply(page, data)
    assert result.state == "applied"
    assert ("click", "input[type='submit']") in page.actions


def test_router_dispatch_alias_still_fills() -> None:
    from magicapply.infrastructure.browser.ats.router_dispatch import apply_router_to_form

    page, data = _composable_data()
    apply_router_to_form(page, data, handler_name="Greenhouse", form_selector="form")
    assert data.resolutions_log


def test_form_selector_prefers_application_form_id() -> None:
    html = """
    <form id="noise"><input name="noise" type="text"></form>
    <form id="application-form">
      <label>Why?<textarea name="why" id="why"></textarea></label>
    </form>
    """
    page, data = _composable_data()
    page = _FakePage(f"<html><body>{html}</body></html>")
    handler = GreenhouseHandler()
    handler._fill_dynamic(page, data)
    assert data.resolutions_log
    assert data.resolutions_log[0].field.selector == "#why"