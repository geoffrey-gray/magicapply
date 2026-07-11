"""Unit tests for ATS-style keyword alignment (JD terms → resume coverage)."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import KeywordBank, KeywordEntry
from magicapply.domain.keywords.alignment import (
    docx_plain_text,
    jd_form_for_entry,
    phrase_in_text,
    score_jd_keyword_coverage,
    score_keyword_alignment,
    serialize_resume_text,
)
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, ExperienceEntry


def _job(**over: object) -> Job:
    d: dict[str, object] = {
        "source_name": "s",
        "url": "https://a.com/1",
        "title": "Senior Engineer",
        "company": "Acme",
        "description": "Build multi-agent systems with Python.",
        "location": "Remote",
    }
    d.update(over)
    return Job.new(**d)  # type: ignore[arg-type]


def _bank() -> KeywordBank:
    return KeywordBank(
        keywords=[
            KeywordEntry(
                term="multi-agent systems",
                synonyms=["agentic framework", "multi-agent orchestration"],
                evidence="Built Condor multi-agent framework",
            ),
            KeywordEntry(
                term="python",
                synonyms=["python3"],
                evidence="Primary language",
            ),
            KeywordEntry(
                term="kubernetes",
                synonyms=["k8s"],
                evidence="Operated clusters",
            ),
        ]
    )


class TestPhraseInText:
    def test_whole_word(self) -> None:
        assert phrase_in_text("sql", "we use sql daily")
        assert not phrase_in_text("sql", "we use postgresql")

    def test_multi_word(self) -> None:
        assert phrase_in_text(
            "distributed systems",
            "experts in distributed systems design",
        )
        assert not phrase_in_text(
            "distributed systems",
            "distributed platform work only",
        )


class TestJdForm:
    def test_prefers_longest_jd_form(self) -> None:
        entry = KeywordEntry(
            term="reinforcement learning",
            synonyms=["rl", "deep reinforcement learning"],
            evidence="e",
        )
        form = jd_form_for_entry(
            entry,
            "We need deep reinforcement learning and RL experience.",
        )
        assert form is not None
        assert form.lower() == "deep reinforcement learning"

    def test_none_when_absent(self) -> None:
        entry = KeywordEntry(term="rust", evidence="e")
        assert jd_form_for_entry(entry, "We use Go and Python.") is None


class TestScoreJdKeywordCoverage:
    def test_example_four_of_ten_is_forty(self) -> None:
        jd_terms = [f"skill-{i}" for i in range(10)]
        resume = "skill-0 skill-1 skill-2 skill-3"
        r = score_jd_keyword_coverage(resume, jd_terms)
        assert r.value == 40
        assert len(r.matched) == 4
        assert len(r.missing) == 6

    def test_empty_jd_terms(self) -> None:
        r = score_jd_keyword_coverage("python everywhere", [])
        assert r.value == 0
        assert "no keywords extracted" in r.rationale

    def test_bank_synonym_credit(self) -> None:
        # JD extracted "k8s"; resume has "kubernetes"; bank links them.
        r = score_jd_keyword_coverage(
            "Operated kubernetes clusters daily.",
            ["k8s", "rust"],
            bank=_bank(),
        )
        assert "k8s" in [m.lower() for m in r.matched]
        assert "rust" in [m.lower() for m in r.missing]
        assert r.value == 50


class TestScoreKeywordAlignmentLegacy:
    """Legacy bank∩JD path still works when jd_terms not supplied."""

    def test_empty_bank(self) -> None:
        r = score_keyword_alignment("python everywhere", _job(), KeywordBank())
        assert r.value == 0

    def test_partial_coverage_via_bank_fallback(self) -> None:
        r = score_keyword_alignment(
            "Senior engineer. Skills: python.",
            _job(),
            _bank(),
        )
        # bank forms in JD: multi-agent systems + python → 1/2 = 50
        assert r.value == 50
        assert "python" in [m.lower() for m in r.matched]


class TestSerializeAndDocx:
    def test_serialize_includes_skills_and_bullets(self) -> None:
        resume = BaseResume(
            name="A",
            summary="Lead.",
            skills=["python", "go"],
            experience=[
                ExperienceEntry(
                    company="X",
                    title="Eng",
                    start="2020-01",
                    bullets=["Shipped multi-agent systems"],
                )
            ],
        )
        text = serialize_resume_text(resume)
        assert "python" in text
        assert "multi-agent systems" in text

    def test_docx_plain_text_roundtrip(self, tmp_path: Path) -> None:
        from docx import Document

        path = tmp_path / "r.docx"
        doc = Document()
        doc.add_paragraph("Hello multi-agent systems")
        doc.save(str(path))
        text = docx_plain_text(path)
        assert "multi-agent systems" in text
