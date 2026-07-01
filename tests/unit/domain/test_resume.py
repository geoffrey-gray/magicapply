"""Tests for BaseResume and TailoredResume models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from magicapply.domain.models.resume import (
    BaseResume,
    EducationEntry,
    ExperienceEntry,
    TailoredResume,
)


class TestBaseResume:
    def test_minimal(self) -> None:
        r = BaseResume(name="Test Person")
        assert r.name == "Test Person"
        assert r.experience == []
        assert r.skills == []

    def test_full_roundtrip(self) -> None:
        raw = {
            "name": "Test Person",
            "email": "t@example.com",
            "summary": "One line",
            "experience": [
                {
                    "company": "Acme",
                    "title": "SWE",
                    "start": "2020-01",
                    "end": "present",
                    "bullets": ["shipped X", "reduced Y"],
                }
            ],
            "education": [{"school": "State U", "degree": "BS"}],
            "skills": ["python"],
        }
        r = BaseResume.model_validate(raw)
        assert r.experience[0].bullets == ["shipped X", "reduced Y"]
        assert r.education[0].school == "State U"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ghost"):
            BaseResume.model_validate({"name": "T", "ghost": "boo"})

    def test_experience_end_optional(self) -> None:
        e = ExperienceEntry(company="A", title="T", start="2020-01")
        assert e.end is None


class TestTailoredResume:
    def test_requires_base_and_job(self) -> None:
        with pytest.raises(ValidationError):
            TailoredResume(name="T")  # type: ignore[call-arg]

    def test_records_changes(self) -> None:
        t = TailoredResume(
            base_name="swe",
            job_id="abc",
            name="Test",
            changes=["reordered bullets by JD relevance", "swapped Python -> Python 3"],
        )
        assert len(t.changes) == 2


class TestEducationEntry:
    def test_minimum(self) -> None:
        e = EducationEntry(school="MIT")
        assert e.degree is None
        assert e.graduated is None
