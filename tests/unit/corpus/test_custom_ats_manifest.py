"""Tests for custom ATS corpus manifest builder (PR5)."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.corpus.custom_ats import (
    manifest_entries_from_jobs,
    merge_manifest,
)


def test_manifest_skips_big_four_and_listing_only_jobs() -> None:
    jobs = [
        Job.new(
            source_name="linkedin-search",
            url="https://www.linkedin.com/jobs/view/1",
            apply_url="https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/1",
            title="DS",
            company="HD",
            raw={"platform": "workday"},
        ),
        Job.new(
            source_name="indeed-search",
            url="https://www.indeed.com/viewjob?jk=abc",
            title="DS",
            company="Acme",
        ),
        Job.new(
            source_name="linkedin-search",
            url="https://www.linkedin.com/jobs/view/2",
            apply_url="https://symetra.eightfold.ai/careers/job/446718943971",
            title="Lead DS",
            company="Symetra",
            raw={"platform": "eightfold"},
        ),
    ]
    entries = manifest_entries_from_jobs(jobs)
    assert len(entries) == 1
    assert entries[0]["platform"] == "eightfold"
    assert entries[0]["apply_url"].endswith("446718943971")


def test_merge_preserves_existing_status_and_capture_dir() -> None:
    existing = [
        {
            "id": "abc123",
            "status": "pass",
            "capture_dir": "tests/fixtures/captured/custom-foo",
            "apply_url": "https://symetra.eightfold.ai/careers/job/1",
        }
    ]
    discovered = [
        {
            "id": "abc123",
            "status": "pending",
            "apply_url": "https://symetra.eightfold.ai/careers/job/1",
            "platform": "eightfold",
        }
    ]
    merged = merge_manifest(existing, discovered)
    assert merged[0]["status"] == "pass"
    assert merged[0]["capture_dir"] == "tests/fixtures/captured/custom-foo"
    assert merged[0]["platform"] == "eightfold"