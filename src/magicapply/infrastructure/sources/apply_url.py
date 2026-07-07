"""Resolve external apply URLs from job listing pages (custom ATS / PR2+PR5).

Pure parsing helpers live here; Playwright fetches stay in per-source adapters.
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from lxml import html as lhtml

from magicapply.domain.models.job import Job, canonicalize_url

logger = logging.getLogger(__name__)

# Host substring → platform id for routing, recipes, and corpus metrics.
PLATFORM_HOST_MARKERS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"),
    "workday": ("myworkdayjobs.com", ".myworkday.com"),
    "lever": ("lever.co", "jobs.lever.co"),
    "ashby": ("ashbyhq.com", "jobs.ashbyhq.com"),
    "eightfold": ("eightfold.ai",),
    "phenom": ("phenom.com", "phenompeople.com"),
    "icims": ("icims.com",),
    "taleo": ("taleo.net",),
    "successfactors": ("successfactors.com", "successfactors.eu"),
    "smartrecruiters": ("smartrecruiters.com",),
    "jazzhr": ("applytojob.com",),
}

BIG_FOUR_PLATFORMS = frozenset({"greenhouse", "workday", "lever", "ashby"})


def decode_linkedin_safety_go(href: str | None) -> str | None:
    """Decode ``linkedin.com/safety/go?url=...`` into the external apply URL."""
    if not href:
        return None
    parsed = urlparse(href.strip())
    if "linkedin.com" not in parsed.netloc.lower():
        return None
    if "/safety/go" not in parsed.path:
        return None
    raw = parse_qs(parsed.query).get("url", [None])[0]
    if not raw:
        return None
    return unquote(raw)


def resolve_apply_href(href: str | None) -> str | None:
    """Normalize an apply anchor href to an external URL when possible."""
    if not href:
        return None
    href = href.strip()
    decoded = decode_linkedin_safety_go(href)
    if decoded:
        return decoded
    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"} and "linkedin.com" not in parsed.netloc.lower():
        return href
    return None


def linkedin_apply_href_from_html(html: str) -> str | None:
    """Extract the Apply link href from a LinkedIn job detail page."""
    tree = lhtml.fromstring(html)
    selectors = (
        "//a[contains(@aria-label, 'Apply')]",
        "//a[contains(translate(@aria-label, 'APPLY', 'apply'), 'apply')]",
        "//a[contains(@class, 'jobs-apply-button')]",
    )
    for xpath in selectors:
        for anchor in tree.xpath(xpath):
            href = (anchor.get("href") or "").strip()
            if href:
                return href
    return None


def apply_url_from_linkedin_detail_html(html: str) -> str | None:
    """Resolve canonical external apply URL from LinkedIn job detail HTML."""
    href = linkedin_apply_href_from_html(html)
    resolved = resolve_apply_href(href)
    if not resolved:
        return None
    return canonicalize_url(resolved)


def indeed_apply_href_from_html(html: str) -> str | None:
    """Extract external apply href from an Indeed job detail page."""
    tree = lhtml.fromstring(html)
    xpaths = (
        "//div[@id='applyButtonLinkContainer']//a[@href]",
        "//a[contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply on company')]",
        "//a[contains(@data-indeed-apply-link, 'http')]",
        "//a[contains(@class, 'ia-IndeedApplyButton')]/@href",
    )
    for xpath in xpaths:
        for node in tree.xpath(xpath):
            href = node if isinstance(node, str) else (node.get("href") or node.get("data-indeed-apply-link"))
            resolved = _resolve_listing_apply_href(href, listing_host="indeed.com")
            if resolved:
                return resolved
    return None


def glassdoor_apply_href_from_html(html: str) -> str | None:
    """Extract external apply href from a Glassdoor job detail page."""
    tree = lhtml.fromstring(html)
    xpaths = (
        "//a[contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply on company')]",
        "//a[contains(@data-test, 'apply') and @href]",
        "//a[contains(@class, 'JobDetails_applyButton')]",
    )
    for xpath in xpaths:
        for anchor in tree.xpath(xpath):
            href = anchor.get("href")
            resolved = _resolve_listing_apply_href(href, listing_host="glassdoor.com")
            if resolved:
                return resolved
    return None


def page_apply_href_from_html(html: str, *, page_url: str) -> str | None:
    """Prefer an explicit apply anchor on a career/job HTML page."""
    tree = lhtml.fromstring(html)
    listing_host = urlparse(page_url).netloc.lower()
    xpaths = (
        "//a[contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply now')]",
        "//a[contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply for')]",
        "//a[contains(translate(@aria-label, 'APPLY', 'apply'), 'apply')]",
        "//a[contains(@href, '/apply')]",
        "//a[contains(@class, 'apply') and @href]",
    )
    for xpath in xpaths:
        for anchor in tree.xpath(xpath):
            href = anchor.get("href")
            if not href:
                continue
            absolute = urljoin(page_url, href)
            resolved = _resolve_listing_apply_href(absolute, listing_host=listing_host)
            if resolved:
                return resolved
    return None


def apply_url_from_indeed_detail_html(html: str) -> str | None:
    href = indeed_apply_href_from_html(html)
    return canonicalize_url(href) if href else None


def apply_url_from_glassdoor_detail_html(html: str) -> str | None:
    href = glassdoor_apply_href_from_html(html)
    return canonicalize_url(href) if href else None


def apply_url_from_page_html(html: str, *, page_url: str) -> str | None:
    href = page_apply_href_from_html(html, page_url=page_url)
    return canonicalize_url(href) if href else None


def enrich_job_apply_url(job: Job, apply_url: str) -> Job:
    """Attach ``apply_url`` + platform sniff without changing listing identity."""
    canonical_apply = canonicalize_url(apply_url)
    if canonical_apply == canonicalize_url(job.url):
        return job
    platform = sniff_platform(canonical_apply)
    logger.info(
        "Enriched apply URL for %s → %s (%s)", job.url, canonical_apply, platform
    )
    return job.model_copy(
        update={
            "apply_url": canonical_apply,
            "raw": {
                **job.raw,
                "listing_url": job.url,
                "platform": platform,
            },
        }
    )


def enrich_job_from_detail_html(
    job: Job,
    html: str,
    *,
    source: Literal["indeed", "glassdoor", "page", "linkedin"],
    page_url: str | None = None,
) -> Job:
    """Resolve apply URL from already-fetched detail HTML."""
    parsers = {
        "indeed": apply_url_from_indeed_detail_html,
        "glassdoor": apply_url_from_glassdoor_detail_html,
        "linkedin": apply_url_from_linkedin_detail_html,
        "page": lambda h: apply_url_from_page_html(h, page_url=page_url or job.url),
    }
    apply_url = parsers[source](html)
    if apply_url:
        return enrich_job_apply_url(job, apply_url)
    if source == "page" and page_url:
        jsonld_url = job.url
        if canonicalize_url(jsonld_url) != canonicalize_url(page_url):
            return enrich_job_apply_url(job, jsonld_url)
    return job


def _resolve_listing_apply_href(
    href: str | None,
    *,
    listing_host: str,
) -> str | None:
    resolved = resolve_apply_href(href)
    if not resolved:
        return None
    host = urlparse(resolved).netloc.lower()
    if listing_host in host:
        return None
    if any(marker in host for marker in ("indeed.com", "glassdoor.com", "linkedin.com")):
        return None
    return resolved


def sniff_platform(url: str) -> str:
    """Classify an apply URL by ATS / platform family."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "/talentcommunity/apply/" in path:
        return "icims"
    if "/phenompeople.net/" in path or "ph-at-" in path:
        return "phenom"
    for platform, markers in PLATFORM_HOST_MARKERS.items():
        if any(marker in host for marker in markers):
            return platform
    return "generic"


def is_big_four_platform(platform: str) -> bool:
    return platform in BIG_FOUR_PLATFORMS