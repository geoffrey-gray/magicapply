"""Unit tests for HybridDriver, LLMDriver, and DriverRegistry."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import FormDriverFieldOverride, FormDriversConfig, StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.drivers.hybrid import HybridDriver
from magicapply.infrastructure.browser.forms.drivers.llm import LLMDriver
from magicapply.infrastructure.browser.forms.drivers.rules import RulesBasedDriver
from magicapply.infrastructure.browser.forms.fields import FormField
from magicapply.infrastructure.browser.forms.registry import DriverRegistry, build_driver_registry


class _RecordingNarrative:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def answer(self, job: Job, question: str) -> str:
        self.calls.append(question)
        return "llm-generated answer"


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html
        self.actions: list[tuple[str, str, str]] = []

    def content(self) -> str:
        return self._html

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector, value))


def _router() -> AnswerRouter:
    static = StaticAnswers(full_name="Jane Doe", email="jane@example.com")
    return AnswerRouter(
        static_answers=static,
        narrative=_RecordingNarrative(),
        resume_docx_path=Path("/tmp/resume.docx"),
    )


def test_hybrid_falls_back_to_llm_for_open_ended() -> None:
    narrative = _RecordingNarrative()
    static = StaticAnswers(full_name="Jane Doe", email="jane@example.com")
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    rules = RulesBasedDriver(router)
    llm = LLMDriver(narrative)
    hybrid = HybridDriver(rules, llm)
    job = Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")
    field = FormField(
        selector="#q1",
        label="Why do you want to work here?",
        kind="textarea",
    )
    resolved = hybrid.resolve(field, job)
    assert resolved.strategy == "narrative"
    assert resolved.value == "llm-generated answer"
    assert narrative.calls == ["Why do you want to work here?"]


def test_registry_ats_override_selects_rules() -> None:
    router = _router()
    narrative = _RecordingNarrative()
    config = FormDriversConfig(default="hybrid", ats={"workday": "rules"})
    registry = build_driver_registry(router, narrative, config)
    field = FormField(selector="#x", label="Anything", kind="text")
    driver = registry.resolve(field, ats="workday")
    assert isinstance(driver, RulesBasedDriver)


def test_registry_label_regex_selects_llm() -> None:
    router = _router()
    narrative = _RecordingNarrative()
    config = FormDriversConfig(
        default="hybrid",
        fields=[FormDriverFieldOverride(label_regex="describe your", driver="llm")],
    )
    registry = build_driver_registry(router, narrative, config)
    field = FormField(
        selector="#q",
        label="Please describe your experience with Python",
        kind="textarea",
    )
    driver = registry.resolve(field, ats="greenhouse")
    assert isinstance(driver, LLMDriver)


def test_registry_variant_override() -> None:
    rules = RulesBasedDriver(_router())
    llm = LLMDriver(_RecordingNarrative())
    hybrid = HybridDriver(rules, llm)
    registry = DriverRegistry(
        drivers={"rules": rules, "llm": llm, "hybrid": hybrid},
        default="hybrid",
        by_variant={"workday_listbox": "rules"},
    )
    field = FormField(
        selector="#v",
        label="Veteran",
        kind="select",
        variant="workday_listbox",
    )
    driver = registry.resolve(field, ats="greenhouse")
    assert isinstance(driver, RulesBasedDriver)


class _FailingLocator:
    """A locator whose click always raises — used to force `_click_radio`
    to walk to the label-based fallback."""

    def __init__(self) -> None:
        self.first = self

    def click(self, **kwargs: object) -> None:
        raise RuntimeError("selector never matched")

    def is_checked(self) -> bool:
        return False


class _SucceedingLocator:
    """Locator whose click records the call and returns cleanly."""

    def __init__(self, *, visible: bool = True) -> None:
        self.clicks: list[dict[str, object]] = []
        self.first = self
        self._visible = visible

    def click(self, **kwargs: object) -> None:
        self.clicks.append(kwargs)

    def is_visible(self, **_kwargs: object) -> bool:
        return self._visible


class _RadioClickPage:
    """PageDriver stub that fails both value-attribute paths and succeeds
    on the accessibility fallback."""

    def __init__(self) -> None:
        self.locator_calls: list[str] = []
        self.role_calls: list[tuple[str, str]] = []
        self.role_target = _SucceedingLocator()

    def locator(self, selector: str) -> _FailingLocator:
        self.locator_calls.append(selector)
        return _FailingLocator()

    def get_by_role(self, role: str, *, name: str) -> _SucceedingLocator:
        self.role_calls.append((role, name))
        return self.role_target


def test_click_radio_returns_false_when_selector_never_matches() -> None:
    """Ashby renders radios without a `value` attribute — the value-attribute
    selector never matches. `_click_radio` must report False so the caller
    can log the field as unhandled. Earlier iterations tried a wrapper-div
    and accessibility-tree fallback, but those either invoked Playwright's
    actionability wait (stalling Chromium into an EPIPE crash on multi-
    radio Ashby forms) or produced spurious hangs. Ashby's consent radios
    are optional in practice, so unhandled is the safe outcome."""
    from magicapply.infrastructure.browser.forms.drivers.rules import _click_radio

    page = _RadioClickPage()  # every locator().first.click() raises
    ok = _click_radio(page, name="ashby-group-uuid", value="I agree")

    assert ok is False
    # Only the value-attribute selector was tried — no expensive
    # fallbacks that could hang Chromium.
    assert len(page.locator_calls) == 1
    assert "value='I agree'" in page.locator_calls[0]


def test_click_radio_uses_fast_fail_timeout() -> None:
    """The one click attempt must use the 500ms budget; longer timeouts
    stalled Chromium into an EPIPE subprocess crash on the pre-fix
    TRM Ashby run."""
    from magicapply.infrastructure.browser.forms.drivers.rules import (
        _RADIO_CLICK_TIMEOUT_MS,
        _click_radio,
    )

    assert _RADIO_CLICK_TIMEOUT_MS == 500

    click_timeouts: list[int] = []

    class _TimeoutCapturingLocator:
        def __init__(self) -> None:
            self.first = self

        def click(self, *, timeout: int, **_: object) -> None:
            click_timeouts.append(timeout)
            raise RuntimeError("stub-fail-after-recording-timeout")

        def is_checked(self) -> bool:
            return False

    class _Page:
        def locator(self, _selector: str) -> _TimeoutCapturingLocator:
            return _TimeoutCapturingLocator()

    _click_radio(_Page(), name="X", value="Y")
    assert click_timeouts == [500]


def test_composer_uses_llm_via_hybrid_default() -> None:
    narrative = _RecordingNarrative()
    static = StaticAnswers(full_name="Jane Doe", email="jane@example.com")
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    registry = build_driver_registry(router, narrative, FormDriversConfig(default="hybrid"))
    job = Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")
    data = ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        answer_router=router,
        job=job,
    )
    composer = FormComposer(drivers=registry, data=data)
    html = """
    <form id="application-form">
      <label for="why">Why do you want this role?</label>
      <textarea id="why" name="why"></textarea>
    </form>
    """
    page = _FakePage(f"<html><body>{html}</body></html>")
    report = composer.fill_scanned(page, "form#application-form", ats="greenhouse")
    assert narrative.calls == ["Why do you want this role?"]
    assert ("fill", "#why", "llm-generated answer") in page.actions
    assert report.unhandled == []