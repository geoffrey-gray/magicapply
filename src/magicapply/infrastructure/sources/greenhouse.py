"""Greenhouse boards-api adapter.

Fetches open postings from ``boards-api.greenhouse.io`` per configured board
slug (e.g. ``reddit``, ``anthropic``). Title filtering is client-side against
``GreenhouseSource.title_keywords``; when the list is empty every posting on
the board is yielded.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from magicapply.config.models import GreenhouseSource
from magicapply.infrastructure.sources.rate_limit import RateLimiter

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job

logger = logging.getLogger(__name__)

_API_URL_TEMPLATE = (
    "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
)
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) MagicApply/0.1 (+https://github.com/geoffreygray/magicapply)"
)
_TAG_RE = re.compile(r"<[^>]+>")


class GreenhouseAdapter:
    """Fetch jobs from the public Greenhouse boards API."""

    def __init__(
        self,
        *,
        name: str,
        boards: list[str],
        title_keywords: list[str],
        rate_limit_per_minute: int,
        http: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.name = name
        self._boards = list(boards)
        self._title_keywords = list(title_keywords)
        self._rate = rate_limit_per_minute
        self._http = http or httpx.Client(
            headers={"User-Agent": _DEFAULT_UA},
            follow_redirects=True,
            timeout=30.0,
        )
        self._rate_limiter = rate_limiter or RateLimiter(rate_limit_per_minute)

    @classmethod
    def from_config(
        cls,
        config: GreenhouseSource,
        *,
        http: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> GreenhouseAdapter:
        return cls(
            name=config.name,
            boards=list(config.boards),
            title_keywords=list(config.title_keywords),
            rate_limit_per_minute=config.rate_limit_per_minute,
            http=http,
            rate_limiter=rate_limiter,
        )

    def discover(self) -> Iterator[Job]:
        if not self._boards:
            return

        for board in self._boards:
            self._rate_limiter.wait()
            url = _API_URL_TEMPLATE.format(slug=board)
            try:
                response = self._http.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Greenhouse fetch failed for board %r: %s", board, exc)
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                logger.warning("Greenhouse invalid JSON for board %r: %s", board, exc)
                continue

            for posting in payload.get("jobs") or []:
                if not isinstance(posting, dict):
                    continue
                title = str(posting.get("title") or "").strip()
                if not title or not _title_matches(title, self._title_keywords):
                    continue
                try:
                    yield _posting_to_job(posting, source_name=self.name, board=board)
                except (KeyError, TypeError, ValueError) as exc:
                    job_url = posting.get("absolute_url") or url
                    logger.warning(
                        "Greenhouse skip malformed posting from %s: %s", job_url, exc
                    )


def _title_matches(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = title.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _posting_to_job(
    posting: dict[str, Any], *, source_name: str, board: str
) -> Job:
    from magicapply.domain.models.job import Job

    title = str(posting["title"]).strip()
    job_url = str(posting["absolute_url"]).strip()
    location_node = posting.get("location") or {}
    location = (
        str(location_node.get("name")).strip()
        if isinstance(location_node, dict) and location_node.get("name")
        else None
    )
    content = posting.get("content") or ""
    description = _html_to_text(str(content))
    posted_at = _parse_updated_at(posting.get("updated_at"))
    company = board.replace("-", " ").replace("_", " ").title()

    return Job.new(
        source_name=source_name,
        url=job_url,
        title=title,
        company=company,
        description=description,
        location=location,
        posted_at=posted_at,
        raw=posting,
    )


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()


def _parse_updated_at(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None