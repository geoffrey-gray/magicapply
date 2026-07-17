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
from magicapply.infrastructure.browser.navigate import linkedin_same_origin_goto
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

_EASY_APPLY_FORM_SELECTORS = (
    "form.jobs-easy-apply-form",
    "div.jobs-easy-apply-content form",
    ".jobs-easy-apply-modal form",
    "form.jobs-easy-apply-content__form",
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
            if final and (
                final.startswith("chrome-error:")
                or final.startswith("chrome://")
                or "chromewebdata" in final
            ):
                raise RuntimeError(
                    f"LinkedIn navigation failed (browser error page): {final}"
                )
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
        # Warm feed/jobs hub then same-origin assign — direct page.goto to
        # /jobs/view/… self-302 loops even with a valid jar (li_at present).
        linkedin_same_origin_goto(page, data.job_url)
        run_pre_steps(page, recipe)
        current = getattr(page, "url", "") or data.job_url
        if is_job_board_listing_url(current):
            # Prefer Easy Apply CTA before generic Apply (offsite).
            _click_any(
                page,
                (
                    "button:has-text('Easy Apply')",
                    "button.jobs-apply-button--top-card",
                    "button.jobs-apply-button",
                    "button:has-text('Apply')",
                    "a.jobs-apply-button",
                ),
                timeout_ms=2000,
            )
        _wait_for_easy_apply_form(page)
        final = getattr(page, "url", "") or current
        host = urlparse(final).netloc.lower()
        if not any(h in host for h in _MATCH_HOSTS):
            # Offsite Apply left LinkedIn — apply() will redispatch.
            return
        _ensure_easy_apply_form(page)

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


def _wait_briefly(page: PageDriver, timeout_ms: int) -> None:
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        with contextlib.suppress(Exception):
            wait(timeout_ms)


def _wait_for_easy_apply_form(page: PageDriver, timeout_ms: int = 5000) -> None:
    """Wait for Easy Apply modal after CTA click (headed UI paint)."""
    wait_for = getattr(page, "wait_for_selector", None)
    if callable(wait_for):
        for selector in _EASY_APPLY_FORM_SELECTORS:
            with contextlib.suppress(Exception):
                wait_for(selector, timeout=timeout_ms)
                return
    _wait_briefly(page, min(timeout_ms, 2000))


def _ensure_easy_apply_form(page: PageDriver) -> None:
    """Fail loud when guest/search chrome is present instead of Easy Apply."""
    for selector in _EASY_APPLY_FORM_SELECTORS:
        if _easy_apply_present(page, selector):
            return
    html = ""
    with contextlib.suppress(Exception):
        html = (page.content() or "").lower()
    if "jobs-guest" in html or "d_jobs_guest" in html:
        raise RuntimeError(
            "LinkedIn: guest page / not authenticated — Easy Apply form missing"
        )
    raise RuntimeError(
        "LinkedIn: Easy Apply form not found (auth expired or offsite-only apply)"
    )


def _easy_apply_present(page: PageDriver, selector: str) -> bool:
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            return locator(selector).first.count() > 0
        except Exception:  # noqa: BLE001
            return False
    with contextlib.suppress(Exception):
        html = page.content() or ""
        if "jobs-easy-apply-form" in selector and "jobs-easy-apply-form" in html:
            return True
        if "jobs-easy-apply-content" in selector and "jobs-easy-apply-content" in html:
            return True
        if "jobs-easy-apply-modal" in selector and "jobs-easy-apply-modal" in html:
            return True
    return False
