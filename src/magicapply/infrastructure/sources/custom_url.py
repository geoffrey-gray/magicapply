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
from collections.abc import Iterator
from typing import TYPE_CHECKING

import httpx

from magicapply.config.models import CareerPageSource, JobUrlSource
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.apply_url import enrich_job_from_detail_html
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job

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
                logger.info("no JSON-LD JobPosting on %s", url)
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
