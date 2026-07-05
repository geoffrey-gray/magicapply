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

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.captcha import detect_captcha


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
    # AnswerRouter is intentionally not typed here to avoid a circular
    # import at module load; the handler does the isinstance check.
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

    CAPTCHA detection runs after navigate and after fill_dynamic. Any exception
    surfaced by the subclass is captured as `state="failed"`.
    """

    @classmethod
    def matches(cls, url: str) -> bool:
        raise NotImplementedError("subclass must override matches")

    def apply(self, page: PageDriver, data: ApplicationData) -> ApplicationResult:
        try:
            self._navigate(page, data)
            captcha = detect_captcha(page.content())
            if captcha:
                return ApplicationResult(
                    state="needs_intervention",
                    error=f"CAPTCHA detected on load: {captcha}",
                )

            self._fill_static(page, data)
            self._fill_dynamic(page, data)

            # W.3: persist a per-form observation record. Template Method
            # invariant — every ATS handler inherits this without
            # duplicating the call in each _fill_dynamic. Handlers
            # accumulate resolutions into data.resolutions_log; we write
            # the yaml once + append narrative/unhandled entries to
            # data/answer_proposals.yaml.
            if data.data_dir is not None and data.resolutions_log:
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
                )

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
            return ApplicationResult(state="failed", error=f"{type(exc).__name__}: {exc}")

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
