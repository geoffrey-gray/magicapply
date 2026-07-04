"""Lever application handler.

Lever's application form is a single-page ``form.posting-form`` container
with standard HTML input names — no data-automation-id gymnastics like
Workday, no per-role custom-field walkers required to hit the identity
fields. What varies per employer is the set of extra questions in a
``ul.application-additional`` block; those go through the shared
``AnswerRouter``.

Full name lives in **one** input (``input[name='name']``), not first/last
like Greenhouse and Workday. Cover letter is a ``textarea[name='comments']``
when the employer opts into cover letters.
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

_MATCH_HOSTS = ("jobs.lever.co", "lever.co")

_RESUME_FILE_SELECTORS = (
    "input[name='resume']",
    "input[type='file'][name*='resume']",
    "input[type='file']",
)


class LeverHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        page.goto(data.job_url)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        # Lever asks full name in a single field.
        with contextlib.suppress(Exception):
            page.fill("input[name='name']", answers.full_name)
        with contextlib.suppress(Exception):
            page.fill("input[name='email']", answers.email)
        if answers.phone:
            with contextlib.suppress(Exception):
                page.fill("input[name='phone']", answers.phone)
        if answers.linkedin_url:
            with contextlib.suppress(Exception):
                page.fill("input[name='urls[LinkedIn]']", answers.linkedin_url)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        # Cover letter goes into textarea[name='comments'] on Lever.
        if data.cover_letter:
            with contextlib.suppress(Exception):
                page.fill("textarea[name='comments']", data.cover_letter)

        # Resume upload. Candidate list because some employers rename the
        # file input to org-specific values.
        for selector in _RESUME_FILE_SELECTORS:
            try:
                page.set_input_files(selector, str(data.resume_docx_path))
                break
            except Exception:  # noqa: BLE001
                continue

        # Per-role custom fields via the shared router. Lever's
        # ul.application-additional block holds these; the scanner walks
        # the entire form so we do not have to target that container.
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
        logger.warning("Lever unhandled fields: %s", unhandled)
