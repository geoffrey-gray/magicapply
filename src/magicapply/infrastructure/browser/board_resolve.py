"""Lazy board → external apply URL resolution at apply / pre-tailor time.

Opens an Indeed or LinkedIn listing once (authenticated Playwright page),
extracts an off-board apply URL when present, optionally upgrades a thin
SERP snippet with destination JD text, and stamps ``raw.board_resolve`` so
retries do not re-hit the board.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Protocol
from urllib.parse import urlparse

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.generic_wizard import _try_click
from magicapply.infrastructure.browser.navigate import safe_goto
from magicapply.infrastructure.sources.apply_url import (
    BOARD_HOSTS_INDEED,
    BOARD_HOSTS_LINKEDIN,
    apply_url_from_indeed_detail_html,
    apply_url_from_linkedin_detail_html,
    description_from_destination_html,
    description_from_indeed_detail_html,
    description_looks_thin,
    enrich_job_apply_url,
    indeed_apply_entry_url,
    is_external_apply_url,
    is_job_board_listing_url,
    mark_board_resolve_done,
    needs_board_destination_resolve,
    sniff_platform,
)

logger = logging.getLogger(__name__)

_MIN_DWELL_MS = 800
_MAX_DWELL_MS = 2200

_INDEED_APPLY_CLICK_SELECTORS = (
    "a[data-indeed-apply-button]",
    "button[data-indeed-apply-button]",
    "#indeedApplyButton",
    "button.ia-IndeedApplyButton",
    "a.ia-IndeedApplyButton",
    "#applyButtonLinkContainer a",
    "a[href*='applystart']",
    "button:has-text('Apply now')",
    "a:has-text('Apply on company site')",
    "button:has-text('Apply on company site')",
)

_LINKEDIN_OFFSITE_CLICK_SELECTORS = (
    "a.jobs-apply-button",
    "button.jobs-apply-button",
    "a[data-control-name='jobdetails_topcard_inapply']",
    "a:has-text('Apply')",
    "button:has-text('Apply')",
)


class _PageProto(Protocol):
    def goto(self, url: str) -> None: ...
    def content(self) -> str: ...
    def click(self, selector: str) -> None: ...

    @property
    def url(self) -> str: ...


def resolve_board_destination(
    page: _PageProto,
    job: Job,
    *,
    dwell: bool = True,
    rng: random.Random | None = None,
) -> tuple[Job, bool]:
    """Resolve external apply URL (+ JD) from a board listing.

    Returns ``(job, changed)`` where ``changed`` is True when apply_url,
    description, or Easy Apply meta was updated. Always stamps
    ``board_resolve=done`` when a listing visit was attempted so we do not
    loop on bot walls.
    """
    if not needs_board_destination_resolve(job):
        # External URL known but JD thin: try destination HTML only.
        if (
            job.apply_url
            and not is_job_board_listing_url(job.apply_url)
            and description_looks_thin(job.description)
        ):
            return _upgrade_jd_from_destination(page, job, dwell=dwell, rng=rng)
        return job, False

    listing = job.url
    host = urlparse(listing).netloc.lower()
    board = _board_kind(host)
    if board is None:
        return mark_board_resolve_done(job, board_resolve_skip="not_board"), False

    logger.info("board_resolve: opening %s listing %s", board, listing)
    try:
        safe_goto(page, listing)
    except Exception as exc:  # noqa: BLE001
        logger.warning("board_resolve: goto failed for %s: %s", listing, exc)
        return mark_board_resolve_done(job, board_resolve_error=str(exc)), False

    if dwell:
        _human_dwell(rng)

    html = ""
    try:
        html = page.content()
    except Exception as exc:  # noqa: BLE001
        logger.warning("board_resolve: content() failed: %s", exc)
        return mark_board_resolve_done(job, board_resolve_error=str(exc)), False

    updated = job
    changed = False

    external = _parse_external_from_html(html, board=board)
    if not external:
        external = _try_click_offsite(page, board=board, rng=rng)
        if external:
            changed = True

    if external and is_external_apply_url(
        external,
        board_hosts=BOARD_HOSTS_INDEED + BOARD_HOSTS_LINKEDIN,
    ):
        updated = enrich_job_apply_url(updated, external)
        changed = True
        if description_looks_thin(updated.description):
            updated, jd_changed = _upgrade_jd_from_destination(
                page, updated, dwell=dwell, rng=rng
            )
            changed = changed or jd_changed
    else:
        # Record Easy Apply / applystart meta for IndeedHandler.
        if board == "indeed":
            entry = indeed_apply_entry_url(updated)
            if not entry and "applystart" in html.lower():
                # Best-effort: keep listing; handler will click Apply.
                pass
            raw = {**(updated.raw or {})}
            if updated.raw.get("indeedApplyable") is None:
                raw["indeedApplyable"] = True
            if entry:
                raw["indeed_apply_url"] = entry
            # Prefer description from viewjob when snippet-thin.
            detail_jd = description_from_indeed_detail_html(html)
            if detail_jd and description_looks_thin(updated.description):
                updated = updated.model_copy(update={"description": detail_jd})
                changed = True
            updated = updated.model_copy(update={"raw": raw})
            changed = True
        elif board == "linkedin":
            raw = {**(updated.raw or {}), "linkedin_easy_apply": True}
            updated = updated.model_copy(update={"raw": raw})
            changed = True

    updated = mark_board_resolve_done(
        updated,
        board_resolve_source=board,
        platform=sniff_platform(updated.apply_url or updated.url),
    )
    return updated, True


def _board_kind(host: str) -> str | None:
    if "indeed.com" in host:
        return "indeed"
    if "linkedin.com" in host:
        return "linkedin"
    if "glassdoor.com" in host or "glassdoor.co.uk" in host:
        return "glassdoor"
    return None


def _parse_external_from_html(html: str, *, board: str) -> str | None:
    if board == "indeed":
        return apply_url_from_indeed_detail_html(html)
    if board == "linkedin":
        return apply_url_from_linkedin_detail_html(html)
    return None


def _try_click_offsite(
    page: _PageProto,
    *,
    board: str,
    rng: random.Random | None,
) -> str | None:
    selectors = (
        _INDEED_APPLY_CLICK_SELECTORS
        if board == "indeed"
        else _LINKEDIN_OFFSITE_CLICK_SELECTORS
    )
    before = getattr(page, "url", "") or ""
    for sel in selectors:
        if not _try_click(page, sel, timeout_ms=800):
            continue
        _human_dwell(rng, lo=400, hi=1200)
        after = getattr(page, "url", "") or before
        if after and after != before and not is_job_board_listing_url(after):
            return after
        # Some boards open company site in same tab via applystart redirect.
        if after and "applystart" in after.lower():
            _human_dwell(rng, lo=600, hi=1500)
            after = getattr(page, "url", "") or after
            if after and not is_job_board_listing_url(after):
                return after
        # Click may have been Easy Apply (stayed on board) — keep looking.
    return None


def _upgrade_jd_from_destination(
    page: _PageProto,
    job: Job,
    *,
    dwell: bool,
    rng: random.Random | None,
) -> tuple[Job, bool]:
    dest = job.apply_url
    if not dest or is_job_board_listing_url(dest):
        return job, False
    try:
        safe_goto(page, dest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("board_resolve: destination JD goto failed: %s", exc)
        return job, False
    if dwell:
        _human_dwell(rng)
    try:
        html = page.content()
    except Exception:  # noqa: BLE001
        return job, False
    text = description_from_destination_html(html)
    if len(text) < 80:
        return job, False
    if not description_looks_thin(job.description) and len(text) <= len(
        (job.description or "")
    ):
        return job, False
    return job.model_copy(
        update={
            "description": text,
            "raw": {
                **(job.raw or {}),
                "description_source": "destination_apply_time",
            },
        }
    ), True


def _human_dwell(
    rng: random.Random | None,
    *,
    lo: int = _MIN_DWELL_MS,
    hi: int = _MAX_DWELL_MS,
) -> None:
    r = rng or random.Random()
    time.sleep(r.randint(lo, hi) / 1000.0)
