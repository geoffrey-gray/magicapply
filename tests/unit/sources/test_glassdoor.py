"""Unit tests for the Glassdoor adapter parsers + guard gates."""

from __future__ import annotations

import pytest

from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.glassdoor import (
    GlassdoorAdapter,
    extract_job_urls,
)


class TestGlassdoorGuards:
    def test_discover_without_ack_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGICAPPLY_GLASSDOOR_ACK", raising=False)
        adapter = GlassdoorAdapter.from_config(
            GlassdoorSource(name="glassdoor-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="MAGICAPPLY_GLASSDOOR_ACK"):
            list(adapter.discover())

    def test_empty_queries_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_GLASSDOOR_ACK", "1")
        adapter = GlassdoorAdapter.from_config(
            GlassdoorSource(name="glassdoor-search", queries=[])
        )
        assert list(adapter.discover()) == []


def test_from_config_honors_enrich_apply_urls_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGICAPPLY_GLASSDOOR_ACK", "1")
    adapter = GlassdoorAdapter.from_config(
        GlassdoorSource(name="glassdoor-search", enrich_apply_urls=False)
    )
    assert adapter._enrich_apply_urls is False


class TestSearchUrlExtractor:
    def test_extracts_job_listing_urls(self) -> None:
        html = """
        <html><body>
          <a href="/job-listing/swe-acme-JV_IC1152672_KO0,3_KE4,8.htm?jl=123">A</a>
          <a href="https://www.glassdoor.com/job-listing/eng-beta-JV_IC0_KE0,3.htm?jl=456">B</a>
          <a href="/job-listing/swe-acme-JV_IC1152672_KO0,3_KE4,8.htm?jl=123&x=y">A dup</a>
          <a href="/Job/jobs.htm?sc.keyword=python">search facet</a>
        </body></html>
        """
        urls = extract_job_urls(html)
        assert urls == [
            "https://www.glassdoor.com/job-listing/eng-beta-JV_IC0_KE0,3.htm",
            "https://www.glassdoor.com/job-listing/swe-acme-JV_IC1152672_KO0,3_KE4,8.htm",
        ]

    def test_empty_page_returns_empty(self) -> None:
        assert extract_job_urls("<html><body></body></html>") == []


class TestSessionCookies:
    """Glassdoor cookie support: legacy `GLASSDOOR_SESSION` single-cookie
    stays working; new `GLASSDOOR_SESSION_COOKIES` adds multi-cookie
    support. Both may be set — both fire, combined into one list."""

    def test_legacy_single_cookie_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_GLASSDOOR_ACK", "1")
        monkeypatch.setenv("GLASSDOOR_SESSION", "abc123")
        monkeypatch.delenv("GLASSDOOR_SESSION_COOKIES", raising=False)
        adapter = GlassdoorAdapter.from_config(
            GlassdoorSource(name="glassdoor-search", queries=["python"])
        )
        assert adapter._session == "abc123"
        assert adapter._session_cookies is None

    def test_new_multi_cookie_env_var_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_GLASSDOOR_ACK", "1")
        monkeypatch.delenv("GLASSDOOR_SESSION", raising=False)
        monkeypatch.setenv(
            "GLASSDOOR_SESSION_COOKIES", "gdSession=abc; ipc=xyz"
        )
        adapter = GlassdoorAdapter.from_config(
            GlassdoorSource(name="glassdoor-search", queries=["python"])
        )
        assert [(c["name"], c["value"]) for c in adapter._session_cookies or []] == [
            ("gdSession", "abc"),
            ("ipc", "xyz"),
        ]

    def test_both_env_vars_combine_at_injection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both `GLASSDOOR_SESSION` (legacy) and `GLASSDOOR_SESSION_COOKIES`
        (new) may be set. `discover()` combines them: gdSession from the
        legacy path plus every cookie from the new path."""
        from magicapply.infrastructure.sources import glassdoor as mod

        added: list[list[dict]] = []

        class _FakeSession:
            def __init__(self, **_kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, cookies) -> None:
                added.append(list(cookies))

            def drop_proxy_context(self, _proxy) -> None:  # noqa: ARG002
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", _FakeSession)
        monkeypatch.setattr(mod, "_fetch", lambda *a, **k: "<html></html>")

        adapter = GlassdoorAdapter(
            name="glassdoor-search",
            queries=["python"],
            rate_limit_per_minute=6000,
            session_cookie="legacy_value",
            acknowledged=True,
            session_cookies=[
                {"name": "ipc", "value": "xyz", "domain": ".glassdoor.com"},
            ],
        )
        list(adapter.discover())
        assert len(added) == 1
        names = [c["name"] for c in added[0]]
        assert names == ["gdSession", "ipc"]

    def test_cookies_win_over_proxy_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Either cookie env var causes the proxy pool to be bypassed —
        auth sessions and per-proxy contexts don't mix."""
        from magicapply.infrastructure.sources import glassdoor as mod

        constructor_kwargs: list[dict] = []

        class _FakeSession:
            def __init__(self, **kwargs) -> None:
                constructor_kwargs.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, _cookies) -> None:
                pass

            def drop_proxy_context(self, _proxy) -> None:  # noqa: ARG002
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", _FakeSession)
        monkeypatch.setattr(mod, "_fetch", lambda *a, **k: "<html></html>")

        sentinel_pool = object()
        adapter = GlassdoorAdapter(
            name="glassdoor-search",
            queries=["python"],
            rate_limit_per_minute=6000,
            session_cookie="legacy_value",
            acknowledged=True,
            proxy_pool=sentinel_pool,  # type: ignore[arg-type]
        )
        list(adapter.discover())
        assert constructor_kwargs == [{"headless": True, "proxy_pool": None}]
