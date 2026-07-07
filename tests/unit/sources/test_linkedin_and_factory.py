"""Unit tests for the LinkedIn adapter guards + build_source dispatch."""

from __future__ import annotations

import json

import pytest

from magicapply.config.models import (
    CareerPageSource,
    GreenhouseSource,
    JobUrlSource,
    LinkedInSource,
)
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)
from magicapply.infrastructure.sources.greenhouse import GreenhouseAdapter
from magicapply.infrastructure.sources.linkedin import (
    LinkedInAdapter,
    extract_job_urls,
    extract_jobs_from_search,
)


class TestLinkedInGuards:
    def test_discover_without_ack_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGICAPPLY_LINKEDIN_ACK", raising=False)
        monkeypatch.setenv("LINKEDIN_LI_AT", "cookie-value")
        adapter = LinkedInAdapter.from_config(
            LinkedInSource(name="linkedin-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="MAGICAPPLY_LINKEDIN_ACK"):
            list(adapter.discover())

    def test_discover_without_cookie_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_LINKEDIN_ACK", "1")
        monkeypatch.delenv("LINKEDIN_LI_AT", raising=False)
        adapter = LinkedInAdapter.from_config(
            LinkedInSource(name="linkedin-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="LINKEDIN_LI_AT"):
            list(adapter.discover())

    def test_empty_queries_ack_returns_nothing_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_LINKEDIN_ACK", "1")
        monkeypatch.setenv("LINKEDIN_LI_AT", "cookie-value")
        adapter = LinkedInAdapter.from_config(
            LinkedInSource(name="linkedin-search", queries=[])
        )
        # No queries -> no browser open, no jobs, no error.
        assert list(adapter.discover()) == []


class TestSearchJobExtractor:
    def test_extracts_voyager_job_posting_cards(self) -> None:
        payload = {
            "included": [
                {
                    "$type": "com.linkedin.voyager.dash.jobs.JobPostingCard",
                    "entityUrn": "urn:li:fsd_jobPostingCard:(4375938610,JOBS_SEARCH)",
                    "jobPostingUrn": "urn:li:fsd_jobPosting:4375938610",
                    "jobPostingTitle": "Data Scientist 5 - Infrastructure Experimentation",
                    "primaryDescription": {"text": "Netflix"},
                    "secondaryDescription": {"text": "United States (Remote)"},
                }
            ]
        }
        html = f"<html><body><code>{json.dumps(payload)}</code></body></html>"
        jobs = extract_jobs_from_search(html, source_name="linkedin-search")
        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Data Scientist 5 - Infrastructure Experimentation"
        assert job.company == "Netflix"
        assert job.location == "United States (Remote)"
        assert job.url == "https://www.linkedin.com/jobs/view/4375938610"


class TestSearchUrlExtractor:
    def test_extracts_relative_and_absolute_urls(self) -> None:
        html = """
        <html><body>
          <a href="/jobs/view/123?refId=x">Job A</a>
          <a href="https://www.linkedin.com/jobs/view/456?trk=y">Job B</a>
          <a href="/jobs/view/123">Job A duplicate</a>
          <a href="/jobs/search/?keywords=go">Search link (skip)</a>
        </body></html>
        """
        urls = extract_job_urls(html)
        assert urls == [
            "https://www.linkedin.com/jobs/view/123",
            "https://www.linkedin.com/jobs/view/456",
        ]

    def test_empty_html_returns_empty(self) -> None:
        assert extract_job_urls("<html><body></body></html>") == []

    def test_strips_query_string(self) -> None:
        html = '<html><body><a href="/jobs/view/999?a=1&b=2">X</a></body></html>'
        assert extract_job_urls(html) == ["https://www.linkedin.com/jobs/view/999"]


class TestFactory:
    def test_career_page(self) -> None:
        cfg = CareerPageSource(name="a", urls=[])
        assert isinstance(build_source(cfg), CareerPageAdapter)

    def test_job_url(self) -> None:
        cfg = JobUrlSource(name="b", urls=[])
        assert isinstance(build_source(cfg), JobUrlAdapter)

    def test_linkedin(self) -> None:
        cfg = LinkedInSource(name="c")
        assert isinstance(build_source(cfg), LinkedInAdapter)

    def test_greenhouse(self) -> None:
        cfg = GreenhouseSource(name="gh", boards=["reddit"])
        assert isinstance(build_source(cfg), GreenhouseAdapter)
