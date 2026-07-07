"""FormComposer — scan and/or load schema, pick driver, fill, log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from magicapply.infrastructure.browser.ats.base import ApplicationData, PageDriver

if TYPE_CHECKING:
    from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats import workday_widgets
from magicapply.infrastructure.browser.ats.form_scan import scan_form
from magicapply.infrastructure.browser.ats.observed_form_log import ResolvedField
from magicapply.infrastructure.browser.forms.fields import FormField
from magicapply.infrastructure.browser.forms.registry import DriverRegistry
from magicapply.infrastructure.browser.forms.resume_resolve import resolve_from_resume
from magicapply.infrastructure.browser.forms.schema import FillReport, FormSchema

_WORKDAY_VARIANTS = frozenset(
    {"workday_listbox", "workday_multiselect", "workday_date_spin"}
)


class FormComposer:
    def __init__(self, *, drivers: DriverRegistry, data: ApplicationData) -> None:
        self._drivers = drivers
        self._data = data

    def fill(self, page: PageDriver, schema: FormSchema) -> FillReport:
        job = self._data.job
        if job is None:
            return FillReport()

        report = FillReport()
        for field in schema.fields:
            if _already_filled(page, field):
                report.skipped.append(field.label)
                continue

            driver = self._drivers.resolve(field, ats=schema.ats)
            resolved = driver.resolve(field, job)
            if resolved.strategy == "unhandled":
                resume_resolved = resolve_from_resume(field, self._data)
                if resume_resolved is not None:
                    resolved = resume_resolved
            self._data.resolutions_log.append(
                ResolvedField(field=field, answer=resolved)
            )

            strategy = resolved.strategy
            if strategy == "unhandled" and not _recipe_driven(field):
                report.unhandled.append(field.label)
                continue

            if not driver.execute(page, field, resolved):
                if strategy != "file":
                    report.errors.append(field.label)

        return report

    def fill_scanned(
        self,
        page: PageDriver,
        form_selector: str,
        *,
        ats: str,
        step_id: str | None = None,
        schema_id: str | None = None,
    ) -> FillReport:
        fields = scan_form(page, form_selector=form_selector)
        if step_id:
            for f in fields:
                f.step_id = step_id
        schema = FormSchema(
            schema_id=schema_id or f"{ats}_scanned",
            ats=ats,
            fields=fields,
            form_selector=form_selector,
            source="scanned",
            step_id=step_id,
        )
        return self.fill(page, schema)

    def fill_recipe(self, page: PageDriver, recipe: FormSchema) -> FillReport:
        return self.fill(page, recipe)


def _recipe_driven(field: FormField) -> bool:
    variant = field.variant or field.kind
    if variant in _WORKDAY_VARIANTS:
        return True
    if variant == "radio" and field.recipe_labels:
        return True
    if field.selector and "--school" in field.selector:
        return True
    return bool(field.recipe_value)


def _already_filled(page: PageDriver, field: FormField) -> bool:
    """Skip fields that already hold a committed value (Workday wizard retries)."""
    variant = field.variant or field.kind
    if variant == "workday_listbox":
        if field.widget_id:
            return workday_widgets.listbox_committed(page, field.widget_id)
        return False
    if variant == "workday_date_spin":
        if field.selector:
            return workday_widgets.date_spin_filled(page, field.selector)
        return False
    if variant == "radio" and field.group_id == "disability_status":
        return workday_widgets.disability_option_selected(page, *field.recipe_labels)
    if variant in {"text", "textarea"} and field.selector:
        return _input_has_value(page, field.selector)
    return False


def _input_has_value(page: PageDriver, selector: str) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        value = locator(selector).first.input_value(timeout=1_000)
    except Exception:  # noqa: BLE001
        return False
    return bool(value.strip())


def composer_from_registry(
    registry: DriverRegistry, data: ApplicationData
) -> FormComposer:
    return FormComposer(drivers=registry, data=data)