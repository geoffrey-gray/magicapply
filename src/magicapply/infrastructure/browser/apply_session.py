"""Open Playwright sessions for apply with optional board auth state."""

from __future__ import annotations

from pathlib import Path

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    is_job_board_listing_url,
    needs_board_destination_resolve,
)


def board_auth_site_for_job(job: Job) -> str | None:
    """Return auth site key when apply may need board cookies."""
    if needs_board_destination_resolve(job):
        url = job.url.lower()
    elif job.apply_url and is_job_board_listing_url(job.apply_url):
        url = job.apply_url.lower()
    elif is_job_board_listing_url(job.url):
        url = job.url.lower()
    else:
        return None
    if "indeed.com" in url:
        return "indeed"
    if "linkedin.com" in url:
        return "linkedin"
    if "glassdoor.com" in url or "glassdoor.co.uk" in url:
        return "glassdoor"
    return None


def open_apply_session(
    *,
    headless: bool,
    data_dir: Path,
    job: Job | None = None,
    prefer_site: str | None = None,
) -> PlaywrightSession:
    """Build a PlaywrightSession, loading board storage_state / cookies when needed.

    Does not enter the context manager — caller uses ``with session:``.
    """
    site = prefer_site or (board_auth_site_for_job(job) if job else None)
    storage_path = None
    cookies = None
    if site:
        auth = resolve_session_auth(site, data_dir)
        storage_path = auth.storage_state_path
        cookies = auth.cookies
    session = PlaywrightSession(
        headless=headless,
        storage_state_path=storage_path,
    )
    # Cookies applied after __enter__; stash for caller.
    session._pending_auth_cookies = cookies  # type: ignore[attr-defined]
    return session


def apply_pending_auth_cookies(session: PlaywrightSession) -> None:
    """Add env/storage cookies after the session context has opened."""
    cookies = getattr(session, "_pending_auth_cookies", None)
    if cookies:
        session.add_cookies(cookies)
        session._pending_auth_cookies = None  # type: ignore[attr-defined]


def configure_apply_page(page: object) -> None:
    """Tight Playwright defaults so board/ATS applies fail fast instead of hanging."""
    if hasattr(page, "set_default_timeout"):
        page.set_default_timeout(15_000)  # type: ignore[attr-defined]
    if hasattr(page, "set_default_navigation_timeout"):
        page.set_default_navigation_timeout(45_000)  # type: ignore[attr-defined]
