"""Tests for the BaseATSHandler Template Method flow."""

from __future__ import annotations

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    ApplicationResult,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler


class FakePage:
    """PageDriver stand-in that records every interaction."""

    def __init__(self, html: str = "<html><body>ok</body></html>") -> None:
        self.url = "https://example.com/original"
        self._html = html
        self.actions: list[tuple[str, tuple[str, ...]]] = []

    def goto(self, url: str) -> None:
        self.url = url
        self.actions.append(("goto", (url,)))

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", (selector, value)))

    def click(self, selector: str) -> None:
        self.actions.append(("click", (selector,)))

    def content(self) -> str:
        return self._html


def _data(url: str = "https://boards.greenhouse.io/acme/jobs/1") -> ApplicationData:
    return ApplicationData(
        job_url=url,
        static_answers=StaticAnswers(full_name="Jane Doe", email="j@example.com", phone="555-0100"),
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        cover_letter="Dear team,\n\nHello.",
    )


class TestGreenhouseFlow:
    def test_happy_path(self) -> None:
        page = FakePage()
        result = GreenhouseHandler().apply(page, _data())
        assert result.state == "applied"
        # Names split correctly
        assert ("fill", ("#first_name", "Jane")) in page.actions
        assert ("fill", ("#last_name", "Doe")) in page.actions
        # Cover letter attempted
        assert any(a[0] == "fill" and a[1][0].startswith("textarea") for a in page.actions)
        # Submitted
        assert ("click", ("input[type='submit']",)) in page.actions

    def test_captcha_on_load_returns_needs_intervention(self) -> None:
        page = FakePage(html='<iframe src="google.com/recaptcha"></iframe>')
        result = GreenhouseHandler().apply(page, _data())
        assert result.state == "needs_intervention"
        assert "CAPTCHA" in (result.error or "")

    def test_exception_in_fill_becomes_failed(self) -> None:
        class BrokenPage(FakePage):
            def fill(self, selector: str, value: str) -> None:
                raise RuntimeError("selector not found")

        result = GreenhouseHandler().apply(BrokenPage(), _data())
        assert result.state == "failed"
        assert "selector not found" in (result.error or "")


class TestDryRun:
    def test_dry_run_short_circuits_submit(self) -> None:
        page = FakePage()
        data = _data()
        data = data.model_copy(update={"dry_run": True})
        result = GreenhouseHandler().apply(page, data)

        # The flow still returns "applied" so the caller's terminal state
        # transition works uniformly.
        assert result.state == "applied"
        assert "dry-run" in (result.error or "")

        # Fills happened (identity, cover letter) but the submit click did NOT.
        assert any(
            a[0] == "fill" and a[1][0] == "#first_name" for a in page.actions
        )
        assert ("click", ("input[type='submit']",)) not in page.actions

    def test_dry_run_still_catches_captcha_first(self) -> None:
        page = FakePage(html='<iframe src="google.com/recaptcha"></iframe>')
        data = _data().model_copy(update={"dry_run": True})
        result = GreenhouseHandler().apply(page, data)
        # CAPTCHA branch wins over dry-run branch.
        assert result.state == "needs_intervention"
        assert "CAPTCHA" in (result.error or "")


class TestBaseHandlerContract:
    def test_matches_must_be_overridden(self) -> None:
        class Minimal(BaseATSHandler):
            pass

        try:
            Minimal.matches("https://x.com")
        except NotImplementedError:
            return
        raise AssertionError("expected NotImplementedError")

    def test_verify_default_returns_applied(self) -> None:
        class Trivial(BaseATSHandler):
            @classmethod
            def matches(cls, url: str) -> bool:
                return True

            def _navigate(self, page: PageDriver, data: ApplicationData) -> None: ...

            def _fill_static(self, page: PageDriver, data: ApplicationData) -> None: ...

            def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None: ...

            def _submit(self, page: PageDriver, data: ApplicationData) -> None: ...

        result: ApplicationResult = Trivial().apply(FakePage(), _data())
        assert result.state == "applied"
