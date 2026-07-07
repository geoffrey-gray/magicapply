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

from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("jobs.lever.co", "lever.co")

_RESUME_FILE_SELECTORS = (
    "input[name='resume']",
    "input[type='file'][name*='resume']",
    "input[type='file']",
)

_LEVER_FORM_SELECTORS = (
    "form.posting-form",
    "form",
)


class LeverHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        url = data.job_url.rstrip("/")
        if not url.endswith("/apply"):
            url = f"{url}/apply"
        page.goto(url)

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

        fill_dynamic_fields(
            page,
            data,
            ats="lever",
            form_selectors=_LEVER_FORM_SELECTORS,
            schema_id="lever_application",
            handler_label="Lever",
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("button[type='submit']")
