"""Glassdoor adapter — SERP first, offsite prefer, capped listing fallback.

Per query, opens the Glassdoor search page and reads job cards (title,
employer, location, snippet). Then:

1. **Offsite** — when ``apply_url`` is external, upgrade short snippets
   from the destination careers/ATS page.
2. **Detail fallback** — if apply URL and/or description still incomplete,
   visit the listing page up to ``max_board_detail_fetches`` times per run.
   Cloudflare on detail is a soft failure.

Search-page Cloudflare still uses proxy burn + requeue. ToS-sensitive:
requires ``MAGICAPPLY_GLASSDOOR_ACK=1``.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from lxml import html as lhtml

from magicapply.config.models import GlassdoorSource
from magicapply.domain.models.job import Job, canonicalize_url
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
from magicapply.infrastructure.browser.proxy_pool import ProxyEntry, ProxyPool
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    BOARD_HOSTS_GLASSDOOR,
    apply_url_from_glassdoor_detail_html,
    description_from_glassdoor_detail_html,
    is_external_apply_url,
    resolve_apply_href,
    sniff_platform,
)
from magicapply.infrastructure.sources.base import SourceError, parse_cookie_string
from magicapply.infrastructure.sources.indeed import _Blocked, looks_like_cloudflare
from magicapply.infrastructure.sources.rate_limit import RateLimiter
from magicapply.infrastructure.sources.serp_enrich import (
    DetailBudget,
    SerpEnrichPolicy,
    post_serp_enrich,
)

logger = logging.getLogger(__name__)

_SEARCH_BASE = "https://www.glassdoor.com/Job/jobs.htm"

_DEFAULT_MAX_QUERY_RETRIES = 3
_DEFAULT_JITTER_RATIO = 0.3


def build_glassdoor_search_url(
    query: str,
    *,
    remote_only: bool = True,
    posted_within_days: int | None = 7,
    page: int = 1,
) -> str:
    """Build a Glassdoor job-search URL with optional remote/date/page filters.

    ``page`` is 1-based (Glassdoor ``p`` param). Date uses ``fromAge`` in days.
    Remote uses ``remoteWorkType=1`` (Glassdoor public search); if a live
    dump shows a different param the builder is the single place to fix it.
    """
    params: list[tuple[str, str]] = [("sc.keyword", query)]
    if remote_only:
        params.append(("remoteWorkType", "1"))
    if posted_within_days and posted_within_days > 0:
        params.append(("fromAge", str(int(posted_within_days))))
    if page > 1:
        params.append(("p", str(int(page))))
    return f"{_SEARCH_BASE}?{urllib.parse.urlencode(params)}"


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
        self._rate = rate_limit_per_minute
        self._session = session_cookie
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls
        self._enrich_descriptions = enrich_descriptions
        self._board_detail_fallback = board_detail_fallback
        self._max_board_detail_fetches = max(0, int(max_board_detail_fetches))
        self._require_external_apply = require_external_apply
        self._max_jobs = max(1, int(max_jobs_per_run))
        self._proxy_pool = proxy_pool
        self._max_query_retries = max(1, int(max_query_retries))
        # Multi-cookie env var (`GLASSDOOR_SESSION_COOKIES`) alongside the
        # legacy single-cookie env var (`GLASSDOOR_SESSION` → `gdSession`
        # only). Both may be set; both fire.
        self._session_cookies = session_cookies or None
        self._remote_only = remote_only
        self._posted_within_days = posted_within_days
        self._max_pages = max(1, int(max_pages))
        self._data_dir = data_dir
        self._detail_budget = DetailBudget(self._max_board_detail_fetches)

    def _effective_proxy_pool(self) -> ProxyPool | None:
        """Auth (storage_state or cookies) wins over proxies."""
        auth = resolve_session_auth("glassdoor", self._data_dir)
        if auth.source != "none" or self._session or self._session_cookies:
            return None
        return self._proxy_pool

    @classmethod
    def from_config(
        cls,
        config: GlassdoorSource,
        *,
        proxy_pool: ProxyPool | None = None,
        data_dir: Path | None = None,
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

    def discover(
        self, *, known_ids: frozenset[str] | None = None
    ) -> Iterator[Job]:
        if not self._ack:
            raise SourceError(
                "Glassdoor scraping refused: set MAGICAPPLY_GLASSDOOR_ACK=1 "
                "to acknowledge you accept the ToS-violation risk. See "
                "docs/VM_DEV.md for the env-var reference."
            )
        if not self._queries:
            return

        rate = RateLimiter(self._rate, jitter_ratio=_DEFAULT_JITTER_RATIO)
        auth = resolve_session_auth("glassdoor", self._data_dir)
        effective_pool = self._effective_proxy_pool()
        self._detail_budget = DetailBudget(self._max_board_detail_fetches)
        # Per-run intake of *new* jobs only (see LinkedIn/Indeed).
        jobs_left = self._max_jobs
        known = known_ids or frozenset()
        if auth.source != "none" and self._proxy_pool is not None:
            logger.info(
                "Glassdoor: auth via %s, skipping proxy pool for this source",
                auth.source,
            )
        logger.info(
            "Glassdoor discover: known_ids=%d max_new=%d max_pages=%d",
            len(known),
            self._max_jobs,
            self._max_pages,
        )
        with PlaywrightSession(
            headless=True,
            proxy_pool=effective_pool,
            storage_state_path=auth.storage_state_path,
        ) as session:
            cookies_to_inject: list[dict] = list(auth.cookies)
            if not cookies_to_inject and self._session:
                cookies_to_inject.append(_session_cookie(self._session))
            if not auth.cookies and self._session_cookies:
                cookies_to_inject.extend(self._session_cookies)
            if cookies_to_inject:
                session.add_cookies(cookies_to_inject)
            queue: deque[tuple[str, int]] = deque((q, 0) for q in self._queries)
            while queue and jobs_left > 0:
                query, attempts = queue.popleft()
                blocked = False
                for job in self._search_one_query(session, query, rate):
                    if isinstance(job, _Blocked):
                        blocked = True
                        break
                    if job.id in known:
                        continue
                    enriched = self._post_serp_enrich(session, job, rate)
                    if enriched is None:
                        continue
                    yield enriched
                    jobs_left -= 1
                    if jobs_left <= 0:
                        logger.info(
                            "Glassdoor max_jobs_per_run=%d new jobs reached; stopping",
                            self._max_jobs,
                        )
                        return
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
            board_hosts=BOARD_HOSTS_GLASSDOOR,
            board_label="Glassdoor",
        )
        return post_serp_enrich(
            session,
            job,
            rate,
            policy=policy,
            budget=self._detail_budget,
            board_detail_fn=self._enrich_from_glassdoor_detail,
        )

    def _enrich_from_glassdoor_detail(
        self,
        session: PlaywrightSession,
        job: Job,
        rate: RateLimiter,
    ) -> Job:
        """One listing-page visit: resolve external apply + fuller description."""
        rate.wait()
        pool = self._effective_proxy_pool()
        proxy = pool.next() if pool else None
        content = _fetch(session, job.url, proxy=proxy)
        if content is None:
            return job
        if looks_like_cloudflare(content):
            logger.warning(
                "Glassdoor detail blocked for %s; leaving incomplete", job.url
            )
            if pool is not None and proxy is not None:
                pool.burn(proxy, reason="cloudflare-detail")
                session.drop_proxy_context(proxy)
            return job

        updates: dict[str, Any] = {"raw": {**job.raw, "glassdoor_detail": True}}
        apply_url = apply_url_from_glassdoor_detail_html(content)
        if apply_url and is_external_apply_url(
            apply_url, board_hosts=BOARD_HOSTS_GLASSDOOR
        ):
            platform = sniff_platform(apply_url)
            updates["apply_url"] = canonicalize_url(apply_url)
            updates["raw"] = {
                **updates["raw"],
                "listing_url": job.url,
                "platform": platform,
                "apply_resolve": "glassdoor_detail",
            }
            logger.info(
                "Glassdoor detail apply-url %s → %s (%s)",
                job.url,
                apply_url,
                platform,
            )
        desc = description_from_glassdoor_detail_html(content)
        if desc and len(desc.strip()) > len((job.description or "").strip()):
            updates["description"] = desc
            updates["raw"] = {
                **updates.get("raw", job.raw),
                "description_source": "glassdoor_detail_html",
            }
            logger.info(
                "Glassdoor detail description for %s (%d chars)",
                job.url,
                len(desc),
            )
        return job.model_copy(update=updates)

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
    ) -> Iterator["Job | _Blocked"]:
        """Paginate Glassdoor search until empty page, full dupes, or max_pages."""
        seen_urls: set[str] = set()
        pool = self._effective_proxy_pool()

        for page_idx in range(self._max_pages):
            page_num = page_idx + 1  # Glassdoor p= is 1-based
            url = build_glassdoor_search_url(
                query,
                remote_only=self._remote_only,
                posted_within_days=self._posted_within_days,
                page=page_num,
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
            if looks_like_cloudflare(content):
                _mark_blocked(session, pool, proxy, kind="search", query=query)
                yield _Blocked()
                return

            # Cards rendered inline — no detail fetches.
            jobs = extract_jobs_from_search(content, source_name=self.name)
            if not jobs and extract_job_urls(content):
                logger.warning(
                    "Glassdoor search page for %r p=%d has %d card URLs but no "
                    "extractable records — check card DOM shape",
                    query,
                    page_num,
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
                "Glassdoor query %r page %d: %d jobs (%d new)",
                query,
                page_num,
                len(jobs),
                new_on_page,
            )
            if not jobs or new_on_page == 0:
                break

        logger.info(
            "Glassdoor query %r finished: %d unique jobs across pages",
            query,
            len(seen_urls),
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


def extract_jobs_from_search(html: str, *, source_name: str) -> list[Job]:
    """Parse full ``Job`` records from a Glassdoor search page.

    Glassdoor renders every result inline as
    ``<li data-test="jobListing" data-jobid="<id>">`` — the anchor
    ``<a data-test="job-title" href="/job-listing/…">`` carries both
    the title text and the canonical detail URL, and sibling elements
    hold the employer name, ``emp-location``, ``descSnippet``, and
    optional salary. Skipping detail-page fetches sidesteps
    Glassdoor's per-listing rate limit and cuts 30× round trips.

    Returns ``[]`` when no jobListing cards are present; the caller
    warns if URL anchors exist but records don't (schema drift)."""
    tree = lhtml.fromstring(html)
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in tree.xpath('//li[@data-test="jobListing"]'):
        jobid = (card.get("data-jobid") or "").strip()
        if not jobid or jobid in seen:
            continue
        title_anchors = card.xpath('.//a[@data-test="job-title"]')
        if not title_anchors:
            continue
        title = _clean_text(title_anchors[0].text_content())
        href = (title_anchors[0].get("href") or "").strip()
        if not (title and href):
            continue
        url = href if href.startswith("http") else f"https://www.glassdoor.com{href}"

        company = _card_company(card)
        if not company:
            continue

        location = _card_first_text(card, './/*[@data-test="emp-location"]')
        snippet = _card_first_text(card, './/*[@data-test="descSnippet"]')
        salary = _card_first_text(card, './/*[@data-test="detailSalary"]')
        job_age = _card_first_text(card, './/*[@data-test="job-age"]')

        seen.add(jobid)
        apply_url = _offsite_apply_from_card(card)
        raw: dict[str, Any] = {
            "jobid": jobid,
            "salary_snippet": salary,
            "posted_age": job_age,
            "source_extraction": "search-page-card",
        }
        if apply_url:
            raw["apply_resolve"] = "serp_card"
            raw["platform"] = sniff_platform(apply_url)
        jobs.append(
            Job.new(
                source_name=source_name,
                url=url.split("?")[0].rstrip("/"),
                title=title,
                company=company,
                description=snippet or "",
                location=location or None,
                apply_url=apply_url,
                raw=raw,
            )
        )
    return jobs


