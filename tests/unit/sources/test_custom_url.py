"""Tests for CareerPageAdapter / JobUrlAdapter (mocked httpx transport)."""

from __future__ import annotations

import httpx
import pytest

from magicapply.config.models import CareerPageSource, JobUrlSource
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)

_TWO_POSTINGS_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Backend Eng", "url": "https://acme.com/j/1",
 "hiringOrganization": {"name": "Acme"}}
</script>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Frontend Eng", "url": "https://acme.com/j/2",
 "hiringOrganization": {"name": "Acme"}}
</script>
</head></html>
"""


def _mock_http(responses: dict[str, tuple[int, str]]) -> httpx.Client:
    """Build an httpx client backed by a MockTransport keyed on URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        try:
            status, body = responses[url_str]
        except KeyError as exc:
            raise AssertionError(f"unexpected fetch: {url_str}") from exc
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestCareerPageAdapter:
    def test_yields_jobs_from_jsonld(self) -> None:
        cfg = CareerPageSource(
            name="acme",
            urls=["https://acme.com/careers"],
            rate_limit_per_minute=600,  # fast for tests
        )
        http = _mock_http({"https://acme.com/careers": (200, _TWO_POSTINGS_HTML)})
        adapter = CareerPageAdapter.from_config(cfg, http=http)

        jobs = list(adapter.discover())
        assert [j.title for j in jobs] == ["Backend Eng", "Frontend Eng"]
        assert {j.source_name for j in jobs} == {"acme"}

    def test_404_skips_url(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = CareerPageSource(name="acme", urls=["https://acme.com/x"], rate_limit_per_minute=600)
        http = _mock_http({"https://acme.com/x": (404, "not found")})
        adapter = CareerPageAdapter.from_config(cfg, http=http)

        assert list(adapter.discover()) == []

    def test_multiple_urls_iterated(self) -> None:
        cfg = CareerPageSource(
            name="acme",
            urls=["https://acme.com/a", "https://acme.com/b"],
            rate_limit_per_minute=600,
        )
        http = _mock_http(
            {
                "https://acme.com/a": (200, _TWO_POSTINGS_HTML),
                "https://acme.com/b": (200, _TWO_POSTINGS_HTML),
            }
        )
        adapter = CareerPageAdapter.from_config(cfg, http=http)
        jobs = list(adapter.discover())
        # 2 URLs x 2 postings = 4 jobs (before any dedup).
        assert len(jobs) == 4


class TestJobUrlAdapter:
    def test_single_url_single_job(self) -> None:
        cfg = JobUrlSource(name="watched", urls=["https://acme.com/j/1"])
        html = """
<script type="application/ld+json">
{"@type": "JobPosting", "title": "SWE", "url": "https://acme.com/j/1",
 "hiringOrganization": {"name": "Acme"}}
</script>
"""
        http = _mock_http({"https://acme.com/j/1": (200, html)})
        adapter = JobUrlAdapter.from_config(cfg, http=http)

        jobs = list(adapter.discover())
        assert len(jobs) == 1
        assert jobs[0].title == "SWE"
