"""Custom URL job sources: career pages and hand-curated job URLs.

Both adapters fetch pages via httpx and extract schema.org JobPosting JSON-LD.
They differ only in what `urls` means:

- CareerPageAdapter: URLs point at listing pages (e.g. `example.com/careers`).
  JSON-LD blocks typically enumerate multiple postings per page.
- JobUrlAdapter: URLs point at individual job postings; each is expected to
  contain exactly one JSON-LD JobPosting.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from magicapply.config.models import CareerPageSource, JobUrlSource
from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.apply_url import enrich_job_from_detail_html
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    pass

# SPA boards (Ashby) often omit JSON-LD from the initial HTTP response.
_ATS_FALLBACK_HOST = re.compile(
    r"(?:jobs\.ashbyhq\.com|jobs\.lever\.co|myworkdayjobs\.com|"
    r"boards\.greenhouse\.io|job-boards\.greenhouse\.io)",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) MagicApply/0.1 (+https://github.com/geoffreygray/magicapply)"
)

# Public re-export so callers can hand a raw HTML page to the parser directly.
extract_jsonld_jobs = extract_jobposting_dicts


class _JsonLdHttpAdapter:
    """Shared implementation for adapters that just fetch URLs and parse JSON-LD."""

    def __init__(
        self,
        *,
        name: str,
        urls: list[str],
        rate_limiter: RateLimiter,
        http: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self._urls = list(urls)
        self._rate_limiter = rate_limiter
        self._http = http or httpx.Client(
            headers={"User-Agent": _DEFAULT_UA},
            follow_redirects=True,
            timeout=20.0,
        )

    def discover(
        self, *, known_ids: frozenset[str] | None = None
    ) -> Iterator[Job]:
        known = known_ids or frozenset()
        for url in self._urls:
            self._rate_limiter.wait()
            try:
                response = self._http.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("fetch failed for %s: %s", url, exc)
                continue

            postings = extract_jobposting_dicts(response.text)
            if not postings:
                job = _job_from_ats_url_fallback(url, source_name=self.name)
                if job is None:
                    logger.info("no JSON-LD JobPosting on %s", url)
                    continue
                if job.id not in known:
                    yield job
                continue

            for posting in postings:
                try:
                    job = jsonld_to_job(
                        posting, source_name=self.name, fallback_url=url
                    )
                    if job.id in known:
                        continue
                    job = enrich_job_from_detail_html(
                        job,
                        response.text,
                        source="page",
                        page_url=url,
                    )
                    yield job
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("skip malformed JSON-LD from %s: %s", url, exc)


def _job_from_ats_url_fallback(url: str, *, source_name: str) -> Job | None:
    """Synthesize a Job when a known ATS posting has no JSON-LD in HTML."""
    if not _ATS_FALLBACK_HOST.search(url):
        return None
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    company = parts[0] if parts else parsed.netloc
    title = parts[-1].replace("-", " ") if len(parts) > 1 else "(untitled)"
    logger.info("JobUrlAdapter: JSON-LD missing; using ATS URL fallback for %s", url)
    return Job.new(
        source_name=source_name,
        url=url,
        apply_url=url,
        title=title.title(),
        company=company.replace("-", " ").title(),
        description="",
        raw={"url_fallback": True, "page_url": url},
    )


class CareerPageAdapter(_JsonLdHttpAdapter):
    """Adapter for company career pages (listing pages)."""

    @classmethod
    def from_config(
        cls,
        config: CareerPageSource,
        *,
        http: httpx.Client | None = None,
    ) -> CareerPageAdapter:
        return cls(
            name=config.name,
            urls=config.urls,
            rate_limiter=RateLimiter(config.rate_limit_per_minute),
            http=http,
        )


class JobUrlAdapter(_JsonLdHttpAdapter):
    """Adapter for a hand-curated list of individual job URLs."""

    @classmethod
    def from_config(
        cls,
        config: JobUrlSource,
        *,
        http: httpx.Client | None = None,
    ) -> JobUrlAdapter:
        # JobUrlSource has no rate_limit_per_minute — pick a conservative default.
        return cls(
            name=config.name,
            urls=config.urls,
            rate_limiter=RateLimiter(30),
            http=http,
        )


# Re-export SourceError so callers only need this module.
__all__ = [
    "CareerPageAdapter",
    "JobUrlAdapter",
    "SourceError",
    "extract_jsonld_jobs",
]
