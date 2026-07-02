"""Job processing: dedup + two-stage scoring."""

from magicapply.domain.jobs.dedup import dedupe_by_key
from magicapply.domain.jobs.scoring import (
    JobScorer,
    LLMScorer,
    Prefilter,
    PrefilterResult,
    Score,
)

__all__ = [
    "JobScorer",
    "LLMScorer",
    "Prefilter",
    "PrefilterResult",
    "Score",
    "dedupe_by_key",
]
