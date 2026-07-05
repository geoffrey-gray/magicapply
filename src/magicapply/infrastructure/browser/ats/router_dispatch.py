"""Shared field-scan + router-dispatch loop for every ATS handler.

Each Phase-K handler used to keep its own ``_apply_router`` helper with
essentially identical logic. Per the "extend, don't multiply" GoF note in
``docs/GOF_PATTERNS.md``, all four handlers now call this shared function.
The function also appends every resolution to ``data.resolutions_log`` so
the Template Method's observation-log write (in
``BaseATSHandler.apply``) captures the full record for free.
"""

from __future__ import annotations

import contextlib
import logging

from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData, PageDriver
from magicapply.infrastructure.browser.ats.form_scan import scan_form
from magicapply.infrastructure.browser.ats.observed_form_log import ResolvedField

logger = logging.getLogger(__name__)


def apply_router_to_form(
    page: PageDriver,
    data: ApplicationData,
    *,
    handler_name: str,
    form_selector: str = "form",
) -> None:
    """Walk every field the router recognises, dispatch by strategy,
    append each (field, resolved) pair to ``data.resolutions_log``.

    ``data.answer_router`` must be an ``AnswerRouter`` and ``data.job``
    must be non-None; when either is missing the call is a no-op (older
    tests build ApplicationData without the router). Unhandled fields
    are logged at WARNING level with the handler name for grep-ability
    from real ATS runs.
    """
    router = data.answer_router
    job = data.job
    if not isinstance(router, AnswerRouter) or job is None:
        return

    unhandled: list[str] = []
    for field in scan_form(page, form_selector=form_selector):
        resolved = router.resolve(field, job)
        # Every field seen is recorded, regardless of strategy — the
        # observation log needs the full picture to feed the answer
        # library growth workflow.
        data.resolutions_log.append(ResolvedField(field=field, answer=resolved))

        strategy = resolved.strategy
        if strategy in {"static", "library", "narrative"}:
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
            # Resume upload already fired earlier in the handler; the
            # router-picked value is the same path, so re-uploading
            # would be redundant. Skip.
            continue
        else:
            unhandled.append(field.label)

    if unhandled:
        logger.warning("%s unhandled fields: %s", handler_name, unhandled)
