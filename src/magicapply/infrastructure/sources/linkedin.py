"""LinkedIn adapter — prefer offsite, fall back to LI detail (capped).

**Default posture:**

1. **SERP first** on linkedin.com → title, company, location, job link,
   offsite apply URL when present on the card.
2. **Offsite next** — when ``apply_url`` is external, fetch JD text from
   the destination careers/ATS page (not from LinkedIn).
3. **LinkedIn detail fallback** — if apply URL or description is still
   missing, visit ``/jobs/view`` once per job, but only up to
   ``max_linkedin_detail_fetches`` times per discover run.

**Anti-bot posture (safe drip):** serial navigation, rate limit with jitter,
page dwell, circuit-break on auth walls, job ceiling + detail-fetch cap.
Never concurrent.

**ToS-sensitive.** Requires ``MAGICAPPLY_LINKEDIN_ACK=1``.
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
    BOARD_HOSTS_LINKEDIN,
    apply_url_from_linkedin_detail_html,
    description_from_destination_html,
    is_external_apply_url as _shared_is_external_apply_url,
    resolve_apply_href,
    sniff_platform,
)
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SEARCH_BASE = "https://www.linkedin.com/jobs/search/"
_PAGE_SIZE = 25  # LinkedIn search offset step
_CODE_BLOCK_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL)
_JOB_ID_RE = re.compile(r"fsd_jobPosting:(\d+)")
_DEFAULT_JITTER_RATIO = 0.35

# Strong auth / challenge markers only. Avoid weak substrings that appear on
# real authenticated search pages (e.g. "join now" nav links, "session_redirect"
# in signup URLs, "captcha" inside feature-flag attribute names).
_AUTH_WALL_MARKERS = (
    'class="authwall',
    "class='authwall",
    "id=\"authwall\"",
    "id='authwall'",
    "/checkpoint/challenge",
    "sign in to linkedin",
    "unusual activity",
    "security verification",
    "please verify your identity",
    "login-email",
    "name=\"session_key\"",  # classic login form
    "name='session_key'",
)

# DOM markers that prove we landed on a real search-results page.
_SEARCH_RESULTS_MARKERS = (
    "job-search-card",
    "jobs-search__results-list",
    "base-search-card__title",
    "jobpostingcard",
    "fsd_jobposting",
)

_JOB_POSTING_URN_RE = re.compile(r"urn:li:jobPosting:(\d+)", re.I)
_JOB_VIEW_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)", re.I)


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
    """Return True when HTML looks like login / checkpoint wall.

    Prefer false-negative over false-positive: a real jobs SERP often embeds
    guest-nav strings (``Join now``, ``session_redirect``, feature-flag
    ``captcha`` attrs). If search-result structure is present, it is not a wall.
    """
    lo = html.lower()
    if any(marker in lo for marker in _SEARCH_RESULTS_MARKERS):
        return False
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
        enrich_apply_urls: bool = True,
        enrich_descriptions: bool = True,
        linkedin_description_fallback: bool = True,
        max_linkedin_detail_fetches: int = 8,
        require_external_apply: bool = False,
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
        self._enrich_descriptions = enrich_descriptions
        self._li_detail_fallback = linkedin_description_fallback
        self._max_detail_fetches = max(0, int(max_linkedin_detail_fetches))
        self._detail_fetches_used = 0
        self._require_external_apply = require_external_apply
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
            enrich_descriptions=config.enrich_descriptions,
            linkedin_description_fallback=config.linkedin_description_fallback,
            max_linkedin_detail_fetches=config.max_linkedin_detail_fetches,
            require_external_apply=config.require_external_apply,
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
                if self._remote_only:
                    jobs = [_annotate_remote_location(j) for j in jobs]
                if not jobs and page_idx == 0:
                    used_legacy_fallback = True
                    for job in self._legacy_detail_jobs(session, html, rate):
                        if job.url in seen_urls:
                            continue
                        seen_urls.add(job.url)
                        if self._remote_only:
                            job = _annotate_remote_location(job)
                        job = self._post_serp_enrich(session, job, rate)
                        if job is None:
                            continue
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
                    job = self._post_serp_enrich(session, job, rate)
                    if job is None:
                        continue
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

    def _detail_budget_remaining(self) -> bool:
        return self._detail_fetches_used < self._max_detail_fetches

    def _consume_detail_budget(self) -> bool:
        """Reserve one LI detail fetch. Returns False if cap already hit."""
        if not self._detail_budget_remaining():
            return False
        self._detail_fetches_used += 1
        if self._detail_fetches_used == self._max_detail_fetches:
            logger.info(
                "LinkedIn detail-fetch cap reached (%d); further jobs stay SERP/offsite only",
                self._max_detail_fetches,
            )
        return True

    def _post_serp_enrich(
        self,
        session: PlaywrightSession,
        job: Job,
        rate: RateLimiter,
    ) -> Job | None:
        """Prefer offsite; fall back to capped LinkedIn detail visits.

        Order:
        1. Offsite description when SERP already has external apply_url
        2. One LI detail visit (budgeted) if apply URL and/or description still missing
        3. Offsite description again if detail resolved a new external apply_url

        Returns None when ``require_external_apply`` and still no offsite URL.
        """
        has_external = _is_external_apply_url(job.apply_url)

        # 1) Offsite first when we already have a destination.
        if (
            self._enrich_descriptions
            and has_external
            and not (job.description or "").strip()
        ):
            job = _enrich_description_from_destination(session, job, rate)
            has_external = _is_external_apply_url(job.apply_url)

        needs_apply = self._enrich_apply_urls and not has_external
        needs_desc = self._enrich_descriptions and not (job.description or "").strip()
        # Fallback when offsite path couldn't complete the job record.
        use_li_detail = self._li_detail_fallback and (needs_apply or needs_desc)

        if use_li_detail and self._consume_detail_budget():
            job = _enrich_from_linkedin_detail(session, job, rate)
            has_external = _is_external_apply_url(job.apply_url)
            # Detail may have just unlocked an external apply URL.
            if (
                self._enrich_descriptions
                and has_external
                and not (job.description or "").strip()
            ):
                job = _enrich_description_from_destination(session, job, rate)
        elif use_li_detail and not self._detail_budget_remaining():
            logger.debug(
                "LinkedIn detail budget exhausted; skipping detail for %s",
                job.url,
            )

        has_external = _is_external_apply_url(job.apply_url)
        if self._require_external_apply and not has_external:
            logger.info(
                "LinkedIn skip %s — no external apply URL after SERP/fallback",
                job.url,
            )
            return None

        return job

    def _legacy_detail_jobs(
        self,
        session: PlaywrightSession,
        html: str,
        rate: RateLimiter,
    ) -> Iterator[Job]:
        """SERP-only fallback: job view links from HTML, no detail fetches.

        Used when card parsers find nothing. Does **not** visit each
        ``/jobs/view`` URL (SERP-only posture).
        """
        del session, rate  # no per-job navigation
        for job_url in extract_job_urls(html):
            m = _JOB_VIEW_ID_RE.search(job_url)
            job_id = m.group(1) if m else None
            yield Job.new(
                source_name=self.name,
                url=job_url,
                title="(unknown title)",
                company="(unknown company)",
                description="",
                location=None,
                raw={"parser": "serp_url_only", "job_id": job_id},
            )


def _is_external_apply_url(url: str | None) -> bool:
    return _shared_is_external_apply_url(url, board_hosts=BOARD_HOSTS_LINKEDIN)


def _annotate_remote_location(job: Job) -> Job:
    """Mark remote-filtered SERP hits so prefilter location rules can match.

    LinkedIn's ``f_WT=2`` remote filter still returns cards with location
    text like ``United States`` (no ``Remote``). Annotate when missing.
    """
    loc = (job.location or "").strip()
    if re.search(r"\bremote\b", loc, re.I):
        return job
    if re.search(r"\bremote\b", job.title or "", re.I):
        return job
    new_loc = f"{loc} (Remote)" if loc else "Remote"
    return job.model_copy(update={"location": new_loc})


def _enrich_description_from_destination(
    session: PlaywrightSession,
    job: Job,
    rate: RateLimiter,
) -> Job:
    """Fetch the external apply/careers page for JD text (off LinkedIn)."""
    target = (job.apply_url or "").strip()
    if not _is_external_apply_url(target):
        return job
    rate.wait()
    page = session.new_page()
    try:
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn offsite description failed for %s: %s", target, exc
            )
            return job
        desc = description_from_destination_html(page.content())
        final_url = page.url
    finally:
        page.close()

    if not desc:
        logger.info(
            "LinkedIn offsite description empty for %s (platform=%s)",
            target,
            sniff_platform(target),
        )
        return job
    logger.info(
        "LinkedIn offsite description for %s → %d chars from %s",
        job.url,
        len(desc),
        sniff_platform(target) or "generic",
    )
    updates: dict[str, Any] = {
        "description": desc,
        "raw": {
            **job.raw,
            "description_source": "destination_html",
            "description_url": final_url,
            "platform": sniff_platform(target) or job.raw.get("platform"),
        },
    }
    return job.model_copy(update=updates)


def _enrich_from_linkedin_detail(
    session: PlaywrightSession,
    job: Job,
    rate: RateLimiter,
) -> Job:
    """One LinkedIn /jobs/view visit: resolve offsite apply + description."""
    rate.wait()
    page = session.new_page()
    try:
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=30_000)
            # Apply CTA + description body hydrate slowly on cold pages.
            page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn detail fallback failed for %s: %s", job.url, exc
            )
            return job
        html = page.content()
        apply_url = apply_url_from_linkedin_detail_html(html)
        if not apply_url:
            apply_url = _try_click_offsite_apply(page)
        desc = description_from_linkedin_detail_html(html)
    finally:
        page.close()

    updates: dict[str, Any] = {"raw": {**job.raw, "linkedin_detail": True}}
    if apply_url and _is_external_apply_url(apply_url):
        platform = sniff_platform(apply_url)
        updates["apply_url"] = canonicalize_url(apply_url)
        updates["raw"] = {
            **updates["raw"],
            "listing_url": job.url,
            "platform": platform,
            "apply_resolve": "linkedin_detail",
        }
        logger.info(
            "LinkedIn detail apply-url %s → %s (%s)",
            job.url,
            apply_url,
            platform,
        )
    if desc and not (job.description or "").strip():
        updates["description"] = desc
        updates["raw"] = {
            **updates.get("raw", job.raw),
            "description_source": "linkedin_detail_html",
        }
        logger.info(
            "LinkedIn detail description for %s (%d chars)", job.url, len(desc)
        )
    return job.model_copy(update=updates)


def _try_click_offsite_apply(page: Any) -> str | None:
    """Click Apply when it opens a new tab to an external host."""
    selectors = (
        "button.jobs-apply-button",
        "button.jobs-apply-button--top-card",
        "a.jobs-apply-button",
        "button:has-text('Apply')",
    )
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            with page.context.expect_page(timeout=6_000) as new_page_info:
                loc.click(timeout=4_000)
            new_page = new_page_info.value
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=20_000)
                url = new_page.url
            finally:
                new_page.close()
            if _is_external_apply_url(url):
                return url
        except Exception:  # noqa: BLE001
            continue
    return None


def description_from_linkedin_detail_html(html: str) -> str:
    """Extract plain-text job description from a LinkedIn detail page."""
    try:
        tree = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001
        return ""
    xpaths = (
        "//*[contains(@class,'show-more-less-html__markup')]",
        "//*[contains(@class,'description__text')]",
        "//*[@id='job-details']",
        "//*[contains(@class,'jobs-description__content')]",
        "//*[contains(@class,'jobs-box__html-content')]",
        "//*[contains(@class,'jobs-description')]",
    )
    for xp in xpaths:
        for el in tree.xpath(xp):
            text = " ".join((el.text_content() or "").split())
            if len(text) >= 80:
                return text
    return ""


def extract_jobs_from_search(html: str, *, source_name: str) -> list[Job]:
    """Parse jobs from LinkedIn search HTML.

    Tries Voyager ``JobPostingCard`` JSON in ``<code>`` blocks first (legacy
    authenticated payload). Falls back to public/guest DOM cards
    (``div.job-search-card`` + ``data-entity-urn``) which is what LinkedIn
    currently serves even to logged-in Playwright sessions.
    """
    jobs = _extract_jobs_from_voyager(html, source_name=source_name)
    if jobs:
        return jobs
    return _extract_jobs_from_dom_cards(html, source_name=source_name)


def _extract_jobs_from_voyager(html: str, *, source_name: str) -> list[Job]:
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


def _extract_jobs_from_dom_cards(html: str, *, source_name: str) -> list[Job]:
    """Parse ``job-search-card`` DOM nodes from public LinkedIn SERP HTML."""
    try:
        tree = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001 — malformed HTML
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    cards = tree.xpath(
        "//div[contains(@class,'job-search-card')]"
        " | //li[contains(@class,'job-search-card')]"
        " | //div[contains(@class,'base-search-card') and @data-entity-urn]"
    )
    for card in cards:
        job = _dom_card_to_job(card, source_name=source_name)
        if job is None or job.url in seen:
            continue
        seen.add(job.url)
        jobs.append(job)
    return jobs


def _dom_card_to_job(card: Any, *, source_name: str) -> Job | None:
    job_id: str | None = None
    urn = (card.get("data-entity-urn") or "").strip()
    if urn:
        m = _JOB_POSTING_URN_RE.search(urn)
        if m:
            job_id = m.group(1)
    if not job_id:
        for href in card.xpath(".//a[@href]/@href"):
            m = _JOB_VIEW_ID_RE.search(href or "")
            if m:
                job_id = m.group(1)
                break
    if not job_id:
        return None

    title = _first_text(
        card,
        ".//*[contains(@class,'base-search-card__title')]",
        ".//h3[contains(@class,'base-search-card__title')]",
        ".//a[contains(@class,'base-card__full-link')]/@aria-label",
    )
    if not title:
        return None
    company = (
        _first_text(
            card,
            ".//*[contains(@class,'base-search-card__subtitle')]",
            ".//h4[contains(@class,'base-search-card__subtitle')]",
            ".//a[contains(@class,'hidden-nested-link')]",
        )
        or "(unknown company)"
    )
    location = _first_text(
        card,
        ".//*[contains(@class,'job-search-card__location')]",
        ".//*[contains(@class,'job-search-card__metadata-location')]",
    )
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    apply_url = _offsite_apply_from_card(card)
    platform = sniff_platform(apply_url) if apply_url else None
    return Job.new(
        source_name=source_name,
        url=url,
        title=title,
        company=company,
        description="",
        location=location,
        apply_url=apply_url,
        raw={
            "parser": "dom_card",
            "entity_urn": urn or None,
            "job_id": job_id,
            "platform": platform,
            "apply_resolve": "serp_card" if apply_url else None,
        },
    )


def _offsite_apply_from_card(card: Any) -> str | None:
    """Best-effort external apply URL from SERP card anchors (often absent)."""
    for href in card.xpath(".//a[@href]/@href"):
        resolved = resolve_apply_href(href)
        if resolved and _is_external_apply_url(resolved):
            return canonicalize_url(resolved)
    return None


def _first_text(node: Any, *xpaths: str) -> str | None:
    for xp in xpaths:
        if xp.endswith("/@aria-label") or xp.endswith("/@href"):
            vals = node.xpath(xp)
            if vals:
                text = str(vals[0]).strip()
                if text:
                    return html_lib.unescape(text).replace("\xa0", " ").strip() or None
            continue
        els = node.xpath(xp)
        for el in els:
            text = " ".join((el.text_content() or "").split())
            if text:
                return html_lib.unescape(text).replace("\xa0", " ").strip() or None
    return None


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