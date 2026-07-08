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
