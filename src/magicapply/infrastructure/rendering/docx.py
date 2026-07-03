"""TailoredResume → DOCX rendering via docxtpl.

The template at ``configs/resume_template.docx`` uses standard Jinja2
syntax inside a Word document — ``{{ name }}`` for substitutions and
``{%p for … %}`` / ``{%p endfor %}`` for block loops (the ``p`` variant
removes the tag paragraph after rendering so the output stays clean).
See ``scripts/generate_resume_template.py`` for the canonical template
that ships with the code; operators can drop their own file at the same
path and it will be used instead.
"""

from __future__ import annotations

from pathlib import Path

from docxtpl import DocxTemplate

from magicapply.domain.models.resume import TailoredResume


class TemplateMissing(FileNotFoundError):
    """Raised when the configured DOCX template is not on disk."""


class DocxResumeRenderer:
    """Render a TailoredResume into a .docx file."""

    def __init__(self, template_path: Path) -> None:
        template_path = Path(template_path)
        if not template_path.exists():
            raise TemplateMissing(
                f"resume template missing: {template_path}. Run "
                "`uv run python scripts/generate_resume_template.py` to "
                "produce the canonical template."
            )
        self._template_path = template_path

    def render(self, tailored: TailoredResume, out_path: Path) -> Path:
        doc = DocxTemplate(str(self._template_path))
        doc.render(_context_from(tailored))
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_path))
        return out_path


def _context_from(tailored: TailoredResume) -> dict:
    """Build the Jinja render context, normalizing None to '' for text fields."""
    data = tailored.model_dump(mode="json")
    for key in ("email", "phone", "location", "summary"):
        if data.get(key) is None:
            data[key] = ""
    for exp in data.get("experience", []):
        if exp.get("end") is None:
            exp["end"] = "present"
        if exp.get("location") is None:
            exp["location"] = ""
    for edu in data.get("education", []):
        for key in ("degree", "field", "graduated"):
            if edu.get(key) is None:
                edu[key] = ""
    return data
