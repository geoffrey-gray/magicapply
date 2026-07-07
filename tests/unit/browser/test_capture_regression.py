"""Offline FormComposer regression against captured DOM fixtures (CF.5 / W.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData

from magicapply.infrastructure.browser.forms.capture_loader import (
    captured_fixtures_root,
    golden_strategies,
    list_capture_dirs,
    load_capture,
    schema_from_capture,
)
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_driver_registry


class _FakeLocator:
    def __init__(self, page: "_RegressionPage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    def filter(self, *, has_text: str) -> _FakeLocator:
        self._page._filter_text = has_text
        return self

    def count(self) -> int:
        if "[role='listbox'] [role='option']" in self._selector:
            return len(self._page._listbox_options)
        return 1

    def click(self, *, timeout: int = 0, force: bool = False) -> None:
        self._page.actions.append(("click", self._selector))
        if self._selector == "label" and self._page._filter_text:
            self._page._radio_checked[self._page._filter_text] = True

    def fill(self, value: str, *, timeout: int = 0) -> None:
        self._page._values[self._selector] = value
        self._page.actions.append(("fill", f"{self._selector}={value}"))

    def blur(self) -> None:
        self._page.actions.append(("blur", self._selector))

    def input_value(self, *, timeout: int = 0) -> str:
        return self._page._values.get(self._selector, "")

    def is_visible(self, *, timeout: int = 0) -> bool:
        return True

    def inner_text(self, *, timeout: int = 0) -> str:
        if "#personalInfoUS--" in self._selector:
            return "Select One"
        return ""

    def scroll_into_view_if_needed(self, *, timeout: int = 0) -> None:
        pass

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._page, selector)

    def is_checked(self) -> bool:
        return self._page._radio_checked.get(self._page._filter_text, False)


class _FakeRoleLocator:
    def __init__(self, page: "_RegressionPage", role: str, name: str) -> None:
        self._page = page
        self._name = name

    def click(self, *, timeout: int = 0) -> None:
        self._page.actions.append(("click", f"role=option:{self._name}"))


class _RegressionPage:
    def __init__(self, html: str) -> None:
        self._html = html
        self.actions: list[tuple[str, str]] = []
        self._values: dict[str, str] = {}
        self._listbox_options: list[str] = []
        self._filter_text = ""
        self._radio_checked: dict[str, bool] = {}

    def content(self) -> str:
        return self._html

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def get_by_role(self, role: str, name: str, *, exact: bool = False) -> _FakeRoleLocator:
        return _FakeRoleLocator(self, role, name)

    def fill(self, selector: str, value: str, *, timeout: int = 0) -> None:
        self._values[selector] = value
        self.actions.append(("fill", f"{selector}={value}"))

    def click(self, selector: str, *, timeout: int = 0) -> None:
        self.actions.append(("click", selector))

    def select_option(self, selector: str, value: str, *, timeout: int = 0) -> None:
        self.actions.append(("select_option", f"{selector}={value}"))

    def check(self, selector: str, *, timeout: int = 0) -> None:
        self.actions.append(("check", selector))


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "Fixture narrative answer."


def _prefill_handler_static(page: _RegressionPage, data: ApplicationData, ats: str) -> None:
    """Mirror ``_fill_static`` so composable scan skips identity fields (CF.5)."""
    answers = data.static_answers
    if ats == "greenhouse":
        page.fill("#first_name", answers.full_name.split()[0])
        page.fill("#last_name", " ".join(answers.full_name.split()[1:]) or "")
        page.fill("#email", answers.email)
        if answers.phone:
            page.fill("#phone", answers.phone)
        if answers.linkedin_url:
            page.fill("input[name='linkedin_url']", answers.linkedin_url)
    elif ats == "lever":
        page.fill("input[name='name']", answers.full_name)
        page.fill("input[name='email']", answers.email)
        if answers.phone:
            page.fill("input[name='phone']", answers.phone)
        if answers.linkedin_url:
            page.fill("input[name='urls[LinkedIn]']", answers.linkedin_url)
    elif ats == "ashby":
        page.fill("input[name='_systemfield_name']", answers.full_name)
        page.fill("input[name='_systemfield_email']", answers.email)
        if answers.phone:
            page.fill("input[name='_systemfield_phone']", answers.phone)
        if answers.linkedin_url:
            page.fill("input[name='_systemfield_linkedin']", answers.linkedin_url)
        if answers.location:
            page.fill("input[name='_systemfield_location']", answers.location)


def _build_data(bundle_job_url: str) -> ApplicationData:
    static = StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-0100",
        linkedin_url="https://linkedin.com/in/jane",
        location="Boston, MA",
        authorized_to_work_us=True,
    )
    narrative = _RecordingNarrative()
    router = AnswerRouter(
        static_answers=static,
        narrative=narrative,
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(
        source_name="fixture",
        url=bundle_job_url,
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
    return data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )


def _run_capture(bundle_dir: Path) -> tuple[ApplicationData, object]:
    bundle = load_capture(bundle_dir)
    page = _RegressionPage(bundle.dom_html)
    data = _build_data(bundle.job_url)
    composer = data.form_composer
    assert isinstance(composer, FormComposer)

    if bundle.meta.source != "recipe":
        _prefill_handler_static(page, data, bundle.meta.ats)

    if bundle.meta.source == "recipe":
        recipe = schema_from_capture(bundle)
        assert recipe is not None
        report = composer.fill_recipe(page, recipe)
    else:
        selector = bundle.meta.form_selectors[0]
        for candidate in bundle.meta.form_selectors:
            from magicapply.infrastructure.browser.ats.form_scan import scan_form

            if scan_form(page, form_selector=candidate):
                selector = candidate
                break
        report = composer.fill_scanned(
            page,
            selector,
            ats=bundle.meta.ats,
            schema_id=bundle.meta.schema_id,
        )

    return data, report


@pytest.fixture(params=list_capture_dirs())
def capture_dir(request: pytest.FixtureRequest) -> Path:
    return request.param


class TestCaptureLoader:
    def test_lists_all_promoted_fixtures(self) -> None:
        names = [p.name for p in list_capture_dirs()]
        assert "custom-e2e-smoke-20260707" in names
        assert "custom-eightfold-wizard-20260707" in names
        assert "greenhouse-e2e-smoke-20260707" in names
        assert "lever-e2e-smoke-20260707" in names
        assert "ashby-e2e-smoke-20260707" in names
        assert "workday-e2e-smoke-20260707" in names
        assert "greenhouse-reddit-20260707" in names
        assert "lever-foodsmart-20260707" in names
        assert "ashby-trm-20260707" in names
        assert "workday-voluntary-20260707" in names
        assert "workday-self-identify-20260707" in names
        assert "workday-circle-staff-ds-20260707" in names

    def test_live_capture_records_variants(self) -> None:
        bundle = load_capture(
            captured_fixtures_root() / "workday-circle-staff-ds-20260707"
        )
        assert bundle.meta.live
        assert bundle.meta.ats == "workday"
        assert (captured_fixtures_root() / "workday-circle-staff-ds-20260707" / "dom.html").stat().st_size > 100_000
        assert any(f.variant == "workday_date_spin" for f in bundle.fields)

    def test_loads_dom_and_meta(self) -> None:
        bundle = load_capture(captured_fixtures_root() / "greenhouse-e2e-smoke-20260707")
        assert "first_name" in bundle.dom_html
        assert bundle.meta.ats == "greenhouse"
        assert bundle.meta.form_selectors == ("form",)


class TestCaptureRegression:
    def test_composer_fills_without_required_unhandled(self, capture_dir: Path) -> None:
        bundle = load_capture(capture_dir)
        if bundle.meta.live:
            pytest.skip("live captures are snapshot fixtures only")
        data, report = _run_capture(capture_dir)
        assert report is not None
        assert report.errors == []
        unexpected = set(report.unhandled) - set(bundle.meta.allowed_unhandled)
        assert not unexpected, f"unexpected unhandled: {unexpected}"

    def test_resolutions_match_golden_strategies(self, capture_dir: Path) -> None:
        bundle = load_capture(capture_dir)
        if bundle.meta.live:
            pytest.skip("live captures are snapshot fixtures only")
        if bundle.meta.source == "recipe":
            pytest.skip("recipe captures log unhandled before widget execute")
        data, _report = _run_capture(capture_dir)
        expected = golden_strategies(bundle)
        actual = {r.field.label: r.answer.strategy for r in data.resolutions_log}
        for label, strategy in expected.items():
            if label in bundle.meta.allowed_unhandled:
                continue
            assert label in actual, f"missing resolution for {label!r}"
            assert actual[label] == strategy, (
                f"{label}: expected {strategy!r}, got {actual[label]!r}"
            )

    def test_workday_self_identify_recipe_fills_date_and_name(self) -> None:
        capture_dir = captured_fixtures_root() / "workday-self-identify-20260707"
        bundle = load_capture(capture_dir)
        page = _RegressionPage(bundle.dom_html)
        data = _build_data(bundle.job_url)
        composer = data.form_composer
        assert isinstance(composer, FormComposer)
        recipe = schema_from_capture(bundle)
        assert recipe is not None
        report = composer.fill_recipe(page, recipe)
        assert report.errors == []
        assert report.unhandled == []
        assert any("dateSectionMonth-input" in a[1] for a in page.actions)
        assert any("selfIdentifiedDisabilityData--name" in a[1] for a in page.actions)

    def test_workday_recipe_capture_clicks_listboxes(self) -> None:
        capture_dir = captured_fixtures_root() / "workday-voluntary-20260707"
        page = _RegressionPage(load_capture(capture_dir).dom_html)
        data = _build_data(load_capture(capture_dir).job_url)
        composer = data.form_composer
        assert isinstance(composer, FormComposer)
        recipe = schema_from_capture(load_capture(capture_dir))
        assert recipe is not None
        report = composer.fill_recipe(page, recipe)
        assert report.errors == []
        assert report.unhandled == []
        assert any("#personalInfoUS--ethnicity" in a[1] for a in page.actions)
        assert any("#personalInfoUS--veteranStatus" in a[1] for a in page.actions)