"""Catch-all ATS handler for custom career sites and unknown apply URLs (PR3).

Registered last in ``ATSHandlerFactory`` — matches any URL the big-four handlers
miss. Relies on ``FormComposer`` + ``AnswerRouter`` for label-driven fills
rather than hardcoded platform selectors.
"""

from __future__ import annotations

import contextlib
import logging

from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields

logger = logging.getLogger(__name__)

_GENERIC_FORM_SELECTORS = (
    "form#application-form",
    "form[action*='apply']",
    "form",
    "main form",
)

_RESUME_FILE_SELECTORS = (
    "input[type='file'][name='resume']",
    "input[type='file'][id*='resume']",
    "input[type='file'][name*='cv']",
    "input[type='file']",
)

_SUBMIT_SELECTORS = (
    "input[type='submit']",
    "button[type='submit']",
    "button[name='submit']",
    "input[name='submit']",
    "button[id*='submit']",
    "[data-testid*='submit']",
    "[data-automation-id*='submit']",
)


class GenericHandler(BaseATSHandler):
    """Scan-fill-submit for arbitrary HTML application forms."""

    @classmethod
    def matches(cls, url: str) -> bool:
        # Catch-all — only consulted after Greenhouse, Workday, Lever, Ashby.
        return True

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        page.goto(data.job_url)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        # Identity and screening answers resolve via composable scan + router.
        pass

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        if data.cover_letter:
            for selector in (
                "textarea[name='cover_letter_text']",
                "textarea[name*='cover']",
                "textarea[id*='cover']",
            ):
                with contextlib.suppress(Exception):
                    page.fill(selector, data.cover_letter)
                    break

        _upload_resume(page, data)

        fill_dynamic_fields(
            page,
            data,
            ats="generic",
            form_selectors=_GENERIC_FORM_SELECTORS,
            schema_id="generic_application",
            handler_label="Generic",
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        for selector in _SUBMIT_SELECTORS:
            if _try_click(page, selector):
                return
        msg = "GenericHandler: no submit control matched"
        logger.error(msg)
        raise RuntimeError(msg)


def _upload_resume(page: PageDriver, data: ApplicationData) -> None:
    for selector in _RESUME_FILE_SELECTORS:
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