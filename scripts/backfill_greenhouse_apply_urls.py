#!/usr/bin/env python3
"""One-shot: set apply_url on Greenhouse jobs that still point at careers pages.

Safe / idempotent. Does not change Application states. After backfill, FAILED
Stripe rows can be re-tried with ``magicapply apply <job-id> --retry --no-submit``.

Usage:
  uv run python scripts/backfill_greenhouse_apply_urls.py
  uv run python scripts/backfill_greenhouse_apply_urls.py --root configs --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

from magicapply.config import load_config
from magicapply.cli.composition import build_repos
from magicapply.infrastructure.sources.apply_url import (
    is_greenhouse_apply_url,
    resolve_greenhouse_apply_url,
    resolve_job_apply_destination,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="configs", help="Config root")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing",
    )
    args = parser.parse_args()

    loaded = load_config(Path(args.root).resolve())
    jobs_repo, _apps = build_repos(loaded.data_dir())

    updated = 0
    scanned = 0
    for job in jobs_repo.list_all():
        scanned += 1
        if job.apply_url and is_greenhouse_apply_url(job.apply_url):
            continue
        dest = resolve_job_apply_destination(job)
        if not is_greenhouse_apply_url(dest):
            # Try board from company slug for greenhouse-boards without raw board.
            if job.source_name and "greenhouse" in job.source_name:
                board = (job.raw or {}).get("greenhouse_board")
                if not board and job.company:
                    board = job.company.lower().replace(" ", "")
                dest2 = resolve_greenhouse_apply_url(
                    board_slug=str(board) if board else None,
                    posting_id=(job.raw or {}).get("id"),
                    absolute_url=(job.raw or {}).get("absolute_url") or job.url,
                )
                if dest2:
                    dest = dest2
        if not is_greenhouse_apply_url(dest):
            continue
        if job.apply_url == dest:
            continue
        print(f"{job.id} {job.company!r}: apply_url {job.apply_url!r} → {dest!r}")
        if not args.dry_run:
            new_job = job.model_copy(
                update={
                    "apply_url": dest,
                    "raw": {
                        **(job.raw or {}),
                        "apply_resolve": "backfill_greenhouse",
                        "platform": "greenhouse",
                    },
                }
            )
            jobs_repo.upsert(new_job)
        updated += 1

    print(f"scanned={scanned} updated={updated} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
