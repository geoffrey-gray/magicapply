"""Greenhouse application handler.

Greenhouse (boards.greenhouse.io / job-boards.greenhouse.io) uses relatively
consistent field names across companies, which is why the plan picks it as
the first ATS. Standard identity fields are filled by ``_fill_static``.
Per-role custom questions — screening prompts, DEI, work-authorization —
are discovered dynamically in ``_fill_dynamic`` via the form scanner +
AnswerRouter (Phase L) when the ApplicationData carries one; otherwise
the handler falls back to the cover-letter + resume upload path from
Phase K.
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

_MATCH_HOSTS = ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io")

_RESUME_FILE_SELECTORS = (
    "input[type='file'][name='resume']",
    "input[type='file'][id*='resume']",
    "input[type='file']",
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
            page.fill("#phone", answers.phone)
        if answers.linkedin_url:
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

        # Per-role custom fields — walk the form and let the AnswerRouter
        # decide how to fill each. When no router is attached (older
        # composition, unit tests) we skip this step and rely on
        # _fill_static + the cover letter / resume calls above.
        router = data.answer_router
        job = data.job
        if isinstance(router, AnswerRouter) and job is not None:
            _apply_router(page, router, job)

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("input[type='submit']")


def _apply_router(page: PageDriver, router: AnswerRouter, job: object) -> None:
    fields = scan_form(page)
    unhandled: list[str] = []
    for field in fields:
        resolved = router.resolve(field, job)
        if resolved.strategy in {"static", "narrative"}:
            with contextlib.suppress(Exception):
                page.fill(field.selector, resolved.value)
        elif resolved.strategy == "select":
            with contextlib.suppress(Exception):
                page.select_option(field.selector, resolved.value)
        elif resolved.strategy == "check":
            if resolved.check:
                with contextlib.suppress(Exception):
                    page.check(field.selector)
        elif resolved.strategy == "file":
            # Resume upload already happened above; the router-picked
            # value is the same path, so re-uploading is redundant but
            # not harmful. Skip to avoid a second network round-trip.
            continue
        else:
            unhandled.append(field.label)
    if unhandled:
        logger.warning("Greenhouse unhandled fields: %s", unhandled)


def _first_name(full: str) -> str:
    return full.strip().split()[0] if full.strip() else ""


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""
