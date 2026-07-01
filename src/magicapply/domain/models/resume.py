"""Structured resume models.

BaseResume is what the user maintains as YAML under `resumes/`. TailoredResume
is what the LLM+Builder produces per job in Phase 8, plus provenance notes
so the user can inspect what was changed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_Strict = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExperienceEntry(BaseModel):
    model_config = _Strict

    company: str
    title: str
    start: str  # "YYYY-MM"
    end: str | None = None  # None or "present" means current
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    model_config = _Strict

    school: str
    degree: str | None = None
    field: str | None = None
    graduated: str | None = None


class BaseResume(BaseModel):
    """User-maintained base resume. Loaded from YAML at `resumes/<name>.yaml`."""

    model_config = _Strict

    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    """A per-job derivative of a BaseResume, produced by TailoredResumeBuilder (Phase 8).

    `changes` records what the tailor did — synonym swaps, bullet reorders,
    summary rewrite — so the user can audit before submission.
    """

    model_config = _Strict

    base_name: str
    job_id: str
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
