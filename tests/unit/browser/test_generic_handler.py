"""Unit tests for GenericHandler catch-all apply (PR3)."""

from __future__ import annotations

import re
from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.generic import GenericHandler
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.forms.capture_loader import (
    captured_fixtures_root,
    load_capture,
)
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_driver_registry


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "The mission aligns with my experience."


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
        if not _selector_matches_html(self._html, selector):
            raise RuntimeError(f"no element for {selector}")
        self.actions.append(("click", selector))

    def set_input_files(self, selector: str, files: str) -> None:
        self.actions.append(("set_input_files", selector, files))

    def select_option(self, selector: str, value: str) -> None:
        self.actions.append(("select_option", selector, value))

    def check(self, selector: str) -> None:
        self.actions.append(("check", selector))


def _selector_matches_html(html: str, selector: str) -> bool:
    """Minimal CSS subset so submit-selector fallthrough mirrors Playwright."""
    patterns: dict[str, re.Pattern[str]] = {
        "input[type='submit']": re.compile(
            r"<input\b[^>]*\btype=['\"]submit['\"]", re.I
        ),
        "button[type='submit']": re.compile(
            r"<button\b[^>]*\btype=['\"]submit['\"]", re.I
        ),
        "button[name='submit']": re.compile(
            r"<button\b[^>]*\bname=['\"]submit['\"]", re.I
        ),
        "input[name='submit']": re.compile(
            r"<input\b[^>]*\bname=['\"]submit['\"]", re.I
        ),
    }
    pattern = patterns.get(selector)
    if pattern is not None:
        return bool(pattern.search(html))
    if "id*='submit'" in selector:
        return bool(re.search(r"\bid=['\"][^'\"]*submit", html, re.I))
    if "data-testid*='submit'" in selector:
        return "data-testid" in html and "submit" in html.lower()
    if "data-automation-id*='submit'" in selector:
        return "data-automation-id" in html and "submit" in html.lower()
    return True


def _composable_data(*, job_url: str) -> tuple[_FakePage, ApplicationData]:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-0100",
        linkedin_url="https://linkedin.com/in/jane",
        authorized_to_work_us=True,
    )
    narrative = _RecordingNarrative()
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(
        source_name="custom",
        url=job_url,
        title="Staff Data Scientist",
        company="Example Co",
    )
    data = ApplicationData(
        job_url=job_url,
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
    bundle = load_capture(captured_fixtures_root() / "custom-e2e-smoke-20260707")
    page = _FakePage(bundle.dom_html)
    return page, data


def test_factory_dispatches_generic_for_unknown_hosts() -> None:
    handler = ATSHandlerFactory.for_url("https://careers.example-custom.com/jobs/1/apply")
    assert isinstance(handler, GenericHandler)
    assert not isinstance(handler, GreenhouseHandler)


def test_factory_still_prefers_greenhouse_over_generic() -> None:
    handler = ATSHandlerFactory.for_url("https://boards.greenhouse.io/acme/jobs/1")
    assert isinstance(handler, GreenhouseHandler)


def test_composable_fill_scanned_fills_custom_form() -> None:
    page, data = _composable_data(
        job_url="https://careers.example-custom.com/jobs/staff-ds/apply"
    )
    GenericHandler()._fill_dynamic(page, data)
    assert any(a[0] == "select_option" for a in page.actions)
    assert any(a[0] == "fill" and "interest" in str(a) for a in page.actions)
    assert any(a[0] == "set_input_files" for a in page.actions)
    assert data.resolutions_log


def test_dry_run_apply_skips_submit() -> None:
    page, data = _composable_data(
        job_url="https://careers.example-custom.com/jobs/staff-ds/apply"
    )
    data = data.model_copy(update={"dry_run": True})
    result = GenericHandler().apply(page, data)
    assert result.state == "applied"
    assert result.error == "dry-run: submit skipped"
    assert not any(a[0] == "click" for a in page.actions)


def test_yes_submit_clicks_button_submit() -> None:
    page, data = _composable_data(
        job_url="https://careers.example-custom.com/jobs/staff-ds/apply"
    )
    result = GenericHandler().apply(page, data)
    assert result.state == "applied"
    assert ("click", "button[type='submit']") in page.actions