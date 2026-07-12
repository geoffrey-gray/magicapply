"""LinkedIn Easy Apply handler.

External apply URLs are preferred at resolve time; this handler only runs when
the destination is still ``linkedin.com`` (Easy Apply). If a click leaves
LinkedIn for an employer ATS, re-dispatch via ``ATSHandlerFactory``.
"""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import urlparse

from magicapply.config.ats_recipes import ATSRecipe, cached_recipes, resolve_recipe
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    ApplicationResult,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.generic_wizard import (
    run_pre_steps,
    run_wizard_or_single_page,
    _click_any,
    _try_click,
    _try_fill,
)
from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields
from magicapply.infrastructure.browser.navigate import safe_goto
from magicapply.infrastructure.sources.apply_url import is_job_board_listing_url

logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("linkedin.com",)

_APPLY_CLICK_SELECTORS = (
    "button.jobs-apply-button",
    "button.jobs-apply-button--top-card",
    "button:has-text('Easy Apply')",
    "button:has-text('Apply')",
    "a.jobs-apply-button",
)


class LinkedInHandler(BaseATSHandler):
    """LinkedIn-hosted Easy Apply modal / multi-step form."""

    def __init__(self, *, recipes: dict[str, ATSRecipe] | None = None) -> None:
        self._recipes = recipes if recipes is not None else cached_recipes()

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urlparse(url or "").netloc.lower()
        return any(h in host for h in _MATCH_HOSTS)

    def apply(self, page: PageDriver, data: ApplicationData) -> ApplicationResult:
        if hasattr(page, "set_default_timeout"):
            page.set_default_timeout(15_000)
        try:
            self._navigate(page, data)
            final = getattr(page, "url", None) or data.job_url
            if final and not self.matches(final):
                return self._redispatch(page, data, final)
            orig_nav = self._navigate
            self._navigate = lambda _p, _d: None  # type: ignore[method-assign]
            try:
                return super().apply(page, data.model_copy(update={"job_url": final}))
            finally:
                self._navigate = orig_nav  # type: ignore[method-assign]
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("LinkedInHandler failed for %s: %s", data.job_url, error_msg)
            return ApplicationResult(state="failed", error=error_msg)

    def _redispatch(
        self, page: PageDriver, data: ApplicationData, final_url: str
    ) -> ApplicationResult:
        from magicapply.domain.models.job import Job
        from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
        from magicapply.infrastructure.sources.apply_url import enrich_job_apply_url

        logger.info("LinkedInHandler: redirected off-board → %s", final_url)
        job = data.job
        if isinstance(job, Job):
            enriched = enrich_job_apply_url(job, final_url)
            data = data.model_copy(update={"job": enriched, "job_url": final_url})
        else:
            data = data.model_copy(update={"job_url": final_url})

        handler = ATSHandlerFactory.for_url(final_url)
        if handler is None or isinstance(handler, LinkedInHandler):
            from magicapply.infrastructure.browser.ats.generic import GenericHandler

            handler = GenericHandler(recipes=self._recipes)
        return handler.apply(page, data)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = resolve_recipe(data.job_url, self._recipes)
        safe_goto(page, data.job_url)
        run_pre_steps(page, recipe)
        current = getattr(page, "url", "") or data.job_url
        if is_job_board_listing_url(current):
            _click_any(page, _APPLY_CLICK_SELECTORS, timeout_ms=800)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        for selector, value in (
            ("input[id*='phone']", answers.phone or ""),
            ("input[name*='phone']", answers.phone or ""),
            ("input[type='tel']", answers.phone or ""),
            ("input[id*='email']", answers.email),
            ("input[type='email']", answers.email),
        ):
            if not value:
                continue
            _try_fill(page, selector, value, timeout_ms=500)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = resolve_recipe(data.job_url, self._recipes)
        for selector in recipe.resume_selectors or (
            "input[type='file']",
        ):
            with contextlib.suppress(Exception):
                page.set_input_files(selector, str(data.resume_docx_path))
                break

        def _fill_step(p: PageDriver, d: ApplicationData, r: ATSRecipe) -> None:
            fill_dynamic_fields(
                p,
                d,
                ats="linkedin",
                form_selectors=r.form_selectors,
                schema_id="linkedin_application",
                handler_label="LinkedIn",
            )

        run_wizard_or_single_page(
            page,
            data,
            recipe,
            fill_step=_fill_step,
            upload_resume=lambda p, d, r: None,
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = resolve_recipe(data.job_url, self._recipes)
        for selector in recipe.submit_selectors:
            if _try_click(page, selector, timeout_ms=800):
                return
        for selector in (
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "button[aria-label*='Submit']",
        ):
            if _try_click(page, selector, timeout_ms=800):
                return
        raise RuntimeError("LinkedInHandler: no submit control matched")
