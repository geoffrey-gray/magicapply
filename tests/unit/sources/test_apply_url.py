"""Tests for apply URL resolution and platform sniffing (PR2)."""

from __future__ import annotations

from magicapply.infrastructure.sources.apply_url import (
    apply_url_from_linkedin_detail_html,
    decode_linkedin_safety_go,
    is_big_four_platform,
    linkedin_apply_href_from_html,
    resolve_apply_href,
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

    def test_big_four_helper(self) -> None:
        assert is_big_four_platform("workday")
        assert not is_big_four_platform("eightfold")