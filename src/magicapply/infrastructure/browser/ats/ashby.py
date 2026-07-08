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

from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("jobs.ashbyhq.com", "ashbyhq.com")

_RESUME_FILE_SELECTORS = (
    "input[name='_systemfield_resume']",
    "input[type='file'][name*='resume']",
    "input[type='file']",
)

_ASHBY_FORM_SELECTORS = (
    "form",
    "[data-testid='application-form']",
    "main",
    "#root",
)


class AshbyHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        url = data.job_url.rstrip("/")
        if not url.endswith("/application"):
            url = f"{url}/application"
        page.goto(url)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        # Fast-fail — Ashby's system-field selectors don't match every
        # tenant's DOM (e.g. TRM Labs uses UUID-scoped input names). The
        # default Playwright 30 s wait per miss piled up to ~120 s of dead
        # time in this method; the composer's scan-fill loop covers the
        # same identity fields on the second pass anyway.
        _fill = _fast_fill
        _fill(page, "input[name='_systemfield_name']", answers.full_name)
        _fill(page, "input[name='_systemfield_email']", answers.email)
        if answers.phone:
            _fill(page, "input[name='_systemfield_phone']", answers.phone)
        if answers.linkedin_url:
            _fill(page, "input[name='_systemfield_linkedin']", answers.linkedin_url)
        if answers.portfolio_url:
            _fill(page, "input[name='_systemfield_website']", answers.portfolio_url)
        if answers.location:
            _fill(page, "input[name='_systemfield_location']", answers.location)

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

        fill_dynamic_fields(
            page,
            data,
            ats="ashby",
            form_selectors=_ASHBY_FORM_SELECTORS,
            schema_id="ashby_application",
            handler_label="Ashby",
        )

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        page.click("button[type='submit']")


_FAST_FILL_TIMEOUT_MS = 500


def _fast_fill(page: PageDriver, selector: str, value: str) -> None:
    """`page.fill(...)` with a 500 ms cap so a missing selector doesn't
    burn Playwright's 30 s default. Silences the miss like the old
    `contextlib.suppress(Exception)` did — the composer's scan-fill loop
    will still handle any identity field with a different DOM name. Falls
    back to a positional call when the page stub rejects the timeout
    kwarg (test-only path)."""
    try:
        page.fill(selector, value, timeout=_FAST_FILL_TIMEOUT_MS)  # type: ignore[call-arg]
    except TypeError:
        with contextlib.suppress(Exception):
            page.fill(selector, value)
    except Exception:  # noqa: BLE001
        pass
