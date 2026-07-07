"""LinkedIn adapter — authenticated search-based scraping.

Uses a PlaywrightSession seeded with an ``li_at`` session cookie (from the
``LINKEDIN_LI_AT`` env var). Per-query the adapter loads the LinkedIn search
page and extracts job cards from the embedded Voyager JSON (``<code>`` blocks).
LinkedIn no longer serves schema.org JSON-LD on job detail pages reliably, so
we avoid per-job detail fetches when the search payload is parseable.

**ToS-sensitive.** LinkedIn's terms forbid automated access. This adapter
refuses to run unless ``MAGICAPPLY_LINKEDIN_ACK=1`` is set — an explicit,
per-operator acknowledgement that they accept the risk. Rate limits default
conservative (10 req/min from ``LinkedInSource.rate_limit_per_minute``);
tune down further if LinkedIn starts throttling.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from lxml import html as lhtml

from magicapply.config.models import LinkedInSource
from magicapply.domain.models.job import Job, canonicalize_url
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    apply_url_from_linkedin_detail_html,
    sniff_platform,
)
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SEARCH_URL_TEMPLATE = "https://www.linkedin.com/jobs/search/?keywords={query}"
_CODE_BLOCK_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL)
_JOB_ID_RE = re.compile(r"fsd_jobPosting:(\d+)")


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
        enrich_apply_urls: bool = True,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._rate = rate_limit_per_minute
        self._li_at = li_at
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls

    @classmethod
    def from_config(cls, config: LinkedInSource) -> LinkedInAdapter:
        return cls(
            name=config.name,
            queries=list(config.queries),
            rate_limit_per_minute=config.rate_limit_per_minute,
            li_at=os.environ.get("LINKEDIN_LI_AT") or None,
            acknowledged=os.environ.get("MAGICAPPLY_LINKEDIN_ACK") == "1",
            enrich_apply_urls=config.enrich_apply_urls,
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
                page.wait_for_timeout(2000)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LinkedIn search failed for %r: %s", query, exc)
                return
            html = page.content()
        finally:
            page.close()

        jobs = extract_jobs_from_search(html, source_name=self.name)
        if jobs:
            for job in jobs:
                if self._enrich_apply_urls:
                    job = _enrich_apply_url(session, job, rate)
                yield job
            return

        # Legacy fallback: detail-page JSON-LD when Voyager cards are absent.
        for job_url in extract_job_urls(html):
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


def _enrich_apply_url(
    session: PlaywrightSession,
    job: Job,
    rate: RateLimiter,
) -> Job:
    """Fetch the LinkedIn listing page and resolve the external apply URL."""
    rate.wait()
    page = session.new_page()
    try:
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=30_000)
            # LinkedIn's Apply anchor is React-rendered and can take 4-5 s to
            # hydrate on cold pages under Chromium — 1500 ms and 3500 ms both
            # consistently missed it (empirical: 5000 ms was the floor at
            # which Symetra / Netflix / Hyatt / Included Health all reliably
            # resolved). Six-second rate-limit interval already dominates the
            # per-job cost, so the extra hydration budget is essentially free.
            page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn apply-url enrich failed for %s: %s", job.url, exc
            )
            return job
        apply_url = apply_url_from_linkedin_detail_html(page.content())
    finally:
        page.close()

    if not apply_url:
        return job

    platform = sniff_platform(apply_url)
    logger.info(
        "LinkedIn enriched apply URL for %s → %s (%s)", job.url, apply_url, platform
    )
    return job.model_copy(
        update={
            "apply_url": canonicalize_url(apply_url),
            "raw": {
                **job.raw,
                "listing_url": job.url,
                "platform": platform,
            },
        }
    )


def extract_jobs_from_search(html: str, *, source_name: str) -> list[Job]:
    """Parse Voyager ``JobPostingCard`` objects embedded in search HTML."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for block in _code_json_blocks(html):
        for card in _walk_nodes(block):
            if not _is_search_job_card(card):
                continue
            job = _card_to_job(card, source_name=source_name)
            if job is None or job.url in seen:
                continue
            seen.add(job.url)
            jobs.append(job)
    return jobs


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


def _code_json_blocks(html: str) -> list[Any]:
    out: list[Any] = []
    for match in _CODE_BLOCK_RE.finditer(html):
        try:
            out.append(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            continue
    return out


def _walk_nodes(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_nodes(item)


def _is_search_job_card(node: dict[str, Any]) -> bool:
    if "JobPostingCard" not in str(node.get("$type", "")):
        return False
    urn = str(node.get("entityUrn", ""))
    if "JOBS_SEARCH" not in urn:
        return False
    return bool(node.get("jobPostingUrn") or node.get("jobPostingTitle"))


def _card_to_job(card: dict[str, Any], *, source_name: str) -> Job | None:
    posting_urn = str(card.get("jobPostingUrn") or "")
    match = _JOB_ID_RE.search(posting_urn)
    if not match:
        return None
    url = f"https://www.linkedin.com/jobs/view/{match.group(1)}"
    title = _text_value(card.get("jobPostingTitle")) or _text_value(card.get("title"))
    if not title:
        return None
    company = _text_value(card.get("primaryDescription")) or "(unknown company)"
    location = _text_value(card.get("secondaryDescription"))
    return Job.new(
        source_name=source_name,
        url=url,
        title=title,
        company=company,
        description="",
        location=location,
        raw=card,
    )


def _text_value(node: Any) -> str | None:
    if isinstance(node, str):
        text = html_lib.unescape(node).replace("\xa0", " ").strip()
        return text or None
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str):
            return _text_value(text)
    return None


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