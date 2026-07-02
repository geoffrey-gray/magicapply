"""Schema.org JSON-LD JobPosting extraction.

Most modern careers pages embed one or more `application/ld+json` script tags
with `JobPosting` objects (schema.org spec). Prefer JSON-LD to per-host HTML
parsers — it's more stable and self-describing.

Not a full HTML parser: we regex-scan for `<script type="application/ld+json">`
blocks and JSON-parse each. Enough for MVP. Upgrade to selectolax if we start
hitting pages that serve JSON-LD inside broken HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any

from magicapply.domain.models.job import Job

_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_jobposting_dicts(html: str) -> list[dict[str, Any]]:
    """Return every JobPosting-typed JSON-LD object found in the page."""
    out: list[dict[str, Any]] = []
    for match in _SCRIPT_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out.extend(_walk_for_jobpostings(data))
    return out


def jsonld_to_job(posting: dict[str, Any], *, source_name: str, fallback_url: str) -> Job:
    """Adapt a schema.org JobPosting dict into our domain Job."""
    title = posting.get("title") or "(untitled)"
    url = posting.get("url") or posting.get("@id") or fallback_url
    org = posting.get("hiringOrganization") or {}
    company = (org.get("name") if isinstance(org, dict) else str(org)) or "(unknown company)"
    description = posting.get("description") or ""
    location = _extract_location(posting.get("jobLocation"))
    return Job.new(
        source_name=source_name,
        url=str(url),
        title=str(title),
        company=str(company),
        description=str(description),
        location=location,
        raw=posting,
    )


def _walk_for_jobpostings(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        node_type = node.get("@type")
        # JSON-LD @type can be a string or a list of strings.
        if node_type == "JobPosting" or (isinstance(node_type, list) and "JobPosting" in node_type):
            return [node]
        # Also walk into common nesting containers like `@graph`.
        found: list[dict[str, Any]] = []
        for v in node.values():
            found.extend(_walk_for_jobpostings(v))
        return found
    if isinstance(node, list):
        found = []
        for item in node:
            found.extend(_walk_for_jobpostings(item))
        return found
    return []


def _extract_location(node: Any) -> str | None:
    if not node:
        return None
    if isinstance(node, list):
        # Multiple locations — take first.
        return _extract_location(node[0])
    if isinstance(node, dict):
        address = node.get("address") or {}
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            joined = ", ".join(str(p) for p in parts if p)
            if joined:
                return joined
        # Fall back to node's name field.
        name = node.get("name")
        return str(name) if name else None
    return str(node)
