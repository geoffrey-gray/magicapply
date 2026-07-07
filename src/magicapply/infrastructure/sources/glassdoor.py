"""Glassdoor adapter — Playwright-driven search with optional session cookie.

Shape mirrors Indeed: per-query the adapter opens the Glassdoor search page,
walks ``/job-listing/`` anchors out of the DOM, then fetches each detail
page and extracts its ``schema.org`` ``JobPosting`` JSON-LD. Cloudflare
challenges are detected via the shared helper and logged+skipped.

If ``GLASSDOOR_SESSION`` is set in env the adapter attaches it as a session
cookie so the authenticated views (which show more jobs and richer detail
pages) are reachable. Unauthenticated calls still work for public listings.

ToS-sensitive: requires ``MAGICAPPLY_GLASSDOOR_ACK=1``.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections.abc import Iterator
from typing import TYPE_CHECKING

from lxml import html as lhtml

from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.indeed import looks_like_cloudflare
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}"


class GlassdoorAdapter:
    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        rate_limit_per_minute: int,
        session_cookie: str | None,
        acknowledged: bool,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._rate = rate_limit_per_minute
        self._session = session_cookie
        self._ack = acknowledged

    @classmethod
    def from_config(cls, config: GlassdoorSource) -> GlassdoorAdapter:
        return cls(
            name=config.name,
            queries=list(config.queries),
            rate_limit_per_minute=config.rate_limit_per_minute,
            session_cookie=os.environ.get("GLASSDOOR_SESSION") or None,
            acknowledged=os.environ.get("MAGICAPPLY_GLASSDOOR_ACK") == "1",
        )

    def discover(self) -> Iterator[Job]:
        if not self._ack:
            raise SourceError(
                "Glassdoor scraping refused: set MAGICAPPLY_GLASSDOOR_ACK=1 "
                "to acknowledge you accept the ToS-violation risk. See "
                "docs/VM_DEV.md for the env-var reference."
            )
        if not self._queries:
            return

        rate = RateLimiter(self._rate)
        with PlaywrightSession(headless=True) as session:
            if self._session:
                session.add_cookies([_session_cookie(self._session)])
            for query in self._queries:
                yield from self._search_one_query(session, query, rate)

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator[Job]:
        url = _SEARCH_URL.format(query=urllib.parse.quote_plus(query))
        rate.wait()
        content = _fetch(session, url)
        if content is None:
            return
        if looks_like_cloudflare(content):
            logger.warning(
                "Glassdoor blocked by bot protection for %r; skipping query", query
            )
            return

        for job_url in extract_job_urls(content):
            rate.wait()
            job_html = _fetch(session, job_url)
            if job_html is None:
                continue
            if looks_like_cloudflare(job_html):
                logger.warning(
                    "Glassdoor blocked by bot protection on detail %s; skipping",
                    job_url,
                )
                continue
            for posting in extract_jobposting_dicts(job_html):
                try:
                    yield jsonld_to_job(
                        posting, source_name=self.name, fallback_url=job_url
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Glassdoor skip malformed JSON-LD from %s: %s", job_url, exc
                    )


def _fetch(session: PlaywrightSession, url: str) -> str | None:
    page = session.new_page()
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Glassdoor fetch failed for %s: %s", url, exc)
            return None
        return page.content()
    finally:
        page.close()


def extract_job_urls(html: str) -> list[str]:
    """Pull ``/job-listing/…`` URLs out of a Glassdoor search HTML page.

    Glassdoor detail-page URLs look like
    ``https://www.glassdoor.com/job-listing/<slug>-JV_...`` — we keep the
    path unchanged (Glassdoor's own routing depends on the slug + jobListingId
    embedded there) and drop the query string.
    """
    tree = lhtml.fromstring(html)
    urls: set[str] = set()
    for anchor in tree.xpath("//a[contains(@href, '/job-listing/')]"):
        href = (anchor.get("href") or "").strip()
        if "/job-listing/" not in href:
            continue
        if href.startswith("/"):
            href = f"https://www.glassdoor.com{href}"
        base = href.split("?", 1)[0].rstrip("/")
        if base:
            urls.add(base)
    return sorted(urls)


def _session_cookie(value: str) -> dict:
    return {
        "name": "gdSession",
        "value": value,
        "domain": ".glassdoor.com",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }
