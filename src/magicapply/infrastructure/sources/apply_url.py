"""Resolve external apply URLs from job listing pages (custom ATS / PR2).

Pure parsing helpers live here; Playwright fetches stay in per-source adapters.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from lxml import html as lhtml

from magicapply.domain.models.job import canonicalize_url

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


def sniff_platform(url: str) -> str:
    """Classify an apply URL by ATS / platform family."""
    host = urlparse(url).netloc.lower()
    for platform, markers in PLATFORM_HOST_MARKERS.items():
        if any(marker in host for marker in markers):
            return platform
    return "generic"


def is_big_four_platform(platform: str) -> bool:
    return platform in BIG_FOUR_PLATFORMS