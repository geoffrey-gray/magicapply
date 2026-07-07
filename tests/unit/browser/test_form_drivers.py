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