"""Unit tests for shared SERP → offsite → capped detail enrichment."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources.apply_url import (
    BOARD_HOSTS_INDEED,
    BOARD_HOSTS_LINKEDIN,
    is_external_apply_url,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter
from magicapply.infrastructure.sources.serp_enrich import (
    DetailBudget,
    SerpEnrichPolicy,
    description_incomplete,
    post_serp_enrich,
)


class _NoopSession:
    def new_page(self, **_kwargs):  # noqa: ANN003
        raise AssertionError("destination fetch should not open a page in these tests")


class TestIsExternalApplyUrl:
    def test_external_ats(self) -> None:
        assert is_external_apply_url(
            "https://jobs.ashbyhq.com/acme/1", board_hosts=BOARD_HOSTS_INDEED
        )

    def test_rejects_board_host(self) -> None:
        assert not is_external_apply_url(
            "https://www.indeed.com/viewjob?jk=1", board_hosts=BOARD_HOSTS_INDEED
        )
        assert not is_external_apply_url(
            "https://www.linkedin.com/jobs/view/1", board_hosts=BOARD_HOSTS_LINKEDIN
        )

    def test_empty(self) -> None:
        assert not is_external_apply_url(None, board_hosts=BOARD_HOSTS_INDEED)
        assert not is_external_apply_url("", board_hosts=BOARD_HOSTS_INDEED)


class TestDescriptionIncomplete:
    def test_short_snippet(self) -> None:
        assert description_incomplete("short teaser")

    def test_long_enough(self) -> None:
        assert not description_incomplete("x" * 250)


class TestDetailBudget:
    def test_cap(self) -> None:
        b = DetailBudget(max_fetches=2)
        assert b.consume(board="Indeed")
        assert b.consume(board="Indeed")
        assert not b.consume(board="Indeed")
        assert not b.remaining()


class TestPostSerpEnrich:
    def test_require_external_skips_without_apply(self) -> None:
        job = Job.new(
            source_name="indeed",
            url="https://www.indeed.com/viewjob?jk=1",
            title="Eng",
            company="Acme",
            description="x" * 50,
        )
        rate = RateLimiter(6000, jitter_ratio=0.0)
        out = post_serp_enrich(
            _NoopSession(),  # type: ignore[arg-type]
            job,
            rate,
            policy=SerpEnrichPolicy(
                enrich_apply_urls=False,
                enrich_descriptions=False,
                board_detail_fallback=False,
                require_external_apply=True,
                board_hosts=BOARD_HOSTS_INDEED,
                board_label="Indeed",
            ),
            budget=DetailBudget(8),
            board_detail_fn=lambda *a, **k: job,
        )
        assert out is None

    def test_budget_zero_skips_detail(self) -> None:
        job = Job.new(
            source_name="indeed",
            url="https://www.indeed.com/viewjob?jk=1",
            title="Eng",
            company="Acme",
            description="short",
        )
        called: list[str] = []

        def detail_fn(session, j, rate):  # noqa: ANN001
            called.append("detail")
            return j

        rate = RateLimiter(6000, jitter_ratio=0.0)
        out = post_serp_enrich(
            _NoopSession(),  # type: ignore[arg-type]
            job,
            rate,
            policy=SerpEnrichPolicy(
                enrich_apply_urls=True,
                enrich_descriptions=True,
                board_detail_fallback=True,
                require_external_apply=False,
                board_hosts=BOARD_HOSTS_INDEED,
                board_label="Indeed",
            ),
            budget=DetailBudget(0),
            board_detail_fn=detail_fn,
        )
        assert out is not None
        assert called == []

    def test_detail_called_when_missing_apply(self) -> None:
        job = Job.new(
            source_name="indeed",
            url="https://www.indeed.com/viewjob?jk=1",
            title="Eng",
            company="Acme",
            description="x" * 250,  # long enough — only need apply
        )
        called: list[str] = []

        def detail_fn(session, j, rate):  # noqa: ANN001
            called.append("detail")
            return j.model_copy(
                update={
                    "apply_url": "https://jobs.ashbyhq.com/acme/1",
                    "raw": {**j.raw, "apply_resolve": "indeed_detail"},
                }
            )

        rate = RateLimiter(6000, jitter_ratio=0.0)
        out = post_serp_enrich(
            _NoopSession(),  # type: ignore[arg-type]
            job,
            rate,
            policy=SerpEnrichPolicy(
                enrich_apply_urls=True,
                enrich_descriptions=True,
                board_detail_fallback=True,
                require_external_apply=False,
                board_hosts=BOARD_HOSTS_INDEED,
                board_label="Indeed",
            ),
            budget=DetailBudget(2),
            board_detail_fn=detail_fn,
        )
        assert called == ["detail"]
        assert out is not None
        assert out.apply_url is not None
        assert "ashbyhq.com" in out.apply_url
