"""Unit tests for KeywordExtractor."""

from __future__ import annotations

from magicapply.domain.keywords.extractor import KeywordExtractor
from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.providers.mock import MockLLMClient


def _job(title: str = "Senior Backend Engineer") -> Job:
    return Job.new(
        source_name="s",
        url="https://example.com/j/1",
        title=title,
        company="Acme",
        description="Python, distributed systems, on-call rotations.",
    )


class TestKeywordExtractor:
    def test_extract_from_canned_response(self) -> None:
        llm = MockLLMClient(['["python", "kubernetes", "postgres"]'])
        ex = KeywordExtractor(llm, extraction_prompt="extract keywords")
        assert ex.extract(_job()) == ["python", "kubernetes", "postgres"]

    def test_empty_prompt_returns_empty_and_skips_llm(self) -> None:
        llm = MockLLMClient(["should-not-be-called"])
        ex = KeywordExtractor(llm, extraction_prompt="")
        assert ex.extract(_job()) == []
        assert llm.calls == []

    def test_strips_markdown_fences(self) -> None:
        llm = MockLLMClient(['```json\n["python", "rust"]\n```'])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        assert ex.extract(_job()) == ["python", "rust"]

    def test_stray_whitespace_tolerated(self) -> None:
        llm = MockLLMClient(['   \n  ["go"]  \n'])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        assert ex.extract(_job()) == ["go"]

    def test_malformed_json_returns_empty(self) -> None:
        llm = MockLLMClient(["not json at all"])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        assert ex.extract(_job()) == []

    def test_non_list_json_returns_empty(self) -> None:
        llm = MockLLMClient(['{"keywords": ["python"]}'])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        assert ex.extract(_job()) == []

    def test_blank_terms_filtered(self) -> None:
        llm = MockLLMClient(['["python", " ", "", "rust"]'])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        assert ex.extract(_job()) == ["python", "rust"]

    def test_non_string_terms_coerced(self) -> None:
        llm = MockLLMClient(['["python", 42, null]'])
        ex = KeywordExtractor(llm, extraction_prompt="p")
        # 42 coerces to "42"; null -> "None" -> stripped, but "None" is
        # kept because str(None) == "None" which is non-blank.
        result = ex.extract(_job())
        assert "python" in result
        assert "42" in result
