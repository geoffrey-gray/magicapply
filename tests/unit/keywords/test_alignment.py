"""Unit tests for ATS-style keyword alignment."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import KeywordBank, KeywordEntry
from magicapply.domain.keywords.alignment import (
    docx_plain_text,
    jd_form_for_entry,
    phrase_in_text,
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


class TestScoreKeywordAlignment:
    def test_empty_bank(self) -> None:
        r = score_keyword_alignment("python everywhere", _job(), KeywordBank())
        assert r.value == 0
        assert "empty keyword bank" in r.rationale

    def test_no_jd_keywords(self) -> None:
        r = score_keyword_alignment(
            "python and kubernetes",
            _job(description="Lead happy teams."),
            _bank(),
        )
        assert r.value == 0
        assert "no bank keywords found in JD" in r.rationale

    def test_partial_coverage(self) -> None:
        # JD emphasizes multi-agent systems + python; resume has only python.
        r = score_keyword_alignment(
            "Senior engineer. Skills: python.",
            _job(),
            _bank(),
        )
        assert r.value == 50
        assert "python" in [m.lower() for m in r.matched]
        assert any("multi-agent" in m.lower() for m in r.missing)

    def test_synonym_only_resume_scores_low_jd_form_credit(self) -> None:
        # Resume says synonym; JD says canonical term → no credit (pre-tailor).
        resume_before = "Built an agentic framework for research."
        r_before = score_keyword_alignment(resume_before, _job(), _bank())
        assert r_before.value == 0  # only multi-agent systems in JD forms; no python in resume either
        # JD has multi-agent systems + python → 0/2
        assert r_before.value == 0

        # After synonym→term swap (what InPlaceDocxTailorer does):
        resume_after = "Built multi-agent systems for research. Also python."
        r_after = score_keyword_alignment(resume_after, _job(), _bank())
        assert r_after.value == 100
        assert r_after.value > r_before.value

    def test_full_match(self) -> None:
        r = score_keyword_alignment(
            "Python expert building multi-agent systems.",
            _job(),
            _bank(),
        )
        assert r.value == 100
        assert not r.missing


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
