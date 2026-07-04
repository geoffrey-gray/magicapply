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
