"""Open Playwright sessions for apply with optional board auth state."""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    is_job_board_listing_url,
    needs_board_destination_resolve,
)

logger = logging.getLogger(__name__)

# When a batch mixes boards, load LinkedIn storage_state first (auth-sensitive),
# then merge Indeed/Glassdoor cookies into the same context.
_BOARD_AUTH_PRIORITY = ("linkedin", "indeed", "glassdoor")

# Stable Chrome UA for board-auth apply sessions. Random Firefox UAs from the
# discovery pool mismatch LinkedIn's headed-login fingerprint and can clear
# ``li_at`` or soft-block job pages even when the jar is valid.
_BOARD_AUTH_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
_BOARD_AUTH_VIEWPORTS: tuple[tuple[int, int], ...] = ((1440, 900),)

_LINKEDIN_COOKIE_NAMES = frozenset(
    {"li_at", "li_rm", "bscookie", "bcookie", "JSESSIONID", "lidc", "lang", "timezone"}
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


def order_board_auth_sites(sites: Sequence[str]) -> list[str]:
    """Stable order: LinkedIn before Indeed/Glassdoor, then any leftovers."""
    ordered: list[str] = []
    for pref in _BOARD_AUTH_PRIORITY:
        if pref in sites and pref not in ordered:
            ordered.append(pref)
    for site in sites:
        if site not in ordered:
            ordered.append(site)
    return ordered


def requires_headed_board_session(sites: Sequence[str]) -> bool:
    """True when any site needs headed Chromium to keep board auth cookies.

    LinkedIn sessions persist under headless when cookie domains are widened
    from ``.www.linkedin.com`` → ``.linkedin.com``; headed is not required.
    """
    return False


def normalize_board_cookie_domains(cookies: list[dict]) -> list[dict]:
    """Widen LinkedIn cookie hosts from ``.www.linkedin.com`` to ``.linkedin.com``.

    Playwright loads ``.www.linkedin.com`` cookies into the context, but LinkedIn
    then clears ``li_at`` on the first navigation (login redirect). Widening to
    ``.linkedin.com`` keeps Easy Apply authenticated from the existing jar.
    """
    out: list[dict] = []
    now = time.time()
    for cookie in cookies:
        if not isinstance(cookie, dict) or not cookie.get("name"):
            continue
        c = dict(cookie)
        # Drop expired cookies (e.g. Cloudflare __cf_bm) that can confuse auth.
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and 0 < exp < now:
            continue
        name = str(c.get("name") or "")
        domain = str(c.get("domain") or "").lower()
        if name in _LINKEDIN_COOKIE_NAMES or domain.endswith("linkedin.com"):
            if domain in {".www.linkedin.com", "www.linkedin.com"}:
                c["domain"] = ".linkedin.com"
        if "path" not in c:
            c["path"] = "/"
        # Drop non-LinkedIn third-party cookies from LinkedIn jars (e.g. Google NID).
        if domain.endswith("google.com"):
            continue
        out.append(c)
    return out


def materialize_normalized_storage_state(path: Path) -> Path:
    """Write a temp storage_state with LinkedIn domains widened for Playwright."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("apply_session: cannot read storage_state %s: %s", path, exc)
        return path
    cookies = raw.get("cookies") or []
    if not isinstance(cookies, list):
        return path
    normalized = normalize_board_cookie_domains(
        [c for c in cookies if isinstance(c, dict)]
    )
    # Skip rewrite when already clean (no www domain, nothing dropped).
    if len(normalized) == len(cookies) and all(
        str(c.get("domain") or "").lower()
        not in {".www.linkedin.com", "www.linkedin.com"}
        for c in cookies
        if isinstance(c, dict)
    ):
        return path
    out = dict(raw)
    out["cookies"] = normalized
    dest = Path(tempfile.mkdtemp(prefix="magicapply-auth-")) / path.name
    dest.write_text(json.dumps(out), encoding="utf-8")
    logger.info(
        "apply_session: normalized storage_state %s → %s (%d cookies)",
        path.name,
        dest,
        len(normalized),
    )
    return dest


def cookies_from_storage_state(path: Path) -> list[dict]:
    """Extract Playwright cookie dicts from a storage_state JSON file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("apply_session: cannot read storage_state %s: %s", path, exc)
        return []
    cookies = raw.get("cookies") or []
    return [c for c in cookies if isinstance(c, dict) and c.get("name")]


def open_apply_session(
    *,
    headless: bool,
    data_dir: Path,
    job: Job | None = None,
    prefer_site: str | None = None,
    also_sites: Sequence[str] = (),
) -> PlaywrightSession:
    """Build a PlaywrightSession, loading board storage_state / cookies when needed.

    ``prefer_site`` (or the job's board) supplies the primary ``storage_state``.
    ``also_sites`` merges additional board cookies into the same context so a
    mixed Indeed+LinkedIn batch does not drop LinkedIn ``li_at`` when Indeed
    wins the primary slot (or vice versa).

    LinkedIn jars often store ``li_at`` under ``.www.linkedin.com``; that host is
    widened to ``.linkedin.com`` before ``storage_state`` load so Easy Apply
    keeps the session without re-login.

    Does not enter the context manager — caller uses ``with session:``.
    """
    primary = prefer_site or (board_auth_site_for_job(job) if job else None)
    sites = order_board_auth_sites(
        [s for s in ((primary,) if primary else ()) + tuple(also_sites) if s]
    )

    storage_path = None
    pending: list[dict] = []
    for index, site in enumerate(sites):
        auth = resolve_session_auth(site, data_dir)
        if index == 0:
            if auth.storage_state_path is not None:
                storage_path = (
                    materialize_normalized_storage_state(auth.storage_state_path)
                    if site == "linkedin"
                    else auth.storage_state_path
                )
            # Env cookies only — never re-inject the same storage_state cookies
            # (double-inject causes LinkedIn ERR_TOO_MANY_REDIRECTS).
            pending.extend(normalize_board_cookie_domains(list(auth.cookies)))
            continue
        # Secondary boards: fold storage_state cookies into the shared context.
        if auth.storage_state_path is not None:
            pending.extend(
                normalize_board_cookie_domains(
                    cookies_from_storage_state(auth.storage_state_path)
                )
            )
        pending.extend(normalize_board_cookie_domains(list(auth.cookies)))

    if sites:
        logger.info(
            "apply_session: primary=%s also=%s storage=%s headless=%s extra_cookies=%d",
            sites[0],
            sites[1:],
            storage_path.name if storage_path else None,
            headless,
            len(pending),
        )

    session = PlaywrightSession(
        headless=headless,
        storage_state_path=storage_path,
        user_agent_pool=_BOARD_AUTH_USER_AGENTS if sites else None,
        viewport_pool=_BOARD_AUTH_VIEWPORTS if sites else None,
    )
    # Cookies applied after __enter__; stash for caller.
    session._pending_auth_cookies = pending or None  # type: ignore[attr-defined]
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
