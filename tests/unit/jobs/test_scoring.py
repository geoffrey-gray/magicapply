"""Tests for prefilter, LLM scorer, keyword scorer, and composed JobScorer."""

from __future__ import annotations

from magicapply.config.models import (
    KeywordBank,
    KeywordEntry,
    ScoringConfig,
    ScoringPrefilter,
)
from magicapply.domain.jobs.scoring import (
    JobScorer,
    KeywordAlignmentScorer,
    LLMScorer,
    Prefilter,
    Score,
    _parse_score,
)
from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.providers.mock import MockLLMClient


def _job(**over: object) -> Job:
    d: dict[str, object] = {
        "source_name": "s",
        "url": "https://a.com/1",
        "title": "Senior Software Engineer",
        "company": "Acme",
        "description": "We use Python and AWS. Remote friendly.",
        "location": "Remote, US",
    }
    d.update(over)
    return Job.new(**d)  # type: ignore[arg-type]


class TestPrefilter:
    def test_default_config_passes_everything(self) -> None:
        cfg = ScoringConfig()  # empty prefilter lists
        assert Prefilter(cfg).check(_job()).passed

    def test_exclude_keyword_fails(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(exclude=["security clearance"]))
        job = _job(description="Requires an active security clearance.")
        r = Prefilter(cfg).check(job)
        assert not r.passed
        assert "exclude" in r.reason

    def test_missing_must_have_fails(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(must_have=["kubernetes"]))
        assert not Prefilter(cfg).check(_job()).passed

    def test_present_must_have_passes(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(must_have=["python"]))
        assert Prefilter(cfg).check(_job()).passed

    def test_location_matches_substring(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(locations=["Remote"]))
        assert Prefilter(cfg).check(_job()).passed

    def test_location_mismatch(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(locations=["Boston"]))
        r = Prefilter(cfg).check(_job(location="Berlin, Germany"))
        assert not r.passed
        assert "location" in r.reason

    def test_location_matches_remote_in_title(self) -> None:
        """LinkedIn often puts Remote in the title, country in location."""
        cfg = ScoringConfig(prefilter=ScoringPrefilter(locations=["Remote"]))
        job = _job(
            title="Data Scientist | Remote",
            location="United States",
            description="",
        )
        assert Prefilter(cfg).check(job).passed

    def test_location_matches_annotated_remote_location(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(locations=["Remote"]))
        job = _job(title="Data Scientist", location="United States (Remote)")
        assert Prefilter(cfg).check(job).passed

    def test_seniority_in_title(self) -> None:
        cfg = ScoringConfig(prefilter=ScoringPrefilter(seniority=["senior", "staff"]))
        assert Prefilter(cfg).check(_job(title="Senior SWE")).passed
        assert not Prefilter(cfg).check(_job(title="Junior SWE")).passed


class TestParseScore:
    def test_clean_json(self) -> None:
        s = _parse_score('{"score": 82, "rationale": "strong python"}')
        assert s.value == 82
        assert s.rationale == "strong python"

    def test_markdown_fence_stripped(self) -> None:
        s = _parse_score('```json\n{"score": 40, "rationale": "meh"}\n```')
        assert s.value == 40

    def test_clamps_out_of_range(self) -> None:
        assert _parse_score('{"score": 150, "rationale": "x"}').value == 100
        assert _parse_score('{"score": -10, "rationale": "y"}').value == 0

    def test_parse_failure_returns_zero(self) -> None:
        s = _parse_score("not a json response at all")
        assert s.value == 0
        assert "parse failure" in s.rationale


class TestLLMScorer:
    def test_uses_cacheable_resume_block(self) -> None:
        llm = MockLLMClient('{"score": 75, "rationale": "ok"}')
        scorer = LLMScorer(llm, base_resume_text="RESUME CONTENT", scoring_prompt="score it")
        s = scorer.score(_job())
        assert s.value == 75
        # Verify caching wiring
        call = llm.calls[0]
        assert call.system is not None
        assert any(b.cacheable for b in call.system)
        # Resume text is in the cacheable block
        assert any("RESUME CONTENT" in b.text for b in call.system if b.cacheable)


class _FixedExtractor:
    """Test double: returns a fixed JD keyword list."""

    def __init__(self, terms: list[str]) -> None:
        self._terms = terms

    def extract(self, job: Job) -> list[str]:  # noqa: ARG002
        return list(self._terms)


class TestKeywordAlignmentScorer:
    def test_scores_fraction_of_extracted_jd_terms_on_resume(self) -> None:
        scorer = KeywordAlignmentScorer(
            resume_text="Skills: python, kubernetes. Built APIs.",
            extractor=_FixedExtractor(
                ["python", "distributed systems", "rust", "golang"]
            ),
        )
        s = scorer.score(_job(description="ignored — extractor is fixed"))
        # Only python is on the resume → 1/4 = 25
        assert s.value == 25
        assert "1/4" in s.rationale

    def test_full_coverage_is_100(self) -> None:
        scorer = KeywordAlignmentScorer(
            resume_text="Primary language: Python.",
            extractor=_FixedExtractor(["python"]),
        )
        s = scorer.score(_job(description="Must know Python."))
        assert s.value == 100


class TestJobScorer:
    def test_prefilter_miss_short_circuits(self) -> None:
        llm = MockLLMClient("should-not-be-called")
        cfg = ScoringConfig(prefilter=ScoringPrefilter(exclude=["python"]))
        combined = JobScorer(
            Prefilter(cfg),
            LLMScorer(llm, base_resume_text="R", scoring_prompt="score it"),
        )
        s = combined.score(_job())
        assert s.value == 0
        assert "prefilter" in s.rationale
        assert llm.calls == []  # LLM never called

    def test_prefilter_pass_calls_llm(self) -> None:
        llm = MockLLMClient('{"score": 88, "rationale": "great"}')
        combined = JobScorer(
            Prefilter(ScoringConfig()),
            LLMScorer(llm, base_resume_text="R", scoring_prompt="score it"),
        )
        s = combined.score(_job())
        assert s == Score(value=88, rationale="great")
        assert len(llm.calls) == 1

    def test_prefilter_pass_calls_keyword_fit(self) -> None:
        combined = JobScorer(
            Prefilter(ScoringConfig()),
            KeywordAlignmentScorer(
                resume_text="I use Python daily.",
                extractor=_FixedExtractor(["python"]),
            ),
        )
        s = combined.score(_job(description="Python required."))
        assert s.value == 100
        assert "keyword alignment" in s.rationale
