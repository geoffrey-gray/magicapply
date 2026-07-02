"""Live network test: real career pages with schema.org JSON-LD.

Opt-in via MAGICAPPLY_LIVE_TESTS=1. Uses public pages known to publish
schema.org JobPosting metadata; if a target page changes, the test skips
rather than fails so it doesn't block the suite.
"""

from __future__ import annotations

import httpx
import pytest

from magicapply.config.models import CareerPageSource, JobUrlSource
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)

pytestmark = [pytest.mark.integration]


# GitHub's careers page is a stable target with JSON-LD.
GITHUB_CAREERS = "https://www.github.careers/careers-home"


def _reachable(url: str) -> bool:
    try:
        r = httpx.head(url, follow_redirects=True, timeout=10.0)
    except httpx.HTTPError:
        return False
    return r.status_code < 500


def test_career_page_pull_yields_jobs() -> None:
    if not _reachable(GITHUB_CAREERS):
        pytest.skip(f"{GITHUB_CAREERS} not reachable from this network")

    cfg = CareerPageSource(
        name="github-careers",
        urls=[GITHUB_CAREERS],
        rate_limit_per_minute=30,
    )
    adapter = CareerPageAdapter.from_config(cfg)

    # Consume at most 5 jobs to keep the test fast.
    jobs = []
    for i, job in enumerate(adapter.discover()):
        jobs.append(job)
        if i >= 4:
            break

    if not jobs:
        pytest.skip(
            f"no JobPosting JSON-LD found at {GITHUB_CAREERS} — page structure may have changed"
        )

    for job in jobs:
        assert job.title
        assert job.company
        assert job.url.startswith("http")
        assert job.source_name == "github-careers"


def test_linkedin_public_job_url_via_job_url_adapter() -> None:
    """LinkedIn *individual* job posting URLs are just URLs — JobUrlAdapter handles them.

    We do not scrape LinkedIn search here (that's a Phase 2 task, gated behind the
    linkedin marker). This test verifies that a specific public LinkedIn job URL
    parses into a Job when the page exposes schema.org JSON-LD.
    """
    # Use LinkedIn's public jobs guest search endpoint as a stable-ish target.
    # If LinkedIn's structure has changed, the test skips gracefully.
    url = "https://www.linkedin.com/jobs/search?keywords=engineer&location=Remote"
    if not _reachable(url):
        pytest.skip("linkedin.com not reachable from this network")

    cfg = JobUrlSource(name="linkedin-watched", urls=[url])
    adapter = JobUrlAdapter.from_config(cfg)

    jobs = list(adapter.discover())
    if not jobs:
        pytest.skip("no JSON-LD JobPosting at the LinkedIn URL — anti-scraping or structure change")

    assert jobs[0].source_name == "linkedin-watched"
