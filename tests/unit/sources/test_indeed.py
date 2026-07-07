"""Unit tests for the Indeed adapter parsers + guard gates."""

from __future__ import annotations

import pytest

from magicapply.config.models import IndeedSource
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.indeed import (
    IndeedAdapter,
    extract_job_urls,
    looks_like_cloudflare,
)


class TestIndeedGuards:
    def test_discover_without_ack_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGICAPPLY_INDEED_ACK", raising=False)
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="MAGICAPPLY_INDEED_ACK"):
            list(adapter.discover())

    def test_empty_queries_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_INDEED_ACK", "1")
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=[])
        )
        assert list(adapter.discover()) == []


class TestSearchUrlExtractor:
    def test_extracts_viewjob_urls_with_jk(self) -> None:
        html = """
        <html><body>
          <a href="/viewjob?jk=abc123&from=serp">Job A</a>
          <a href="https://www.indeed.com/viewjob?jk=def456&other=x">Job B</a>
          <a href="/viewjob?jk=abc123">Job A duplicate</a>
          <a href="/rc/clk?jk=noop">Anchor with jk but wrong path (skip)</a>
          <a href="/jobs?q=go">Search facet (skip)</a>
        </body></html>
        """
        urls = extract_job_urls(html)
        assert urls == [
            "https://www.indeed.com/viewjob?jk=abc123",
            "https://www.indeed.com/viewjob?jk=def456",
        ]

    def test_empty_page_returns_empty(self) -> None:
        assert extract_job_urls("<html><body></body></html>") == []

    def test_ignores_viewjob_without_jk(self) -> None:
        html = '<html><body><a href="/viewjob?foo=bar">skip</a></body></html>'
        assert extract_job_urls(html) == []


class TestCloudflareDetection:
    def test_detects_cloudflare_challenge_title(self) -> None:
        assert looks_like_cloudflare("<title>Just a moment...</title>")

    def test_detects_challenges_domain(self) -> None:
        assert looks_like_cloudflare(
            '<script src="https://challenges.cloudflare.com/turnstile"></script>'
        )

    def test_detects_cf_challenge_marker(self) -> None:
        assert looks_like_cloudflare('<div id="cf-challenge-running">')

    def test_detects_checking_your_browser(self) -> None:
        assert looks_like_cloudflare("<h1>Checking your browser</h1>")

    def test_detects_indeed_blocked_title(self) -> None:
        assert looks_like_cloudflare("<title>Blocked - Indeed.com</title>")

    def test_clean_html_is_not_flagged(self) -> None:
        assert not looks_like_cloudflare(
            "<html><body><h1>Software Engineer</h1></body></html>"
        )
