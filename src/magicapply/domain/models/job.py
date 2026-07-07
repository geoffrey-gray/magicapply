"""The Job model — the single most-passed-around type in the system.

Includes URL canonicalization and a dedup-key helper. Job.id is a deterministic
hash of the canonical URL, so re-scraping the same posting produces the same
row. Cross-URL dedup (same job listed on multiple sources) uses `dedup_key`,
which normalizes company + title.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gh_src",
        "ref",
        "referrer",
        "src",
        "trk",
    }
)

_SLUG_STRIP = re.compile(r"[^\w\s]")
_SLUG_WS = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    """Normalize a URL for identity comparisons.

    - schemeless input (e.g. `acme.com/j`) is treated as https
    - lowercase scheme + netloc
    - drop tracking query params (utm_*, ref, etc.)
    - sort remaining query params
    - strip trailing slash from path (except root)
    - drop fragment
    """
    stripped = url.strip()
    if "://" not in stripped:
        stripped = "https://" + stripped
    parts = urlsplit(stripped)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, netloc, path, query, ""))


def hash_url(canonical: str) -> str:
    """Deterministic 16-char job ID derived from a canonical URL."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _slug(text: str) -> str:
    stripped = _SLUG_STRIP.sub("", text.lower())
    return _SLUG_WS.sub(" ", stripped).strip()


class Job(BaseModel):
    """A single discovered job posting."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    source_name: str
    url: str  # canonical listing / discovery URL (identity + dedup)
    apply_url: str | None = None  # canonical external apply destination when known
    title: str
    company: str
    location: str | None = None
    description: str = ""
    posted_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def effective_apply_url(self) -> str:
        """URL passed to ATS handlers — external apply link when enriched."""
        return self.apply_url or self.url

    @classmethod
    def new(
        cls,
        *,
        source_name: str,
        url: str,
        title: str,
        company: str,
        apply_url: str | None = None,
        description: str = "",
        location: str | None = None,
        posted_at: datetime | None = None,
        raw: dict[str, Any] | None = None,
    ) -> Job:
        """Build a Job from raw discovery inputs, canonicalizing url and deriving id."""
        canonical = canonicalize_url(url)
        canonical_apply = canonicalize_url(apply_url) if apply_url else None
        return cls(
            id=hash_url(canonical),
            source_name=source_name,
            url=canonical,
            apply_url=canonical_apply,
            title=title,
            company=company,
            description=description,
            location=location,
            posted_at=posted_at,
            raw=raw or {},
        )

    @property
    def dedup_key(self) -> str:
        """Key used to detect the same job showing up from multiple sources."""
        return f"{_slug(self.company)}::{_slug(self.title)}"
