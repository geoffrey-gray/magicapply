"""Unit tests for the AshbyHandler template flow."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.ashby import AshbyHandler
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_driver_registry


class _RecordingPage:
    def __init__(self, html: str = "<html><body>ok</body></html>") -> None:
        self.url = "https://example.com/original"
        self._html = html
        self.actions: list[tuple[str, tuple[str, ...]]] = []

    def goto(self, url: str, **_: object) -> None:
        self.url = url
        self.actions.append(("goto", (url,)))

    def fill(self, selector: str, value: str, **_: object) -> None:
        self.actions.append(("fill", (selector, value)))

    def click(self, selector: str, **_: object) -> None:
        self.actions.append(("click", (selector,)))

    def content(self) -> str:
        return self._html

    def set_input_files(self, selector: str, files: str, **_: object) -> None:
        self.actions.append(("set_input_files", (selector, files)))

    def select_option(self, selector: str, value: str, **_: object) -> None:
        self.actions.append(("select_option", (selector, value)))

    def check(self, selector: str, **_: object) -> None:
        self.actions.append(("check", (selector,)))


def _data(url: str = "https://jobs.ashbyhq.com/acme/xyz") -> ApplicationData:
    return ApplicationData(
        job_url=url,
        static_answers=StaticAnswers(
            full_name="Jane Doe",
            email="j@example.com",
            phone="555-0100",
            linkedin_url="https://linkedin.com/in/jane",
            location="Boston, MA",
        ),
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        cover_letter="Cover.",
    )


class TestMatches:
    def test_matches_ashbyhq(self) -> None:
        assert AshbyHandler.matches("https://jobs.ashbyhq.com/acme/xyz")

    def test_matches_apply_path(self) -> None:
        assert AshbyHandler.matches("https://jobs.ashbyhq.com/acme/xyz/application")

    def test_does_not_match_lever(self) -> None:
        assert not AshbyHandler.matches("https://jobs.lever.co/acme/abc")


class TestFactoryDispatch:
    def test_ashby_url_selects_ashby(self) -> None:
        h = ATSHandlerFactory.for_url("https://jobs.ashbyhq.com/acme/xyz")
        assert isinstance(h, AshbyHandler)


class TestFlow:
    def test_happy_path_fills_system_fields_and_submits(self) -> None:
        page = _RecordingPage()
        result = AshbyHandler().apply(page, _data())
        assert result.state == "applied"

        fills = {(a[1][0], a[1][1]) for a in page.actions if a[0] == "fill"}
        assert ("input[name='_systemfield_name']", "Jane Doe") in fills
        assert ("input[name='_systemfield_email']", "j@example.com") in fills
        assert ("input[name='_systemfield_phone']", "555-0100") in fills
        assert (
            "input[name='_systemfield_linkedin']",
            "https://linkedin.com/in/jane",
        ) in fills
        assert ("input[name='_systemfield_location']", "Boston, MA") in fills
        assert any(
            a[0] == "set_input_files" and a[1][0] == "input[name='_systemfield_resume']"
            for a in page.actions
        )
        assert ("click", ("button[type='submit']",)) in page.actions

    def test_dry_run_short_circuits_submit(self) -> None:
        page = _RecordingPage()
        data = _data().model_copy(update={"dry_run": True})
        result = AshbyHandler().apply(page, data)
        assert result.state == "applied"
        assert "dry-run" in (result.error or "")
        assert not any(
            a[0] == "click" and a[1][0] == "button[type='submit']"
            for a in page.actions
        )


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "Because I like the mission."


_ASHBY_FORM_HTML = """
<form>
  <label>Full name <input name="_systemfield_name" type="text"></label>
  <label>Are you authorized to work in the US?
    <select name="authorized" id="authorized">
      <option value="">--</option>
      <option value="Yes">Yes</option>
      <option value="No">No</option>
    </select>
  </label>
  <label>Why Ashby?
    <textarea id="why" name="why"></textarea>
  </label>
  <input type="file" name="_systemfield_resume">
  <button type="submit">Submit application</button>
</form>
"""


def _composable_data() -> tuple[_RecordingPage, ApplicationData]:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="j@example.com",
        authorized_to_work_us=True,
    )
    narrative = _RecordingNarrative()
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(
        source_name="ashby",
        url="https://jobs.ashbyhq.com/acme/xyz/application",
        title="Senior Engineer",
        company="Acme",
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
    page = _RecordingPage(f"<html><body>{_ASHBY_FORM_HTML}</body></html>")
    return page, data


class TestComposablePath:
    def test_fill_scanned_fills_custom_fields(self) -> None:
        page, data = _composable_data()
        AshbyHandler()._fill_dynamic(page, data)
        assert any(a[0] == "select_option" for a in page.actions)
        assert any(a[0] == "fill" and "why" in a[1][0] for a in page.actions)
        assert data.resolutions_log

    def test_composable_apply_happy_path(self) -> None:
        page, data = _composable_data()
        result = AshbyHandler().apply(page, data)
        assert result.state == "applied"
        assert ("click", ("button[type='submit']",)) in page.actions

    def test_router_dispatch_alias_still_fills(self) -> None:
        from magicapply.infrastructure.browser.ats.router_dispatch import apply_router_to_form

        page, data = _composable_data()
        apply_router_to_form(page, data, handler_name="Ashby", form_selector="form")
        assert data.resolutions_log
