"""Smoke tests for PlaywrightSession.

Skipped when Chromium is not installed in the Playwright cache
(``~/.cache/ms-playwright``). Run ``uv run playwright install chromium``
once inside the dev VM to enable them.

Proxy / UA / viewport rotation is tested separately below with a hand-
rolled Browser stub so we don't need Chromium for those code paths.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from magicapply.infrastructure.browser.proxy_pool import ProxyEntry
from magicapply.infrastructure.browser.session import (
    DEFAULT_USER_AGENTS,
    DEFAULT_VIEWPORTS,
    PlaywrightSession,
)

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


def _chromium_installed() -> bool:
    return _CHROMIUM_CACHE.exists() and any(_CHROMIUM_CACHE.glob("chromium-*"))


@pytest.mark.skipif(
    not _chromium_installed(),
    reason="Chromium not installed; run: uv run playwright install chromium",
)
class TestSessionLifecycle:
    def test_enter_exit(self) -> None:
        with PlaywrightSession(headless=True) as session:
            page = session.new_page()
            assert page is not None
            page.close()

    def test_new_page_without_enter_raises(self) -> None:
        session = PlaywrightSession(headless=True)
        with pytest.raises(RuntimeError, match="not open"):
            session.new_page()

    def test_storage_state_round_trips(self, tmp_path: Path) -> None:
        state_path = tmp_path / "storage.json"
        with PlaywrightSession(
            headless=True, storage_state_path=state_path
        ) as session:
            page = session.new_page()
            page.goto("about:blank")
            session.save_state()
        assert state_path.exists()
        # And a fresh session can re-load it without exploding.
        with PlaywrightSession(headless=True, storage_state_path=state_path):
            pass


# ---- Proxy / UA / viewport rotation (no Chromium required) ----------------


class _FakePage:
    pass


class _FakeContext:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.pages: list[_FakePage] = []
        self.closed = False

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []

    def new_context(self, **kwargs: object) -> _FakeContext:
        ctx = _FakeContext(dict(kwargs))
        self.contexts.append(ctx)
        return ctx


def _open_stub_session(rng: random.Random | None = None) -> PlaywrightSession:
    """Bypass Chromium — patch the browser slot directly for proxy-path tests."""
    session = PlaywrightSession(rng=rng or random.Random(0))
    session._browser = _FakeBrowser()  # type: ignore[assignment]  # noqa: SLF001
    return session


class TestProxyContexts:
    def test_new_page_with_proxy_creates_per_proxy_context(self) -> None:
        session = _open_stub_session()
        proxy = ProxyEntry(server="http://p:1")
        session.new_page(proxy=proxy)
        browser = session._browser  # type: ignore[assignment]  # noqa: SLF001
        assert len(browser.contexts) == 1  # type: ignore[union-attr]
        ctx = browser.contexts[0]  # type: ignore[union-attr]
        assert ctx.kwargs["proxy"] == {"server": "http://p:1"}
        assert ctx.kwargs["user_agent"] in DEFAULT_USER_AGENTS
        vp = ctx.kwargs["viewport"]
        assert (vp["width"], vp["height"]) in DEFAULT_VIEWPORTS

    def test_repeated_new_page_same_proxy_reuses_context(self) -> None:
        session = _open_stub_session()
        proxy = ProxyEntry(server="http://p:1")
        session.new_page(proxy=proxy)
        session.new_page(proxy=proxy)
        browser = session._browser  # type: ignore[union-attr]  # noqa: SLF001
        # Only one context; the second call reuses it.
        assert len(browser.contexts) == 1  # type: ignore[union-attr]
        assert len(browser.contexts[0].pages) == 2  # type: ignore[union-attr]

    def test_different_proxies_get_different_contexts(self) -> None:
        session = _open_stub_session()
        session.new_page(proxy=ProxyEntry(server="http://p:1"))
        session.new_page(proxy=ProxyEntry(server="http://p:2"))
        assert len(session._browser.contexts) == 2  # type: ignore[union-attr]  # noqa: SLF001

    def test_drop_proxy_context_closes_and_forgets(self) -> None:
        session = _open_stub_session()
        proxy = ProxyEntry(server="http://p:1")
        session.new_page(proxy=proxy)
        ctx = session._browser.contexts[0]  # type: ignore[union-attr]  # noqa: SLF001
        session.drop_proxy_context(proxy)
        assert ctx.closed is True
        # Fresh context created on next call.
        session.new_page(proxy=proxy)
        assert len(session._browser.contexts) == 2  # type: ignore[union-attr]  # noqa: SLF001

    def test_ua_and_viewport_sampled_from_pools(self) -> None:
        """Same seed → same choice, so we can verify the sampled entry
        comes from the configured pools."""
        session = _open_stub_session(rng=random.Random(123))
        session.new_page(proxy=ProxyEntry(server="http://p:1"))
        ctx = session._browser.contexts[0]  # type: ignore[union-attr]  # noqa: SLF001
        assert ctx.kwargs["user_agent"] in DEFAULT_USER_AGENTS
        vp = ctx.kwargs["viewport"]
        assert (vp["width"], vp["height"]) in DEFAULT_VIEWPORTS

    def test_new_page_without_proxy_uses_shared_context(self) -> None:
        session = _open_stub_session()
        session._context = _FakeContext({})  # type: ignore[assignment]  # noqa: SLF001
        page = session.new_page()
        assert page in session._context.pages  # type: ignore[union-attr]  # noqa: SLF001
        assert session._browser.contexts == []  # type: ignore[union-attr]  # noqa: SLF001


# ---- Shared context UA / viewport (regression: Cloudflare-defeat fix) -----


class TestSharedContextIdentity:
    """The shared context (used by non-proxied fetches — LinkedIn cookie
    path, Indeed/Glassdoor when cookies win over proxies, etc.) must have
    a realistic User-Agent and viewport. Playwright's default headless
    UA (`HeadlessChrome/…`) is trivially fingerprinted by Cloudflare;
    empirically that lands us on a 35 KB "Blocked - Indeed.com" page,
    while a real Chrome UA + realistic viewport yields 1.59 MB of real
    search results. This regression test locks the fix in."""

    def _patched_session(
        self, monkeypatch: pytest.MonkeyPatch, rng: random.Random | None = None
    ) -> PlaywrightSession:
        """Return a PlaywrightSession that has already been `__enter__`-ed
        against a fake Playwright — no Chromium needed."""
        from magicapply.infrastructure.browser import session as session_mod

        fake_browser = _FakeBrowser()

        class _FakeChromium:
            def launch(self, **_kwargs) -> _FakeBrowser:
                return fake_browser

        class _FakePlaywright:
            chromium = _FakeChromium()

            def stop(self) -> None:
                pass

        class _FakeStarter:
            def start(self) -> _FakePlaywright:
                return _FakePlaywright()

        monkeypatch.setattr(session_mod, "sync_playwright", _FakeStarter)
        s = PlaywrightSession(rng=rng or random.Random(0))
        s.__enter__()
        return s

    def test_shared_context_gets_ua_from_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = self._patched_session(monkeypatch)
        # __enter__ created exactly one context: the shared one.
        assert len(s._browser.contexts) == 1  # type: ignore[union-attr]  # noqa: SLF001
        ctx = s._browser.contexts[0]  # type: ignore[union-attr]  # noqa: SLF001
        assert ctx.kwargs["user_agent"] in DEFAULT_USER_AGENTS
        vp = ctx.kwargs["viewport"]
        assert (vp["width"], vp["height"]) in DEFAULT_VIEWPORTS

    def test_shared_context_never_uses_headless_chrome_default_ua(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: the whole point of this fix is to NOT let Playwright
        fall back to `HeadlessChrome/...`. The pool must never leak that
        string."""
        s = self._patched_session(monkeypatch)
        ctx = s._browser.contexts[0]  # type: ignore[union-attr]  # noqa: SLF001
        assert "HeadlessChrome" not in ctx.kwargs["user_agent"]

    def test_shared_context_ua_deterministic_from_rng(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same seed → same UA. Lets operators pin a specific UA when
        reproducing a discover run."""
        seed = 42
        first = self._patched_session(monkeypatch, rng=random.Random(seed))
        second = self._patched_session(monkeypatch, rng=random.Random(seed))
        assert (
            first._browser.contexts[0].kwargs["user_agent"]  # type: ignore[union-attr]  # noqa: SLF001
            == second._browser.contexts[0].kwargs["user_agent"]  # type: ignore[union-attr]  # noqa: SLF001
        )
