"""Tests for apply URL resolution and platform sniffing (PR2)."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources.apply_url import (
    apply_url_from_linkedin_detail_html,
    decode_linkedin_safety_go,
    description_from_destination_html,
    is_big_four_platform,
    is_greenhouse_apply_url,
    is_job_board_listing_url,
    linkedin_apply_href_from_html,
    resolve_apply_href,
    resolve_greenhouse_apply_url,
    resolve_job_apply_destination,
    sniff_platform,
)

_PARAMOUNT_SAFETY = (
    "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fcareers%2Eparamount%2Ecom"
    "%2Fjob%2FNew-York-Senior-Data-Scientist-NY-10036%2F1394334600%2F%3FfeedId%3D404800"
    "&urlhash=KIRx"
)
_PARAMOUNT_EXTERNAL = (
    "https://careers.paramount.com/job/New-York-Senior-Data-Scientist-NY-10036/1394334600/"
    "?feedId=404800"
)


class TestDecodeLinkedInSafetyGo:
    def test_decodes_paramount_redirect(self) -> None:
        decoded = decode_linkedin_safety_go(_PARAMOUNT_SAFETY)
        assert decoded is not None
        assert decoded.startswith("https://careers.paramount.com/job/")

    def test_non_linkedin_href_returns_none(self) -> None:
        assert decode_linkedin_safety_go("https://careers.paramount.com/job/1") is None

    def test_none_returns_none(self) -> None:
        assert decode_linkedin_safety_go(None) is None


class TestResolveApplyHref:
    def test_safety_go_resolves_to_external(self) -> None:
        url = resolve_apply_href(_PARAMOUNT_SAFETY)
        assert url is not None
        assert "careers.paramount.com" in url

    def test_direct_external_passthrough(self) -> None:
        direct = "https://symetra.eightfold.ai/careers/job/446718943971"
        assert resolve_apply_href(direct) == direct


class TestLinkedInDetailHtml:
    def test_extracts_apply_href_from_detail_page(self) -> None:
        html = f'<html><body><a aria-label="Apply" href="{_PARAMOUNT_SAFETY}">Apply</a></body></html>'
        assert linkedin_apply_href_from_html(html) == _PARAMOUNT_SAFETY

    def test_resolves_external_apply_url_from_detail_html(self) -> None:
        html = f'<html><body><a aria-label="Apply" href="{_PARAMOUNT_SAFETY}">Apply</a></body></html>'
        apply_url = apply_url_from_linkedin_detail_html(html)
        assert apply_url is not None
        assert "careers.paramount.com" in apply_url
        assert "utm" not in apply_url


class TestDescriptionFromDestination:
    def test_jsonld_jobposting_description(self) -> None:
        html = """
        <html><body>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Engineer",
         "description":"<p>We need Python, Kubernetes, and SQL for production systems across distributed services and data platforms at scale.</p>"}
        </script>
        </body></html>
        """
        desc = description_from_destination_html(html)
        assert "Python" in desc
        assert "Kubernetes" in desc
        assert "<p>" not in desc

    def test_main_content_fallback(self) -> None:
        body = "x" * 50 + " Looking for a Staff Data Scientist with Spark experience. " + "y" * 50
        html = f"<html><body><main>{body}</main></body></html>"
        desc = description_from_destination_html(html)
        assert "Staff Data Scientist" in desc
        assert "Spark" in desc


class TestSniffPlatform:
    def test_workday(self) -> None:
        assert (
            sniff_platform(
                "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/Req185496"
            )
            == "workday"
        )

    def test_eightfold(self) -> None:
        assert (
            sniff_platform("https://symetra.eightfold.ai/careers/job/446718943971")
            == "eightfold"
        )

    def test_greenhouse(self) -> None:
        assert (
            sniff_platform("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"
        )

    def test_unknown_is_generic(self) -> None:
        assert sniff_platform("https://careers.hyatt.com/en-US/careers/jobdetails/1") == "generic"

    def test_icims_talentcommunity_path(self) -> None:
        assert (
            sniff_platform(
                "https://careers.paramount.com/talentcommunity/apply/1394334600/"
            )
            == "icims"
        )

    def test_big_four_helper(self) -> None:
        assert is_big_four_platform("workday")
        assert not is_big_four_platform("eightfold")


class TestGreenhouseApplyResolve:
    def test_already_greenhouse_absolute_url(self) -> None:
        url = "https://job-boards.greenhouse.io/reddit/jobs/7772274"
        assert resolve_greenhouse_apply_url(
            board_slug="reddit",
            posting_id=7772274,
            absolute_url=url,
        ) == url

    def test_stripe_careers_rewritten_via_board_and_id(self) -> None:
        got = resolve_greenhouse_apply_url(
            board_slug="stripe",
            posting_id=8044460,
            absolute_url="https://stripe.com/jobs/search?gh_jid=8044460",
        )
        assert got == "https://job-boards.greenhouse.io/stripe/jobs/8044460"
        assert is_greenhouse_apply_url(got)

    def test_gh_jid_query_with_board_slug(self) -> None:
        got = resolve_greenhouse_apply_url(
            board_slug="stripe",
            posting_id=None,
            absolute_url="https://stripe.com/jobs/search?gh_jid=12345",
        )
        assert got == "https://job-boards.greenhouse.io/stripe/jobs/12345"

    def test_missing_slug_and_non_gh_absolute_returns_none(self) -> None:
        assert (
            resolve_greenhouse_apply_url(
                board_slug=None,
                posting_id=1,
                absolute_url="https://stripe.com/jobs/search?gh_jid=1",
            )
            is None
        )


class TestJobBoardListingAndDestination:
    def test_indeed_viewjob_is_board_listing(self) -> None:
        assert is_job_board_listing_url(
            "https://www.indeed.com/viewjob?jk=5d994596ea5f047b"
        )

    def test_greenhouse_is_not_board_listing(self) -> None:
        assert not is_job_board_listing_url(
            "https://job-boards.greenhouse.io/reddit/jobs/1"
        )

    def test_resolve_destination_from_raw_greenhouse_board(self) -> None:
        job = Job.new(
            source_name="greenhouse-boards",
            url="https://stripe.com/jobs/search?gh_jid=8044460",
            title="AI Engineer",
            company="Stripe",
            raw={
                "id": 8044460,
                "absolute_url": "https://stripe.com/jobs/search?gh_jid=8044460",
                "greenhouse_board": "stripe",
            },
        )
        dest = resolve_job_apply_destination(job)
        assert dest == "https://job-boards.greenhouse.io/stripe/jobs/8044460"
        assert is_greenhouse_apply_url(dest)