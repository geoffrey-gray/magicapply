"""Domain model re-exports."""

from magicapply.domain.models.application import (
    ALLOWED_TRANSITIONS,
    Application,
    ApplicationState,
    InvalidTransition,
    StateTransition,
)
from magicapply.domain.models.job import Job, canonicalize_url, hash_url
from magicapply.domain.models.resume import (
    BaseResume,
    EducationEntry,
    ExperienceEntry,
    TailoredResume,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Application",
    "ApplicationState",
    "BaseResume",
    "EducationEntry",
    "ExperienceEntry",
    "InvalidTransition",
    "Job",
    "StateTransition",
    "TailoredResume",
    "canonicalize_url",
    "hash_url",
]
