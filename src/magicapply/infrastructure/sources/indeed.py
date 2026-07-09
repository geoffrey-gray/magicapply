"""Indeed adapter — Playwright-driven search with Cloudflare-challenge surfacing.

Per configured query, opens the Indeed search page, walks the
``/viewjob?jk=<id>`` anchors out of the rendered HTML, fetches each job
detail page, and extracts its ``schema.org`` ``JobPosting`` JSON-LD block
(reuses ``sources/jsonld.py``).

Indeed hides behind Cloudflare. Two escape hatches, in order:

1. **Proxy rotation** (when a ``ProxyPool`` is injected) — the adapter
   requests a fresh proxy per search attempt. On ``looks_like_bot_block``,
   the current proxy is burned and dropped, then the query is requeued
   with a bounded retry budget. Distributes load across the pool so a
   single Cloudflare-flagged IP doesn't sink a whole discover run.
2. **Log + skip** — with no pool available (or after exhausting the
   retry budget), the query is logged and dropped. Other queries and
   other sources are unaffected.

ToS-sensitive: like LinkedIn, requires an explicit acknowledgement
(``MAGICAPPLY_INDEED_ACK=1``) before making requests.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING

from lxml import html as lhtml

from magicapply.config.models import IndeedSource
from magicapply.infrastructure.browser.proxy_pool import ProxyEntry, ProxyPool
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import enrich_job_from_detail_html
from magicapply.infrastructure.sources.base import SourceError, parse_cookie_string
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

_DEFAULT_MAX_QUERY_RETRIES = 3
_DEFAULT_JITTER_RATIO = 0.3


class IndeedAdapter:
    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        location: str | None,
        rate_limit_per_minute: int,
        acknowledged: bool,
        enrich_apply_urls: bool = True,
        proxy_pool: ProxyPool | None = None,
        max_query_retries: int = _DEFAULT_MAX_QUERY_RETRIES,
        session_cookies: list[dict] | None = None,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._location = location
        self._rate = rate_limit_per_minute
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls
        self._proxy_pool = proxy_pool
        self._max_query_retries = max(1, int(max_query_retries))
        self._session_cookies = session_cookies or None

    def _effective_proxy_pool(self) -> ProxyPool | None:
        """Cookies-win-over-proxies: when session cookies are configured,
        the adapter routes every fetch through the shared context (no
        proxy rotation). Called from both `discover()` and
        `_search_one_query` so the rule stays consistent."""
        return None if self._session_cookies else self._proxy_pool

    @classmethod
    def from_config(
        cls,
        config: IndeedSource,
        *,
        proxy_pool: ProxyPool | None = None,
    ) -> IndeedAdapter:
        cookie_env = os.environ.get("INDEED_SESSION_COOKIES")
        session_cookies = (
            parse_cookie_string(cookie_env, domain=".indeed.com")
            if cookie_env
            else None
        )
        return cls(
            name=config.name,
            queries=list(config.queries),
            location=config.location,
            rate_limit_per_minute=config.rate_limit_per_minute,
            acknowledged=os.environ.get("MAGICAPPLY_INDEED_ACK") == "1",
            enrich_apply_urls=config.enrich_apply_urls,
            proxy_pool=proxy_pool,
            session_cookies=session_cookies,
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

        rate = RateLimiter(self._rate, jitter_ratio=_DEFAULT_JITTER_RATIO)
        # Cookies win over proxies. Per-proxy contexts (spun by
        # `session.new_page(proxy=...)`) are anonymous and would break
        # any auth session — same-account-from-multiple-IPs is also a
        # bot-detection signal. When cookies are present we route every
        # fetch through the shared context.
        effective_pool = self._effective_proxy_pool()
        if self._session_cookies and self._proxy_pool is not None:
            logger.info(
                "Indeed: session cookies present, skipping proxy pool for this source"
            )
        with PlaywrightSession(headless=True, proxy_pool=effective_pool) as session:
            if self._session_cookies:
                session.add_cookies(self._session_cookies)
            queue: deque[tuple[str, int]] = deque((q, 0) for q in self._queries)
            while queue:
                query, attempts = queue.popleft()
                did_yield, blocked = False, False
                # We yield inside the loop; capture the intent via flags.
                for job in self._search_one_query(session, query, rate):
                    if isinstance(job, _Blocked):
                        blocked = True
                        break
                    did_yield = True
                    yield job
                if blocked and attempts + 1 < self._max_query_retries:
                    logger.info(
                        "Indeed requeueing query %r (attempt %d/%d)",
                        query, attempts + 2, self._max_query_retries,
                    )
                    queue.append((query, attempts + 1))
                elif blocked:
                    logger.warning(
                        "Indeed exhausted retry budget for query %r; skipping",
                        query,
                    )

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator["Job | _Blocked"]:
        location_part = (
            f"&l={urllib.parse.quote_plus(self._location)}" if self._location else ""
        )
        url = _SEARCH_URL.format(
            query=urllib.parse.quote_plus(query),
            location=location_part,
        )
        rate.wait()

        pool = self._effective_proxy_pool()
        proxy = pool.next() if pool else None
        content = _fetch(session, url, proxy=proxy)
        if content is None:
            # Nav timeout / network error. Treat the same as a bot block
            # when we have a proxy pool: burn the proxy and requeue so
            # the pool learns which endpoints are dead-for-Indeed.
            if pool is not None and proxy is not None:
                _mark_blocked(
                    session, pool, proxy, kind="timeout", query=query
                )
                yield _Blocked()
            return
        if looks_like_bot_block(content):
            _mark_blocked(session, pool, proxy, kind="search", query=query)
            yield _Blocked()
            return

        job_urls = extract_job_urls(content)
        for job_url in job_urls:
            rate.wait()
            # Reuse the same proxy that worked for the search page — the
            # per-context state is already warm and Cloudflare-cleared.
            job_html = _fetch(session, job_url, proxy=proxy)
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
                    job = jsonld_to_job(
                        posting, source_name=self.name, fallback_url=job_url
                    )
                    if self._enrich_apply_urls:
                        job = enrich_job_from_detail_html(
                            job, job_html, source="indeed"
                        )
                    yield job
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Indeed skip malformed JSON-LD from %s: %s", job_url, exc
                    )


class _Blocked:
    """Sentinel yielded by `_search_one_query` when Cloudflare blocked the
    search page. The outer `discover()` loop uses it to decide whether to
    requeue the query with a fresh proxy."""

    __slots__ = ()


def _fetch(
    session: PlaywrightSession,
    url: str,
    *,
    proxy: ProxyEntry | None = None,
) -> str | None:
    page = session.new_page(proxy=proxy)
    # Shorter timeout when routing through a proxy — dead proxies should
    # fail fast so the pool cycles rather than blocking a whole discover
    # run on a single stuck endpoint. Direct fetches keep the historical
    # 30 s budget.
    timeout_ms = 12_000 if proxy is not None else 30_000
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Indeed fetch failed for %s: %s", url, exc)
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
    """Burn the current proxy and drop its context so the retry gets a
    fresh identity. When no pool is configured, log and continue — the
    outer loop will decide whether to requeue anyway."""
    if pool is not None and proxy is not None:
        pool.burn(proxy, reason=f"cloudflare-{kind}")
        session.drop_proxy_context(proxy)
        logger.warning(
            "Indeed blocked by bot protection for %r on proxy %s; requeuing",
            query, proxy.server,
        )
    else:
        logger.warning(
            "Indeed blocked by bot protection for %r; skipping query", query
        )


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
