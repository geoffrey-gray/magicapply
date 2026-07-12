"""Shared Playwright navigation helpers with bounded timeouts."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_NAV_TIMEOUT_MS = 45_000


def safe_goto(page: object, url: str, *, timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS) -> None:
    """Navigate with a hard timeout; no-op on empty URL."""
    if not url or not str(url).strip():
        return
    goto = getattr(page, "goto", None)
    if not callable(goto):
        raise TypeError("page has no goto()")
    try:
        goto(str(url).strip(), timeout=timeout_ms)
    except TypeError:
        # Fake pages / PageDriver stubs without timeout kwarg.
        goto(str(url).strip())
