"""Unit tests for the Glassdoor adapter parsers + guard gates."""

from __future__ import annotations

import pytest

from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.glassdoor import (
    GlassdoorAdapter,
    extract_job_urls,
    extract_jobs_from_search,
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


class TestSearchPageCardExtractor:
    """Verify `extract_jobs_from_search` pulls full Job records straight
    from the search-page's `<li data-test="jobListing">` DOM cards,
    bypassing detail-page fetches. Shape mirrors what a real Glassdoor
    search page renders — see live capture from 2026-07-09."""

    @staticmethod
    def _card_html(
        *,
        jobid: str,
        title: str,
        company: str | None,
        href: str = "/job-listing/some-slug-JV_IC1_KO0.htm?jl={jobid}",
        location: str | None = "Remote",
        snippet: str | None = "Great role summary here.",
        salary: str | None = None,
    ) -> str:
        parts = [
            f'<li data-test="jobListing" data-jobid="{jobid}">',
            f'  <a data-test="job-title" href="{href.format(jobid=jobid)}">{title}</a>',
        ]
        if company is not None:
            parts.append(
                f'  <div id="job-employer-{jobid}">'
                f'    <span class="EmployerProfile_compactEmployerName__xyz">{company}</span>'
                f'    <span class="rating-single-star_RatingText">3.5</span>'
                f"  </div>"
            )
        if location is not None:
            parts.append(f'  <div data-test="emp-location">{location}</div>')
        if snippet is not None:
            parts.append(f'  <div data-test="descSnippet">{snippet}</div>')
        if salary is not None:
            parts.append(f'  <div data-test="detailSalary">{salary}</div>')
        parts.append("</li>")
        return "\n".join(parts)

    def _page(self, cards_html: list[str]) -> str:
        return (
            '<html><body><ul class="JobsList">'
            + "".join(cards_html)
            + "</ul></body></html>"
        )

    def test_extracts_full_card(self) -> None:
        html = self._page([
            self._card_html(
                jobid="777",
                title="Senior Data Scientist",
                company="Acme AI",
                location="San Francisco, CA",
                snippet="Own ML platform.",
                salary="$200K - $260K",
            )
        ])
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Senior Data Scientist"
        assert j.company == "Acme AI"
        assert j.location == "San Francisco, CA"
        assert "ML platform" in j.description
        assert j.url == (
            "https://www.glassdoor.com/job-listing/some-slug-JV_IC1_KO0.htm"
        )
        assert j.raw["jobid"] == "777"
        assert j.raw["salary_snippet"] == "$200K - $260K"
        assert j.raw["source_extraction"] == "search-page-card"

    def test_dedupes_by_jobid(self) -> None:
        html = self._page([
            self._card_html(jobid="42", title="A", company="Acme"),
            self._card_html(jobid="42", title="A dup", company="Acme"),
            self._card_html(jobid="99", title="B", company="Beta"),
        ])
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert [j.raw["jobid"] for j in jobs] == ["42", "99"]

    def test_skips_cards_missing_company_or_title(self) -> None:
        html = self._page([
            self._card_html(jobid="1", title="OK", company="Acme"),
            self._card_html(jobid="2", title="", company="Beta"),
            self._card_html(jobid="3", title="No company", company=None),
        ])
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert [j.raw["jobid"] for j in jobs] == ["1"]

    def test_absolute_href_kept_as_is(self) -> None:
        html = self._page([
            self._card_html(
                jobid="55",
                title="Eng",
                company="Delta",
                href="https://www.glassdoor.com/job-listing/eng-delta-JV_1.htm?jl={jobid}",
            )
        ])
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert jobs[0].url == "https://www.glassdoor.com/job-listing/eng-delta-JV_1.htm"

    def test_no_cards_returns_empty(self) -> None:
        assert extract_jobs_from_search(
            "<html><body>no cards here</body></html>",
            source_name="glassdoor-search",
        ) == []

    def test_card_captures_external_apply_url(self) -> None:
        html = (
            '<html><body><ul>'
            '<li data-test="jobListing" data-jobid="88">'
            '  <a data-test="job-title" href="/job-listing/x-JV_1.htm">Role</a>'
            '  <div id="job-employer-88">'
            '    <span class="EmployerProfile_compactEmployerName__xyz">Acme</span>'
            "  </div>"
            '  <a href="https://boards.greenhouse.io/acme/jobs/123">Apply on company site</a>'
            "</li></ul></body></html>"
        )
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert len(jobs) == 1
        assert jobs[0].apply_url is not None
        assert "greenhouse.io" in jobs[0].apply_url
        assert jobs[0].raw.get("apply_resolve") == "serp_card"

    def test_company_fallback_strips_rating_suffix(self) -> None:
        """When the compactEmployerName span isn't present, the extractor
        falls back to the `job-employer-<id>` div's full text and strips
        the trailing rating (e.g. `Ultragenyx3.4` → `Ultragenyx`)."""
        html = (
            '<html><body><ul>'
            '<li data-test="jobListing" data-jobid="99">'
            '  <a data-test="job-title" href="/job-listing/x-JV_1.htm">Role</a>'
            '  <div id="job-employer-99">Ultragenyx3.4</div>'
            '  <div data-test="emp-location">Remote</div>'
            '</li></ul></body></html>'
        )
        jobs = extract_jobs_from_search(html, source_name="glassdoor-search")
        assert len(jobs) == 1
        assert jobs[0].company == "Ultragenyx"


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
        assert constructor_kwargs[0]["headless"] is True
        assert constructor_kwargs[0]["proxy_pool"] is None
