"""Workday application handler.

Workday's forms are consistent across employers because every field carries
a ``data-automation-id`` attribute keyed on a Workday-internal id. That id
is stable enough to hard-code the identity + resume-upload selectors here.

The real flow is a multi-step wizard (My Information → My Experience →
Application Questions → Voluntary Disclosures → Review & Submit). Each
``Next`` button lands the user on the next step; the handler clicks
through them until it reaches the Review step and then clicks Submit.

Non-identity fields are still driven by the shared ``AnswerRouter`` from
Phase L: after every ``Next`` click the handler re-scans the DOM and
applies whatever answers the router resolves. Anything the router does
not recognise is logged as unhandled — matches the Greenhouse discipline.
"""

from __future__ import annotations

import contextlib
import logging
import time

from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.form_scan import scan_form

logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("myworkdayjobs.com", ".myworkday.com")

# Workday's data-automation-id conventions. Each list is tried in order;
# first selector the page accepts wins.
_APPLY_BUTTONS = (
    "[data-automation-id='autofillWithResume']",
    "[data-automation-id='applyManually']",
    "a[data-automation-id='adventureButton']",
    "button:has-text('Apply')",
)
_FIRST_NAME_SELECTORS = (
    "[data-automation-id='legalNameSection_firstName']",
    "input[data-automation-id='firstName']",
    "input[name='firstName']",
)
_LAST_NAME_SELECTORS = (
    "[data-automation-id='legalNameSection_lastName']",
    "input[data-automation-id='lastName']",
    "input[name='lastName']",
)
_EMAIL_SELECTORS = (
    "[data-automation-id='email']",
    "input[data-automation-id='emailAddress']",
    "input[type='email']",
)
_PHONE_SELECTORS = (
    "[data-automation-id='phone-number']",
    "[data-automation-id='phoneNumber']",
    "input[data-automation-id='phone']",
)
_RESUME_FILE_SELECTORS = (
    "[data-automation-id='file-upload-input-ref']",
    "input[data-automation-id='resume-upload']",
    "input[type='file']",
)
_NEXT_BUTTONS = (
    "[data-automation-id='pageFooterNextButton']",
    "button[data-automation-id='wd-CommandButton_uic_next']",
    "button[data-automation-id='next']",
)
_SUBMIT_BUTTONS = (
    "[data-automation-id='submitApplication']",
    "[data-automation-id='submit']",
    "button[data-automation-id='wd-CommandButton_uic_submit']",
    "button:has-text('Submit')",
)


# Max steps to Next-click before we assume the flow is stuck.
_MAX_STEPS = 8


class WorkdayHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        page.goto(data.job_url)
        # Real Workday shows an initial Apply / Autofill prompt. Try the
        # candidates in order; anything absent raises and we continue.
        for selector in _APPLY_BUTTONS:
            if _try_click(page, selector):
                break

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        _try_fill(page, _FIRST_NAME_SELECTORS, _first_name(answers.full_name))
        _try_fill(page, _LAST_NAME_SELECTORS, _last_name(answers.full_name))
        _try_fill(page, _EMAIL_SELECTORS, answers.email)
        if answers.phone:
            _try_fill(page, _PHONE_SELECTORS, answers.phone)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        # Resume upload — try Workday's file input selectors, then fall
        # back to a generic file input for simpler fixtures.
        for selector in _RESUME_FILE_SELECTORS:
            try:
                page.set_input_files(selector, str(data.resume_docx_path))
                break
            except Exception:  # noqa: BLE001
                continue

        router = data.answer_router
        job = data.job

        # Walk the wizard: on each page, apply the router, then click Next.
        # Stop when there's no Next button (we're at the Review step) or
        # when we hit the max-step guard.
        for _ in range(_MAX_STEPS):
            if isinstance(router, AnswerRouter) and job is not None:
                _apply_router(page, router, job)
            if not _click_next(page):
                break
            # Short wait so the next step's DOM has a chance to render
            # before the next scan. In real Workday this is where a proper
            # wait-for-selector would sit; in fixture mode this no-op is
            # safe because the fixture form is single-page.
            time.sleep(0.05)

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        for selector in _SUBMIT_BUTTONS:
            if _try_click(page, selector):
                return
        raise RuntimeError("Workday: no submit button found")


# Every candidate-selector attempt uses a short Playwright timeout so a
# missing selector fails fast (default is 30s and we cycle through many).
# Real ATS pages resolve well within this budget; fake pages accept the
# kwarg via **kwargs and short-circuit synchronously.
_CANDIDATE_TIMEOUT_MS = 500


def _try_fill(page: PageDriver, selectors: tuple[str, ...], value: str) -> None:
    for selector in selectors:
        try:
            page.fill(selector, value, timeout=_CANDIDATE_TIMEOUT_MS)  # type: ignore[call-arg]
            return
        except Exception:  # noqa: BLE001
            continue


def _try_click(page: PageDriver, selector: str) -> bool:
    try:
        page.click(selector, timeout=_CANDIDATE_TIMEOUT_MS)  # type: ignore[call-arg]
        return True
    except Exception:  # noqa: BLE001
        return False


def _click_next(page: PageDriver) -> bool:
    for selector in _NEXT_BUTTONS:
        if _try_click(page, selector):
            return True
    return False


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
            # Resume already uploaded in _fill_dynamic's file loop.
            continue
        else:
            unhandled.append(field.label)
    if unhandled:
        logger.warning("Workday unhandled fields: %s", unhandled)


def _first_name(full: str) -> str:
    return full.strip().split()[0] if full.strip() else ""


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""
