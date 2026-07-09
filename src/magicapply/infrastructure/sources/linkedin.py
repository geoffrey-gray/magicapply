"""LinkedIn adapter — authenticated search-based scraping.

Uses a PlaywrightSession seeded with an ``li_at`` session cookie (from the
``LINKEDIN_LI_AT`` env var). Per-query the adapter loads the LinkedIn search
page and extracts job cards from the embedded Voyager JSON (``<code>`` blocks).
LinkedIn no longer serves schema.org JSON-LD on job detail pages reliably, so
we avoid per-job detail fetches when the search payload is parseable.

**Anti-bot posture (safe drip):** serial single-page navigation, rate limit
with jitter, random page dwell, periodic longer pauses, hard circuit-break
on redirects / auth walls, and a per-run job ceiling. Never concurrent.

**ToS-sensitive.** LinkedIn's terms forbid automated access. This adapter
refuses to run unless ``MAGICAPPLY_LINKEDIN_ACK=1`` is set — an explicit,
per-operator acknowledgement that they accept the risk.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import random
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lxml import html as lhtml

from magicapply.config.models import LinkedInSource
from magicapply.domain.models.job import Job, canonicalize_url
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
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

_SEARCH_BASE = "https://www.linkedin.com/jobs/search/"
_PAGE_SIZE = 25  # LinkedIn search offset step
_CODE_BLOCK_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL)
_JOB_ID_RE = re.compile(r"fsd_jobPosting:(\d+)")
_DEFAULT_JITTER_RATIO = 0.35

# Auth / challenge markers in HTML (lowercase). Pure-function circuit break.
_AUTH_WALL_MARKERS = (
    "authwall",
    "session_redirect",
    "/checkpoint/challenge",
    "login-email",
    "join now",
    "sign in to linkedin",
    "unusual activity",
    "security verification",
    "captcha",
    "please verify",
)


def build_linkedin_search_url(
    query: str,
    *,
    remote_only: bool = True,
    posted_within_days: int | None = 7,
    start: int = 0,
) -> str:
    """Build a LinkedIn job-search URL with optional remote/date/page filters."""
    from urllib.parse import urlencode

    params: list[tuple[str, str]] = [("keywords", query)]
    if remote_only:
        # f_WT=2 → workplace type Remote (LinkedIn public search param).
        params.append(("f_WT", "2"))
    if posted_within_days and posted_within_days > 0:
        # f_TPR=r{seconds} → posted in the last N seconds.
        params.append(("f_TPR", f"r{int(posted_within_days) * 86400}"))
    if start > 0:
        params.append(("start", str(int(start))))
    return f"{_SEARCH_BASE}?{urlencode(params)}"


def looks_like_linkedin_auth_wall(html: str) -> bool:
    """Return True when HTML looks like login / checkpoint / CAPTCHA wall."""
    lo = html.lower()
    return any(marker in lo for marker in _AUTH_WALL_MARKERS)


def _is_redirect_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "err_too_many_redirects" in msg
        or "too many redirects" in msg
        or "net::err_aborted" in msg
    )


class LinkedInAdapter:
    """Authenticated LinkedIn scraper over Playwright (serial, safe drip)."""

    def __init__(
        self,
        *,
        name: str,
        queries: list[str],
        rate_limit_per_minute: int,
        li_at: str | None,
        acknowledged: bool,
        enrich_apply_urls: bool = False,
        remote_only: bool = True,
        posted_within_days: int | None = 7,
        max_pages: int = 6,
        page_dwell_ms_min: int = 2500,
        page_dwell_ms_max: int = 8000,
        pause_every_n_pages: int = 3,
        pause_ms_min: int = 15_000,
        pause_ms_max: int = 45_000,
        stop_on_redirect_error: bool = True,
        max_jobs_per_run: int = 150,
        data_dir: Path | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.name = name
        self._queries = list(queries)
        self._rate = rate_limit_per_minute
        self._li_at = li_at
        self._ack = acknowledged
        self._enrich_apply_urls = enrich_apply_urls
        self._remote_only = remote_only
        self._posted_within_days = posted_within_days
        self._max_pages = max(1, int(max_pages))
        self._dwell_min = max(0, int(page_dwell_ms_min))
        self._dwell_max = max(self._dwell_min, int(page_dwell_ms_max))
        self._pause_every = max(0, int(pause_every_n_pages))
        self._pause_min = max(0, int(pause_ms_min))
        self._pause_max = max(self._pause_min, int(pause_ms_max))
        self._stop_on_redirect = stop_on_redirect_error
        self._max_jobs = max(1, int(max_jobs_per_run))
        self._data_dir = data_dir
        self._rng = rng or random.Random()

    @classmethod
    def from_config(
        cls,
        config: LinkedInSource,
        *,
        data_dir: Path | None = None,
    ) -> LinkedInAdapter:
        return cls(
            name=config.name,
            queries=list(config.queries),
            rate_limit_per_minute=config.rate_limit_per_minute,
            li_at=os.environ.get("LINKEDIN_LI_AT") or None,
            acknowledged=os.environ.get("MAGICAPPLY_LINKEDIN_ACK") == "1",
            enrich_apply_urls=config.enrich_apply_urls,
            remote_only=config.remote_only,
            posted_within_days=config.posted_within_days,
            max_pages=config.max_pages,
            page_dwell_ms_min=config.page_dwell_ms_min,
            page_dwell_ms_max=config.page_dwell_ms_max,
            pause_every_n_pages=config.pause_every_n_pages,
            pause_ms_min=config.pause_ms_min,
            pause_ms_max=config.pause_ms_max,
            stop_on_redirect_error=config.stop_on_redirect_error,
            max_jobs_per_run=config.max_jobs_per_run,
            data_dir=data_dir,
        )

    def discover(self) -> Iterator[Job]:
        # ToS gate first: nothing else is attempted without an explicit ack.
        if not self._ack:
            raise SourceError(
                "LinkedIn scraping refused: set MAGICAPPLY_LINKEDIN_ACK=1 to "
                "acknowledge you accept the ToS-violation risk. See "
                "docs/VM_DEV.md for the env-var reference."
            )
        auth = resolve_session_auth("linkedin", self._data_dir)
        if auth.source == "none" and not self._li_at:
            raise SourceError(
                "LinkedIn needs auth: run `magicapply auth login linkedin`, "
                "or set LINKEDIN_SESSION_COOKIES / LINKEDIN_LI_AT in .env."
            )
        if not self._queries:
            return

        # Jitter is required — fixed 6s intervals are a bot signal.
        rate = RateLimiter(self._rate, jitter_ratio=_DEFAULT_JITTER_RATIO)
        jobs_left = self._max_jobs
        logger.info("LinkedIn auth resolved via %s", auth.source)

        with PlaywrightSession(
            headless=True,
            storage_state_path=auth.storage_state_path,
        ) as session:
            if auth.cookies:
                session.add_cookies(auth.cookies)
            elif auth.source == "none" and self._li_at:
                session.add_cookies([_li_at_cookie(self._li_at)])
            for query in self._queries:
                if jobs_left <= 0:
                    break
                for job in self._search_one_query(
                    session, query, rate, jobs_left=jobs_left
                ):
                    yield job
                    jobs_left -= 1
                    if jobs_left <= 0:
                        logger.info(
                            "LinkedIn max_jobs_per_run=%d reached; stopping",
                            self._max_jobs,
                        )
                        return

    def _search_one_query(
        self,
        session: PlaywrightSession,
        query: str,
        rate: RateLimiter,
        *,
        jobs_left: int,
    ) -> Iterator[Job]:
        """Paginate LinkedIn search: one Page, serial gotos, circuit-break on walls."""
        seen_urls: set[str] = set()
        used_legacy_fallback = False
        yielded = 0
        # Reuse a single page for all offsets — more human than open/close.
        page = session.new_page()
        try:
            for page_idx in range(self._max_pages):
                if yielded >= jobs_left:
                    break
                if (
                    self._pause_every > 0
                    and page_idx > 0
                    and page_idx % self._pause_every == 0
                ):
                    pause_ms = self._rng.randint(self._pause_min, self._pause_max)
                    logger.info(
                        "LinkedIn human-like pause %dms before page %d",
                        pause_ms,
                        page_idx + 1,
                    )
                    time.sleep(pause_ms / 1000.0)

                start = page_idx * _PAGE_SIZE
                search_url = build_linkedin_search_url(
                    query,
                    remote_only=self._remote_only,
                    posted_within_days=self._posted_within_days,
                    start=start,
                )
                rate.wait()
                try:
                    page.goto(
                        search_url, wait_until="domcontentloaded", timeout=30_000
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "LinkedIn search failed for %r start=%d: %s",
                        query,
                        start,
                        exc,
                    )
                    if self._stop_on_redirect and _is_redirect_error(exc):
                        logger.warning(
                            "LinkedIn circuit-break: redirect/auth navigation "
                            "error — stop paging this run (refresh li_at if persistent)"
                        )
                    break

                dwell_ms = self._rng.randint(self._dwell_min, self._dwell_max)
                wait = getattr(page, "wait_for_timeout", None)
                if callable(wait):
                    wait(dwell_ms)
                else:
                    time.sleep(dwell_ms / 1000.0)

                html = page.content()
                if looks_like_linkedin_auth_wall(html):
                    logger.warning(
                        "LinkedIn circuit-break: auth/checkpoint wall on page %d "
                        "for %r — stop this run and refresh LINKEDIN_LI_AT",
                        page_idx + 1,
                        query,
                    )
                    break

                jobs = extract_jobs_from_search(html, source_name=self.name)
                if not jobs and page_idx == 0:
                    used_legacy_fallback = True
                    for job in self._legacy_detail_jobs(session, html, rate):
                        if job.url in seen_urls:
                            continue
                        seen_urls.add(job.url)
                        yield job
                        yielded += 1
                        if yielded >= jobs_left:
                            break
                    break

                new_on_page = 0
                for job in jobs:
                    if job.url in seen_urls:
                        continue
                    seen_urls.add(job.url)
                    new_on_page += 1
                    if self._enrich_apply_urls:
                        job = _enrich_apply_url(session, job, rate)
                    yield job
                    yielded += 1
                    if yielded >= jobs_left:
                        break

                logger.info(
                    "LinkedIn query %r page %d (start=%d): %d jobs (%d new)",
                    query,
                    page_idx + 1,
                    start,
                    len(jobs),
                    new_on_page,
                )
                if not jobs or new_on_page == 0:
                    break
        finally:
            page.close()

        if not used_legacy_fallback:
            logger.info(
                "LinkedIn query %r finished: %d unique jobs across pages",
                query,
                len(seen_urls),
            )

    def _legacy_detail_jobs(
        self,
        session: PlaywrightSession,
        html: str,
        rate: RateLimiter,
    ) -> Iterator[Job]:
        """Detail-page JSON-LD when Voyager cards are absent (first page only)."""
        for job_url in extract_job_urls(html):
            rate.wait()
            job_page = session.new_page()
            try:
                try:
                    job_page.goto(
                        job_url, wait_until="domcontentloaded", timeout=30_000
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "LinkedIn detail fetch failed for %s: %s", job_url, exc
                    )
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