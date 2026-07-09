"""Registry of sites that support optional Playwright login-once auth.

Adding a new site: add an ``AuthSiteSpec`` here and call
``resolve_session_auth(name, data_dir)`` from that site's discovery adapter.
No new CLI commands are required.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AuthSiteSpec:
    """One site in the auth registry."""

    name: str
    login_url: str
    state_filename: str
    cookie_env: str
    cookie_domain: str
    """Env var holding a full browser Cookie: header string."""
    legacy_env_keys: tuple[str, ...] = ()
    """Optional single-cookie env vars (e.g. LINKEDIN_LI_AT → name li_at)."""
    legacy_cookie_name: str | None = None
    """Cookie name used when a single legacy env value is present."""
    success_url_substr: str | None = None


# Canonical registry. Order is display order for `auth sites` / status.
AUTH_SITES: dict[str, AuthSiteSpec] = {
    "linkedin": AuthSiteSpec(
        name="linkedin",
        login_url="https://www.linkedin.com/login",
        state_filename="linkedin_storage_state.json",
        cookie_env="LINKEDIN_SESSION_COOKIES",
        cookie_domain=".linkedin.com",
        legacy_env_keys=("LINKEDIN_LI_AT",),
        legacy_cookie_name="li_at",
        success_url_substr="linkedin.com",
    ),
    "indeed": AuthSiteSpec(
        name="indeed",
        login_url="https://secure.indeed.com/auth",
        state_filename="indeed_storage_state.json",
        cookie_env="INDEED_SESSION_COOKIES",
        cookie_domain=".indeed.com",
        success_url_substr="indeed.com",
    ),
    "glassdoor": AuthSiteSpec(
        name="glassdoor",
        login_url="https://www.glassdoor.com/profile/login_input.htm",
        state_filename="glassdoor_storage_state.json",
        cookie_env="GLASSDOOR_SESSION_COOKIES",
        cookie_domain=".glassdoor.com",
        legacy_env_keys=("GLASSDOOR_SESSION",),
        legacy_cookie_name="gdSession",
        success_url_substr="glassdoor.com",
    ),
}


def list_sites() -> list[AuthSiteSpec]:
    return list(AUTH_SITES.values())


def get_site(name: str) -> AuthSiteSpec:
    key = name.strip().lower()
    if key not in AUTH_SITES:
        known = ", ".join(sorted(AUTH_SITES))
        raise KeyError(f"unknown auth site {name!r}; known: {known}")
    return AUTH_SITES[key]
