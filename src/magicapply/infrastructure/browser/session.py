"""Playwright browser session lifecycle.

Callers (the CLI ``apply``/``run`` commands) open one PlaywrightSession per
command invocation, get one Page per Application from ``new_page()``, and
close the whole session on exit. Storage state (cookies, localStorage) is
optional and, when a path is supplied, is loaded on enter and saved on exit
so repeat runs keep any authenticated sessions the operator established
during CAPTCHA intervention.

Kept minimal on purpose: this file only owns Playwright's process/browser/
context tree. ATS-specific navigation and form filling live in
``infrastructure/browser/ats/`` behind the PageDriver Protocol.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)


class PlaywrightSession(AbstractContextManager["PlaywrightSession"]):
    """One Chromium browser + context, shared across an apply batch."""

    def __init__(
        self,
        *,
        headless: bool = True,
        storage_state_path: Path | None = None,
    ) -> None:
        self._headless = headless
        self._storage_state_path = storage_state_path
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> PlaywrightSession:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        storage_state: str | None = None
        if (
            self._storage_state_path is not None
            and self._storage_state_path.exists()
        ):
            storage_state = str(self._storage_state_path)
        self._context = self._browser.new_context(
            storage_state=storage_state,
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        return self

    def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("PlaywrightSession is not open; use it as a context manager")
        return self._context.new_page()

    def add_cookies(self, cookies: list[dict]) -> None:
        """Add cookies to the shared context (e.g., LinkedIn li_at)."""
        if self._context is None:
            raise RuntimeError("PlaywrightSession is not open; use it as a context manager")
        self._context.add_cookies(cookies)  # type: ignore[arg-type]

    def save_state(self) -> None:
        """Persist the current context's storage state to disk.

        No-op when no path was configured. Callers should invoke this before
        exiting the session if they want to keep an authenticated state.
        """
        if self._storage_state_path is None or self._context is None:
            return
        self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self._storage_state_path))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None
