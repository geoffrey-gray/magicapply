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
from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)
from magicapply.infrastructure.sources.greenhouse import GreenhouseAdapter
from magicapply.infrastructure.sources.apply_url import apply_url_from_linkedin_detail_html
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
        monkeypatch.delenv("LINKEDIN_SESSION_COOKIES", raising=False)
        adapter = LinkedInAdapter.from_config(
            LinkedInSource(name="linkedin-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="auth login linkedin|LINKEDIN"):
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


class TestApplyUrlEnrichment:
    def test_detail_html_yields_workday_apply_url(self) -> None:
        safety = (
            "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fhomedepot.wd5"
            ".myworkdayjobs.com%2FCareerDepot%2Fjob%2FReq185496"
        )
        html = f'<html><body><a aria-label="Apply" href="{safety}">Apply</a></body></html>'
        apply_url = apply_url_from_linkedin_detail_html(html)
        assert apply_url is not None
        assert "myworkdayjobs.com" in apply_url

    def test_enriched_job_model_copy_preserves_listing_id(self) -> None:
        listing = "https://www.linkedin.com/jobs/view/4432714211"
        apply_url = "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/1"
        job = Job.new(
            source_name="linkedin-search",
            url=listing,
            title="Data Science Manager",
            company="The Home Depot",
        )
        enriched = job.model_copy(
            update={
                "apply_url": apply_url,
                "raw": {**job.raw, "platform": "workday", "listing_url": listing},
            }
        )
        assert enriched.id == job.id
        assert enriched.url == listing
        assert enriched.apply_url == apply_url
        assert enriched.effective_apply_url == apply_url

    def test_from_config_honors_enrich_apply_urls_flag(self) -> None:
        adapter = LinkedInAdapter.from_config(
            LinkedInSource(name="linkedin-search", enrich_apply_urls=False)
        )
        assert adapter._enrich_apply_urls is False


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
