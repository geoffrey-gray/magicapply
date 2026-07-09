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

**Anti-bot posture (Phase B):** `new_page(proxy=<ProxyEntry>)` spins a
fresh per-proxy `BrowserContext` with a randomly-sampled User-Agent and
viewport from the configured pools. This is the escape hatch that
adapters (Indeed / Glassdoor) reach for when they want an anonymous
scraping identity. The shared context is left untouched so authenticated
sources (LinkedIn with li_at) keep their cookies.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
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

from magicapply.infrastructure.browser.proxy_pool import ProxyEntry, ProxyPool

# Realistic modern-desktop UAs. Pool intentionally small — the point is to
# blend into common Chrome/Firefox traffic on Linux/macOS, not to look
# exotic. Add entries when they start looking dated.
DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

DEFAULT_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (1920, 1080),
    (1440, 900),
    (1366, 768),
    (1536, 864),
    (1680, 1050),
)


class PlaywrightSession(AbstractContextManager["PlaywrightSession"]):
    """One Chromium browser + context, shared across an apply batch."""

    def __init__(
        self,
        *,
        headless: bool = True,
        storage_state_path: Path | None = None,
        proxy_pool: ProxyPool | None = None,
        user_agent_pool: Sequence[str] | None = None,
        viewport_pool: Sequence[tuple[int, int]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._headless = headless
        self._storage_state_path = storage_state_path
        self._proxy_pool = proxy_pool
        self._user_agent_pool = tuple(user_agent_pool) if user_agent_pool else DEFAULT_USER_AGENTS
        self._viewport_pool = tuple(viewport_pool) if viewport_pool else DEFAULT_VIEWPORTS
        self._rng = rng or random.Random()
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        # Per-proxy contexts created via `new_page(proxy=...)`. Reused when
        # the same proxy is requested twice — Playwright context creation
        # is ~100ms, not free.
        self._proxy_contexts: dict[str, BrowserContext] = {}

    def __enter__(self) -> PlaywrightSession:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        storage_state: str | None = None
        if (
            self._storage_state_path is not None
            and self._storage_state_path.exists()
        ):
            storage_state = str(self._storage_state_path)
        # Apply UA + viewport to the shared context too — not just per-proxy
        # ones. Cloudflare on Indeed / Glassdoor fingerprints the default
        # `HeadlessChrome/…` UA and serves a static block page before
        # cookies / auth ever come into play. Empirically verified: same
        # request with a real `Chrome/124.0.0.0` UA + realistic viewport
        # yields 1.59 MB of real search results; default headless UA gets
        # a 35 KB "Blocked - Indeed.com" page. UA/viewport are pinned per
        # session (not per fetch) so the request signature stays
        # consistent within one discover run — matches real-user behavior
        # and satisfies Cloudflare's "stable browser identity" heuristic.
        self._context = self._browser.new_context(
            storage_state=storage_state,
            locale="en-US",
            user_agent=self._rng.choice(self._user_agent_pool),
            viewport={
                "width": (v := self._rng.choice(self._viewport_pool))[0],
                "height": v[1],
            },
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        return self

    def new_page(self, *, proxy: ProxyEntry | None = None) -> Page:
        """Return a fresh Page.

        Without ``proxy``: use the shared context (keeps cookies / storage
        state across pages).

        With ``proxy``: create — or reuse — a per-proxy anonymous context
        with a randomly-sampled User-Agent and viewport from the pools.
        Discovery adapters use this to blend in when scraping sites that
        fingerprint session identity.
        """
        if self._browser is None:
            raise RuntimeError("PlaywrightSession is not open; use it as a context manager")
        if proxy is None:
            if self._context is None:  # defensive; __enter__ sets both together
                raise RuntimeError("PlaywrightSession is not open; use it as a context manager")
            return self._context.new_page()
        ctx = self._proxy_contexts.get(proxy.server)
        if ctx is None:
            ctx = self._browser.new_context(
                proxy=proxy.as_playwright_proxy(),  # type: ignore[arg-type]
                locale="en-US",
                user_agent=self._rng.choice(self._user_agent_pool),
                viewport={
                    "width": (v := self._rng.choice(self._viewport_pool))[0],
                    "height": v[1],
                },
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            self._proxy_contexts[proxy.server] = ctx
        return ctx.new_page()

    def add_cookies(self, cookies: list[dict]) -> None:
        """Add cookies to the shared context (e.g., LinkedIn li_at)."""
        if self._context is None:
            raise RuntimeError("PlaywrightSession is not open; use it as a context manager")
        self._context.add_cookies(cookies)  # type: ignore[arg-type]

    def drop_proxy_context(self, proxy: ProxyEntry) -> None:
        """Close and forget a per-proxy context. Adapters call this after
        `pool.burn(proxy)` so the next `new_page(proxy=<new>)` doesn't reuse
        a poisoned context (Playwright caches DNS / connection state per
        context and a burned proxy may leave a bad TCP state)."""
        ctx = self._proxy_contexts.pop(proxy.server, None)
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

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
        for ctx in list(self._proxy_contexts.values()):
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self._proxy_contexts.clear()
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None
