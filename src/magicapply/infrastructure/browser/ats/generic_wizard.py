"""Multi-step wizard loop for GenericHandler (custom ATS / PR4)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from magicapply.config.ats_recipes import ATSRecipe
from magicapply.infrastructure.browser.ats.base import ApplicationData, PageDriver

logger = logging.getLogger(__name__)

_CANDIDATE_TIMEOUT_MS = 500


def run_wizard_or_single_page(
    page: PageDriver,
    data: ApplicationData,
    recipe: ATSRecipe,
    *,
    fill_step: Callable[[PageDriver, ApplicationData, ATSRecipe], None],
    upload_resume: Callable[[PageDriver, ApplicationData, ATSRecipe], None],
) -> None:
    """Scan-fill each wizard step, clicking Next until review/submit is visible."""
    wizard = recipe.wizard
    if wizard is None:
        upload_resume(page, data, recipe)
        fill_step(page, data, recipe)
        return

    for step_idx in range(wizard.max_steps):
        upload_resume(page, data, recipe)
        fill_step(page, data, recipe)
        if _at_review_step(page, wizard.review_selectors):
            logger.info("Generic wizard: review/submit step reached at %d", step_idx)
            break
        if not _click_any(page, wizard.next_selectors):
            logger.info("Generic wizard: no Next control at step %d — stopping", step_idx)
            break
        _wait_brief(page, 250)


def run_pre_steps(page: PageDriver, recipe: ATSRecipe) -> None:
    for step in recipe.pre_steps:
        if step.action == "wait":
            _wait_brief(page, step.timeout_ms)
            continue
        _click_any(page, (step.selector,), timeout_ms=step.timeout_ms)


def _at_review_step(page: PageDriver, review_selectors: tuple[str, ...]) -> bool:
    html = page.content().lower()
    if "review" in html and "submit" in html:
        return True
    for selector in review_selectors:
        if _selector_likely_present(page, selector):
            return True
    return False


def _selector_likely_present(page: PageDriver, selector: str) -> bool:
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            return locator(selector).first.count() > 0
        except Exception:  # noqa: BLE001
            return False
    html = page.content()
    if selector == "button[type='submit']":
        return "type=\"submit\"" in html or "type='submit'" in html
    if ":has-text('Submit')" in selector:
        return ">submit<" in html.lower() or "submit application" in html.lower()
    if selector.startswith("#"):
        return f'id="{selector[1:]}"' in html or f"id='{selector[1:]}'" in html
    return selector in html


def _click_any(
    page: PageDriver,
    selectors: tuple[str, ...],
    *,
    timeout_ms: int = _CANDIDATE_TIMEOUT_MS,
) -> bool:
    for selector in selectors:
        if _try_click(page, selector, timeout_ms=timeout_ms):
            return True
    return False


def _try_click(page: PageDriver, selector: str, *, timeout_ms: int) -> bool:
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            locator(selector).first.click(timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        page.click(selector)
        return True
    except Exception:  # noqa: BLE001
        return False


def _try_fill(
    page: PageDriver, selector: str, value: str, *, timeout_ms: int
) -> bool:
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            locator(selector).first.fill(value, timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        page.fill(selector, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_brief(page: PageDriver, ms: int) -> None:
    wait_fn = getattr(page, "wait_for_timeout", None)
    if callable(wait_fn):
        wait_fn(ms)
        return
    time.sleep(ms / 1000.0)