"""Tests for in-run dedup."""

from __future__ import annotations

from magicapply.domain.jobs.dedup import dedupe_by_key
from magicapply.domain.models.job import Job


def _j(url: str, title: str, company: str) -> Job:
    return Job.new(source_name="s", url=url, title=title, company=company)


class TestDedupeByKey:
    def test_removes_second_occurrence(self) -> None:
        a = _j("https://a.com/1", "Senior SWE", "Acme")
        b = _j("https://b.com/xyz", "senior swe", "acme")  # same dedup_key
        result = list(dedupe_by_key([a, b]))
        assert len(result) == 1
        assert result[0].url == "https://a.com/1"  # first wins

    def test_preserves_order(self) -> None:
        jobs = [
            _j("https://a.com/1", "A", "X"),
            _j("https://a.com/2", "B", "Y"),
            _j("https://a.com/3", "C", "Z"),
        ]
        result = list(dedupe_by_key(jobs))
        assert [j.title for j in result] == ["A", "B", "C"]

    def test_empty(self) -> None:
        assert list(dedupe_by_key([])) == []
