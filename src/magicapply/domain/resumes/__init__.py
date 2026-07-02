"""Resume tailoring + narrative engine."""

from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.domain.resumes.tailor import TailoredResumeBuilder, Tailorer

__all__ = ["NarrativeEngine", "TailoredResumeBuilder", "Tailorer"]
