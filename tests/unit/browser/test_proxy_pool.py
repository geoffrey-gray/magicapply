"""Tests for the rotating proxy pool + providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from magicapply.infrastructure.browser.proxy_pool import (
    FallbackProvider,
    FreeListScraperProvider,
    ProxyEntry,
    ProxyPool,
    StaticListProvider,
    _parse_proxy_line,
    _parse_proxy_string,
)


# ---- ProxyEntry -----------------------------------------------------------


class TestProxyEntry:
    def test_bare_server_playwright_shape(self) -> None:
        e = ProxyEntry(server="http://1.2.3.4:8080")
        assert e.as_playwright_proxy() == {"server": "http://1.2.3.4:8080"}

    def test_authed_playwright_shape(self) -> None:
        e = ProxyEntry(server="http://p:1", username="u", password="pw")
        assert e.as_playwright_proxy() == {
            "server": "http://p:1",
            "username": "u",
            "password": "pw",
        }

    def test_httpx_url_bare(self) -> None:
        assert ProxyEntry(server="http://p:1").as_httpx_proxy() == "http://p:1"

    def test_httpx_url_authed(self) -> None:
        e = ProxyEntry(server="http://host:8080", username="u", password="pw")
        assert e.as_httpx_proxy() == "http://u:pw@host:8080"

    def test_frozen(self) -> None:
        e = ProxyEntry(server="http://p:1")
        with pytest.raises(Exception):  # noqa: PT011 — Pydantic frozen error
            e.server = "http://x:2"  # type: ignore[misc]


# ---- Parsing --------------------------------------------------------------


class TestParsing:
    def test_bare_ip_port_defaults_to_http(self) -> None:
        e = _parse_proxy_string("1.2.3.4:8080")
        assert e.server == "http://1.2.3.4:8080"
        assert e.username is None

    def test_scheme_preserved(self) -> None:
        e = _parse_proxy_string("socks5://host:1080")
        assert e.server == "socks5://host:1080"

    def test_userinfo_split(self) -> None:
        e = _parse_proxy_string("user:pw@host:8080")
        assert e.server == "http://host:8080"
        assert e.username == "user"
        assert e.password == "pw"

    def test_scheme_and_userinfo(self) -> None:
        e = _parse_proxy_string("https://u:p@host:443")
        assert e.server == "https://host:443"
        assert e.username == "u"
        assert e.password == "p"

    def test_userinfo_no_password(self) -> None:
        e = _parse_proxy_string("user@host:8080")
        assert e.username == "user"
        assert e.password is None

    def test_line_parser_skips_blank_and_comment(self) -> None:
        assert _parse_proxy_line("") is None
        assert _parse_proxy_line("   ") is None
        assert _parse_proxy_line("# a comment") is None

    def test_line_parser_ignores_garbage(self) -> None:
        # unparseable → raise from _parse_proxy_string; line parser should
        # NOT swallow — the FreeListScraperProvider guards this itself
        # via try/except on the fetch loop.
        with pytest.raises(ValueError):
            _parse_proxy_line("not a proxy")


# ---- StaticListProvider ---------------------------------------------------


class TestStaticListProvider:
    def test_load_from_strings(self) -> None:
        p = StaticListProvider(["1.2.3.4:8080", "socks5://5.6.7.8:1080"])
        entries = p.load()
        assert len(entries) == 2
        assert entries[0].server == "http://1.2.3.4:8080"
        assert entries[1].server == "socks5://5.6.7.8:1080"

    def test_load_from_proxy_entries(self) -> None:
        e = ProxyEntry(server="http://x:1")
        assert StaticListProvider([e]).load() == [e]

    def test_empty(self) -> None:
        assert StaticListProvider([]).load() == []

    def test_name_reports_count(self) -> None:
        assert StaticListProvider(["1.1.1.1:80"]).name() == "static(1)"


# ---- FreeListScraperProvider ---------------------------------------------


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, bodies: dict[str, str]) -> None:
        self._bodies = bodies

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = self._bodies.get(str(request.url))
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=body, request=request)


class TestFreeListScraperProvider:
    def test_parses_lines_and_deduplicates_across_sources(self) -> None:
        bodies = {
            "https://a.example/list.txt": "1.1.1.1:80\n2.2.2.2:80\n#comment\n",
            "https://b.example/list.txt": "2.2.2.2:80\n3.3.3.3:80\n",
        }
        client = httpx.Client(transport=_FakeTransport(bodies))
        p = FreeListScraperProvider(
            sources=list(bodies.keys()), http=client
        )
        entries = p.load()
        assert [e.server for e in entries] == [
            "http://1.1.1.1:80",
            "http://2.2.2.2:80",
            "http://3.3.3.3:80",
        ]

    def test_unreachable_source_is_skipped(self) -> None:
        # 404 → not fatal; other sources still contribute.
        bodies = {"https://ok.example/list.txt": "1.1.1.1:80\n"}
        client = httpx.Client(transport=_FakeTransport(bodies))
        p = FreeListScraperProvider(
            sources=["https://ok.example/list.txt", "https://gone.example/list.txt"],
            http=client,
        )
        entries = p.load()
        assert [e.server for e in entries] == ["http://1.1.1.1:80"]

    def test_malformed_lines_are_ignored(self) -> None:
        bodies = {"https://s.example": "1.1.1.1:80\ngarbage\n2.2.2.2:80\n"}
        client = httpx.Client(transport=_FakeTransport(bodies))
        entries = FreeListScraperProvider(
            sources=list(bodies.keys()), http=client
        ).load()
        # `garbage` raises inside _parse_proxy_string; provider must skip
        # rather than aborting the whole source.
        assert [e.server for e in entries] == [
            "http://1.1.1.1:80",
            "http://2.2.2.2:80",
        ]

    def test_default_sources_are_the_four_documented_feeds(self) -> None:
        p = FreeListScraperProvider(http=httpx.Client())
        assert len(p._DEFAULT_SOURCES) == 4  # noqa: SLF001


# ---- FallbackProvider ----------------------------------------------------


class _StubProvider:
    def __init__(self, entries: list[ProxyEntry], *, raises: Exception | None = None) -> None:
        self._entries = entries
        self._raises = raises
        self.load_calls = 0

    def name(self) -> str:
        return f"stub({len(self._entries)})"

    def load(self) -> list[ProxyEntry]:
        self.load_calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._entries)


class TestFallbackProvider:
    def test_returns_first_non_empty_child(self) -> None:
        primary = _StubProvider([ProxyEntry(server="http://a:1")])
        secondary = _StubProvider([ProxyEntry(server="http://b:2")])
        fb = FallbackProvider([primary, secondary])
        entries = fb.load()
        assert [e.server for e in entries] == ["http://a:1"]
        # secondary should not be consulted — primary yielded.
        assert secondary.load_calls == 0

    def test_falls_through_when_primary_empty(self) -> None:
        primary = _StubProvider([])
        secondary = _StubProvider([ProxyEntry(server="http://b:2")])
        entries = FallbackProvider([primary, secondary]).load()
        assert [e.server for e in entries] == ["http://b:2"]
        assert primary.load_calls == 1
        assert secondary.load_calls == 1

    def test_skips_child_that_raises(self) -> None:
        # A single provider outage must NOT starve the pool.
        primary = _StubProvider([], raises=RuntimeError("network down"))
        secondary = _StubProvider([ProxyEntry(server="http://b:2")])
        entries = FallbackProvider([primary, secondary]).load()
        assert [e.server for e in entries] == ["http://b:2"]

    def test_all_empty_returns_empty(self) -> None:
        assert FallbackProvider([_StubProvider([]), _StubProvider([])]).load() == []

    def test_recursive_composition(self) -> None:
        inner = FallbackProvider(
            [_StubProvider([]), _StubProvider([ProxyEntry(server="http://c:3")])]
        )
        outer = FallbackProvider([_StubProvider([]), inner])
        assert [e.server for e in outer.load()] == ["http://c:3"]


# ---- ProxyPool -----------------------------------------------------------


class _StaticClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _pool_with(entries: list[ProxyEntry], **kwargs: Any) -> tuple[ProxyPool, _StaticClock]:
    clock = _StaticClock(datetime(2026, 1, 1, tzinfo=UTC))
    provider = _StubProvider(entries)
    pool = ProxyPool(provider, clock=clock, **kwargs)
    return pool, clock


class TestProxyPool:
    def test_next_returns_none_before_refresh(self) -> None:
        pool, _ = _pool_with([ProxyEntry(server="http://a:1")])
        assert pool.next() is None

    def test_refresh_populates_alive_via_health_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [ProxyEntry(server=f"http://{i}.{i}.{i}.{i}:80") for i in range(1, 4)]
        pool, _ = _pool_with(entries)

        # Force _probe to accept only the middle proxy.
        def fake_probe(self: ProxyPool, entry: ProxyEntry) -> bool:
            return entry.server == "http://2.2.2.2:80"

        monkeypatch.setattr(ProxyPool, "_probe", fake_probe)
        pool.refresh()
        assert pool.alive_count() == 1
        assert pool.next() == ProxyEntry(server="http://2.2.2.2:80")

    def test_next_is_round_robin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [ProxyEntry(server=f"http://{i}.{i}.{i}.{i}:80") for i in range(1, 4)]
        pool, _ = _pool_with(entries)
        monkeypatch.setattr(ProxyPool, "_probe", lambda self, e: True)
        pool.refresh()

        picks = [pool.next().server for _ in range(6)]  # type: ignore[union-attr]
        # Two full cycles.
        assert picks == [
            "http://1.1.1.1:80", "http://2.2.2.2:80", "http://3.3.3.3:80",
            "http://1.1.1.1:80", "http://2.2.2.2:80", "http://3.3.3.3:80",
        ]

    def test_burn_removes_from_rotation_until_cooldown_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries = [ProxyEntry(server="http://a:1"), ProxyEntry(server="http://b:2")]
        pool, clock = _pool_with(entries, cooldown_seconds=60)
        monkeypatch.setattr(ProxyPool, "_probe", lambda self, e: True)
        pool.refresh()
        assert pool.alive_count() == 2

        pool.burn(entries[0], reason="test")
        assert pool.alive_count() == 1
        # Only b is left in rotation.
        assert pool.next() == entries[1]

        # Cooldown still active — burned proxy stays out even on refresh.
        clock.advance(30)
        pool.refresh()
        assert pool.alive_count() == 1

        # After cooldown, refresh brings it back.
        clock.advance(31)
        pool.refresh()
        assert pool.alive_count() == 2

    def test_burn_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [ProxyEntry(server="http://a:1"), ProxyEntry(server="http://b:2")]
        pool, _ = _pool_with(entries)
        monkeypatch.setattr(ProxyPool, "_probe", lambda self, e: True)
        pool.refresh()
        pool.burn(entries[0], reason="first")
        pool.burn(entries[0], reason="second")
        assert pool.alive_count() == 1

    def test_empty_provider_yields_empty_pool(self) -> None:
        pool, _ = _pool_with([])
        pool.refresh()
        assert pool.alive_count() == 0
        assert pool.next() is None
