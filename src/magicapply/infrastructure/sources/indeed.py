"""Indeed adapter — SERP first, offsite prefer, capped viewjob fallback.

Per configured query, opens the Indeed search page and reads title,
company, location, snippet, ``jobkey``, and ``thirdPartyApplyUrl`` from
the mosaic hydration blob. Then:

1. **Offsite** — when ``apply_url`` is external, upgrade short snippets
   with JD text from the destination careers/ATS page.
2. **Detail fallback** — if apply URL and/or description still incomplete,
   visit ``/viewjob?jk=…`` up to ``max_board_detail_fetches`` times per run.
   Bot-blocked details are soft failures (do not kill the whole query).

Indeed hides behind Cloudflare on search and often on detail. Search
uses proxy rotation / requeue; detail blocks are logged and skipped.

Session cookies (``INDEED_SESSION_COOKIES``) take precedence over proxy
rotation. ToS-sensitive: requires ``MAGICAPPLY_INDEED_ACK=1``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from lxml import html as lhtml

from magicapply.config.models import IndeedSource
from magicapply.domain.models.job import Job, canonicalize_url
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
from magicapply.infrastructure.browser.proxy_pool import ProxyEntry, ProxyPool
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    BOARD_HOSTS_INDEED,
    apply_url_from_indeed_detail_html,
    description_from_indeed_detail_html,
    is_external_apply_url,
    sniff_platform,
)
from magicapply.infrastructure.sources.base import SourceError, parse_cookie_string
from magicapply.infrastructure.sources.rate_limit import RateLimiter
from magicapply.infrastructure.sources.serp_enrich import (
    DetailBudget,
    SerpEnrichPolicy,
    apply_url_if_external,
    post_serp_enrich,
)

logger = logging.getLogger(__name__)

_SEARCH_BASE = "https://www.indeed.com/jobs"
_PAGE_SIZE = 10  # Indeed classic result offset step

_BOT_BLOCK_MARKERS = (
    "challenges.cloudflare.com",
    "cf-challenge",
    "just a moment...",
    "checking your browser",
    "blocked - indeed.com",
    # Indeed's second-tier interstitial (post-Cloudflare). Fires on detail
    # pages when we survive the CF challenge but Indeed still rate-limits.
    # We no longer fetch detail pages, but the marker stays as a
    # search-page defensive check.
    "additional verification required",
    "security check - indeed.com",
)

# Indeed hydrates the search results into a JS global before rendering.
# The job card list lives under
# `window.mosaic.providerData["mosaic-provider-jobcards"] = {…}`, wrapping
# a `mosaicProviderJobCardsModel.results` array. `initialData` is a
# separate, smaller blob holding page-level metadata (country, csrf, …)
# — not the jobs. Anchor on the opening `{` after the assignment so
# `raw_decode` picks up the object literal cleanly.
_JOB_CARDS_BLOB_RE = re.compile(
    r'window\.mosaic\.providerData\[["\']mosaic-provider-jobcards["\']\]\s*=\s*(\{)'
)

_DEFAULT_MAX_QUERY_RETRIES = 3
_DEFAULT_JITTER_RATIO = 0.3


def build_indeed_search_url(
    query: str,
    *,
    location: str | None = None,
    remote_only: bool = True,
    posted_within_days: int | None = 7,
    start: int = 0,
) -> str:
    """Build an Indeed search URL with optional remote/date/page filters."""
    loc = location
    if remote_only and not loc:
        loc = "Remote"
    params: list[tuple[str, str]] = [("q", query)]
    if loc:
        params.append(("l", loc))
    if posted_within_days and posted_within_days > 0:
        params.append(("fromage", str(int(posted_within_days))))
    if start > 0:
        params.append(("start", str(int(start))))
    return f"{_SEARCH_BASE}?{urllib.parse.urlencode(params)}"


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
        enrich_descriptions: bool = True,
        board_detail_fallback: bool = True,
        max_board_detail_fetches: int = 8,
        require_external_apply: bool = False,
        max_jobs_per_run: int = 50,
        proxy_pool: ProxyPool | None = None,
        max_query_retries: int = _DEFAULT_MAX_QUERY_RETRIES,
        session_cookies: list[dict] | None = None,
        remote_only: bool = True,
        posted_within_days: int | None = 7,
        max_pages: int = 40,
        data_dir: Path | None = None,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._location = location
        self._rate = rate_limit_per_minute
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls
        self._enrich_descriptions = enrich_descriptions
        self._board_detail_fallback = board_detail_fallback
        self._max_board_detail_fetches = max(0, int(max_board_detail_fetches))
        self._require_external_apply = require_external_apply
        self._max_jobs = max(1, int(max_jobs_per_run))
        self._proxy_pool = proxy_pool
        self._max_query_retries = max(1, int(max_query_retries))
        self._session_cookies = session_cookies or None
        self._remote_only = remote_only
        self._posted_within_days = posted_within_days
        self._max_pages = max(1, int(max_pages))
        self._data_dir = data_dir
        self._detail_budget = DetailBudget(self._max_board_detail_fetches)

    def _effective_proxy_pool(self) -> ProxyPool | None:
        """Cookies/storage_state win over proxies: when session auth is
        configured, the adapter routes every fetch through the shared
        context (no proxy rotation)."""
        auth = resolve_session_auth("indeed", self._data_dir)
        if auth.source != "none" or self._session_cookies:
            return None
        return self._proxy_pool

    @classmethod
    def from_config(
        cls,
        config: IndeedSource,
        *,
        proxy_pool: ProxyPool | None = None,
        data_dir: Path | None = None,
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
            enrich_descriptions=config.enrich_descriptions,
            board_detail_fallback=config.board_detail_fallback,
            max_board_detail_fetches=config.max_board_detail_fetches,
            require_external_apply=config.require_external_apply,
            max_jobs_per_run=config.max_jobs_per_run,
            proxy_pool=proxy_pool,
            session_cookies=session_cookies,
            remote_only=config.remote_only,
            posted_within_days=config.posted_within_days,
            max_pages=config.max_pages,
            data_dir=data_dir,
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
        auth = resolve_session_auth("indeed", self._data_dir)
        effective_pool = self._effective_proxy_pool()
        # Fresh budget each discover run.
        self._detail_budget = DetailBudget(self._max_board_detail_fetches)
        jobs_left = self._max_jobs
        if auth.source != "none" and self._proxy_pool is not None:
            logger.info(
                "Indeed: auth via %s, skipping proxy pool for this source",
                auth.source,
            )
        with PlaywrightSession(
            headless=True,
            proxy_pool=effective_pool,
            storage_state_path=auth.storage_state_path,
        ) as session:
            cookies = auth.cookies or self._session_cookies
            if cookies:
                session.add_cookies(cookies)
            queue: deque[tuple[str, int]] = deque((q, 0) for q in self._queries)
            while queue and jobs_left > 0:
                query, attempts = queue.popleft()
                blocked = False
                for job in self._search_one_query(session, query, rate):
                    if isinstance(job, _Blocked):
                        blocked = True
                        break
                    enriched = self._post_serp_enrich(session, job, rate)
                    if enriched is None:
                        continue
                    yield enriched
                    jobs_left -= 1
                    if jobs_left <= 0:
                        logger.info(
                            "Indeed max_jobs_per_run=%d reached; stopping",
                            self._max_jobs,
                        )
                        return
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

    def _post_serp_enrich(
        self,
        session: PlaywrightSession,
        job: Job,
        rate: RateLimiter,
    ) -> Job | None:
        policy = SerpEnrichPolicy(
            enrich_apply_urls=self._enrich_apply_urls,
            enrich_descriptions=self._enrich_descriptions,
            board_detail_fallback=self._board_detail_fallback,
            require_external_apply=self._require_external_apply,
            board_hosts=BOARD_HOSTS_INDEED,
            board_label="Indeed",
        )
        return post_serp_enrich(
            session,
            job,
            rate,
            policy=policy,
            budget=self._detail_budget,
            board_detail_fn=self._enrich_from_indeed_detail,
        )

    def _enrich_from_indeed_detail(
        self,
        session: PlaywrightSession,
        job: Job,
        rate: RateLimiter,
    ) -> Job:
        """One ``/viewjob`` visit: resolve external apply + fuller description."""
        rate.wait()
        pool = self._effective_proxy_pool()
        proxy = pool.next() if pool else None
        content = _fetch(session, job.url, proxy=proxy)
        if content is None:
            return job
        if looks_like_bot_block(content):
            logger.warning(
                "Indeed detail blocked for %s; leaving incomplete", job.url
            )
            if pool is not None and proxy is not None:
                pool.burn(proxy, reason="cloudflare-detail")
                session.drop_proxy_context(proxy)
            return job

        updates: dict[str, Any] = {"raw": {**job.raw, "indeed_detail": True}}
        apply_url = apply_url_from_indeed_detail_html(content)
        if apply_url and is_external_apply_url(
            apply_url, board_hosts=BOARD_HOSTS_INDEED
        ):
            platform = sniff_platform(apply_url)
            updates["apply_url"] = canonicalize_url(apply_url)
            updates["raw"] = {
                **updates["raw"],
                "listing_url": job.url,
                "platform": platform,
                "apply_resolve": "indeed_detail",
            }
            logger.info(
                "Indeed detail apply-url %s → %s (%s)",
                job.url,
                apply_url,
                platform,
            )
        desc = description_from_indeed_detail_html(content)
        if desc and len(desc.strip()) > len((job.description or "").strip()):
            updates["description"] = desc
            updates["raw"] = {
                **updates.get("raw", job.raw),
                "description_source": "indeed_detail_html",
            }
            logger.info(
                "Indeed detail description for %s (%d chars)", job.url, len(desc)
            )
        return job.model_copy(update=updates)

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator["Job | _Blocked"]:
        """Paginate Indeed search until empty page, full dupes, or max_pages."""
        seen_urls: set[str] = set()
        pool = self._effective_proxy_pool()

        for page_idx in range(self._max_pages):
            start = page_idx * _PAGE_SIZE
            url = build_indeed_search_url(
                query,
                location=self._location,
                remote_only=self._remote_only,
                posted_within_days=self._posted_within_days,
                start=start,
            )
            rate.wait()
            proxy = pool.next() if pool else None
            content = _fetch(session, url, proxy=proxy)
            if content is None:
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

            # Full job records from mosaic hydration — no detail fetches.
            jobs = extract_jobs_from_search(content, source_name=self.name)
            if not jobs and extract_job_urls(content):
                logger.warning(
                    "Indeed search page for %r start=%d has %d job URLs but no "
                    "hydration records — check mosaic shape",
                    query,
                    start,
                    len(extract_job_urls(content)),
                )

            new_on_page = 0
            for job in jobs:
                if job.url in seen_urls:
                    continue
                seen_urls.add(job.url)
                new_on_page += 1
                yield job

            logger.info(
                "Indeed query %r page %d (start=%d): %d jobs (%d new)",
                query,
                page_idx + 1,
                start,
                len(jobs),
                new_on_page,
            )
            if not jobs or new_on_page == 0:
                break

        logger.info(
            "Indeed query %r finished: %d unique jobs across pages",
            query,
            len(seen_urls),
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


def extract_jobs_from_search(html: str, *, source_name: str) -> list[Job]:
    """Parse full ``Job`` records from an Indeed search page's hydration
    blob (``window.mosaic.providerData["mosaic-provider-jobcards"]``).

    Indeed embeds every result on the page — title, company,
    ``formattedLocation``, HTML snippet, ``jobkey``, apply/redirect URLs
    — into a JSON literal assigned to a JS global before rendering.
    Parsing that blob directly means we never navigate to
    ``/viewjob?jk=…`` for each result, sidestepping Indeed's second-tier
    "Additional Verification Required" interstitial that fires on those
    URLs even when the search page loaded cleanly.

    Returns ``[]`` when the hydration blob is absent or malformed; the
    caller decides whether that means "empty search" or "shape changed"
    based on whether ``extract_job_urls`` finds candidate ``data-jk``
    anchors."""
    matches = list(_JOB_CARDS_BLOB_RE.finditer(html))
    if not matches:
        return []
    # Escaped copies live inside the JS bundle string literal. The real
    # payload is the last match — its `{` is unescaped.
    start = matches[-1].start(1)
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        logger.warning("Indeed job-cards blob failed to parse: %s", exc)
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    for record in _walk_for_job_records(data):
        jk = record.get("jobkey")
        if not isinstance(jk, str) or jk in seen:
            continue
        title = record.get("title") or record.get("displayTitle")
        company = record.get("company")
        if not (title and company):
            continue
        seen.add(jk)
        third = record.get("thirdPartyApplyUrl")
        apply_url = apply_url_if_external(
            str(third) if third else None,
            board_hosts=BOARD_HOSTS_INDEED,
        )
        # Indeed applystart / Easy Apply links stay on indeed.com — not external ATS.
        if third and not apply_url:
            logger.info(
                "indeed: thirdPartyApplyUrl not external for jk=%s (board-only)",
                jk,
            )
        raw: dict[str, Any] = {
            "jobkey": jk,
            "createDate": record.get("createDate"),
            "pubDate": record.get("pubDate"),
            "sourceId": record.get("sourceId"),
            "sponsored": record.get("sponsored"),
            "indeedApplyable": record.get("indeedApplyable"),
            "thirdPartyApplyUrl": record.get("thirdPartyApplyUrl"),
            "source_extraction": "search-page-hydration",
        }
        if apply_url:
            raw["apply_resolve"] = "serp_hydration"
            raw["platform"] = sniff_platform(apply_url)
        jobs.append(
            Job.new(
                source_name=source_name,
                url=f"https://www.indeed.com/viewjob?jk={jk}",
                title=str(title),
                company=str(company),
                description=str(record.get("snippet") or ""),
                location=str(record["formattedLocation"])
                if record.get("formattedLocation")
                else None,
                apply_url=apply_url,
                raw=raw,
            )
        )
    return jobs


def _walk_for_job_records(node: Any) -> list[dict[str, Any]]:
    """Depth-first walk of the parsed hydration dict, collecting any node
    that looks like an Indeed job record (a dict with a string
    ``jobkey``). Handles arbitrary nesting under ``mosaicProviderJobCards``
    / ``results`` / etc. without hard-coding paths."""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("jobkey"), str):
            out.append(node)
        for v in node.values():
            out.extend(_walk_for_job_records(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk_for_job_records(v))
    return out


def extract_job_urls(html: str) -> list[str]:
    """Pull ``/viewjob?jk=<id>`` URLs out of an Indeed search HTML page.

    Indeed's modern search page renders cards as ``<a data-jk="…">`` and
    constructs the ``/viewjob?jk=…`` URL client-side — the ``href``
    attribute is either the same value or a placeholder. Empirically
    verified: a real search page shows 25 ``data-jk`` anchors but only
    1 ``href*="/viewjob"`` anchor (a sponsored/example card).

    We accept both shapes: the modern ``data-jk`` attribute (primary) and
    the legacy ``href="/viewjob?jk=…"`` fallback for older snapshots or
    static captures used in tests / fixtures. Both funnel through the
    same JK-→-canonical-URL builder so downstream code is unchanged."""
    tree = lhtml.fromstring(html)
    jks: set[str] = set()
    # Modern: <a data-jk="…"> cards.
    for anchor in tree.xpath("//a[@data-jk]"):
        jk = (anchor.get("data-jk") or "").strip()
        if jk:
            jks.add(jk)
    # Legacy / fallback: <a href="/viewjob?jk=…">.
    for anchor in tree.xpath("//a[contains(@href, '/viewjob')]"):
        href = (anchor.get("href") or "").strip()
        if "jk=" not in href:
            continue
        parts = urllib.parse.urlsplit(
            href if href.startswith("http") else f"https://www.indeed.com{href}"
        )
        query = urllib.parse.parse_qs(parts.query)
        jk = query.get("jk", [""])[0]
        if jk:
            jks.add(jk)
    return sorted(
        f"https://www.indeed.com/viewjob?jk={jk}" for jk in jks
    )


def looks_like_bot_block(html: str) -> bool:
    """Return True if the HTML is a bot-protection or access-denied page."""
    lowered = html.lower()
    return any(marker in lowered for marker in _BOT_BLOCK_MARKERS)


def looks_like_cloudflare(html: str) -> bool:
    """Alias kept for Glassdoor + tests."""
    return looks_like_bot_block(html)
