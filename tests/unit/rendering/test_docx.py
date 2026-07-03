"""Unit tests for DocxResumeRenderer against the shipped canonical template."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from magicapply.domain.models.resume import (
    EducationEntry,
    ExperienceEntry,
    TailoredResume,
)
from magicapply.infrastructure.rendering.docx import (
    DocxResumeRenderer,
    TemplateMissing,
)


_TEMPLATE = Path(__file__).resolve().parents[3] / "configs" / "resume_template.docx"


def _tailored() -> TailoredResume:
    return TailoredResume(
        base_name="Jane Doe",
        job_id="abc123",
        name="Jane Doe",
        email="jane@example.com",
        phone="555-0100",
        location="Boston, MA",
        summary="Backend engineer with distributed systems focus.",
        experience=[
            ExperienceEntry(
                company="Acme",
                title="Senior Engineer",
                start="2021-03",
                end=None,
                bullets=[
                    "Led migration to microservices.",
                    "Cut p99 latency by 40 percent.",
                ],
            ),
            ExperienceEntry(
                company="Beta",
                title="Engineer",
                start="2019-01",
                end="2021-02",
                bullets=["Shipped the payments service."],
            ),
        ],
        education=[
            EducationEntry(school="MIT", degree="B.S.", field="CS", graduated="2018"),
        ],
        skills=["python", "distributed systems", "kubernetes"],
    )


def _extract_text(path: Path) -> str:
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


class TestDocxResumeRenderer:
    def test_missing_template_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateMissing):
            DocxResumeRenderer(tmp_path / "no-such.docx")

    def test_render_produces_valid_docx(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        assert out.exists()
        # ZIP/OOXML magic bytes.
        assert out.read_bytes()[:4] == b"PK\x03\x04"

    def test_render_writes_identity_and_summary(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        text = _extract_text(out)
        assert "Jane Doe" in text
        assert "jane@example.com" in text
        assert "Boston, MA" in text
        assert "Backend engineer with distributed systems focus." in text

    def test_render_writes_experience_and_bullets(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        text = _extract_text(out)
        assert "Senior Engineer" in text
        assert "Acme" in text
        assert "Led migration to microservices." in text
        assert "Cut p99 latency by 40 percent." in text
        # Second experience block
        assert "Beta" in text
        assert "Shipped the payments service." in text

    def test_render_handles_null_end_date(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        text = _extract_text(out)
        # The current-role entry has end=None; the renderer normalizes it.
        assert "present" in text

    def test_render_writes_skills_comma_joined(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        text = _extract_text(out)
        assert "python, distributed systems, kubernetes" in text

    def test_render_writes_education(self, tmp_path: Path) -> None:
        renderer = DocxResumeRenderer(_TEMPLATE)
        out = renderer.render(_tailored(), tmp_path / "resume.docx")
        text = _extract_text(out)
        assert "MIT" in text
        assert "B.S." in text