def _offsite_apply_from_card(card: object) -> str | None:
    """Best-effort external apply URL from SERP card anchors (often absent)."""
    for href in card.xpath(".//a[@href]/@href"):  # type: ignore[attr-defined]
        resolved = resolve_apply_href(href)
        if resolved and is_external_apply_url(
            resolved, board_hosts=BOARD_HOSTS_GLASSDOOR
        ):
            return canonicalize_url(resolved)
    return None


def _card_company(card: object) -> str | None:
    """Extract the employer name from a Glassdoor card.

    Preferred: the ``span.EmployerProfile_compactEmployerName…`` child of
    ``div#job-employer-<jobid>`` — Glassdoor keeps ``compactEmployerName``
    stable across CSS-module hash rotations. Fallback: the
    ``div#job-employer-…`` container text with the rating stripped off
    the tail."""
    span = card.xpath(  # type: ignore[attr-defined]
        './/span[contains(@class, "compactEmployerName")]'
    )
    if span:
        cleaned = _clean_text(span[0].text_content())
        if cleaned:
            return cleaned
    emp_div = card.xpath(  # type: ignore[attr-defined]
        './/div[starts-with(@id, "job-employer-")]'
    )
    if emp_div:
        full = _clean_text(emp_div[0].text_content())
        # Ratings look like "Ultragenyx3.4" or "Ultragenyx 3.4" — chop
        # off any trailing digit/decimal run.
        import re as _re
        return _re.sub(r"\s*\d(?:\.\d+)?\s*$", "", full) or None
    return None


def _card_first_text(card: object, xpath: str) -> str | None:
    hits = card.xpath(xpath)  # type: ignore[attr-defined]
    if not hits:
        return None
    return _clean_text(hits[0].text_content()) or None


def _clean_text(text: str) -> str:
    return " ".join(text.split())


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
