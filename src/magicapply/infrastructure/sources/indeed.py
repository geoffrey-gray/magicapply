"""Indeed adapter — Playwright-driven search with Cloudflare-challenge surfacing.

Per configured query, opens the Indeed search page, walks the
``/viewjob?jk=<id>`` anchors out of the rendered HTML, fetches each job
detail page, and extracts its ``schema.org`` ``JobPosting`` JSON-LD block
(reuses ``sources/jsonld.py``).

Indeed hides behind Cloudflare. When we hit the "Just a moment..." challenge
page the parser detects that and logs+skips rather than misinterpreting the
challenge HTML as a jobless search result. The Job source falls back to
zero jobs for that query; other queries and other sources are unaffected.

ToS-sensitive: like LinkedIn, requires an explicit acknowledgement
(``MAGICAPPLY_INDEED_ACK=1``) before making requests.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections.abc import Iterator
from typing import TYPE_CHECKING

from lxml import html as lhtml

from magicapply.config.models import IndeedSource
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.indeed.com/jobs?q={query}{location}"

_BOT_BLOCK_MARKERS = (
    "challenges.cloudflare.com",
    "cf-challenge",
    "just a moment...",
    "checking your browser",
    "blocked - indeed.com",
)


class IndeedAdapter:
    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        location: str | None,
        rate_limit_per_minute: int,
        acknowledged: bool,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._location = location
        self._rate = rate_limit_per_minute
        self._ack = acknowledged

    @classmethod
    def from_config(cls, config: IndeedSource) -> IndeedAdapter:
        return cls(
            name=config.name,
            queries=list(config.queries),
            location=config.location,
            rate_limit_per_minute=config.rate_limit_per_minute,
            acknowledged=os.environ.get("MAGICAPPLY_INDEED_ACK") == "1",
        )

    def discover(self) -> Iterator[Job]:
        if not self._ack:
            raise SourceError(
                "Indeed scraping refused: set MAGICAPPLY_INDEED_ACK=1 to "
                "acknowledge you accept the ToS-violation risk. See "
                "docs/VM_DEV.md for the env-var reference."
            )
        if not self._queries:
            return

        rate = RateLimiter(self._rate)
        with PlaywrightSession(headless=True) as session:
            for query in self._queries:
                yield from self._search_one_query(session, query, rate)

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator[Job]:
        location_part = (
            f"&l={urllib.parse.quote_plus(self._location)}" if self._location else ""
        )
        url = _SEARCH_URL.format(
            query=urllib.parse.quote_plus(query),
            location=location_part,
        )
        rate.wait()

        content = _fetch(session, url)
        if content is None:
            return
        if looks_like_bot_block(content):
            logger.warning(
                "Indeed blocked by bot protection for %r; skipping query", query
            )
            return

        job_urls = extract_job_urls(content)
        for job_url in job_urls:
            rate.wait()
            job_html = _fetch(session, job_url)
            if job_html is None:
                continue
            if looks_like_bot_block(job_html):
                logger.warning(
                    "Indeed blocked by bot protection on detail page %s; skipping",
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
                        "Indeed skip malformed JSON-LD from %s: %s", job_url, exc
                    )


def _fetch(session: PlaywrightSession, url: str) -> str | None:
    page = session.new_page()
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Indeed fetch failed for %s: %s", url, exc)
            return None
        return page.content()
    finally:
        page.close()


def extract_job_urls(html: str) -> list[str]:
    """Pull ``/viewjob?jk=<id>`` URLs out of an Indeed search HTML page."""
    tree = lhtml.fromstring(html)
    urls: set[str] = set()
    for anchor in tree.xpath("//a[contains(@href, '/viewjob')]"):
        href = (anchor.get("href") or "").strip()
        if "jk=" not in href:
            continue
        if href.startswith("/"):
            href = f"https://www.indeed.com{href}"
        # Keep the jk parameter; drop everything else.
        parts = urllib.parse.urlsplit(href)
        query = urllib.parse.parse_qs(parts.query)
        jk = query.get("jk", [""])[0]
        if not jk:
            continue
        urls.add(
            urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, "/viewjob", f"jk={jk}", "")
            )
        )
    return sorted(urls)


def looks_like_bot_block(html: str) -> bool:
    """Return True if the HTML is a bot-protection or access-denied page."""
    lowered = html.lower()
    return any(marker in lowered for marker in _BOT_BLOCK_MARKERS)


def looks_like_cloudflare(html: str) -> bool:
    """Alias kept for Glassdoor + tests."""
    return looks_like_bot_block(html)
