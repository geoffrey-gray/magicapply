"""Unit tests for the LeverHandler template flow."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.lever import LeverHandler
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


def _data(url: str = "https://jobs.lever.co/acme/abc-def") -> ApplicationData:
    return ApplicationData(
        job_url=url,
        static_answers=StaticAnswers(
            full_name="Jane Doe",
            email="j@example.com",
            phone="555-0100",
            linkedin_url="https://linkedin.com/in/jane",
        ),
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        cover_letter="Cover.",
    )


class TestMatches:
    def test_matches_lever_host(self) -> None:
        assert LeverHandler.matches("https://jobs.lever.co/acme/abc-def")

    def test_matches_lever_apply_path(self) -> None:
        assert LeverHandler.matches("https://jobs.lever.co/acme/abc-def/apply")

    def test_does_not_match_greenhouse(self) -> None:
        assert not LeverHandler.matches("https://boards.greenhouse.io/acme/jobs/1")


class TestFactoryDispatch:
    def test_lever_url_selects_lever(self) -> None:
        h = ATSHandlerFactory.for_url("https://jobs.lever.co/acme/abc")
        assert isinstance(h, LeverHandler)


class TestFlow:
    def test_happy_path_fills_identity_and_submits(self) -> None:
        page = _RecordingPage()
        result = LeverHandler().apply(page, _data())
        assert result.state == "applied"
        fills = {(a[1][0], a[1][1]) for a in page.actions if a[0] == "fill"}
        # Lever asks full name in one input, not split.
        assert ("input[name='name']", "Jane Doe") in fills
        assert ("input[name='email']", "j@example.com") in fills
        assert ("input[name='phone']", "555-0100") in fills
        assert ("input[name='urls[LinkedIn]']", "https://linkedin.com/in/jane") in fills
        assert ("textarea[name='comments']", "Cover.") in fills
        # Resume upload via the first candidate selector.
        assert any(
            a[0] == "set_input_files" and a[1][0] == "input[name='resume']"
            for a in page.actions
        )
        assert ("click", ("button[type='submit']",)) in page.actions

    def test_dry_run_short_circuits_submit(self) -> None:
        page = _RecordingPage()
        data = _data().model_copy(update={"dry_run": True})
        result = LeverHandler().apply(page, data)
        assert result.state == "applied"
        assert "dry-run" in (result.error or "")
        assert not any(
            a[0] == "click" and a[1][0] == "button[type='submit']"
            for a in page.actions
        )


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "Because I like the mission."


_LEVER_FORM_HTML = """
<form class="posting-form">
  <label>Full name <input name="name" type="text"></label>
  <label>Are you authorized to work in the US?
    <select name="authorized" id="authorized">
      <option value="">--</option>
      <option value="Yes">Yes</option>
      <option value="No">No</option>
    </select>
  </label>
  <label>Why Lever?
    <textarea id="why" name="why"></textarea>
  </label>
  <input type="file" name="resume">
  <button type="submit">Apply</button>
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
        source_name="lever",
        url="https://jobs.lever.co/acme/abc-def/apply",
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
    page = _RecordingPage(f"<html><body>{_LEVER_FORM_HTML}</body></html>")
    return page, data


class TestComposablePath:
    def test_fill_scanned_fills_custom_fields(self) -> None:
        page, data = _composable_data()
        LeverHandler()._fill_dynamic(page, data)
        assert any(a[0] == "select_option" for a in page.actions)
        assert any(a[0] == "fill" and "why" in a[1][0] for a in page.actions)
        assert data.resolutions_log

    def test_composable_apply_happy_path(self) -> None:
        page, data = _composable_data()
        result = LeverHandler().apply(page, data)
        assert result.state == "applied"
        assert ("click", ("button[type='submit']",)) in page.actions

    def test_router_dispatch_alias_still_fills(self) -> None:
        from magicapply.infrastructure.browser.ats.router_dispatch import apply_router_to_form

        page, data = _composable_data()
        apply_router_to_form(page, data, handler_name="Lever", form_selector="form.posting-form")
        assert data.resolutions_log

    def test_form_selector_prefers_posting_form(self) -> None:
        html = """
        <form id="noise"><input name="noise" type="text"></form>
        <form class="posting-form">
          <label>Why?<textarea name="why" id="why"></textarea></label>
        </form>
        """
        page, data = _composable_data()
        page = _RecordingPage(f"<html><body>{html}</body></html>")
        LeverHandler()._fill_dynamic(page, data)
        assert data.resolutions_log
        assert data.resolutions_log[0].field.selector == "#why"
