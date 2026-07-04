"""Unit tests for the LeverHandler template flow."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.lever import LeverHandler


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
