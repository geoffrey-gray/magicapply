"""Catch-all ATS handler for custom career sites and unknown apply URLs (PR3/PR4).

Registered last in ``ATSHandlerFactory`` — matches any URL the big-four handlers
miss. Relies on ``FormComposer`` + ``AnswerRouter`` for label-driven fills
rather than hardcoded platform selectors. Multi-step flows use YAML recipes
(``configs/ats_recipes/``) and the wizard loop in ``generic_wizard``.
"""

from __future__ import annotations

import contextlib
import logging

from magicapply.config.ats_recipes import ATSRecipe, cached_recipes, resolve_navigation_url, resolve_recipe
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.generic_wizard import run_pre_steps, run_wizard_or_single_page
from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields

logger = logging.getLogger(__name__)


class GenericHandler(BaseATSHandler):
    """Scan-fill-submit for arbitrary HTML application forms."""

    def __init__(self, *, recipes: dict[str, ATSRecipe] | None = None) -> None:
        self._recipes = recipes if recipes is not None else cached_recipes()

    @classmethod
    def matches(cls, url: str) -> bool:
        # Catch-all — only consulted after Greenhouse, Workday, Lever, Ashby.
        return True

    def _recipe_for(self, url: str) -> ATSRecipe:
        return resolve_recipe(url, self._recipes)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = self._recipe_for(data.job_url)
        target = resolve_navigation_url(data.job_url, recipe)
        page.goto(target)
        run_pre_steps(page, recipe)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        # Identity and screening answers resolve via composable scan + router.
        pass

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = self._recipe_for(data.job_url)
        if data.cover_letter:
            for selector in (
                "textarea[name='cover_letter_text']",
                "textarea[name*='cover']",
                "textarea[id*='cover']",
            ):
                with contextlib.suppress(Exception):
                    page.fill(selector, data.cover_letter)
                    break

        run_wizard_or_single_page(
            page,
            data,
            recipe,
            fill_step=_fill_scanned_step,
            upload_resume=_upload_resume,
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = self._recipe_for(data.job_url)
        for selector in recipe.submit_selectors:
            if _try_click(page, selector):
                return
        msg = "GenericHandler: no submit control matched"
        logger.error(msg)
        raise RuntimeError(msg)


def _fill_scanned_step(page: PageDriver, data: ApplicationData, recipe: ATSRecipe) -> None:
    fill_dynamic_fields(
        page,
        data,
        ats="generic",
        form_selectors=recipe.form_selectors,
        schema_id=f"{recipe.platform}_application",
        handler_label="Generic",
    )


def _upload_resume(page: PageDriver, data: ApplicationData, recipe: ATSRecipe) -> None:
    for selector in recipe.resume_selectors:
        try:
            page.set_input_files(selector, str(data.resume_docx_path))
            return
        except Exception:  # noqa: BLE001
            continue


def _try_click(page: PageDriver, selector: str) -> bool:
    try:
        page.click(selector)
        return True
    except Exception:  # noqa: BLE001
        return False