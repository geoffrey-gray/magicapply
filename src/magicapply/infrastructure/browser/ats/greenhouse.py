"""Greenhouse application handler.

Greenhouse (boards.greenhouse.io / job-boards.greenhouse.io) uses relatively
consistent field names across companies, which is why the plan picks it as the
first ATS. Selectors here target the standard Greenhouse form fields; per-role
custom questions vary and fall through to `_fill_dynamic` which is a TODO stub.

Cover-letter and per-question filling need to iterate over the actual form on
a real posting; that lives in Phase 9 completion work, not this bootstrap.
"""

from __future__ import annotations

import contextlib

from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)

_MATCH_HOSTS = ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io")


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
            page.fill("#phone", answers.phone)
        if answers.linkedin_url:
            page.fill("input[name='linkedin_url']", answers.linkedin_url)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        # Cover letter often goes into a "cover_letter_text" textarea.
        # Per-role custom fields need per-form discovery; stub for now.
        if data.cover_letter:
            # Some Greenhouse forms don't expose a cover letter field.
            with contextlib.suppress(Exception):
                page.fill("textarea[name='cover_letter_text']", data.cover_letter)

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("input[type='submit']")


def _first_name(full: str) -> str:
    return full.strip().split()[0] if full.strip() else ""


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""
