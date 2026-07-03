"""Smoke tests for PlaywrightSession.

Skipped when Chromium is not installed in the Playwright cache
(``~/.cache/ms-playwright``). Run ``uv run playwright install chromium``
once inside the dev VM to enable them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magicapply.infrastructure.browser.session import PlaywrightSession

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
