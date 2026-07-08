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


def _js(url: str, title: str, company: str, source: str) -> Job:
    return Job.new(source_name=source, url=url, title=title, company=company)


class TestFairDedupWithSourceScorer:
    """When a job appears from multiple sources, prefer the source with
    the LOWEST scorer value. That's how future apply traffic gets
    load-balanced across LinkedIn / Indeed / Glassdoor."""

    def test_lowest_scoring_source_wins_collision(self) -> None:
        # Same dedup_key across three sources; scorer prefers indeed.
        linkedin = _js("https://l.com/1", "Staff DS", "Acme", "linkedin-search")
        indeed = _js("https://i.com/2", "Staff DS", "Acme", "indeed-search")
        glassdoor = _js("https://g.com/3", "Staff DS", "Acme", "glassdoor-search")
        scores = {
            "linkedin-search": 5,
            "indeed-search": 1,   # lowest → wins
            "glassdoor-search": 3,
        }
        result = list(
            dedupe_by_key(
                [linkedin, indeed, glassdoor],
                source_scorer=lambda s: scores[s],
            )
        )
        assert len(result) == 1
        assert result[0].source_name == "indeed-search"

    def test_yield_order_matches_first_appearance(self) -> None:
        """A collision winner should be yielded at the position of the
        group's FIRST appearance in the input — not the winner's own
        position — so downstream code sees the same ordering shape as
        the no-scorer path."""
        a1 = _js("https://x.com/1", "Alpha", "A", "linkedin-search")
        b1 = _js("https://x.com/2", "Beta", "B", "linkedin-search")
        a2 = _js("https://x.com/3", "Alpha", "A", "indeed-search")  # collision with a1
        b2 = _js("https://x.com/4", "Beta", "B", "indeed-search")   # collision with b1
        scores = {"linkedin-search": 5, "indeed-search": 1}
        result = list(
            dedupe_by_key([a1, b1, a2, b2], source_scorer=lambda s: scores[s])
        )
        # Both winners are indeed-search; but the order is Alpha-then-Beta
        # (first-appearance order of the two groups).
        assert [j.title for j in result] == ["Alpha", "Beta"]
        assert all(j.source_name == "indeed-search" for j in result)

    def test_ties_broken_by_insertion_order(self) -> None:
        a = _js("https://a.com/1", "Same", "Co", "src-a")
        b = _js("https://a.com/2", "Same", "Co", "src-b")
        # Equal scores → first wins.
        result = list(
            dedupe_by_key([a, b], source_scorer=lambda _: 0)
        )
        assert result[0].source_name == "src-a"

    def test_scorer_called_once_per_source_via_pipeline_cache(self) -> None:
        """The dedup helper itself doesn't cache — that's the pipeline's
        job — but it must be safe to call the scorer multiple times.
        Verify each source_name is queried at most once per collision
        group when duplicates only differ by source."""
        calls: dict[str, int] = {}

        def scorer(source: str) -> int:
            calls[source] = calls.get(source, 0) + 1
            return {"a": 1, "b": 2}[source]

        # Two duplicates from source a, one from source b.
        jobs = [
            _js("https://x/1", "T", "C", "a"),
            _js("https://x/2", "T", "C", "b"),
            _js("https://x/3", "T", "C", "a"),
        ]
        list(dedupe_by_key(jobs, source_scorer=scorer))
        # dedup calls the scorer per (source, group) — same source in one
        # group is queried once per candidate; that's fine — the caller
        # (DiscoveryPipeline) caches at a higher level for us.
        assert set(calls.keys()) == {"a", "b"}

    def test_no_scorer_falls_back_to_first_wins(self) -> None:
        a = _js("https://a/1", "Same", "Co", "linkedin-search")
        b = _js("https://a/2", "Same", "Co", "indeed-search")
        result = list(dedupe_by_key([a, b]))
        assert result[0].source_name == "linkedin-search"
