"""Composable scan-fill entry points for inline-form ATS handlers.

All dynamic field filling goes through ``FormComposer`` — wired in
``composition.build_application_data``. There is no rules-only fallback.
"""

from __future__ import annotations

import logging

from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData, PageDriver
from magicapply.infrastructure.browser.ats.form_scan import scan_form
from magicapply.infrastructure.browser.forms.composer import FormComposer

logger = logging.getLogger(__name__)

_HANDLER_TO_ATS = {
    "Greenhouse": "greenhouse",
    "Workday": "workday",
    "Lever": "lever",
    "Ashby": "ashby",
    "Generic": "generic",
    "Indeed": "indeed",
    "LinkedIn": "linkedin",
}


def fill_dynamic_fields(
    page: PageDriver,
    data: ApplicationData,
    *,
    ats: str,
    form_selectors: tuple[str, ...],
    schema_id: str,
    handler_label: str,
) -> None:
    """Fill per-role fields via ``FormComposer`` (CF.6 default for all handlers)."""
    fill_composable_scanned(
        page,
        data,
        ats=ats,
        form_selectors=form_selectors,
        schema_id=schema_id,
        handler_label=handler_label,
    )


def fill_composable_scanned(
    page: PageDriver,
    data: ApplicationData,
    *,
    ats: str,
    form_selectors: tuple[str, ...],
    schema_id: str,
    handler_label: str,
) -> None:
    """Scan the first matching form selector and fill via ``FormComposer``."""
    router = data.answer_router
    job = data.job
    if not isinstance(router, AnswerRouter) or job is None:
        return

    composer = _require_composer(data)
    if composer is None:
        return

    selector = _first_scannable_selector(page, form_selectors)
    if selector is None:
        msg = f"{handler_label}: no application form found for composable fill"
        logger.warning(msg)
        # GenericHandler is the catch-all for LinkedIn/Indeed listing pages
        # with no apply form — fail so dry-run does not false-positive APPLIED.
        # Big-4 handlers still fill via _fill_static selectors when the scan
        # finds nothing (fixture tests, partial pages).
        if ats in {"generic", "indeed", "linkedin"} or handler_label in {
            "Generic",
            "Indeed",
            "LinkedIn",
        }:
            raise RuntimeError(msg)
        return

    report = composer.fill_scanned(page, selector, ats=ats, schema_id=schema_id)
    if report.unhandled:
        logger.warning("%s unhandled fields: %s", handler_label, report.unhandled)


def apply_router_to_form(
    page: PageDriver,
    data: ApplicationData,
    *,
    handler_name: str,
    form_selector: str = "form",
) -> None:
    """Alias for ``fill_composable_scanned`` — kept for Workday + legacy tests."""
    ats = _HANDLER_TO_ATS.get(handler_name, handler_name.lower())
    fill_composable_scanned(
        page,
        data,
        ats=ats,
        form_selectors=(form_selector,),
        schema_id=f"{ats}_router",
        handler_label=handler_name,
    )


def _first_scannable_selector(
    page: PageDriver, form_selectors: tuple[str, ...]
) -> str | None:
    for selector in form_selectors:
        if scan_form(page, form_selector=selector):
            return selector
    return None


def _require_composer(data: ApplicationData) -> FormComposer | None:
    composer = data.form_composer
    if isinstance(composer, FormComposer):
        return composer
    logger.error(
        "form_composer is required for composable fill; wire via build_application_data"
    )
    return None
