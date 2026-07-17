"""Indeed Easy Apply / applystart handler.

Navigates to applystart (when known) or the viewjob Apply CTA. If Indeed
redirects off-site to an employer ATS, re-dispatches via ``ATSHandlerFactory``.
Otherwise fills the Indeed-hosted application form via FormComposer + recipe.
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
from magicapply.infrastructure.sources.apply_url import (
    indeed_apply_entry_url,
    is_job_board_listing_url,
)

logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("indeed.com",)

_APPLY_CLICK_SELECTORS = (
    "a[href*='applystart']",
    "button[data-indeed-apply-button]",
    "a[data-indeed-apply-button]",
    "#indeedApplyButton",
    "button.ia-IndeedApplyButton",
    "a.ia-IndeedApplyButton",
    "#applyButtonLinkContainer a",
    "button:has-text('Apply now')",
    "a:has-text('Apply now')",
    "button:has-text('Continue')",
)

_INDEED_APPLY_FORM_SELECTORS = (
    "form#ia-container",
    "form.ia-BasePage-card",
    "div[data-testid='ia-BaseForm'] form",
    "form.ia-Form",
    "div.ia-BasePage form",
)


class IndeedHandler(BaseATSHandler):
    """Indeed-hosted Easy Apply and applystart entry points."""

    def __init__(self, *, recipes: dict[str, ATSRecipe] | None = None) -> None:
        self._recipes = recipes if recipes is not None else cached_recipes()

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urlparse(url or "").netloc.lower()
        return any(h in host for h in _MATCH_HOSTS)

    def apply(self, page: PageDriver, data: ApplicationData) -> ApplicationResult:
        """Navigate, then re-dispatch if Indeed redirected to an external ATS."""
        if hasattr(page, "set_default_timeout"):
            page.set_default_timeout(15_000)
        try:
            self._navigate(page, data)
            final = getattr(page, "url", None) or data.job_url
            if final and not self.matches(final):
                return self._redispatch(page, data, final)
            # Already on Indeed form — continue Template Method without re-nav.
            orig_nav = self._navigate
            self._navigate = lambda _p, _d: None  # type: ignore[method-assign]
            try:
                return super().apply(page, data.model_copy(update={"job_url": final}))
            finally:
                self._navigate = orig_nav  # type: ignore[method-assign]
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("IndeedHandler failed for %s: %s", data.job_url, error_msg)
            return ApplicationResult(state="failed", error=error_msg)

    def _redispatch(
        self, page: PageDriver, data: ApplicationData, final_url: str
    ) -> ApplicationResult:
        from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
        from magicapply.domain.models.job import Job
        from magicapply.infrastructure.sources.apply_url import enrich_job_apply_url

        logger.info("IndeedHandler: redirected off-board → %s", final_url)
        job = data.job
        if isinstance(job, Job):
            enriched = enrich_job_apply_url(job, final_url)
            data = data.model_copy(update={"job": enriched, "job_url": final_url})
        else:
            data = data.model_copy(update={"job_url": final_url})

        handler = ATSHandlerFactory.for_url(final_url)
        if handler is None or isinstance(handler, IndeedHandler):
            from magicapply.infrastructure.browser.ats.generic import GenericHandler

            handler = GenericHandler(recipes=self._recipes)
        return handler.apply(page, data)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = resolve_recipe(data.job_url, self._recipes)
        job = data.job
        entry = None
        if job is not None:
            entry = indeed_apply_entry_url(job)  # type: ignore[arg-type]
        target = entry or data.job_url
        safe_goto(page, target)
        run_pre_steps(page, recipe)
        # If still on viewjob (not applystart), click Apply.
        current = getattr(page, "url", "") or target
        if "applystart" not in current.lower() and is_job_board_listing_url(current):
            _click_any(page, _APPLY_CLICK_SELECTORS, timeout_ms=800)
        _wait_for_indeed_apply_form(page)
        final = getattr(page, "url", "") or current
        # Enforce IA form on applystart; also catch auth walls.
        # Listing pages often redirect offsite after Apply — leave that to redispatch.
        if self.matches(final) and (
            "applystart" in final.lower()
            or "secure.indeed.com/auth" in final.lower()
            or "/account/login" in final.lower()
        ):
            _ensure_indeed_apply_form(page)
        elif self.matches(final) and is_job_board_listing_url(final):
            _click_any(page, _APPLY_CLICK_SELECTORS, timeout_ms=1200)
            _wait_for_indeed_apply_form(page)
            final = getattr(page, "url", "") or final
            if self.matches(final) and (
                "applystart" in final.lower()
                or "secure.indeed.com/auth" in final.lower()
            ):
                _ensure_indeed_apply_form(page)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        for selector, value in (
            ("input[name='name']", answers.full_name),
            ("input[id*='name' i]", answers.full_name),
            ("input[name='email']", answers.email),
            ("input[type='email']", answers.email),
            ("input[name='phone']", answers.phone or ""),
            ("input[type='tel']", answers.phone or ""),
        ):
            if not value:
                continue
            _try_fill(page, selector, value, timeout_ms=500)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        recipe = resolve_recipe(data.job_url, self._recipes)
        # Prefer iframe content if Easy Apply embeds a form.
        self._maybe_switch_iframe(page)
        for selector in recipe.resume_selectors or (
            "input[type='file'][name*='resume']",
            "input[type='file']",
        ):
            with contextlib.suppress(Exception):
                page.set_input_files(selector, str(data.resume_docx_path))
                break

        def _fill_step(p: PageDriver, d: ApplicationData, r: ATSRecipe) -> None:
            fill_dynamic_fields(
                p,
                d,
                ats="indeed",
                form_selectors=r.form_selectors,
                schema_id="indeed_application",
                handler_label="Indeed",
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
        raise RuntimeError("IndeedHandler: no submit control matched")

    def _maybe_switch_iframe(self, page: PageDriver) -> None:
        """Best-effort: if Playwright exposes frame_locator, prefer apply iframe."""
        frame_locator = getattr(page, "frame_locator", None)
        if frame_locator is None:
            return
        for sel in (
            "iframe[src*='indeed']",
            "iframe[id*='indeed']",
            "iframe[name*='indeed']",
            "iframe",
        ):
            with contextlib.suppress(Exception):
                # Presence check only — FormComposer still uses the top page;
                # real iframe fills need a richer PageDriver. Pre-steps click
                # is enough to surface the form in many flows.
                frame_locator(sel)
                break


def _wait_briefly(page: PageDriver, timeout_ms: int) -> None:
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        with contextlib.suppress(Exception):
            wait(timeout_ms)


def _wait_for_indeed_apply_form(page: PageDriver, timeout_ms: int = 5000) -> None:
    """Wait for Indeed Apply / IA form after navigation or CTA click."""
    wait_for = getattr(page, "wait_for_selector", None)
    if callable(wait_for):
        for selector in _INDEED_APPLY_FORM_SELECTORS:
            with contextlib.suppress(Exception):
                wait_for(selector, timeout=timeout_ms)
                return
    _wait_briefly(page, min(timeout_ms, 2000))


def _ensure_indeed_apply_form(page: PageDriver) -> None:
    """Fail loud when login wall / missing Apply form instead of false APPLIED."""
    for selector in _INDEED_APPLY_FORM_SELECTORS:
        if _indeed_form_present(page, selector):
            return
    url = (getattr(page, "url", "") or "").lower()
    # Only treat URL hosts/paths as login walls — footer "Sign in" copy is
    # present on authenticated Indeed pages and must not false-positive.
    if (
        "secure.indeed.com/auth" in url
        or "secure.indeed.com/account" in url
        or "/account/login" in url
        or "/oauth" in url
    ):
        raise RuntimeError(
            "Indeed: login wall / not authenticated — Apply form missing"
        )
    raise RuntimeError(
        "Indeed: Apply form not found (auth expired, CAPTCHA, or offsite-only apply)"
    )


def _indeed_form_present(page: PageDriver, selector: str) -> bool:
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            return locator(selector).first.count() > 0
        except Exception:  # noqa: BLE001
            return False
    with contextlib.suppress(Exception):
        html = (page.content() or "").lower()
        return any(
            m in html
            for m in ("ia-container", "ia-basepage", "ia-baseform", "ia-form", "indeedapply")
        )
    return False

