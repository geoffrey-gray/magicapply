"""Custom ATS corpus manifest helpers (PR5 / PR7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from magicapply.domain.models.job import Job
from magicapply.infrastructure.sources.apply_url import is_big_four_platform, sniff_platform


def manifest_entries_from_jobs(jobs: list[Job]) -> list[dict[str, Any]]:
    """Build manifest rows for non–big-4 jobs that have an enriched ``apply_url``."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in sorted(jobs, key=lambda j: j.discovered_at, reverse=True):
        if not job.apply_url:
            continue
        target = job.effective_apply_url
        platform = str(job.raw.get("platform") or sniff_platform(target))
        if is_big_four_platform(platform):
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        entries.append(
            {
                "id": job.id,
                "source": job.source_name,
                "listing_url": job.url,
                "apply_url": job.apply_url,
                "platform": platform,
                "title": job.title,
                "company": job.company,
                "status": "pending",
                "live_gate": True,
            }
        )
    return entries


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(payload, list):
        raise ValueError(f"manifest must be a YAML list: {path}")
    return payload


def merge_manifest(
    existing: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve operator ``status`` / ``capture_dir`` for known ids; append new rows."""
    by_id = {str(row["id"]): dict(row) for row in existing if row.get("id")}
    for row in discovered:
        job_id = str(row["id"])
        if job_id in by_id:
            merged = {**row, **{k: v for k, v in by_id[job_id].items() if v is not None}}
            by_id[job_id] = merged
        else:
            by_id[job_id] = row
    return sorted(by_id.values(), key=lambda r: str(r.get("id", "")))


def write_manifest(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Custom ATS corpus manifest — generated {datetime.now(UTC).isoformat()}\n"
        "# status: pending | pass | fail | skipped\n"
    )
    path.write_text(
        header + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )