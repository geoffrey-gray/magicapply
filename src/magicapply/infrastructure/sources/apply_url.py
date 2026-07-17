"""Resolve external apply URLs from job listing pages (custom ATS / PR2+PR5).

Pure parsing helpers live here; Playwright fetches stay in per-source adapters.
"""

from __future__ import annotations

import logging
import re
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
    "workable": ("workable.com", "apply.workable.com", "jobs.workable.com"),
    "jazzhr": ("applytojob.com",),
}

BIG_FOUR_PLATFORMS = frozenset({"greenhouse", "workday", "lever", "ashby"})

# Hosts that count as "still on the job board" (not an external ATS apply URL).
BOARD_HOSTS_LINKEDIN = ("linkedin.com",)
BOARD_HOSTS_INDEED = ("indeed.com",)
BOARD_HOSTS_GLASSDOOR = ("glassdoor.com", "glassdoor.co.uk")
BOARD_HOSTS_ALL = BOARD_HOSTS_LINKEDIN + BOARD_HOSTS_INDEED + BOARD_HOSTS_GLASSDOOR

# Greenhouse application hosts (not company careers pages that embed gh_jid).
GREENHOUSE_APPLY_HOST_MARKERS = (
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
)


def is_external_apply_url(
    url: str | None,
    *,
    board_hosts: tuple[str, ...] = (),
) -> bool:
    """True when ``url`` is an HTTP(S) apply target off the given job board(s)."""
    if not url or not str(url).strip():
        return False
    raw = str(url).strip()
    if not raw.startswith("http"):
        return False
    host = urlparse(raw).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for board in board_hosts:
        b = board.lower().lstrip(".")
        if host == b or host.endswith("." + b):
            return False
    return True


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


def description_from_destination_html(html: str) -> str:
    """Extract plain-text job description from an employer/ATS posting page.

    Prefer schema.org JobPosting JSON-LD (Greenhouse, many career sites), then
    common content containers (Ashby, Lever, generic). Used so LinkedIn
    discovery can leave description fetch off linkedin.com.
    """
    from magicapply.infrastructure.sources.jsonld import extract_jobposting_dicts

    for posting in extract_jobposting_dicts(html):
        raw = posting.get("description")
        if not raw:
            continue
        text = _strip_html_to_text(str(raw))
        if len(text) >= 80:
            return text

    try:
        tree = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001
        return ""

    xpaths = (
        # Ashby job description (posting, not form)
        "//*[contains(@class,'ashby-job-posting-brief')]",
        "//*[contains(@class,'ashby-job-posting-description')]",
        "//*[@data-testid='job-description']",
        # Greenhouse
        "//div[@id='content']",
        "//div[contains(@class,'job__description')]",
        "//div[contains(@class,'content-wrapper')]",
        # Lever
        "//div[contains(@class,'posting-page')]",
        "//div[contains(@class,'section-wrapper')]",
        # Workday-ish / generic
        "//*[contains(@class,'job-description')]",
        "//*[contains(@class,'jobDescription')]",
        "//article",
        "//main",
    )
    for xp in xpaths:
        for el in tree.xpath(xp):
            text = " ".join((el.text_content() or "").split())
            if len(text) >= 120:
                return text
    return ""


def _strip_html_to_text(raw: str) -> str:
    import html as html_lib
    import re

    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())


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


def description_from_indeed_detail_html(html: str) -> str:
    """Extract job description text from an Indeed ``/viewjob`` page."""
    try:
        tree = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001
        return ""
    xpaths = (
        "//div[@id='jobDescriptionText']",
        "//div[contains(@class,'jobsearch-jobDescriptionText')]",
        "//div[@data-testid='jobsearch-JobComponent-description']",
        "//div[contains(@class,'job-description')]",
    )
    for xp in xpaths:
        for el in tree.xpath(xp):
            text = " ".join((el.text_content() or "").split())
            if len(text) >= 80:
                return text
    # Fall back to generic destination extractors (JSON-LD, main, …).
    return description_from_destination_html(html)


def description_from_glassdoor_detail_html(html: str) -> str:
    """Extract job description text from a Glassdoor listing page."""
    try:
        tree = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001
        return ""
    xpaths = (
        "//div[@data-test='description']",
        "//div[contains(@class,'JobDetails_jobDescription')]",
        "//div[contains(@class,'jobDescriptionContent')]",
        "//div[@id='JobDescriptionContainer']",
        "//div[contains(@class,'desc')]",
    )
    for xp in xpaths:
        for el in tree.xpath(xp):
            text = " ".join((el.text_content() or "").split())
            if len(text) >= 80:
                return text
    return description_from_destination_html(html)


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


def _netloc(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        return host[4:]
    return host


def is_greenhouse_apply_url(url: str | None) -> bool:
    """True when URL is a Greenhouse job-application host (not a careers search page)."""
    if not url or not str(url).strip():
        return False
    host = _netloc(str(url).strip())
    return any(host == m or host.endswith("." + m) for m in GREENHOUSE_APPLY_HOST_MARKERS)


def is_job_board_listing_url(url: str | None) -> bool:
    """True when URL is still on LinkedIn / Indeed / Glassdoor (no employer ATS)."""
    if not url or not str(url).strip():
        return False
    return not is_external_apply_url(url, board_hosts=BOARD_HOSTS_ALL)


_GH_BOARD_JOB_PATH = re.compile(
    r"(?:job-boards\.)?greenhouse\.io/([^/?#]+)/jobs/(\d+)",
    re.IGNORECASE,
)


def greenhouse_embed_apply_url(*, board_slug: str, posting_id: str | int) -> str:
    """Direct Greenhouse application form (survives Stripe-style careers redirects).

    Board job URLs like ``job-boards.greenhouse.io/{slug}/jobs/{id}`` often
    302 to the employer's marketing site (Stripe). The embed endpoint keeps
    the real ``#application-form`` with ``#first_name`` etc.
    """
    slug = str(board_slug).strip().strip("/")
    pid = str(posting_id).strip()
    return canonicalize_url(
        f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={pid}"
    )


def resolve_greenhouse_apply_url(
    *,
    board_slug: str | None = None,
    posting_id: str | int | None = None,
    absolute_url: str | None = None,
) -> str | None:
    """Canonical Greenhouse **application form** URL for boards-api postings.

    Always prefers the Greenhouse embed application endpoint when board slug
    + job id are known. Company career pages (``stripe.com/jobs/…?gh_jid=``)
    and board listing URLs that redirect off Greenhouse are rewritten to
    embed so ``GreenhouseHandler`` sees a real form.
    """
    slug = (str(board_slug).strip().strip("/") if board_slug else "") or None
    pid: str | None = str(posting_id).strip() if posting_id is not None else None

    if absolute_url and str(absolute_url).strip():
        can = canonicalize_url(str(absolute_url).strip())
        # Already on the embed form — keep it.
        if "/embed/job_app" in can and is_greenhouse_apply_url(can):
            return can
        # Parse board job path: greenhouse.io/{slug}/jobs/{id}
        path_match = _GH_BOARD_JOB_PATH.search(can)
        if path_match:
            slug = slug or path_match.group(1)
            pid = pid or path_match.group(2)
        # Careers embed: ?gh_jid=
        jid = parse_qs(urlparse(can).query).get("gh_jid", [None])[0]
        if jid:
            pid = pid or str(jid).strip()
        # embed already has for= & token=
        qs = parse_qs(urlparse(can).query)
        if qs.get("for") and qs.get("token"):
            slug = slug or qs["for"][0]
            pid = pid or qs["token"][0]

    if slug and pid:
        return greenhouse_embed_apply_url(board_slug=slug, posting_id=pid)
    return None


def is_indeed_applystart_url(url: str | None) -> bool:
    """True when URL is Indeed's hosted applystart entry point."""
    if not url or not str(url).strip():
        return False
    parsed = urlparse(str(url).strip())
    host = parsed.netloc.lower()
    if "indeed.com" not in host:
        return False
    return "applystart" in parsed.path.lower()


def indeed_apply_entry_url(job: Job) -> str | None:
    """Indeed-hosted apply entry (applystart) from raw metadata or viewjob ``jk``."""
    raw = job.raw or {}
    for key in ("indeed_apply_url", "thirdPartyApplyUrl"):
        val = raw.get(key)
        if val and is_indeed_applystart_url(str(val)):
            return normalize_indeed_applystart_url(str(val))
    # Synthesize applystart from listing ``jk`` when metadata is missing —
    # viewjob+click often lands on offsite / expired CTAs without an IA form.
    for candidate in (job.apply_url, job.url):
        if not candidate or "indeed.com" not in str(candidate).lower():
            continue
        from urllib.parse import parse_qs, urlparse

        jk = parse_qs(urlparse(str(candidate)).query).get("jk", [None])[0]
        if jk:
            return normalize_indeed_applystart_url(
                f"https://www.indeed.com/applystart?jk={jk}"
            )
    return None


def normalize_indeed_applystart_url(url: str) -> str:
    """Strip tracking params; keep ``jk`` for a stable applystart entry."""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(str(url).strip())
    jk = parse_qs(parsed.query).get("jk", [None])[0]
    host = parsed.netloc.lower() or "www.indeed.com"
    if "indeed.com" not in host:
        host = "www.indeed.com"
    path = "/applystart"
    if jk:
        query = urlencode({"jk": jk})
        return urlunparse(("https", host, path, "", query, ""))
    return canonicalize_url(f"https://{host}{path}")


def description_looks_thin(description: str | None, *, min_chars: int = 200) -> bool:
    """True when JD text is missing or only a SERP-length snippet."""
    text = (description or "").strip()
    return len(text) < min_chars


def needs_board_destination_resolve(job: Job) -> bool:
    """True when apply should open the board listing to find an external URL.

    Skip when we already have a usable off-board ``apply_url`` and a
    non-thin description (or Easy Apply meta is already recorded).
    """
    if job.apply_url and not is_job_board_listing_url(job.apply_url):
        if not description_looks_thin(job.description):
            return False
        # External URL known but JD still thin — still worth offsite JD fetch
        # without re-opening the board if we can fetch the destination.
        return False
    raw = job.raw or {}
    if raw.get("board_resolve") == "done":
        # Already attempted resolve; do not re-hit the board.
        return False
    if is_job_board_listing_url(job.url):
        return True
    return False


def mark_board_resolve_done(job: Job, **extra: object) -> Job:
    """Stamp raw so we do not re-resolve the same listing."""
    raw = {**(job.raw or {}), "board_resolve": "done", **extra}
    return job.model_copy(update={"raw": raw})


def resolve_job_apply_destination(job: Job) -> str:
    """Best URL for ATS handler selection and navigation.

    Order: Greenhouse embed resolve (raw / existing apply_url) → existing
    external ``apply_url`` → listing ``url``.
    """
    raw = job.raw or {}
    board = raw.get("greenhouse_board")
    if board is None and isinstance(raw.get("board"), str):
        board = raw.get("board")
    posting_id = raw.get("id")
    # Prefer absolute_url / listing for slug+id extraction; also re-resolve
    # stale board job URLs (…/jobs/{id}) into embed form URLs.
    absolute = raw.get("absolute_url") or job.apply_url or job.url
    resolved = resolve_greenhouse_apply_url(
        board_slug=str(board) if board else None,
        posting_id=posting_id,
        absolute_url=str(absolute) if absolute else None,
    )
    if resolved:
        return resolved

    if job.apply_url and str(job.apply_url).strip():
        apply = str(job.apply_url).strip()
        if not is_job_board_listing_url(apply):
            # Last chance: rewrite GH /jobs/ paths even without raw board id.
            gh = resolve_greenhouse_apply_url(absolute_url=apply)
            if gh:
                return gh
            return apply

    return job.url


def job_with_resolved_apply_url(job: Job) -> Job:
    """Return a copy of ``job`` with ``apply_url`` set when resolution improves it."""
    destination = resolve_job_apply_destination(job)
    if not destination or destination == (job.apply_url or job.url):
        if job.apply_url:
            return job
        if destination != job.url:
            return job.model_copy(update={"apply_url": destination})
        return job
    if job.apply_url and canonicalize_url(job.apply_url) == canonicalize_url(destination):
        return job
    return job.model_copy(
        update={
            "apply_url": canonicalize_url(destination),
            "raw": {
                **(job.raw or {}),
                "apply_resolve": "destination_resolve",
                "platform": sniff_platform(destination),
            },
        }
    )


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