"""Tests for TailoredResumeBuilder (pure) and Tailorer (LLM-driven)."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, ExperienceEntry
from magicapply.domain.resumes.tailor import (
    TailoredResumeBuilder,
    Tailorer,
    _serialize_resume,
)
from magicapply.infrastructure.llm.providers.mock import MockLLMClient


def _base() -> BaseResume:
    return BaseResume(
        name="Test Person",
        email="t@example.com",
        summary="Engineer with 8 years experience.",
        experience=[
            ExperienceEntry(
                company="Acme",
                title="SWE",
                start="2020-01",
                end="present",
                bullets=["Led migration", "Reduced latency"],
            ),
        ],
        skills=["python", "aws"],
    )


def _job() -> Job:
    return Job.new(
        source_name="s",
        url="https://a.com/1",
        title="Senior Python Engineer",
        company="Foo Corp",
        description="We need Python + AWS.",
    )


class TestBuilder:
    def test_no_overrides_returns_base_shape(self) -> None:
        base = _base()
        job = _job()
        tailored = TailoredResumeBuilder(base, job).build()

        assert tailored.base_name == base.name
        assert tailored.job_id == job.id
        assert tailored.summary == base.summary
        assert tailored.skills == base.skills
        assert tailored.changes == []

    def test_summary_override_records_change(self) -> None:
        tailored = (
            TailoredResumeBuilder(_base(), _job())
            .with_summary("New tailored summary.", "aligned focus")
            .build()
        )
        assert tailored.summary == "New tailored summary."
        assert len(tailored.changes) == 1
        assert "summary" in tailored.changes[0]

    def test_empty_summary_is_ignored(self) -> None:
        tailored = TailoredResumeBuilder(_base(), _job()).with_summary("   ", "trim").build()
        assert tailored.summary == _base().summary
        assert tailored.changes == []

    def test_reordered_experience_recorded(self) -> None:
        new_exp = [ExperienceEntry(company="Beta", title="Lead", start="2018-01", bullets=[])]
        tailored = (
            TailoredResumeBuilder(_base(), _job())
            .with_reordered_experience(new_exp, "most relevant first")
            .build()
        )
        assert [e.company for e in tailored.experience] == ["Beta"]
        assert any("reordered" in c for c in tailored.changes)


class TestSerialize:
    def test_includes_key_sections(self) -> None:
        text = _serialize_resume(_base())
        assert "Test Person" in text
        assert "SWE at Acme" in text
        assert "Led migration" in text
        assert "python" in text


class TestTailorer:
    def test_llm_summary_applied_when_different(self) -> None:
        llm = MockLLMClient("Rewritten focused summary.")
        tailored = Tailorer(llm, _base(), summary_prompt="rewrite the summary").tailor_for(_job())
        assert tailored.summary == "Rewritten focused summary."
        assert any("summary" in c for c in tailored.changes)

    def test_llm_summary_matching_base_skipped(self) -> None:
        base = _base()
        # LLM returns the same text as the base summary — treat as no-op.
        llm = MockLLMClient(base.summary or "")
        tailored = Tailorer(llm, base, summary_prompt="rewrite the summary").tailor_for(_job())
        assert tailored.changes == []

    def test_resume_goes_in_cacheable_system_block(self) -> None:
        llm = MockLLMClient("ok summary")
        Tailorer(llm, _base(), summary_prompt="rewrite the summary").tailor_for(_job())
        call = llm.calls[0]
        assert call.system is not None
        assert any(b.cacheable and "BASE RESUME" in b.text for b in call.system)
