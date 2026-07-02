"""Two-stage job scoring.

Stage 1: `Prefilter` — cheap rule checks (location, seniority, must-have /
exclude keywords). Filters out anything hopeless before spending LLM tokens.

Stage 2: `LLMScorer` — sends surviving jobs to the LLM, receives a
`{score: 0-100, rationale: "..."}` JSON payload. The base resume is placed
in a cacheable SystemBlock so a discovery run of N jobs pays the resume
tokens exactly once.

`JobScorer` composes both. A prefilter miss short-circuits with score=0 and
a diagnostic rationale — no LLM call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from magicapply.config.models import ScoringConfig
from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock

logger = logging.getLogger(__name__)

_SCORING_INSTRUCTIONS = """\
You are a strict but fair evaluator scoring how well a job posting fits a candidate's resume.

Return ONLY a JSON object with this exact shape:
{"score": <integer 0-100>, "rationale": "<one or two sentences>"}

Scoring rubric:
- 90-100: exceptional fit; must-have skills present, seniority matches, no red flags
- 70-89: strong fit; most requirements met
- 50-69: partial fit; some requirements met, some gaps
- 25-49: weak fit; significant gaps
- 0-24: poor fit; fundamentally wrong role

Do not include commentary outside the JSON. Do not wrap in markdown fences."""


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Score:
    value: int  # 0-100
    rationale: str


class Prefilter:
    """Fast rule-based screening before any LLM call."""

    def __init__(self, config: ScoringConfig) -> None:
        self._pre = config.prefilter

    def check(self, job: Job) -> PrefilterResult:
        haystack = f"{job.title}\n{job.description}".lower()
        title_lower = job.title.lower()

        for kw in self._pre.exclude:
            if kw.lower() in haystack:
                return PrefilterResult(False, f"contains exclude keyword: {kw!r}")

        for kw in self._pre.must_have:
            if kw.lower() not in haystack:
                return PrefilterResult(False, f"missing must-have keyword: {kw!r}")

        if self._pre.locations:
            location = (job.location or "").lower()
            if not any(loc.lower() in location for loc in self._pre.locations):
                return PrefilterResult(
                    False,
                    f"location {job.location!r} does not match {self._pre.locations}",
                )

        if self._pre.seniority and not any(s.lower() in title_lower for s in self._pre.seniority):
            return PrefilterResult(
                False,
                f"title {job.title!r} does not match seniority terms {self._pre.seniority}",
            )

        return PrefilterResult(True, "")


class LLMScorer:
    """LLM-backed scorer. Base resume cached; JD sent per call."""

    def __init__(self, llm: LLMClient, *, base_resume_text: str) -> None:
        self._llm = llm
        self._resume = base_resume_text

    def score(self, job: Job) -> Score:
        system = [
            SystemBlock(text=_SCORING_INSTRUCTIONS, cacheable=False),
            SystemBlock(text=f"BASE RESUME:\n{self._resume}", cacheable=True),
        ]
        user = (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"LOCATION: {job.location or 'unspecified'}\n"
            f"\nDESCRIPTION:\n{job.description}\n"
        )
        result = self._llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            max_tokens=400,
            temperature=0.0,
        )
        return _parse_score(result.text)


class JobScorer:
    """Composed scorer: prefilter first, then LLM if it passes."""

    def __init__(self, prefilter: Prefilter, llm_scorer: LLMScorer) -> None:
        self._pre = prefilter
        self._llm = llm_scorer

    def score(self, job: Job) -> Score:
        gate = self._pre.check(job)
        if not gate.passed:
            return Score(value=0, rationale=f"prefilter: {gate.reason}")
        return self._llm.score(job)


def _parse_score(raw: str) -> Score:
    """Extract {score, rationale} JSON. Defensive against markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip a ```json ... ``` fence if the model added one anyway.
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
        value = int(data["score"])
        rationale = str(data.get("rationale", "")).strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("LLM score parse failed: %s (raw=%r)", exc, raw[:200])
        return Score(value=0, rationale=f"parse failure: {exc}")

    value = max(0, min(100, value))
    return Score(value=value, rationale=rationale)
