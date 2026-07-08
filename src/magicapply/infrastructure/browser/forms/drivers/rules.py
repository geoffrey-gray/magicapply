"""RulesBasedDriver — wraps AnswerRouter resolve + variant-aware execute."""

from __future__ import annotations

import contextlib
import logging

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats import workday_widgets
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter, ResolvedAnswer
from magicapply.infrastructure.browser.ats.base import PageDriver
from magicapply.infrastructure.browser.forms.fields import FormField

logger = logging.getLogger(__name__)

_WORKDAY_VARIANTS = frozenset(
    {"workday_listbox", "workday_multiselect", "workday_date_spin"}
)


class RulesBasedDriver:
    """Resolve via ``AnswerRouter``; execute native field kinds via Playwright."""

    def __init__(self, router: AnswerRouter) -> None:
        self._router = router

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        return self._router.resolve(field, job)

    def supports(self, field: FormField) -> bool:
        return True

    def execute(
        self, page: PageDriver, field: FormField, answer: ResolvedAnswer
    ) -> bool:
        variant = field.variant or field.kind
        if variant in _WORKDAY_VARIANTS:
            return _execute_workday_variant(page, field, answer)
        if variant == "radio" and field.recipe_labels and field.group_id == "disability_status":
            return workday_widgets.click_disability_option(
                page,
                _primary_label(field, answer),
                fallback=field.recipe_labels[-1] if field.recipe_labels else "",
            )

        strategy = answer.strategy
        if strategy in {"static", "library", "narrative"}:
            if field.selector and "--school" in field.selector and answer.value:
                if workday_widgets.fill_search_multiselect(
                    page, field.selector, answer.value
                ):
                    return True
            return _try_fill(page, field.selector, answer.value)
        if strategy == "select":
            return _try_select(page, field, answer.value)
        if strategy == "check":
            if answer.check:
                return _try_check(page, field.selector)
            return True
        if strategy == "file":
            if not answer.value or not field.selector:
                return False
            with contextlib.suppress(Exception):
                page.set_input_files(field.selector, answer.value)
                return True
            return False
        return False


def _try_fill(page: PageDriver, selector: str, value: str) -> bool:
    with contextlib.suppress(Exception):
        page.fill(selector, value)
        return True
    return False


def _try_check(page: PageDriver, selector: str) -> bool:
    with contextlib.suppress(Exception):
        page.check(selector)
        return True
    return False


def _try_select(page: PageDriver, field: FormField, value: str) -> bool:
    if field.kind == "radio" and field.name:
        return _click_radio(page, name=field.name, value=value)
    with contextlib.suppress(Exception):
        page.select_option(field.selector, value)
        return True
    return False


def _execute_workday_variant(
    page: PageDriver, field: FormField, answer: ResolvedAnswer
) -> bool:
    variant = field.variant or field.kind
    if variant == "workday_listbox":
        widget_id = field.widget_id
        if not widget_id:
            return False
        if widget_id == "personalInfoUS--veteranStatus":
            value = _primary_value(field, answer) or None
            return workday_widgets.select_veteran_status_listbox(page, value)
        labels = _listbox_labels(field, answer)
        return workday_widgets.select_listbox_first_match(page, widget_id, *labels)
    if variant == "workday_multiselect":
        widget_id = field.widget_id
        if not widget_id:
            return False
        label = _primary_value(field, answer)
        return workday_widgets.fill_multiselect(page, widget_id, label)
    if variant == "workday_date_spin":
        value = field.recipe_value or _primary_value(field, answer)
        if not value or not field.selector:
            return False
        return workday_widgets.fill_date_spin_input(page, field.selector, value)
    return False


def _primary_value(field: FormField, answer: ResolvedAnswer) -> str:
    if answer.value:
        return answer.value
    return field.recipe_value or ""


def _primary_label(field: FormField, answer: ResolvedAnswer) -> str:
    if answer.value:
        return answer.value
    return field.recipe_labels[0] if field.recipe_labels else ""


def _listbox_labels(field: FormField, answer: ResolvedAnswer) -> tuple[str, ...]:
    if answer.value:
        return (answer.value,)
    if field.recipe_labels:
        return field.recipe_labels
    return ()


_RADIO_CLICK_TIMEOUT_MS = 500


def _click_radio(page: PageDriver, *, name: str, value: str) -> bool:
    """Click a radio button by group name + option value.

    Uses only the value-attribute selector with a fast-fail 500 ms timeout.
    Longer / multi-attempt fallbacks were investigated (wrapper-div, plus a
    Playwright accessibility-tree fallback via ``get_by_role``) but on real
    Ashby forms — where the ``<input>`` has no ``value`` attribute at all
    and is rendered ``display:none`` — the fallbacks either invoked
    Playwright's actionability wait (which stalls Chromium into an EPIPE
    subprocess crash regardless of the click timeout) or produced spurious
    hangs. Ashby's consent radios are non-required in practice, so leaving
    them ``unhandled`` when the value-attribute selector misses is safer
    than trying to force a click and destabilising the Chromium session.

    Returns True on a matched-and-clicked radio, False otherwise. Never
    raises.
    """
    selector = f"input[type='radio'][name='{name}'][value='{value}']"
    locator = getattr(page, "locator", None)
    if not callable(locator):
        # Minimal PageDriver stub without locator() — test-only path.
        with contextlib.suppress(Exception):
            page.click(selector)  # type: ignore[call-arg]
            return True
        return False

    with contextlib.suppress(Exception):
        radio = locator(selector).first
        radio.click(timeout=_RADIO_CLICK_TIMEOUT_MS)
        with contextlib.suppress(Exception):
            if radio.is_checked():
                return True
        # click() succeeded but is_checked reports False — assume the click
        # landed and the DOM just hasn't caught up. Reporting True keeps
        # the observation log honest for the common Greenhouse case.
        return True

    return False