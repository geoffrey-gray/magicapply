"""Unit tests for match_bank."""

from __future__ import annotations

from magicapply.config.models import KeywordBank, KeywordEntry
from magicapply.domain.keywords.matcher import match_bank


def _bank(*entries: tuple[str, list[str], str]) -> KeywordBank:
    return KeywordBank(
        keywords=[
            KeywordEntry(term=term, synonyms=synonyms, evidence=evidence)
            for term, synonyms, evidence in entries
        ]
    )


class TestMatchBank:
    def test_empty_extracted_returns_empty(self) -> None:
        bank = _bank(("python", [], "5y"))
        assert match_bank([], bank) == []

    def test_empty_bank_returns_empty(self) -> None:
        assert match_bank(["python"], KeywordBank()) == []

    def test_term_match_case_insensitive(self) -> None:
        bank = _bank(("Python", [], "5y"))
        hits = match_bank(["python"], bank)
        assert [h.term for h in hits] == ["Python"]

    def test_synonym_match(self) -> None:
        bank = _bank(("distributed systems", ["microservices", "SOA"], "Acme migration"))
        hits = match_bank(["Microservices"], bank)
        assert [h.term for h in hits] == ["distributed systems"]

    def test_substring_match_both_directions(self) -> None:
        # "python 3" (JD) contains "python" (bank).
        bank = _bank(("python", [], "5y"))
        assert [h.term for h in match_bank(["python 3.11"], bank)] == ["python"]
        # "distributed systems" (bank) contains "distributed" (JD).
        bank2 = _bank(("distributed systems", [], "Acme"))
        assert [h.term for h in match_bank(["distributed"], bank2)] == [
            "distributed systems"
        ]

    def test_no_match_returns_empty(self) -> None:
        bank = _bank(("rust", [], "hobby"))
        assert match_bank(["cobol"], bank) == []

    def test_result_order_follows_bank(self) -> None:
        # Even when JD terms come in a different order, matches echo bank order.
        bank = _bank(
            ("python", [], "5y"),
            ("distributed systems", [], "Acme"),
            ("go", [], "2y"),
        )
        hits = match_bank(["go", "python", "distributed"], bank)
        assert [h.term for h in hits] == ["python", "distributed systems", "go"]

    def test_no_duplicate_hits(self) -> None:
        bank = _bank(("python", ["py"], "5y"))
        # Both "python" and "py" match; entry included once.
        hits = match_bank(["python", "py"], bank)
        assert len(hits) == 1
