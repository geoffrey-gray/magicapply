"""Greenhouse application handler.

Greenhouse (boards.greenhouse.io / job-boards.greenhouse.io) uses relatively
consistent field names across companies, which is why the plan picks it as
the first ATS. Standard identity fields are filled by ``_fill_static``.
Per-role custom questions — screening prompts, DEI, work-authorization —
are discovered dynamically in ``_fill_dynamic`` via ``FormComposer``.
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

_MATCH_HOSTS = ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io")

_RESUME_FILE_SELECTORS = (
    "input[type='file'][name='resume']",
    "input[type='file'][id*='resume']",
    "input[type='file']",
)

# Reddit inline forms use ``id="application-form"``; hermetic fixture uses plain ``form``.
_GREENHOUSE_FORM_SELECTORS = (
    "form#application-form",
    "form",
)


class GreenhouseHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        return any(host in url.lower() for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        page.goto(data.job_url)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        page.fill("#first_name", _first_name(answers.full_name))
        page.fill("#last_name", _last_name(answers.full_name))
        page.fill("#email", answers.email)
        if answers.phone:
            with contextlib.suppress(Exception):
                page.fill("#phone", answers.phone)
        if answers.linkedin_url:
            # Not every Greenhouse form exposes LinkedIn; never block the flow.
            with contextlib.suppress(Exception):
                page.fill("input[name='linkedin_url']", answers.linkedin_url)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        # Cover letter often goes into a "cover_letter_text" textarea.
        if data.cover_letter:
            # Some Greenhouse forms don't expose a cover letter field.
            with contextlib.suppress(Exception):
                page.fill("textarea[name='cover_letter_text']", data.cover_letter)

        # Resume upload. Greenhouse's file input naming varies across
        # employers; try the most-specific candidate first and fall back
        # to a generic file input. First selector that does not raise wins.
        for selector in _RESUME_FILE_SELECTORS:
            try:
                page.set_input_files(selector, str(data.resume_docx_path))
                break
            except Exception:  # noqa: BLE001
                continue

        fill_dynamic_fields(
            page,
            data,
            ats="greenhouse",
            form_selectors=_GREENHOUSE_FORM_SELECTORS,
            schema_id="greenhouse_application",
            handler_label="Greenhouse",
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("input[type='submit']")


def _first_name(full: str) -> str:
    return full.strip().split()[0] if full.strip() else ""


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""