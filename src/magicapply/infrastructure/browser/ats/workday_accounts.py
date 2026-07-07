"""Per-tenant Workday apply credentials persisted under ``data_dir``.

Workday requires a one-time apply account per employer tenant before the
resume-upload wizard. The store records email + password keyed on the
tenant host (e.g. ``pluralsight.wd1.myworkdayjobs.com``) so later runs
sign in instead of re-creating, and the operator can log in manually with
the same credentials.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_FILENAME = "workday_accounts.yaml"


class WorkdayAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    email: str
    password: str
    created_at: str
    last_used_at: str | None = None


class WorkdayAccountStore:
    """YAML-backed registry of Workday apply accounts per tenant host."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._accounts: dict[str, WorkdayAccount] = {}
        self._load()

    @staticmethod
    def default_path(data_dir: Path) -> Path:
        return data_dir / _FILENAME

    @staticmethod
    def tenant_from_url(url: str) -> str:
        return urlparse(url).netloc.lower()

    @staticmethod
    def normalize_en_us_url(url: str) -> str:
        """Force a Workday careers URL onto the ``en-US`` locale segment."""
        return _normalize_en_us_url(url)

    @staticmethod
    def url_locale(url: str) -> str | None:
        """Return the first path locale segment (e.g. ``en-US``), if present."""
        return _url_locale(url)

    @staticmethod
    def url_needs_en_us(url: str) -> bool:
        """True when a Workday careers URL is not already on ``en-US``."""
        locale = _url_locale(url)
        return locale is not None and locale != "en-US"

    @staticmethod
    def careers_login_url(page_url: str) -> str | None:
        """Build the tenant login URL with a redirect back to ``page_url``."""
        page_url = _normalize_en_us_url(page_url)
        parsed = urlparse(page_url)
        path = parsed.path
        if "/login" in path.lower():
            return page_url
        careers_base = _careers_base_from_path(path)
        if careers_base is None:
            return None
        redirect = quote(path, safe="")
        return f"{parsed.scheme}://{parsed.netloc}{careers_base}/login?redirect={redirect}"

    def get(self, tenant: str) -> WorkdayAccount | None:
        return self._accounts.get(tenant.lower())

    def has(self, tenant: str) -> bool:
        return tenant.lower() in self._accounts

    def upsert(self, tenant: str, *, email: str, password: str) -> WorkdayAccount:
        key = tenant.lower()
        now = _utc_now()
        existing = self._accounts.get(key)
        if existing is None:
            account = WorkdayAccount(
                tenant=key,
                email=email,
                password=password,
                created_at=now,
                last_used_at=now,
            )
        else:
            account = existing.model_copy(
                update={
                    "email": email,
                    "password": password,
                    "last_used_at": now,
                }
            )
        self._accounts[key] = account
        self._save()
        return account

    def touch(self, tenant: str) -> None:
        key = tenant.lower()
        account = self._accounts.get(key)
        if account is None:
            return
        self._accounts[key] = account.model_copy(update={"last_used_at": _utc_now()})
        self._save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load %s: %s", self._path, exc)
            return
        for entry in raw.get("accounts", []):
            try:
                account = WorkdayAccount.model_validate(entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skip malformed workday account: %s", exc)
                continue
            self._accounts[account.tenant.lower()] = account

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": [
                self._accounts[k].model_dump()
                for k in sorted(self._accounts)
            ]
        }
        self._path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:[-_][A-Za-z]+)?$")


def _url_locale(url: str) -> str | None:
    parts = urlparse(url).path.split("/")
    if len(parts) > 2 and _LOCALE_SEGMENT.match(parts[1]):
        return parts[1]
    return None


def _normalize_en_us_url(url: str) -> str:
    """Replace any non-``en-US`` path locale segment with ``en-US``."""
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    if len(parts) > 2 and _LOCALE_SEGMENT.match(parts[1]) and parts[1] != "en-US":
        parts[1] = "en-US"
    return parsed._replace(path="/".join(parts)).geturl()


def _careers_base_from_path(path: str) -> str | None:
    """Return the ``/en-US/Careers``-style prefix from a Workday path."""
    for pattern in (
        r"(/[^/]+/Careers)",
        r"(/[^/]+/External)",
        r"(/[^/]+/[^/]+_External_Career)",
        # Employer-branded sites (e.g. ``/en-US/Circle/job/...``).
        r"(/[^/]+/[^/]+)(?=/job/)",
    ):
        match = re.search(pattern, path)
        if match:
            return match.group(1)
    return None