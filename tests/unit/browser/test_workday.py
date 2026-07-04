"""Unit tests for the WorkdayHandler template flow."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.workday import WorkdayHandler


class _RecordingPage:
    """Records every interaction; every method succeeds unconditionally."""

    def __init__(self, html: str = "<html><body>ok</body></html>") -> None:
        self.url = "https://example.com/original"
        self._html = html
        self.actions: list[tuple[str, tuple[str, ...]]] = []

    # Every method accepts **kwargs so timeout=… from the handler's
    # fast-fail helpers works uniformly across fake and real pages.

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


class _StopAtNext(_RecordingPage):
    """Like _RecordingPage but raises on Next-button clicks so the wizard
    loop terminates after one step. Also raises on the Apply button so the
    _navigate try loop moves on and does not spam clicks.
    """

    _STOP = (
        "[data-automation-id='pageFooterNextButton']",
        "button[data-automation-id='wd-CommandButton_uic_next']",
        "button[data-automation-id='next']",
        "[data-automation-id='autofillWithResume']",
        "[data-automation-id='applyManually']",
        "a[data-automation-id='adventureButton']",
        "button:has-text('Apply')",
    )

    def click(self, selector: str, **_: object) -> None:
        if selector in self._STOP:
            raise RuntimeError(f"stop at {selector}")
        super().click(selector)


def _data(url: str = "https://acme.wd1.myworkdayjobs.com/careers/job/1") -> ApplicationData:
    return ApplicationData(
        job_url=url,
        static_answers=StaticAnswers(
            full_name="Jane Doe", email="j@example.com", phone="555-0100"
        ),
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        cover_letter="Cover.",
    )


class TestMatches:
    def test_matches_myworkdayjobs(self) -> None:
        assert WorkdayHandler.matches("https://acme.wd1.myworkdayjobs.com/careers/job/1")

    def test_matches_wd_subdomain(self) -> None:
        assert WorkdayHandler.matches("https://wd5.myworkday.com/tenant/apply/1")

    def test_does_not_match_greenhouse(self) -> None:
        assert not WorkdayHandler.matches("https://boards.greenhouse.io/acme/jobs/1")


class TestFactoryDispatch:
    def test_workday_url_selects_workday_handler(self) -> None:
        h = ATSHandlerFactory.for_url("https://acme.wd1.myworkdayjobs.com/j/1")
        assert isinstance(h, WorkdayHandler)


class TestFlow:
    def test_happy_path_ends_at_applied(self) -> None:
        page = _StopAtNext()
        result = WorkdayHandler().apply(page, _data())
        assert result.state == "applied"
        # Identity fields filled via data-automation-id selectors.
        fills = {a[1][0] for a in page.actions if a[0] == "fill"}
        assert "[data-automation-id='legalNameSection_firstName']" in fills
        assert "[data-automation-id='legalNameSection_lastName']" in fills
        assert "[data-automation-id='email']" in fills
        assert "[data-automation-id='phone-number']" in fills
        # Resume upload happened via the Workday selector.
        assert any(
            a[0] == "set_input_files"
            and a[1][0] == "[data-automation-id='file-upload-input-ref']"
            for a in page.actions
        )
        # Submit click.
        assert any(
            a[0] == "click"
            and a[1][0] == "[data-automation-id='submitApplication']"
            for a in page.actions
        )

    def test_dry_run_short_circuits_submit(self) -> None:
        page = _StopAtNext()
        data = _data().model_copy(update={"dry_run": True})
        result = WorkdayHandler().apply(page, data)
        assert result.state == "applied"
        assert "dry-run" in (result.error or "")
        # The wizard still filled fields but never clicked Submit.
        assert not any(
            a[0] == "click" and "submit" in a[1][0].lower()
            for a in page.actions
        )
