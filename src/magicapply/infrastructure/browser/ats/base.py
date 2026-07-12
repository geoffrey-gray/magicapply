"""ATSHandler Protocol + BaseATSHandler Template Method.

Strategy pattern: concrete ATSHandlers per platform (Greenhouse, Lever,
Workday, Ashby). Template Method: `apply()` is a concrete invariant sequence;
subclasses override the abstract hooks. See docs/GOF_PATTERNS.md.

A `PageDriver` Protocol abstracts the minimum surface we need from a browser
page — this lets unit tests use a plain fake instead of Playwright. Real
runs pass a `playwright.sync_api.Page` instance; it satisfies the Protocol
structurally.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

_logger = logging.getLogger(__name__)

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.captcha import (
    detect_blocking_captcha,
    detect_captcha,
)

if TYPE_CHECKING:
    from magicapply.config.models import ATSTimeoutsConfig
    from magicapply.domain.models.job import Job
    from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
    from magicapply.infrastructure.browser.ats.workday_accounts import WorkdayAccountStore
    from magicapply.infrastructure.browser.forms.composer import FormComposer


class PageDriver(Protocol):
    """Minimal browser page surface used by ATS handlers.

    playwright's `Page` satisfies this structurally (goto, fill, click,
    content, set_input_files, select_option, check are all methods on it),
    so real runs need no adapter.
    """

    def goto(self, url: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def content(self) -> str: ...
    def set_input_files(self, selector: str, files: str) -> None: ...
    def select_option(self, selector: str, value: str) -> None: ...
    def check(self, selector: str) -> None: ...


class ApplicationData(BaseModel):
    """Everything a handler needs to fill and submit one application.

    ``dry_run`` toggles the pre-submit short-circuit in
    ``BaseATSHandler.apply``: when True, the template method navigates and
    fills every field but stops one click short of the submit button and
    reports success with a marker error. The pipeline copies the flag onto
    the Application row so a downstream ``status`` split can distinguish
    real applications from dry runs.

    ``resume_docx_path`` points at the rendered DOCX the tailoring pipeline
    produced (see ``TailoringPipeline`` + ``DocxResumeRenderer``); handlers
    call ``page.set_input_files`` with it against whichever file input the
    ATS exposes.

    ``answer_router`` (when present) drives Phase L per-form field
    discovery — the handler scans the DOM, hands each field to
    ``AnswerRouter.resolve`` alongside the Job, and dispatches the
    resulting strategy. When None, handlers fall back to their historical
    fixed selector list.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    job_url: str
    static_answers: StaticAnswers
    tailored_resume: TailoredResume
    resume_docx_path: Path
    cover_letter: str | None = None
    dry_run: bool = False
    # Phase L: answer router for dynamic field discovery
    # Type checkers see AnswerRouter via TYPE_CHECKING; Pydantic sees object.
    answer_router: object | None = None
    # Optional per-application Job — the answer router needs it to hand
    # screening questions to NarrativeEngine.answer. Kept optional so
    # simpler tests don't have to construct one.
    job: object | None = None
    # W.3: mutable list handlers append to as they resolve form fields.
    # BaseATSHandler.apply persists the full observation in one shot
    # after _fill_dynamic — Template-Method extension of a cross-cutting
    # concern (see docs/GOF_PATTERNS.md "extend, don't multiply").
    resolutions_log: list = Field(default_factory=list)
    # W.3: destination for the observed-form yaml + answer_proposals.yaml.
    # None → observation logging is skipped (unit tests without a real
    # data dir).
    data_dir: Path | None = None
    # W.4b: per-tenant Workday apply credentials (data/workday_accounts.yaml).
    workday_account_store: object | None = None
    # CF.1: composable form orchestrator (FormComposer); optional opaque slot.
    form_composer: object | None = None
    # CF.2: configurable timeouts per ATS (workday, greenhouse, etc.)
    ats_timeouts: object | None = None


