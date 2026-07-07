#!/usr/bin/env python3
"""Seed ``tests/corpus/custom_ats_manifest.yaml`` from SQLite jobs after discover."""

from __future__ import annotations

import argparse
from pathlib import Path

from magicapply.infrastructure.corpus.custom_ats import (
    load_manifest,
    manifest_entries_from_jobs,
    merge_manifest,
    write_manifest,
)
from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url, migrate_schema
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/magicapply.db"),
        help="SQLite database path (default: data/magicapply.db)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/corpus/custom_ats_manifest.yaml"),
        help="Output manifest path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print entry count without writing the manifest",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    engine = create_engine_from_url(f"sqlite:///{db_path}")
    migrate_schema(engine)
    create_db(engine)
    jobs = SqlJobsRepository(engine).list_all()
    discovered = manifest_entries_from_jobs(jobs)
    merged = merge_manifest(load_manifest(args.manifest), discovered)

    print(f"jobs in db: {len(jobs)}")
    print(f"custom apply_url candidates: {len(discovered)}")
    print(f"manifest entries after merge: {len(merged)}")

    if args.dry_run:
        return

    write_manifest(args.manifest, merged)
    print(f"wrote {args.manifest}")


if __name__ == "__main__":
    main()