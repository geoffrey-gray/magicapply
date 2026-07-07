#!/usr/bin/env python3
"""Backfill ``apply_url`` on stale LinkedIn ``jobs`` rows.

LinkedIn Voyager search returns ~7 jobs per query and rotates over time, so
rows persisted before the enrichment wait was tuned (1500 ms → 5000 ms) may
never resurface through the discover loop to trigger the upsert-backfill.
This script walks the DB, opens Chromium once with the operator's ``li_at``
cookie, and hits each stale row's LinkedIn listing URL directly. Uses the
same ``apply_url_from_linkedin_detail_html`` parser as the adapter, so a
successful resolution means routing (``ATSHandlerFactory.for_url``) will
also see the correct target on the next apply.

Rows genuinely on LinkedIn Easy Apply (no external URL) stay empty — the
script prints them so the operator can mark them ``skipped`` in the corpus
manifest if they want to.

Idempotent: rows with ``apply_url`` already set are skipped; rows still
empty after enrichment stay empty (no destructive fallback).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.infrastructure.persistence.db import (
    create_db,
    create_engine_from_url,
    sqlite_url_for,
)
from magicapply.infrastructure.persistence.repositories.jobs import (
    SqlJobsRepository,
)
from magicapply.infrastructure.sources.apply_url import (
    apply_url_from_linkedin_detail_html,
    sniff_platform,
)

HYDRATION_MS = 5000
RATE_LIMIT_S = 6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/magicapply.sqlite3"),
        help="SQLite database path (default: data/magicapply.sqlite3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidate rows without opening Chromium.",
    )
    args = parser.parse_args()

    li_at = os.environ.get("LINKEDIN_LI_AT")
    if not li_at and not args.dry_run:
        print("LINKEDIN_LI_AT not set — export it or source .env first", file=sys.stderr)
        return 1

    engine = create_engine_from_url(sqlite_url_for(args.db.resolve()))
    create_db(engine)
    repo = SqlJobsRepository(engine)

    stale = [
        j
        for j in repo.list_all()
        if j.source_name == "linkedin-search" and not j.apply_url
    ]
    print(f"candidates: {len(stale)}")
    for job in stale:
        print(f"  - {job.company[:22]:22} | {job.title[:40]:40} | {job.url}")
    if args.dry_run or not stale:
        return 0

    enriched = 0
    easy_apply = 0
    errors: list[str] = []
    with PlaywrightSession(headless=True) as session:
        session.add_cookies(
            [{"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"}]
        )
        for i, job in enumerate(stale):
            if i > 0:
                time.sleep(RATE_LIMIT_S)
            page = session.new_page()
            try:
                try:
                    page.goto(job.url, wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(HYDRATION_MS)
                    html = page.content()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{job.company}: {exc}")
                    print(f"[{i+1}/{len(stale)}] {job.company[:22]:22} — nav error: {exc}")
                    continue
            finally:
                page.close()

            apply_url = apply_url_from_linkedin_detail_html(html)
            if not apply_url:
                if "Easy Apply to this job" in html:
                    easy_apply += 1
                    print(f"[{i+1}/{len(stale)}] {job.company[:22]:22} — Easy Apply (skip)")
                else:
                    print(f"[{i+1}/{len(stale)}] {job.company[:22]:22} — no apply anchor found")
                continue

            platform = sniff_platform(apply_url)
            enriched_job = job.model_copy(
                update={
                    "apply_url": apply_url,
                    "raw": {**job.raw, "listing_url": job.url, "platform": platform},
                }
            )
            repo.upsert(enriched_job)
            enriched += 1
            print(
                f"[{i+1}/{len(stale)}] {job.company[:22]:22} → {platform:12} | {apply_url}"
            )

    print(
        f"---\nenriched={enriched} easy_apply={easy_apply} errors={len(errors)} "
        f"stale_remaining={len(stale) - enriched - easy_apply - len(errors)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
