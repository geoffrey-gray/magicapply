"""Apply URL enrichment parsers for Indeed, Glassdoor, and career pages (PR5)."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources.apply_url import (
    apply_url_from_glassdoor_detail_html,
    apply_url_from_indeed_detail_html,
    apply_url_from_page_html,
    enrich_job_apply_url,
    enrich_job_from_detail_html,
    glassdoor_apply_href_from_html,
    indeed_apply_href_from_html,
)

_WORKDAY_APPLY = (
    "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/STORE-SUPPORT"
)


class TestIndeedDetailHtml:
    def test_apply_on_company_site_container(self) -> None:
        html = f"""
        <html><body>
          <div id="applyButtonLinkContainer">
            <a href="{_WORKDAY_APPLY}">Apply on company site</a>
          </div>
        </body></html>
        """
        assert indeed_apply_href_from_html(html) == _WORKDAY_APPLY
        apply_url = apply_url_from_indeed_detail_html(html)
        assert apply_url is not None
        assert "myworkdayjobs.com" in apply_url

    def test_skips_indeed_internal_apply_links(self) -> None:
        html = """
        <html><body>
          <a href="https://www.indeed.com/applystart?jk=abc">Apply now</a>
        </body></html>
        """
        assert indeed_apply_href_from_html(html) is None


class TestGlassdoorDetailHtml:
    def test_apply_on_company_site_link(self) -> None:
        html = f"""
        <html><body>
          <a href="{_WORKDAY_APPLY}">Apply on company site</a>
        </body></html>
        """
        assert glassdoor_apply_href_from_html(html) == _WORKDAY_APPLY
        assert apply_url_from_glassdoor_detail_html(html) is not None


class TestCareerPageHtml:
    def test_prefers_explicit_apply_anchor(self) -> None:
        page_url = "https://careers.example.com/jobs/staff-ds"
        external = "https://boards.greenhouse.io/acme/jobs/42"
        html = f"""
        <html><body>
          <a href="{external}">Apply now</a>
        </body></html>
        """
        assert apply_url_from_page_html(html, page_url=page_url) == external

    def test_enrich_job_from_page_sets_apply_url(self) -> None:
        listing = "https://careers.example.com/jobs/staff-ds"
        external = "https://symetra.eightfold.ai/careers/job/446718943971"
        job = Job.new(
            source_name="career-page",
            url=listing,
            title="Staff DS",
            company="Example",
        )
        html = f'<html><body><a href="{external}">Apply now</a></body></html>'
        enriched = enrich_job_from_detail_html(
            job, html, source="page", page_url=listing
        )
        assert enriched.apply_url == external
        assert enriched.url == listing
        assert enriched.raw["platform"] == "eightfold"


class TestEnrichJobApplyUrl:
    def test_noop_when_apply_matches_listing(self) -> None:
        url = "https://careers.example.com/jobs/1"
        job = Job.new(source_name="s", url=url, title="T", company="C")
        assert enrich_job_apply_url(job, url) is job