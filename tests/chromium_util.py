"""Shared Chromium install detection for skip markers.

Delegates to production ``find_playwright_chromium_cache`` so Linux
(``~/.cache/ms-playwright``) and macOS (``~/Library/Caches/ms-playwright``)
are both recognized.
"""

from __future__ import annotations

from magicapply.infrastructure.browser.session import find_playwright_chromium_cache

SKIP_NO_CHROMIUM = "Chromium not installed; run: uv run playwright install chromium"


def chromium_installed() -> bool:
    """True when a Playwright Chromium (or headless shell) tree is on disk."""
    return find_playwright_chromium_cache() is not None
