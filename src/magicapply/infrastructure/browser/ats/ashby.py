"""Ashby application handler.

Ashby (jobs.ashbyhq.com) uses a React-rendered form with system fields
named ``_systemfield_<key>``. The system fields are the identity block
(name, email, phone, LinkedIn, website, location, resume). Employer
custom questions get their own field names but the ``AnswerRouter`` from
Phase L walks them regardless. Single-page form; submit is a plain
``button[type='submit']`` inside the application container.
"""

from __future__ import annotations

import contextlib
import logging

from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.form_scan import scan_form

logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("jobs.ashbyhq.com", "ashbyhq.com")

_RESUME_FILE_SELECTORS = (
    "input[name='_systemfield_resume']",
    "input[type='file'][name*='resume']",
    "input[type='file']",
)


class AshbyHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        page.goto(data.job_url)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        # Ashby also uses a single full-name field.
        with contextlib.suppress(Exception):
            page.fill("input[name='_systemfield_name']", answers.full_name)
        with contextlib.suppress(Exception):
            page.fill("input[name='_systemfield_email']", answers.email)
        if answers.phone:
            with contextlib.suppress(Exception):
                page.fill("input[name='_systemfield_phone']", answers.phone)
        if answers.linkedin_url:
            with contextlib.suppress(Exception):
                page.fill("input[name='_systemfield_linkedin']", answers.linkedin_url)
        if answers.portfolio_url:
            with contextlib.suppress(Exception):
                page.fill("input[name='_systemfield_website']", answers.portfolio_url)
        if answers.location:
            with contextlib.suppress(Exception):
                page.fill("input[name='_systemfield_location']", answers.location)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        # Resume upload. Ashby's real file input is hidden behind a
        # drop-zone widget; the actual <input type="file"> still exists
        # in the DOM and set_input_files works on it. Try the
        # system-field name first, then generic fallbacks.
        for selector in _RESUME_FILE_SELECTORS:
            try:
                page.set_input_files(selector, str(data.resume_docx_path))
                break
            except Exception:  # noqa: BLE001
                continue

        # Custom questions via the shared router.
        router = data.answer_router
        job = data.job
        if isinstance(router, AnswerRouter) and job is not None:
            _apply_router(page, router, job)

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("button[type='submit']")


def _apply_router(page: PageDriver, router: AnswerRouter, job: object) -> None:
    unhandled: list[str] = []
    for field in scan_form(page):
        resolved = router.resolve(field, job)
        strategy = resolved.strategy
        if strategy in {"static", "narrative"}:
            with contextlib.suppress(Exception):
                page.fill(field.selector, resolved.value)
        elif strategy == "select":
            with contextlib.suppress(Exception):
                page.select_option(field.selector, resolved.value)
        elif strategy == "check":
            if resolved.check:
                with contextlib.suppress(Exception):
                    page.check(field.selector)
        elif strategy == "file":
            continue
        else:
            unhandled.append(field.label)
    if unhandled:
        logger.warning("Ashby unhandled fields: %s", unhandled)