class ApplicationResult(BaseModel):
    """Outcome of a single application attempt."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["applied", "needs_intervention", "failed"]
    error: str | None = None
    submitted_url: str | None = None


class ATSHandler(Protocol):
    """One handler per ATS platform."""

    @classmethod
    def matches(cls, url: str) -> bool: ...

    def apply(self, page: PageDriver, data: ApplicationData) -> ApplicationResult: ...


class BaseATSHandler:
    """Template Method for the invariant application flow.

    Subclasses override the abstract hooks:
        navigate → fill_static → fill_dynamic → submit → verify

    Blocking CAPTCHA detection runs after navigate (interstitial pages only).
    Widget detection runs after fill_dynamic, only before a real submit — dry-
    run skips it so dormant invisible reCAPTCHA on Greenhouse forms does not
    block the fill-to-Submit-button path. Any exception surfaced by the
    subclass is captured as `state="failed"`.
    """

    @classmethod
    def matches(cls, url: str) -> bool:
        raise NotImplementedError("subclass must override matches")

    def apply(self, page: PageDriver, data: ApplicationData) -> ApplicationResult:
        import os
        import time as _time
        _trace = os.environ.get("MAGICAPPLY_TRACE_COMPOSER") == "1"
        _t0 = _time.monotonic()

        def _step(label: str) -> None:
            if _trace:
                _logger.warning("[trace] step=%s at %.2fs", label, _time.monotonic() - _t0)

        try:
            _step("navigate.start")
            self._navigate(page, data)
            _step("navigate.done")
            blocking = detect_blocking_captcha(page.content())
            if blocking:
                return ApplicationResult(
                    state="needs_intervention",
                    error=f"CAPTCHA detected on load: {blocking}",
                )
            _step("fill_static.start")
            self._fill_static(page, data)
            _step("fill_static.done")
            _step("fill_dynamic.start")
            self._fill_dynamic(page, data)
            _step("fill_dynamic.done")

            # W.3: persist a per-form observation record. Template Method
            # invariant — every ATS handler inherits this without
            # duplicating the call in each _fill_dynamic. Handlers
            # accumulate resolutions into data.resolutions_log; we write
            # the yaml once + append narrative/unhandled entries to
            # data/answer_proposals.yaml.
            if data.data_dir is not None and data.resolutions_log:
                _step("log_observed_form.start")
                from magicapply.infrastructure.browser.ats.observed_form_log import (
                    log_observed_form,
                )

                job = data.job
                job_url = getattr(job, "url", data.job_url)
                app_id = getattr(job, "id", "unknown")
                log_observed_form(
                    app_id=app_id,
                    job_url=job_url,
                    resolutions=data.resolutions_log,
                    data_dir=data.data_dir,
                    page=page,
                )
                _step("log_observed_form.done")

            if not data.dry_run:
                captcha = detect_captcha(page.content())
                if captcha:
                    return ApplicationResult(
                        state="needs_intervention",
                        error=f"CAPTCHA detected before submit: {captcha}",
                    )

            if data.dry_run:
                # Full application short of the click. Same terminal shape as
                # a real submit; the Application row's dry_run flag is what
                # distinguishes downstream reporting.
                return ApplicationResult(
                    state="applied",
                    submitted_url=getattr(page, "url", None),
                    error="dry-run: submit skipped",
                )

            self._submit(page, data)
            return self._verify(page, data)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            _logger.error(
                "ATS handler failed for %s: %s",
                getattr(data, "job_url", "unknown"),
                error_msg,
                exc_info=True,
            )
            return ApplicationResult(state="failed", error=error_msg)

    # ---- Abstract hooks (must be overridden) ----

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        raise NotImplementedError

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        raise NotImplementedError

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        raise NotImplementedError

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        raise NotImplementedError

    def _verify(self, page: PageDriver, data: ApplicationData) -> ApplicationResult:
        return ApplicationResult(
            state="applied",
            submitted_url=getattr(page, "url", None),
        )
