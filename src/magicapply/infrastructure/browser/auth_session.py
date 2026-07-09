"""Generic login-once + session resolution for discovery sources.

Priority for every site:

1. ``data/auth/<site>_storage_state.json`` (from ``magicapply auth login``)
2. Full Cookie header env (``{SITE}_SESSION_COOKIES``)
3. Legacy single-cookie env (e.g. ``LINKEDIN_LI_AT``)
4. None
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from magicapply.infrastructure.browser.auth_sites import (
    AuthSiteSpec,
    get_site,
    list_sites,
)

logger = logging.getLogger(__name__)

AuthSource = Literal["storage_state", "env_cookies", "legacy", "none"]


@dataclass(frozen=True, slots=True)
class AuthResolution:
    """How to authenticate a PlaywrightSession for one site."""

    storage_state_path: Path | None
    cookies: list[dict] = field(default_factory=list)
    source: AuthSource = "none"
    site: str = ""


def auth_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "auth"


def auth_state_path(data_dir: Path, site: str) -> Path:
    spec = get_site(site)
    return auth_dir(data_dir) / spec.state_filename


def resolve_session_auth(site: str, data_dir: Path | None) -> AuthResolution:
    """Resolve storage_state and/or cookies for a site (never logs values)."""
    spec = get_site(site)
    if data_dir is not None:
        state = auth_state_path(data_dir, site)
        if state.is_file() and state.stat().st_size > 2:
            return AuthResolution(
                storage_state_path=state,
                cookies=[],
                source="storage_state",
                site=spec.name,
            )

    multi = os.environ.get(spec.cookie_env, "").strip()
    if multi:
        # Lazy import avoids circular import (sources.factory → adapters → here).
        from magicapply.infrastructure.sources.base import parse_cookie_string

        cookies = parse_cookie_string(multi, domain=spec.cookie_domain)
        if cookies:
            return AuthResolution(
                storage_state_path=None,
                cookies=cookies,
                source="env_cookies",
                site=spec.name,
            )

    legacy_cookies = _legacy_cookies(spec)
    if legacy_cookies:
        return AuthResolution(
            storage_state_path=None,
            cookies=legacy_cookies,
            source="legacy",
            site=spec.name,
        )

    return AuthResolution(
        storage_state_path=None,
        cookies=[],
        source="none",
        site=spec.name,
    )


def _legacy_cookies(spec: AuthSiteSpec) -> list[dict]:
    if not spec.legacy_env_keys or not spec.legacy_cookie_name:
        return []
    for key in spec.legacy_env_keys:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        return [
            {
                "name": spec.legacy_cookie_name,
                "value": raw,
                "domain": spec.cookie_domain,
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
    return []


def site_status(data_dir: Path | None) -> list[dict[str, str | bool]]:
    """Per-site status for CLI (no secret values)."""
    rows: list[dict[str, str | bool]] = []
    for spec in list_sites():
        state_path = (
            auth_state_path(data_dir, spec.name) if data_dir is not None else None
        )
        state_ok = bool(state_path and state_path.is_file())
        env_ok = bool(os.environ.get(spec.cookie_env, "").strip())
        legacy_ok = any(
            os.environ.get(k, "").strip() for k in spec.legacy_env_keys
        )
        if state_ok:
            source = "storage_state"
        elif env_ok:
            source = "env_cookies"
        elif legacy_ok:
            source = "legacy"
        else:
            source = "none"
        rows.append(
            {
                "site": spec.name,
                "storage_state": state_ok,
                "env_cookies": env_ok,
                "legacy": legacy_ok,
                "resolved": source,
                "path": str(state_path) if state_path else "",
            }
        )
    return rows


def clear_auth_state(data_dir: Path, site: str) -> bool:
    """Remove saved storage_state for site. Returns True if a file was deleted."""
    path = auth_state_path(data_dir, site)
    if path.is_file():
        path.unlink()
        return True
    return False


def run_interactive_login(
    site: str,
    *,
    data_dir: Path,
    headed: bool = True,
    force: bool = False,
    auto_save: bool = False,
    wait_cookie: str | None = None,
    timeout_seconds: int = 600,
) -> Path:
    """Open a browser for the operator to log in; save Playwright storage_state.

    Returns the path written. Raises ``RuntimeError`` on missing display when
    headed is required, or if Chromium/Playwright fails.

    When ``auto_save`` is True (or stdin is not a TTY), wait until a cookie
    named ``wait_cookie`` appears (default: site legacy name or any cookie),
    then save — no Enter required.
    """
    import time

    from magicapply.infrastructure.browser.session import PlaywrightSession

    spec = get_site(site)
    out = auth_state_path(data_dir, site)
    if out.is_file() and not force:
        raise FileExistsError(
            f"auth state already exists at {out}; pass --force to overwrite"
        )

    if headed and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError(
            "No DISPLAY/WAYLAND_DISPLAY — cannot open a headed browser. "
            "Options: run from a desktop/VNC session, set DISPLAY, or set "
            f"{spec.cookie_env} in .env from a Cookie header paste instead."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    # Force: delete existing so the login window does not reload old cookies.
    if force and out.is_file():
        out.unlink()

    target_cookie = wait_cookie or spec.legacy_cookie_name
    use_auto = auto_save or not _stdin_is_tty()

    with PlaywrightSession(
        headless=not headed,
        storage_state_path=out,
    ) as session:
        page = session.new_page()
        page.goto(spec.login_url, wait_until="domcontentloaded", timeout=60_000)
        if use_auto:
            print(
                f"\n=== MagicApply auth login: {spec.name} (auto-save) ===\n"
                f"1. Log in in the browser window that opened.\n"
                f"2. Complete any CAPTCHA / 2FA if prompted.\n"
                f"3. Waiting up to {timeout_seconds}s for session cookie"
                f"{f' ({target_cookie})' if target_cookie else ''}…\n"
                f"   (State file: {out})\n"
            )
            deadline = time.monotonic() + max(30, int(timeout_seconds))
            while time.monotonic() < deadline:
                names = _cookie_names(session)
                if target_cookie and target_cookie in names:
                    print(f"Detected cookie {target_cookie!r}; saving…")
                    break
                if not target_cookie and len(names) >= 3:
                    print(f"Detected {len(names)} cookies; saving…")
                    break
                time.sleep(2.0)
            else:
                names = _cookie_names(session)
                if not names:
                    raise RuntimeError(
                        f"Timed out waiting for login on {spec.name} "
                        f"(no cookies yet). Try again or use env cookies."
                    )
                print(
                    f"Timeout reached with {len(names)} cookies; saving anyway."
                )
        else:
            print(
                f"\n=== MagicApply auth login: {spec.name} ===\n"
                f"1. Log in in the browser window that opened.\n"
                f"2. Complete any CAPTCHA / 2FA if prompted.\n"
                f"3. Return here and press Enter to save the session.\n"
                f"   (State file: {out})\n"
            )
            try:
                input("Press Enter after you have logged in… ")
            except EOFError as exc:
                raise RuntimeError(
                    "Non-interactive stdin — pass --auto-save or use a TTY"
                ) from exc

        names = _cookie_names(session)
        if not names:
            logger.warning(
                "auth login %s: no cookies visible after login — saving anyway",
                spec.name,
            )
            print("Warning: no cookies detected; saving state anyway.")
        else:
            logger.info(
                "auth login %s: %d cookies present (names only): %s",
                spec.name,
                len(names),
                ", ".join(names[:20]),
            )
            print(f"Session has {len(names)} cookies (values not shown).")

        session.save_state()

    if not out.is_file():
        raise RuntimeError(f"failed to write auth state to {out}")
    print(f"Saved auth state → {out}")
    return out


def _stdin_is_tty() -> bool:
    try:
        return bool(os.isatty(0))
    except Exception:  # noqa: BLE001
        return False


def _cookie_names(session: object) -> list[str]:
    ctx = getattr(session, "_context", None)
    if ctx is None:
        return []
    try:
        cookies = ctx.cookies()
    except Exception:  # noqa: BLE001
        return []
    return sorted({c.get("name", "") for c in cookies if c.get("name")})
