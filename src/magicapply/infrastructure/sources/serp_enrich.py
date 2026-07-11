"""Shared SERP → offsite → capped board-detail enrichment.

Used by LinkedIn, Indeed, and Glassdoor discover adapters so the three
boards share one mental model:

1. Prefer data already on the search card / hydration blob.
2. When an external ``apply_url`` exists, prefer description from the
   destination careers/ATS page (not the job board listing).
3. If apply URL and/or description are still incomplete, optionally visit
   the board listing detail page — but only up to ``max_fetches`` per run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from magicapply.domain.models.job import Job, canonicalize_url
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.sources.apply_url import (
    description_from_destination_html,
    is_external_apply_url,
    sniff_platform,
)
from magicapply.infrastructure.sources.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

# SERP snippets shorter than this are treated as incomplete for offsite upgrade.
DEFAULT_MIN_DESCRIPTION_CHARS = 200

BoardDetailFn = Callable[
    [PlaywrightSession, Job, RateLimiter],
    Job,
]


class _HasEnrichFlags(Protocol):
    enrich_apply_urls: bool
    enrich_descriptions: bool
    board_detail_fallback: bool
    require_external_apply: bool
    min_description_chars: int


@dataclass
class DetailBudget:
    """Hard cap on board listing-detail visits per discover run."""

    max_fetches: int
    used: int = 0

    def remaining(self) -> bool:
        return self.used < max(0, self.max_fetches)

    def consume(self, *, board: str) -> bool:
        if not self.remaining():
            return False
        self.used += 1
        if self.used == self.max_fetches:
            logger.info(
                "%s detail-fetch cap reached (%d); further jobs stay SERP/offsite only",
                board,
                self.max_fetches,
            )
        return True


@dataclass(frozen=True, slots=True)
class SerpEnrichPolicy:
    """Per-adapter enrich knobs (mirrors LinkedIn / Indeed / GD config)."""

    enrich_apply_urls: bool = True
    enrich_descriptions: bool = True
    board_detail_fallback: bool = True
    require_external_apply: bool = False
    min_description_chars: int = DEFAULT_MIN_DESCRIPTION_CHARS
    board_hosts: tuple[str, ...] = ()
    board_label: str = "board"


def description_incomplete(
    description: str | None,
    *,
    min_chars: int = DEFAULT_MIN_DESCRIPTION_CHARS,
) -> bool:
    text = (description or "").strip()
    return not text or len(text) < min_chars


def enrich_description_from_destination(
    session: PlaywrightSession,
    job: Job,
    rate: RateLimiter,
    *,
    board_hosts: tuple[str, ...],
) -> Job:
    """Fetch JD text from external apply_url (off the job board)."""
    target = (job.apply_url or "").strip()
    if not is_external_apply_url(target, board_hosts=board_hosts):
        return job
    rate.wait()
    page = session.new_page()
    try:
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            wait = getattr(page, "wait_for_timeout", None)
            if callable(wait):
                wait(3000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Offsite description failed for %s: %s", target, exc)
            return job
        desc = description_from_destination_html(page.content())
        final_url = page.url
    finally:
        page.close()

    if not desc:
        logger.info(
            "Offsite description empty for %s (platform=%s)",
            target,
            sniff_platform(target),
        )
        return job
    logger.info(
        "Offsite description for %s → %d chars from %s",
        job.url,
        len(desc),
        sniff_platform(target) or "generic",
    )
    return job.model_copy(
        update={
            "description": desc,
            "raw": {
                **job.raw,
                "description_source": "destination_html",
                "description_url": final_url,
                "platform": sniff_platform(target) or job.raw.get("platform"),
            },
        }
    )


def post_serp_enrich(
    session: PlaywrightSession,
    job: Job,
    rate: RateLimiter,
    *,
    policy: SerpEnrichPolicy,
    budget: DetailBudget,
    board_detail_fn: BoardDetailFn,
) -> Job | None:
    """Prefer offsite; fall back to capped board-detail visits.

    Returns None when ``require_external_apply`` and still no external URL.
    """
    hosts = policy.board_hosts
    has_external = is_external_apply_url(job.apply_url, board_hosts=hosts)

    # 1) Offsite first when SERP already has a destination.
    if (
        policy.enrich_descriptions
        and has_external
        and description_incomplete(
            job.description, min_chars=policy.min_description_chars
        )
    ):
        job = enrich_description_from_destination(
            session, job, rate, board_hosts=hosts
        )
        has_external = is_external_apply_url(job.apply_url, board_hosts=hosts)

    needs_apply = policy.enrich_apply_urls and not has_external
    needs_desc = policy.enrich_descriptions and description_incomplete(
        job.description, min_chars=policy.min_description_chars
    )
    use_detail = policy.board_detail_fallback and (needs_apply or needs_desc)

    if use_detail and budget.consume(board=policy.board_label):
        job = board_detail_fn(session, job, rate)
        has_external = is_external_apply_url(job.apply_url, board_hosts=hosts)
        if (
            policy.enrich_descriptions
            and has_external
            and description_incomplete(
                job.description, min_chars=policy.min_description_chars
            )
        ):
            job = enrich_description_from_destination(
                session, job, rate, board_hosts=hosts
            )
    elif use_detail and not budget.remaining():
        logger.debug(
            "%s detail budget exhausted; skipping detail for %s",
            policy.board_label,
            job.url,
        )

    has_external = is_external_apply_url(job.apply_url, board_hosts=hosts)
    if policy.require_external_apply and not has_external:
        logger.info(
            "%s skip %s — no external apply URL after SERP/fallback",
            policy.board_label,
            job.url,
        )
        return None
    return job


def apply_url_if_external(
    raw: str | None,
    *,
    board_hosts: tuple[str, ...],
) -> str | None:
    """Return canonical external apply URL or None."""
    if not raw or not str(raw).strip():
        return None
    candidate = str(raw).strip()
    if not candidate.startswith("http"):
        return None
    if not is_external_apply_url(candidate, board_hosts=board_hosts):
        return None
    return canonicalize_url(candidate)
