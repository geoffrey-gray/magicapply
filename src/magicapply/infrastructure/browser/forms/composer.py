"""FormComposer — scan and/or load schema, pick driver, fill, log."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

_TRACE = os.environ.get("MAGICAPPLY_TRACE_COMPOSER") == "1"

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

        if _TRACE:
            logger.warning(
                "[trace] composer.fill start: schema=%s ats=%s field_count=%d",
                schema.schema_id, schema.ats, len(schema.fields),
            )
        report = FillReport()
        for idx, field in enumerate(schema.fields):
            step_t0 = time.monotonic() if _TRACE else 0.0
            if _already_filled(page, field):
                if _TRACE:
                    logger.warning(
                        "[trace] field %d/%d SKIP (already filled) label=%r kind=%s in %.2fs",
                        idx + 1, len(schema.fields), field.label, field.kind,
                        time.monotonic() - step_t0,
                    )
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
                if _TRACE:
                    logger.warning(
                        "[trace] field %d/%d UNHANDLED label=%r in %.2fs",
                        idx + 1, len(schema.fields), field.label,
                        time.monotonic() - step_t0,
                    )
                report.unhandled.append(field.label)
                continue

            if not driver.execute(page, field, resolved):
                if strategy != "file":
                    report.errors.append(field.label)
                if _TRACE:
                    logger.warning(
                        "[trace] field %d/%d EXECUTE FAILED strategy=%s label=%r in %.2fs",
                        idx + 1, len(schema.fields), strategy, field.label,
                        time.monotonic() - step_t0,
                    )
            elif _TRACE:
                logger.warning(
                    "[trace] field %d/%d ok strategy=%s label=%r in %.2fs",
                    idx + 1, len(schema.fields), strategy, field.label,
                    time.monotonic() - step_t0,
                )

        if _TRACE:
            logger.warning(
                "[trace] composer.fill done: unhandled=%d errors=%d skipped=%d",
                len(report.unhandled), len(report.errors), len(report.skipped),
            )
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
        if _TRACE:
            t0 = time.monotonic()
            logger.warning("[trace] scan_form start selector=%r", form_selector)
        fields = scan_form(page, form_selector=form_selector)
        if _TRACE:
            logger.warning(
                "[trace] scan_form done: %d fields in %.2fs",
                len(fields), time.monotonic() - t0,
            )
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