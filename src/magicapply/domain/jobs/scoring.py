"""Two-stage job scoring.

Stage 1: `Prefilter` — cheap rule checks (location, seniority, must-have /
exclude keywords). Filters out anything hopeless before spending LLM tokens
(or running keyword alignment).

Stage 2: a fit scorer — either:

- `KeywordAlignmentScorer` (Phase 1 default) — extract keywords from the
  JD via YAKE (no LLM), then score = fraction of those terms present on
  the resume (e.g. 4 of 10 JD keywords → 40). KeywordBank is optional
  synonym credit only, not the scoring vocabulary.
- `LLMScorer` — sends surviving jobs to the LLM, receives a
  `{score: 0-100, rationale: "..."}` JSON payload. The base resume is placed
  in a cacheable SystemBlock so a discovery run of N jobs pays the resume
  tokens exactly once.

`JobScorer` composes both stages. A prefilter miss short-circuits with
score=0 and a diagnostic rationale — no fit scorer call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from magicapply.config.models import KeywordBank, ScoringConfig
from magicapply.domain.keywords.alignment import score_jd_keyword_coverage
from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock

logger = logging.getLogger(__name__)


class JobKeywordExtractor(Protocol):
    """Pull skill/tool keyphrases from a Job (YAKE, LLM, …)."""

    def extract(self, job: Job) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Score:
    value: int  # 0-100
    rationale: str


class FitScorer(Protocol):
    """Second-stage scorer: produce a 0–100 fit score for one Job."""

    def score(self, job: Job) -> Score: ...


class Prefilter:
    """Fast rule-based screening before any fit scorer call."""

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
            # Match against location *and* title/description: LinkedIn remote
            # SERPs often put workplace type in the title ("… | Remote") while
            # the location field is only a country ("United States").
            location_haystack = "\n".join(
                [
                    job.location or "",
                    job.title or "",
                    job.description or "",
                ]
            ).lower()
            if not any(
                loc.lower() in location_haystack for loc in self._pre.locations
            ):
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


class KeywordAlignmentScorer:
    """JD-extracted keyword coverage on the resume.

    1. Extractor (default: YAKE) pulls keyphrases from the real JD text.
    2. Each term is checked against the resume text (bank synonyms optional).
    3. Score = 100 * matched / total extracted terms.
    """

    def __init__(
        self,
        *,
        resume_text: str,
        extractor: JobKeywordExtractor,
        bank: KeywordBank | None = None,
    ) -> None:
        self._resume_text = resume_text
        self._extractor = extractor
        self._bank = bank

    def score(self, job: Job) -> Score:
        jd_terms = self._extractor.extract(job)
        result = score_jd_keyword_coverage(
            self._resume_text, jd_terms, bank=self._bank
        )
        return Score(value=result.value, rationale=result.rationale)


class LLMScorer:
    """LLM-backed scorer. Base resume cached; JD sent per call."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        base_resume_text: str,
        scoring_prompt: str,
    ) -> None:
        self._llm = llm
        self._resume = base_resume_text
        self._prompt = scoring_prompt

    def score(self, job: Job) -> Score:
        system = [
            SystemBlock(text=self._prompt, cacheable=False),
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
    """Composed scorer: prefilter first, then fit scorer if it passes."""

    def __init__(self, prefilter: Prefilter, fit_scorer: FitScorer) -> None:
        self._pre = prefilter
        self._fit = fit_scorer

    def score(self, job: Job) -> Score:
        gate = self._pre.check(job)
        if not gate.passed:
            return Score(value=0, rationale=f"prefilter: {gate.reason}")
        return self._fit.score(job)



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
