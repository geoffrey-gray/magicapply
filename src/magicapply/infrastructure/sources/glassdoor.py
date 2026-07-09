"""Glassdoor adapter — Playwright-driven search with optional session cookie.

Shape mirrors Indeed: per-query the adapter opens the Glassdoor search page,
walks ``/job-listing/`` anchors out of the DOM, then fetches each detail
page and extracts its ``schema.org`` ``JobPosting`` JSON-LD. Cloudflare
challenges are detected via the shared helper and drive a proxy-rotation
retry loop (see `sources/indeed.py` for the full pattern — Glassdoor is a
sister adapter with the same escape hatches).

If ``GLASSDOOR_SESSION`` is set in env the adapter attaches it as a session
cookie so the authenticated views (which show more jobs and richer detail
pages) are reachable. Unauthenticated calls still work for public listings.
When a `ProxyPool` is also configured, the session cookie is only applied
to the SHARED context — per-proxy contexts stay anonymous, which is the
right behavior for scraping through random IPs.

ToS-sensitive: requires ``MAGICAPPLY_GLASSDOOR_ACK=1``.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING

from lxml import html as lhtml

from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.browser.proxy_pool import ProxyEntry, ProxyPool
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import enrich_job_from_detail_html
from magicapply.infrastructure.sources.base import SourceError, parse_cookie_string
from magicapply.infrastructure.sources.indeed import _Blocked, looks_like_cloudflare
from magicapply.infrastructure.sources.jsonld import (
    extract_jobposting_dicts,
    jsonld_to_job,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}"

_DEFAULT_MAX_QUERY_RETRIES = 3
_DEFAULT_JITTER_RATIO = 0.3


class GlassdoorAdapter:
    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        rate_limit_per_minute: int,
        session_cookie: str | None,
        acknowledged: bool,
        enrich_apply_urls: bool = True,
        proxy_pool: ProxyPool | None = None,
        max_query_retries: int = _DEFAULT_MAX_QUERY_RETRIES,
        session_cookies: list[dict] | None = None,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._rate = rate_limit_per_minute
        self._session = session_cookie
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls
        self._proxy_pool = proxy_pool
        self._max_query_retries = max(1, int(max_query_retries))
        # Multi-cookie env var (`GLASSDOOR_SESSION_COOKIES`) alongside the
        # legacy single-cookie env var (`GLASSDOOR_SESSION` → `gdSession`
        # only). Both may be set; both fire.
        self._session_cookies = session_cookies or None

    def _effective_proxy_pool(self) -> ProxyPool | None:
        """Cookies-win-over-proxies: either the legacy single-cookie or
        the multi-cookie env var routes every fetch through the shared
        context. Called from both `discover()` and `_search_one_query`."""
        if self._session or self._session_cookies:
            return None
        return self._proxy_pool

    @classmethod
    def from_config(
        cls,
        config: GlassdoorSource,
        *,
        proxy_pool: ProxyPool | None = None,
    ) -> GlassdoorAdapter:
        cookie_env = os.environ.get("GLASSDOOR_SESSION_COOKIES")
        session_cookies = (
            parse_cookie_string(cookie_env, domain=".glassdoor.com")
            if cookie_env
            else None
        )
        return cls(
            name=config.name,
            queries=list(config.queries),
            rate_limit_per_minute=config.rate_limit_per_minute,
            session_cookie=os.environ.get("GLASSDOOR_SESSION") or None,
            acknowledged=os.environ.get("MAGICAPPLY_GLASSDOOR_ACK") == "1",
            enrich_apply_urls=config.enrich_apply_urls,
            proxy_pool=proxy_pool,
            session_cookies=session_cookies,
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

        rate = RateLimiter(self._rate, jitter_ratio=_DEFAULT_JITTER_RATIO)
        # Cookies win over proxies (see `_effective_proxy_pool` docstring).
        effective_pool = self._effective_proxy_pool()
        if (self._session or self._session_cookies) and self._proxy_pool is not None:
            logger.info(
                "Glassdoor: session cookies present, skipping proxy pool for this source"
            )
        with PlaywrightSession(headless=True, proxy_pool=effective_pool) as session:
            cookies_to_inject: list[dict] = []
            if self._session:
                # Legacy single-cookie env var — always wires `gdSession`.
                cookies_to_inject.append(_session_cookie(self._session))
            if self._session_cookies:
                # New multi-cookie env var. Both may be set; both fire.
                cookies_to_inject.extend(self._session_cookies)
            if cookies_to_inject:
                session.add_cookies(cookies_to_inject)
            queue: deque[tuple[str, int]] = deque((q, 0) for q in self._queries)
            while queue:
                query, attempts = queue.popleft()
                blocked = False
                for job in self._search_one_query(session, query, rate):
                    if isinstance(job, _Blocked):
                        blocked = True
                        break
                    yield job
                if blocked and attempts + 1 < self._max_query_retries:
                    logger.info(
                        "Glassdoor requeueing query %r (attempt %d/%d)",
                        query, attempts + 2, self._max_query_retries,
                    )
                    queue.append((query, attempts + 1))
                elif blocked:
                    logger.warning(
                        "Glassdoor exhausted retry budget for query %r; skipping",
                        query,
                    )

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator["Job | _Blocked"]:
        url = _SEARCH_URL.format(query=urllib.parse.quote_plus(query))
        rate.wait()
        pool = self._effective_proxy_pool()
        proxy = pool.next() if pool else None
        content = _fetch(session, url, proxy=proxy)
        if content is None:
            if pool is not None and proxy is not None:
                _mark_blocked(
                    session, pool, proxy, kind="timeout", query=query
                )
                yield _Blocked()
            return
        if looks_like_cloudflare(content):
            _mark_blocked(session, pool, proxy, kind="search", query=query)
            yield _Blocked()
            return

        for job_url in extract_job_urls(content):
            rate.wait()
            job_html = _fetch(session, job_url, proxy=proxy)
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
                    job = jsonld_to_job(
                        posting, source_name=self.name, fallback_url=job_url
                    )
                    if self._enrich_apply_urls:
                        job = enrich_job_from_detail_html(
                            job, job_html, source="glassdoor"
                        )
                    yield job
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Glassdoor skip malformed JSON-LD from %s: %s", job_url, exc
                    )


def _fetch(
    session: PlaywrightSession,
    url: str,
    *,
    proxy: ProxyEntry | None = None,
) -> str | None:
    page = session.new_page(proxy=proxy)
    timeout_ms = 12_000 if proxy is not None else 30_000
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Glassdoor fetch failed for %s: %s", url, exc)
            return None
        return page.content()
    finally:
        page.close()


def _mark_blocked(
    session: PlaywrightSession,
    pool: ProxyPool | None,
    proxy: ProxyEntry | None,
    *,
    kind: str,
    query: str,
) -> None:
    if pool is not None and proxy is not None:
        pool.burn(proxy, reason=f"cloudflare-{kind}")
        session.drop_proxy_context(proxy)
        logger.warning(
            "Glassdoor blocked by bot protection for %r on proxy %s; requeuing",
            query, proxy.server,
        )
    else:
        logger.warning(
            "Glassdoor blocked by bot protection for %r; skipping query", query
        )


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
