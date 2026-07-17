"""Unit tests for multi-board apply session auth merge."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.apply_session import (
    board_auth_site_for_job,
    cookies_from_storage_state,
    materialize_normalized_storage_state,
    normalize_board_cookie_domains,
    open_apply_session,
    order_board_auth_sites,
    requires_headed_board_session,
)


def _job(*, url: str, apply_url: str | None = None) -> Job:
    return Job(
        id="j1",
        title="Engineer",
        company="Acme",
        url=url,
        apply_url=apply_url,
        source_name="test",
    )


class TestBoardAuthSite:
    def test_indeed_listing(self) -> None:
        assert board_auth_site_for_job(
            _job(url="https://www.indeed.com/viewjob?jk=abc")
        ) == "indeed"

    def test_linkedin_listing(self) -> None:
        assert board_auth_site_for_job(
            _job(url="https://www.linkedin.com/jobs/view/123")
        ) == "linkedin"

    def test_ats_direct_no_board(self) -> None:
        assert (
            board_auth_site_for_job(
                _job(url="https://boards.greenhouse.io/acme/jobs/1")
            )
            is None
        )


class TestOrderBoardAuthSites:
    def test_linkedin_before_indeed(self) -> None:
        assert order_board_auth_sites(["indeed", "linkedin"]) == [
            "linkedin",
            "indeed",
        ]

    def test_stable_unique(self) -> None:
        assert order_board_auth_sites(["indeed", "indeed", "glassdoor"]) == [
            "indeed",
            "glassdoor",
        ]


class TestRequiresHeaded:
    def test_never_forced(self) -> None:
        # Domain-normalized LinkedIn jar works headless; do not force headed.
        assert not requires_headed_board_session(["linkedin"])
        assert not requires_headed_board_session(["linkedin", "indeed"])
        assert not requires_headed_board_session(["indeed", "glassdoor"])
        assert not requires_headed_board_session([])


class TestNormalizeDomains:
    def test_widens_www_linkedin(self) -> None:
        cookies = normalize_board_cookie_domains(
            [
                {
                    "name": "li_at",
                    "value": "x",
                    "domain": ".www.linkedin.com",
                    "path": "/",
                }
            ]
        )
        assert cookies[0]["domain"] == ".linkedin.com"

    def test_drops_expired(self) -> None:
        cookies = normalize_board_cookie_domains(
            [
                {
                    "name": "__cf_bm",
                    "value": "x",
                    "domain": ".linkedin.com",
                    "path": "/",
                    "expires": 1,
                },
                {
                    "name": "li_at",
                    "value": "y",
                    "domain": ".linkedin.com",
                    "path": "/",
                    "expires": time.time() + 86400,
                },
            ]
        )
        assert [c["name"] for c in cookies] == ["li_at"]


class TestMaterializeNormalizedStorage:
    def test_rewrites_www_domain(self, tmp_path: Path) -> None:
        src = tmp_path / "linkedin_storage_state.json"
        src.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "li_at",
                            "value": "tok",
                            "domain": ".www.linkedin.com",
                            "path": "/",
                        }
                    ]
                }
            )
        )
        out = materialize_normalized_storage_state(src)
        assert out != src
        raw = json.loads(out.read_text())
        assert raw["cookies"][0]["domain"] == ".linkedin.com"


class TestCookiesFromStorageState:
    def test_reads_cookies(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "li_at",
                            "value": "tok",
                            "domain": ".linkedin.com",
                            "path": "/",
                        },
                        {"name": "", "value": "skip"},
                    ]
                }
            )
        )
        cookies = cookies_from_storage_state(path)
        assert len(cookies) == 1
        assert cookies[0]["name"] == "li_at"


class TestOpenApplySessionMerge:
    def test_mixed_batch_merges_secondary_cookies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        linkedin = auth_dir / "linkedin_storage_state.json"
        indeed = auth_dir / "indeed_storage_state.json"
        linkedin.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "li_at",
                            "value": "L",
                            "domain": ".linkedin.com",
                            "path": "/",
                        }
                    ]
                }
            )
        )
        indeed.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "CTK",
                            "value": "I",
                            "domain": ".indeed.com",
                            "path": "/",
                        }
                    ]
                }
            )
        )
        monkeypatch.delenv("LINKEDIN_LI_AT", raising=False)
        monkeypatch.delenv("LINKEDIN_SESSION_COOKIES", raising=False)
        monkeypatch.delenv("INDEED_SESSION_COOKIES", raising=False)

        captured: dict = {}

        def fake_session(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            sess = MagicMock()
            sess._pending_auth_cookies = None
            return sess

        monkeypatch.setattr(
            "magicapply.infrastructure.browser.apply_session.PlaywrightSession",
            fake_session,
        )

        # LinkedIn primary via normalized storage_state; Indeed cookies merge.
        session = open_apply_session(
            headless=True,
            data_dir=tmp_path,
            prefer_site="linkedin",
            also_sites=("indeed",),
        )
        assert captured["storage_state_path"] is not None
        assert captured["headless"] is True
        pending = session._pending_auth_cookies
        assert pending is not None
        names = {c["name"] for c in pending}
        assert "CTK" in names
        # li_at comes from storage_state, not pending (avoids double-inject).
        assert "li_at" not in names
