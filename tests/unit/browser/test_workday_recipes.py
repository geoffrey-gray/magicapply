"""Unit tests for Workday recipe schemas and composable fill (CF.3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.workday import (
    WorkdayHandler,
    _fill_workday_self_identify,
    _fill_workday_voluntary_disclosures,
)
from magicapply.infrastructure.browser.ats.workday_recipes import (
    DEI_DECLINE_LABELS,
    self_identify_schema,
    voluntary_disclosures_schema,
)
from magicapply.infrastructure.browser.forms.composer import FormComposer
from magicapply.infrastructure.browser.forms.registry import build_rules_registry


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    def filter(self, *, has_text: str) -> _FakeLocator:
        self._page._filter_text = has_text
        return self

    def count(self) -> int:
        if "input[type='radio']" in self._selector:
            return 1
        return 1

    def click(self, *, timeout: int = 0, force: bool = False) -> None:
        self._page.actions.append(("click", self._selector))

    def fill(self, value: str, *, timeout: int = 0) -> None:
        self._page._values[self._selector] = value
        self._page.actions.append(("fill", f"{self._selector}={value}"))

    def blur(self) -> None:
        self._page.actions.append(("blur", self._selector))

    def input_value(self, *, timeout: int = 0) -> str:
        return self._page._values.get(self._selector, "")

    def is_checked(self) -> bool:
        return self._page._radio_checked.get(self._page._filter_text, False)

    def is_visible(self, *, timeout: int = 0) -> bool:
        return True

    def inner_text(self, *, timeout: int = 0) -> str:
        if "#personalInfoUS--ethnicity" in self._selector:
            return self._page._button_labels.get("personalInfoUS--ethnicity", "Select One")
        if "#personalInfoUS--gender" in self._selector:
            return self._page._button_labels.get("personalInfoUS--gender", "Select One")
        if "#personalInfoUS--veteranStatus" in self._selector:
            return self._page._button_labels.get(
                "personalInfoUS--veteranStatus", "Select One"
            )
        return ""

    def scroll_into_view_if_needed(self, *, timeout: int = 0) -> None:
        pass

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._page, selector)


class _FakeRoleLocator:
    def __init__(self, page: "_FakePage", role: str, name: str) -> None:
        self._page = page
        self._name = name

    def click(self, *, timeout: int = 0) -> None:
        self._page.actions.append(("click", f"role=option:{self._name}"))


class _FakePage:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self._values: dict[str, str] = {}
        self._button_labels: dict[str, str] = {}
        self._radio_checked: dict[str, bool] = {}
        self._filter_text = ""
        self._html = "<html><body>ok</body></html>"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def get_by_role(self, role: str, name: str, *, exact: bool = False) -> _FakeRoleLocator:
        return _FakeRoleLocator(self, role, name)

    def click(self, selector: str, *, timeout: int = 0) -> None:
        self.actions.append(("click", selector))

    def fill(self, selector: str, value: str, *, timeout: int = 0) -> None:
        self._values[selector] = value
        self.actions.append(("fill", f"{selector}={value}"))

    def content(self) -> str:
        return self._html


class _RecordingNarrative:
    def answer(self, job: Job, question: str) -> str:
        return "canned"


def _build_data(static: StaticAnswers) -> tuple[_FakePage, ApplicationData]:
    page = _FakePage()
    router = AnswerRouter(
        static_answers=static,
        narrative=_RecordingNarrative(),
        resume_docx_path=Path("/tmp/resume.docx"),
    )
    job = Job.new(source_name="s", url="https://acme.wd1.myworkdayjobs.com/j", title="Eng", company="Acme")
    data = ApplicationData(
        job_url=job.url,
        static_answers=static,
        tailored_resume=TailoredResume(base_name="R", job_id="abc", name="Jane Doe"),
        resume_docx_path=Path("/tmp/resume.docx"),
        answer_router=router,
        job=job,
    )
    registry = build_rules_registry(router)
    data = data.model_copy(
        update={"form_composer": FormComposer(drivers=registry, data=data)}
    )
    return page, data


class TestWorkdayRecipes:
    def test_voluntary_disclosures_schema_fields(self) -> None:
        schema = voluntary_disclosures_schema(StaticAnswers(full_name="Jane", email="j@e.com"))
        assert schema.schema_id == "workday_voluntary_disclosures"
        assert len(schema.fields) == 3
        assert schema.fields[0].variant == "workday_listbox"
        assert schema.fields[0].recipe_labels == DEI_DECLINE_LABELS

    def test_self_identify_schema_date_values(self) -> None:
        schema = self_identify_schema(
            StaticAnswers(full_name="Jane", email="j@e.com"),
            today=date(2026, 7, 6),
        )
        date_fields = [f for f in schema.fields if f.variant == "workday_date_spin"]
        assert [f.recipe_value for f in date_fields] == ["7", "6", "2026"]


class TestComposableFill:
    def test_voluntary_disclosures_fills_listboxes(self) -> None:
        static = StaticAnswers(full_name="Jane Doe", email="j@example.com")
        page, data = _build_data(static)
        composer = data.form_composer
        assert isinstance(composer, FormComposer)

        report = composer.fill_recipe(page, voluntary_disclosures_schema(static))
        ethnicity_clicks = [a for a in page.actions if "#personalInfoUS--ethnicity" in a[1]]
        veteran_clicks = [a for a in page.actions if "#personalInfoUS--veteranStatus" in a[1]]
        assert ethnicity_clicks
        assert veteran_clicks
        assert report.unhandled == []

    def test_self_identify_fills_name_dates_and_disability(self) -> None:
        static = StaticAnswers(full_name="Jane Doe", email="j@example.com")
        page, data = _build_data(static)
        composer = data.form_composer
        assert isinstance(composer, FormComposer)

        report = composer.fill_recipe(
            page,
            self_identify_schema(static, today=date(2026, 7, 6)),
        )
        assert any("selfIdentifiedDisabilityData--name" in a[1] for a in page.actions)
        assert any("dateSectionMonth-input=7" in a[1] for a in page.actions)
        assert any("dateSectionDay-input=6" in a[1] for a in page.actions)
        assert any("dateSectionYear-input=2026" in a[1] for a in page.actions)
        assert any(
            a[0] == "click"
            and ("radio" in a[1] or "checkbox" in a[1] or "label" in a[1])
            for a in page.actions
        )
        assert report.unhandled == []

    def test_skips_already_filled_listbox_and_date_spin(self) -> None:
        static = StaticAnswers(full_name="Jane Doe", email="j@example.com")
        page, data = _build_data(static)
        page._button_labels["personalInfoUS--ethnicity"] = "Decline to answer"
        page._button_labels["personalInfoUS--gender"] = "Decline to answer"
        page._button_labels["personalInfoUS--veteranStatus"] = "I DO NOT WISH TO SELF-IDENTIFY"
        page._values["#selfIdentifiedDisabilityData--dateSignedOn-dateSectionMonth-input"] = "7"
        page._values["#selfIdentifiedDisabilityData--dateSignedOn-dateSectionDay-input"] = "6"
        page._values["#selfIdentifiedDisabilityData--dateSignedOn-dateSectionYear-input"] = "2026"
        page._values["#selfIdentifiedDisabilityData--name"] = "Jane Doe"
        page._radio_checked["I do not want to answer"] = True

        composer = data.form_composer
        assert isinstance(composer, FormComposer)
        vd_report = composer.fill_recipe(page, voluntary_disclosures_schema(static))
        si_report = composer.fill_recipe(
            page,
            self_identify_schema(static, today=date(2026, 7, 6)),
        )
        assert "Ethnicity" in vd_report.skipped
        assert not any("#personalInfoUS--ethnicity" in a[1] for a in page.actions)
        assert "Signature date month" in si_report.skipped
        assert "Disability status" in si_report.skipped


class TestWorkdayHandlerComposableBranch:
    def test_handler_delegates_voluntary_disclosures_to_composer(self) -> None:
        static = StaticAnswers(full_name="Jane Doe", email="j@example.com")
        page, data = _build_data(static)
        handler = WorkdayHandler()
        handler._fill_dynamic = lambda p, d: None  # type: ignore[method-assign]
        _fill_workday_voluntary_disclosures(page, data)
        assert any("#personalInfoUS--ethnicity" in a[1] for a in page.actions)

    def test_handler_delegates_self_identify_to_composer(self) -> None:
        static = StaticAnswers(full_name="Jane Doe", email="j@example.com")
        page, data = _build_data(static)
        _fill_workday_self_identify(page, data)
        assert any("selfIdentifiedDisabilityData--name" in a[1] for a in page.actions)