"""Unit tests for volume discovery URL builders + pagination stop conditions."""

from __future__ import annotations

from magicapply.infrastructure.sources.glassdoor import build_glassdoor_search_url
from magicapply.infrastructure.sources.indeed import build_indeed_search_url
from magicapply.infrastructure.sources.linkedin import build_linkedin_search_url


class TestLinkedInSearchUrl:
    def test_remote_and_week_and_start(self) -> None:
        url = build_linkedin_search_url(
            "Staff Data Scientist",
            remote_only=True,
            posted_within_days=7,
            start=50,
        )
        assert "keywords=Staff+Data+Scientist" in url or "Staff%20Data%20Scientist" in url
        assert "f_WT=2" in url
        assert "f_TPR=r604800" in url
        assert "start=50" in url

    def test_no_filters_omits_params(self) -> None:
        url = build_linkedin_search_url(
            "python",
            remote_only=False,
            posted_within_days=None,
            start=0,
        )
        assert "f_WT=" not in url
        assert "f_TPR=" not in url
        assert "start=" not in url


class TestIndeedSearchUrl:
    def test_remote_location_fromage_start(self) -> None:
        url = build_indeed_search_url(
            "Staff Data Scientist",
            location="Remote",
            remote_only=True,
            posted_within_days=7,
            start=20,
        )
        assert "q=Staff" in url
        assert "l=Remote" in url
        assert "fromage=7" in url
        assert "start=20" in url

    def test_remote_only_defaults_location(self) -> None:
        url = build_indeed_search_url(
            "ds",
            location=None,
            remote_only=True,
            posted_within_days=0,
            start=0,
        )
        assert "l=Remote" in url
        assert "fromage=" not in url


class TestGlassdoorSearchUrl:
    def test_remote_age_and_page(self) -> None:
        url = build_glassdoor_search_url(
            "Staff Data Scientist",
            remote_only=True,
            posted_within_days=7,
            page=3,
        )
        assert "sc.keyword=" in url
        assert "remoteWorkType=1" in url
        assert "fromAge=7" in url
        assert "p=3" in url

    def test_page_one_omits_p(self) -> None:
        url = build_glassdoor_search_url(
            "ds",
            remote_only=False,
            posted_within_days=None,
            page=1,
        )
        assert "p=" not in url
        assert "remoteWorkType=" not in url


class TestConfigDefaults:
    def test_source_models_default_safe_drip_knobs(self) -> None:
        from magicapply.config.models import (
            GlassdoorSource,
            IndeedSource,
            LinkedInSource,
        )

        li = LinkedInSource(name="t", queries=["x"])
        assert li.remote_only is True
        assert li.posted_within_days == 7
        assert li.max_pages == 6
        assert li.rate_limit_per_minute == 3
        assert li.enrich_apply_urls is True
        assert li.linkedin_description_fallback is True
        assert li.max_linkedin_detail_fetches == 8
        assert li.max_jobs_per_run == 150
        assert li.stop_on_redirect_error is True

        for cls in (IndeedSource, GlassdoorSource):
            src = cls(name="t", queries=["x"])
            assert src.remote_only is True
            assert src.posted_within_days == 7
            assert src.max_pages == 5
            assert src.rate_limit_per_minute == 3
            assert src.enrich_apply_urls is True
            assert src.enrich_descriptions is True
            assert src.board_detail_fallback is True
            assert src.max_board_detail_fetches == 8
            assert src.max_jobs_per_run == 50


class TestLinkedInAuthWall:
    def test_login_wall_detected(self) -> None:
        from magicapply.infrastructure.sources.linkedin import (
            looks_like_linkedin_auth_wall,
        )

        assert looks_like_linkedin_auth_wall(
            "<html><body>Sign in to LinkedIn to continue</body></html>"
        )
        assert looks_like_linkedin_auth_wall(
            "<html><div class=\"authwall\">please log in</div></html>"
        )
        assert looks_like_linkedin_auth_wall(
            "<html><form><input name=\"session_key\"/></form></html>"
        )

    def test_normal_search_html_not_wall(self) -> None:
        from magicapply.infrastructure.sources.linkedin import (
            looks_like_linkedin_auth_wall,
        )

        assert not looks_like_linkedin_auth_wall(
            "<html><body><code>{\"entityUrn\":\"urn:li:fsd_jobPosting:1\"}</code>"
            "Staff Data Scientist Remote</body></html>"
        )

    def test_guest_nav_strings_on_serp_not_wall(self) -> None:
        """Real SERPs embed 'Join now' / session_redirect / captcha flags."""
        from magicapply.infrastructure.sources.linkedin import (
            looks_like_linkedin_auth_wall,
        )

        html = """
        <html><body>
          <a href="/signup/cold-join?session_redirect=%2Fjobs">Join now</a>
          <div data-recaptcha-v3-integration-lix-value="control"></div>
          <div class="job-search-card" data-entity-urn="urn:li:jobPosting:1">
            <h3 class="base-search-card__title">Engineer</h3>
          </div>
        </body></html>
        """
        assert not looks_like_linkedin_auth_wall(html)
