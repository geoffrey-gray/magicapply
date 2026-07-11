"""Unit tests for GreenhouseAdapter (mocked httpx transport)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import httpx

from magicapply.config.models import GreenhouseSource
from magicapply.infrastructure.sources.greenhouse import GreenhouseAdapter
from magicapply.infrastructure.sources.rate_limit import RateLimiter

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "greenhouse_reddit_jobs.json"


def _mock_http(responses: dict[str, tuple[int, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        try:
            status, body = responses[url_str]
        except KeyError as exc:
            raise AssertionError(f"unexpected fetch: {url_str}") from exc
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestGreenhouseAdapter:
    def test_yields_matching_postings_only(self) -> None:
        body = _FIXTURE.read_text(encoding="utf-8")
        cfg = GreenhouseSource(
            name="gh-reddit",
            boards=["reddit"],
            title_keywords=["staff ml", "staff machine learning"],
            rate_limit_per_minute=600,
        )
        http = _mock_http(
            {
                "https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true": (
                    200,
                    body,
                )
            }
        )
        adapter = GreenhouseAdapter.from_config(cfg, http=http)

        jobs = list(adapter.discover())
        assert len(jobs) == 1
        job = jobs[0]
        assert job.title.startswith("Senior Staff Machine Learning Engineer")
        assert job.url == "https://job-boards.greenhouse.io/reddit/jobs/7772274"
        assert job.apply_url == (
            "https://job-boards.greenhouse.io/embed/job_app?for=reddit&token=7772274"
        )
        assert job.raw.get("greenhouse_board") == "reddit"
        assert job.company == "Reddit"
        assert job.location == "Remote - United States"
        assert "ML platform" in job.description
        assert job.source_name == "gh-reddit"

    def test_stripe_style_absolute_url_sets_greenhouse_apply_url(self) -> None:
        body = json.dumps(
            {
                "jobs": [
                    {
                        "id": 8044460,
                        "title": "AI Engineer",
                        "absolute_url": "https://stripe.com/jobs/search?gh_jid=8044460",
                        "location": {"name": "Remote"},
                        "updated_at": "2026-06-17T10:00:00-04:00",
                        "content": "<p>Build AI systems.</p>",
                    }
                ]
            }
        )
        cfg = GreenhouseSource(
            name="greenhouse-boards",
            boards=["stripe"],
            title_keywords=[],
            rate_limit_per_minute=600,
        )
        http = _mock_http(
            {
                "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true": (
                    200,
                    body,
                )
            }
        )
        adapter = GreenhouseAdapter.from_config(cfg, http=http)
        jobs = list(adapter.discover())
        assert len(jobs) == 1
        job = jobs[0]
        assert job.url == "https://stripe.com/jobs/search?gh_jid=8044460"
        assert job.apply_url == (
            "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8044460"
        )
        assert job.raw.get("greenhouse_board") == "stripe"

    def test_title_filter_drops_non_matching_roles(self) -> None:
        body = _FIXTURE.read_text(encoding="utf-8")
        cfg = GreenhouseSource(
            name="gh-reddit",
            boards=["reddit"],
            title_keywords=["staff data scientist"],
            rate_limit_per_minute=600,
        )
        http = _mock_http(
            {
                "https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true": (
                    200,
                    body,
                )
            }
        )
        adapter = GreenhouseAdapter.from_config(cfg, http=http)
        assert list(adapter.discover()) == []

    def test_rate_limiter_invoked_between_board_fetches(self) -> None:
        body = json.dumps({"jobs": []})
        cfg = GreenhouseSource(
            name="gh-multi",
            boards=["reddit", "anthropic"],
            title_keywords=[],
            rate_limit_per_minute=600,
        )
        http = _mock_http(
            {
                "https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true": (
                    200,
                    body,
                ),
                "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true": (
                    200,
                    body,
                ),
            }
        )
        rate = Mock(spec=RateLimiter)
        adapter = GreenhouseAdapter.from_config(cfg, http=http, rate_limiter=rate)

        list(adapter.discover())
        assert rate.wait.call_count == 2