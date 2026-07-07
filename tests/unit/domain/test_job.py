"""Tests for the Job model and URL canonicalization."""

from __future__ import annotations

from magicapply.domain.models.job import Job, canonicalize_url, hash_url


class TestCanonicalizeUrl:
    def test_lowercases_scheme_and_host(self) -> None:
        assert canonicalize_url("HTTPS://Acme.COM/jobs/1") == "https://acme.com/jobs/1"

    def test_drops_tracking_params(self) -> None:
        url = "https://acme.com/jobs/1?utm_source=x&utm_campaign=y&ref=slack&role=eng"
        assert canonicalize_url(url) == "https://acme.com/jobs/1?role=eng"

    def test_sorts_remaining_params(self) -> None:
        url = "https://acme.com/j?b=2&a=1"
        assert canonicalize_url(url) == "https://acme.com/j?a=1&b=2"

    def test_strips_trailing_slash(self) -> None:
        assert canonicalize_url("https://acme.com/jobs/1/") == "https://acme.com/jobs/1"

    def test_root_path_kept(self) -> None:
        assert canonicalize_url("https://acme.com/") == "https://acme.com/"

    def test_drops_fragment(self) -> None:
        assert canonicalize_url("https://acme.com/j/1#apply") == "https://acme.com/j/1"

    def test_default_scheme_https(self) -> None:
        # Schemeless input should be treated as https.
        assert canonicalize_url("acme.com/jobs/1") == "https://acme.com/jobs/1"


class TestHashUrl:
    def test_stable_and_deterministic(self) -> None:
        assert hash_url("https://a.com/j/1") == hash_url("https://a.com/j/1")

    def test_different_inputs_different_hashes(self) -> None:
        assert hash_url("https://a.com/j/1") != hash_url("https://a.com/j/2")

    def test_length_16(self) -> None:
        assert len(hash_url("https://a.com/j/1")) == 16


class TestJob:
    def test_new_canonicalizes_url(self) -> None:
        j = Job.new(
            source_name="acme-careers",
            url="HTTPS://Acme.COM/j/1?utm_source=x",
            title="Senior SWE",
            company="Acme",
        )
        assert j.url == "https://acme.com/j/1"
        assert j.id == hash_url("https://acme.com/j/1")

    def test_same_url_diff_case_same_id(self) -> None:
        a = Job.new(source_name="s", url="https://a.com/1", title="t", company="c")
        b = Job.new(source_name="s", url="HTTPS://A.COM/1/", title="t", company="c")
        assert a.id == b.id

    def test_different_urls_different_ids(self) -> None:
        a = Job.new(source_name="s", url="https://a.com/1", title="t", company="c")
        b = Job.new(source_name="s", url="https://a.com/2", title="t", company="c")
        assert a.id != b.id

    def test_dedup_key_normalizes_company_and_title(self) -> None:
        a = Job.new(
            source_name="s1",
            url="https://a.com/1",
            title="Senior SWE!",
            company="Acme, Inc.",
        )
        b = Job.new(
            source_name="s2",
            url="https://b.com/xyz",
            title="senior swe",
            company="acme inc",
        )
        assert a.dedup_key == b.dedup_key
        assert a.id != b.id  # same job, different upstream URLs

    def test_description_defaults_empty(self) -> None:
        j = Job.new(source_name="s", url="https://a.com/1", title="t", company="c")
        assert j.description == ""

    def test_discovered_at_is_utc(self) -> None:
        j = Job.new(source_name="s", url="https://a.com/1", title="t", company="c")
        assert j.discovered_at.tzinfo is not None

    def test_apply_url_canonicalized_separately_from_listing(self) -> None:
        listing = "https://www.linkedin.com/jobs/view/4432714211"
        apply = (
            "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/Req185496"
            "?utm_source=LinkedIn"
        )
        j = Job.new(
            source_name="linkedin-search",
            url=listing,
            apply_url=apply,
            title="Data Science Manager",
            company="The Home Depot",
        )
        assert j.url == listing
        assert j.apply_url == (
            "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/Req185496"
        )
        assert j.id == hash_url(listing)
        assert j.effective_apply_url == j.apply_url

    def test_effective_apply_url_falls_back_to_listing(self) -> None:
        j = Job.new(source_name="s", url="https://a.com/1", title="t", company="c")
        assert j.effective_apply_url == j.url
