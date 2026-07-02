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

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.captcha import detect_captcha


class PageDriver(Protocol):
    """Minimal browser page surface used by ATS handlers.

    playwright's `Page` satisfies this structurally (goto, fill, click,
    content are all methods on it), so real runs need no adapter.
    """

    def goto(self, url: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def content(self) -> str: ...


class ApplicationData(BaseModel):
    """Everything a handler needs to fill and submit one application."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    job_url: str
    static_answers: StaticAnswers
    tailored_resume: TailoredResume
    cover_letter: str | None = None


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

            captcha = detect_captcha(page.content())
            if captcha:
                return ApplicationResult(
                    state="needs_intervention",
                    error=f"CAPTCHA detected before submit: {captcha}",
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
