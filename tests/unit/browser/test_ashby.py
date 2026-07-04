"""Unit tests for the AshbyHandler template flow."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.ashby import AshbyHandler
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory


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
