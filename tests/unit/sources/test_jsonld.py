"""Tests for JSON-LD JobPosting extraction."""

from __future__ import annotations

from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)

_JOBPOSTING_SNIPPET = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Software Engineer",
  "description": "Build fun things.",
  "url": "https://acme.com/j/1",
  "hiringOrganization": {"@type": "Organization", "name": "Acme"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Boston",
      "addressRegion": "MA",
      "addressCountry": "US"
    }
  }
}
</script>
</head>
</html>
"""

_MULTIPLE_POSTINGS = """
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "JobPosting", "title": "Backend Eng", "hiringOrganization": {"name": "A"}},
    {"@type": "JobPosting", "title": "Frontend Eng", "hiringOrganization": {"name": "A"}}
  ]
}
</script>
"""


class TestExtract:
    def test_single_posting(self) -> None:
        postings = extract_jobposting_dicts(_JOBPOSTING_SNIPPET)
        assert len(postings) == 1
        assert postings[0]["title"] == "Senior Software Engineer"

    def test_multiple_via_graph(self) -> None:
        postings = extract_jobposting_dicts(_MULTIPLE_POSTINGS)
        titles = sorted(p["title"] for p in postings)
        assert titles == ["Backend Eng", "Frontend Eng"]

    def test_ignores_non_jobposting_types(self) -> None:
        html = """
<script type="application/ld+json">
{"@type": "Organization", "name": "Acme"}
</script>
"""
        assert extract_jobposting_dicts(html) == []

    def test_ignores_invalid_json(self) -> None:
        html = '<script type="application/ld+json">not-json{{</script>'
        assert extract_jobposting_dicts(html) == []

    def test_handles_missing_script(self) -> None:
        assert extract_jobposting_dicts("<html></html>") == []

    def test_type_can_be_a_list(self) -> None:
        html = """
<script type="application/ld+json">
{"@type": ["Thing", "JobPosting"], "title": "SWE"}
</script>
"""
        assert len(extract_jobposting_dicts(html)) == 1


class TestJsonldToJob:
    def test_full_posting(self) -> None:
        posting = extract_jobposting_dicts(_JOBPOSTING_SNIPPET)[0]
        job = jsonld_to_job(posting, source_name="acme-careers", fallback_url="https://acme.com")
        assert job.title == "Senior Software Engineer"
        assert job.company == "Acme"
        assert job.location == "Boston, MA, US"
        assert job.url == "https://acme.com/j/1"
        assert job.source_name == "acme-careers"

    def test_missing_url_uses_fallback(self) -> None:
        job = jsonld_to_job(
            {"@type": "JobPosting", "title": "T", "hiringOrganization": {"name": "C"}},
            source_name="s",
            fallback_url="https://fallback.com/careers",
        )
        assert job.url.startswith("https://fallback.com/careers")

    def test_missing_company_becomes_unknown(self) -> None:
        job = jsonld_to_job(
            {"@type": "JobPosting", "title": "T", "url": "https://a.com/1"},
            source_name="s",
            fallback_url="https://fallback.com",
        )
        assert "unknown" in job.company.lower()

    def test_missing_title_becomes_untitled(self) -> None:
        job = jsonld_to_job(
            {"@type": "JobPosting", "hiringOrganization": {"name": "C"}, "url": "https://a.com/1"},
            source_name="s",
            fallback_url="https://a.com",
        )
        assert "untitled" in job.title.lower()
