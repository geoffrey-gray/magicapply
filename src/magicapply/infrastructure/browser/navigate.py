"""Shared Playwright navigation helpers with bounded timeouts."""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import urlparse

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
        goto(str(url).strip(), wait_until="domcontentloaded", timeout=timeout_ms)
    except TypeError:
        # Fake pages / PageDriver stubs without timeout/wait_until kwargs.
        try:
            goto(str(url).strip(), timeout=timeout_ms)
        except TypeError:
            goto(str(url).strip())


def linkedin_same_origin_goto(
    page: object,
    url: str,
    *,
    # Prefer feed only. Bare https://www.linkedin.com/ often 302-loops under
    # Playwright even with a valid li_at (ERR_TOO_MANY_REDIRECTS).
    warm_urls: tuple[str, ...] = (
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/jobs/",
    ),
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
) -> None:
    """Open a LinkedIn job URL without ``page.goto`` self-302 loops.

    Direct ``goto`` to ``/jobs/view/…`` often returns ``ERR_TOO_MANY_REDIRECTS``
    (302 → same URL) even with a valid ``li_at``. Warming on feed/jobs hub,
    then ``location.assign`` of the path, keeps the cookie jar and loads the
    job document.
    """
    target = (url or "").strip()
    if not target:
        return
    parsed = urlparse(target)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if not path.startswith("/"):
        path = "/" + path

    def _on_linkedin() -> bool:
        current = (getattr(page, "url", "") or "").lower()
        return "linkedin.com" in current and "chrome-error" not in current

    def _assert_not_rate_limited() -> None:
        content = ""
        content_fn = getattr(page, "content", None)
        if callable(content_fn):
            with contextlib.suppress(Exception):
                content = content_fn() or ""
        # Empty shell after a warm/nav is the usual Playwright view of HTTP 429.
        if len(content) < 200:
            raise RuntimeError(
                "LinkedIn returned an empty page (likely HTTP 429 rate limit). "
                "Cookie jar is present; wait and retry without re-login."
            )

    if not _on_linkedin():
        last_exc: Exception | None = None
        for warm in warm_urls:
            try:
                safe_goto(page, warm, timeout_ms=timeout_ms)
                if _on_linkedin():
                    last_exc = None
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.debug("linkedin warm %s failed: %s", warm, exc)
        if not _on_linkedin():
            if last_exc is not None:
                raise RuntimeError(
                    f"LinkedIn session warm failed before job navigate: {last_exc}"
                ) from last_exc
            raise RuntimeError(
                "LinkedIn session warm failed before job navigate "
                f"(url={getattr(page, 'url', '')!r})"
            )
        _assert_not_rate_limited()

    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        safe_goto(page, target, timeout_ms=timeout_ms)
        return

    try:
        evaluate("(p) => { window.location.assign(p); }", path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"LinkedIn job assign failed ({exc}); page={getattr(page, 'url', '')!r}"
        ) from exc

    # Full document navigations from assign are async; wait briefly then verify.
    wait_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_timeout):
        with contextlib.suppress(Exception):
            wait_timeout(3500)

    wait_fn = getattr(page, "wait_for_url", None)
    if callable(wait_fn):
        with contextlib.suppress(Exception):
            wait_fn(f"**{parsed.path.rstrip('/')}**", timeout=min(timeout_ms, 20_000))

    if callable(wait_timeout):
        with contextlib.suppress(Exception):
            wait_timeout(1500)

    current = (getattr(page, "url", "") or "").lower()
    if "chrome-error" in current:
        raise RuntimeError(
            "LinkedIn job navigate landed on chrome-error "
            "(redirect loop / rate limit); cookie jar may need a cool-down"
        )
    job_marker = (parsed.path or "").rstrip("/").lower()
    if job_marker and job_marker not in current:
        raise RuntimeError(
            "LinkedIn job navigate did not reach the job URL "
            f"(wanted {path!r}, page={getattr(page, 'url', '')!r})"
        )
    _assert_not_rate_limited()
