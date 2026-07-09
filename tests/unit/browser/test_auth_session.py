"""Unit tests for generic auth registry + resolve priority."""

from __future__ import annotations

from pathlib import Path

import pytest

from magicapply.infrastructure.browser.auth_session import (
    auth_state_path,
    clear_auth_state,
    resolve_session_auth,
    site_status,
)
from magicapply.infrastructure.browser.auth_sites import get_site, list_sites


class TestRegistry:
    def test_three_builtin_sites(self) -> None:
        names = {s.name for s in list_sites()}
        assert names == {"linkedin", "indeed", "glassdoor"}

    def test_unknown_site_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown auth site"):
            get_site("not-a-site")


class TestResolvePriority:
    def test_storage_state_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LINKEDIN_SESSION_COOKIES", "li_at=fromenv")
        monkeypatch.setenv("LINKEDIN_LI_AT", "legacy")
        path = auth_state_path(tmp_path, "linkedin")
        path.parent.mkdir(parents=True)
        path.write_text('{"cookies":[]}\n')
        auth = resolve_session_auth("linkedin", tmp_path)
        assert auth.source == "storage_state"
        assert auth.storage_state_path == path
        assert auth.cookies == []

    def test_env_cookies_second(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LINKEDIN_LI_AT", raising=False)
        monkeypatch.setenv(
            "LINKEDIN_SESSION_COOKIES", "li_at=abc; lidc=xyz"
        )
        auth = resolve_session_auth("linkedin", tmp_path)
        assert auth.source == "env_cookies"
        assert auth.storage_state_path is None
        names = {c["name"] for c in auth.cookies}
        assert "li_at" in names
        assert "lidc" in names

    def test_legacy_li_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LINKEDIN_SESSION_COOKIES", raising=False)
        monkeypatch.setenv("LINKEDIN_LI_AT", "only-legacy")
        auth = resolve_session_auth("linkedin", tmp_path)
        assert auth.source == "legacy"
        assert len(auth.cookies) == 1
        assert auth.cookies[0]["name"] == "li_at"
        assert auth.cookies[0]["value"] == "only-legacy"

    def test_none_when_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LINKEDIN_SESSION_COOKIES", raising=False)
        monkeypatch.delenv("LINKEDIN_LI_AT", raising=False)
        auth = resolve_session_auth("linkedin", tmp_path)
        assert auth.source == "none"

    def test_indeed_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INDEED_SESSION_COOKIES", "CTK=1; INDEED_CSRF_TOKEN=2")
        auth = resolve_session_auth("indeed", tmp_path)
        assert auth.source == "env_cookies"
        assert len(auth.cookies) == 2


class TestClearAndStatus:
    def test_clear_and_status(self, tmp_path: Path) -> None:
        path = auth_state_path(tmp_path, "glassdoor")
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        rows = {r["site"]: r for r in site_status(tmp_path)}
        assert rows["glassdoor"]["storage_state"] is True
        assert rows["glassdoor"]["resolved"] == "storage_state"
        assert clear_auth_state(tmp_path, "glassdoor") is True
        assert clear_auth_state(tmp_path, "glassdoor") is False
