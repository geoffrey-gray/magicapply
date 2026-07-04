"""LinkedIn adapter — authenticated search-based scraping.

Uses a PlaywrightSession seeded with an ``li_at`` session cookie (from the
``LINKEDIN_LI_AT`` env var). Per-query the adapter loads the LinkedIn search
page, extracts the ``/jobs/view/<id>`` URLs from the results, and then
fetches each job detail page and pulls its ``schema.org`` ``JobPosting``
JSON-LD block (LinkedIn embeds one on every real job listing).

**ToS-sensitive.** LinkedIn's terms forbid automated access. This adapter
refuses to run unless ``MAGICAPPLY_LINKEDIN_ACK=1`` is set — an explicit,
per-operator acknowledgement that they accept the risk. Rate limits default
conservative (10 req/min from ``LinkedInSource.rate_limit_per_minute``);
tune down further if LinkedIn starts throttling.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

from lxml import html as lhtml

from magicapply.config.models import LinkedInSource
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

_SEARCH_URL_TEMPLATE = "https://www.linkedin.com/jobs/search/?keywords={query}"


class LinkedInAdapter:
    """Authenticated LinkedIn scraper over Playwright."""

    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        rate_limit_per_minute: int,
        li_at: str | None,
        acknowledged: bool,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._rate = rate_limit_per_minute
        self._li_at = li_at
        self._ack = acknowledged

    @classmethod
    def from_config(cls, config: LinkedInSource) -> LinkedInAdapter:
        return cls(
            name=config.name,
            queries=list(config.queries),
            rate_limit_per_minute=config.rate_limit_per_minute,
            li_at=os.environ.get("LINKEDIN_LI_AT") or None,
            acknowledged=os.environ.get("MAGICAPPLY_LINKEDIN_ACK") == "1",
        )

    def discover(self) -> Iterator[Job]:
        # ToS gate first: nothing else is attempted without an explicit ack.
        if not self._ack:
            raise SourceError(
                "LinkedIn scraping refused: set MAGICAPPLY_LINKEDIN_ACK=1 to "
                "acknowledge you accept the ToS-violation risk. See "
                "docs/VM_DEV.md for the env-var reference."
            )
        if not self._li_at:
            raise SourceError(
                "LinkedIn scraping needs an authenticated session cookie: set "
                "LINKEDIN_LI_AT to the value of your logged-in `li_at` cookie."
            )
        if not self._queries:
            return

        rate = RateLimiter(self._rate)
        cookie = _li_at_cookie(self._li_at)

        with PlaywrightSession(headless=True) as session:
            session.add_cookies([cookie])
            for query in self._queries:
                yield from self._search_one_query(session, query, rate)

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator[Job]:
        search_url = _SEARCH_URL_TEMPLATE.format(query=query.replace(" ", "%20"))
        rate.wait()
        page = session.new_page()
        try:
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LinkedIn search failed for %r: %s", query, exc)
                return
            job_urls = extract_job_urls(page.content())
        finally:
            page.close()

        for job_url in job_urls:
            rate.wait()
            job_page = session.new_page()
            try:
                try:
                    job_page.goto(
                        job_url, wait_until="domcontentloaded", timeout=30_000
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LinkedIn detail fetch failed for %s: %s", job_url, exc)
                    continue
                content = job_page.content()
            finally:
                job_page.close()

            for posting in extract_jobposting_dicts(content):
                try:
                    yield jsonld_to_job(
                        posting, source_name=self.name, fallback_url=job_url
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "LinkedIn skip malformed JSON-LD from %s: %s", job_url, exc
                    )


def extract_job_urls(html: str) -> list[str]:
    """Pull ``/jobs/view/<id>`` URLs out of a LinkedIn search HTML page.

    Normalises relative URLs to absolute and strips query strings so
    duplicates collapse. Order is stable (sorted).
    """
    tree = lhtml.fromstring(html)
    urls: set[str] = set()
    for anchor in tree.xpath("//a[contains(@href, '/jobs/view/')]"):
        href = (anchor.get("href") or "").strip()
        if "/jobs/view/" not in href:
            continue
        if href.startswith("/"):
            href = f"https://www.linkedin.com{href}"
        # Drop query string so /jobs/view/12345?refId=... collapses.
        base = href.split("?", 1)[0].rstrip("/")
        if base:
            urls.add(base)
    return sorted(urls)


def _li_at_cookie(li_at: str) -> dict:
    """Construct a Playwright cookie dict for the LinkedIn ``li_at`` session cookie."""
    return {
        "name": "li_at",
        "value": li_at,
        "domain": ".linkedin.com",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }
