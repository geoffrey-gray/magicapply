"""YAKE-based JD keyword extraction (no LLM).

Extracts keyphrases from a job posting via YAKE, then the scorer measures
what fraction of those phrases appear on the resume:

    score = 100 * (JD keywords on resume) / (JD keywords extracted)

Default for ``scoring.mode: keyword``. LLM extraction remains available
separately for narrative / Phase 2.
"""

from __future__ import annotations

import logging
import re

import yake

from magicapply.domain.keywords.alignment import normalize_jd_terms
from magicapply.domain.models.job import Job

logger = logging.getLogger(__name__)

# Single-token / phrase fillers common in job ads (not skills).
_STOP_PHRASES = frozenset(
    {
        "experience",
        "years",
        "year",
        "team",
        "teams",
        "work",
        "role",
        "roles",
        "position",
        "positions",
        "job",
        "jobs",
        "opportunity",
        "opportunities",
        "company",
        "companies",
        "candidate",
        "candidates",
        "ability",
        "skills",
        "skill",
        "requirements",
        "responsibilities",
        "preferred",
        "required",
        "including",
        "using",
        "strong",
        "excellent",
        "good",
        "great",
        "new",
        "best",
        "equal opportunity",
        "equal opportunity employer",
        "united states",
        "remote",
        "resource",
        "employee",
        "experiences",
        "contract",
        "compensation",
        "full time",
        "full-time",
        "part time",
        "part-time",
        "benefits",
        "salary",
        "compensation",
        "apply",
        "application",
        "looking",
        "join",
        "about",
        "overview",
        "description",
        "qualifications",
        "minimum",
        "plus",
        "etc",
        "ability to",
        "years of experience",
        "bachelor",
        "master",
        "degree",
        "related field",
        "communication skills",
        "written",
        "verbal",
        "presentations",
        "employer",
        "founding",
        "founded",
        "hiring",
        "position overview",
    }
)

# Drop multi-word phrases that contain these filler tokens.
_FILLER_TOKENS = frozenset(
    {
        "experience",
        "strong",
        "excellent",
        "including",
        "including",
        "preferred",
        "required",
        "requirements",
        "responsibilities",
        "ability",
        "looking",
        "opportunity",
        "opportunities",
        "candidate",
        "candidates",
        "years",
        "year",
        "join",
        "our",
        "your",
        "will",
        "must",
        "should",
        "across",
        "within",
        "through",
        "using",
        "based",
        "related",
        "field",
        "etc",
        "and",
        "or",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "for",
        "with",
        "on",
        "at",
        "by",
        "as",
        "is",
        "are",
        "be",
        "we",
        "you",
        "their",
        "this",
        "that",
        "from",
        "into",
        "about",
        "over",
        "under",
        "such",
        "other",
        "more",
        "most",
        "all",
        "any",
        "both",
        "each",
        "few",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "can",
        "just",
        "don",
        "should",
        "now",
    }
)

_MIN_TERM_LEN = 2
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#/\-]*")
# LinkedIn/HTML often glues sentences: "Python.Experience" or "workflowsDevelop"
_CAMEL_BOUNDARY = re.compile(r"([a-z])([A-Z])")
_SENTENCE_GLUE = re.compile(r"([a-z.])([A-Z][a-z])")

# Single tokens that are too generic to count as skills by themselves.
_GENERIC_SINGLE = frozenset(
    {
        "data",
        "product",
        "science",
        "scientist",
        "scientists",
        "analytics",
        "analysis",
        "development",
        "engineering",
        "engineer",
        "strategy",
        "build",
        "building",
        "learning",
        "machine",
        "type",
        "location",
        "complex",
        "support",
        "robust",
        "financial",
        "institutions",
        "modeling",
        "collect",
        "commitment",
        "week",
        "hour",
        "hours",
        "hourly",
        "role",
        "position",
        "business",
        "products",
        "systems",
        "tools",
        "platforms",
        "projects",
        "solutions",
        "quality",
        "workflows",
        "pipelines",
        "datasets",
        "insights",
        "models",
        "credit",
        "market",
        "access",
        "decisions",
        "founded",
        "remote",
        "resource",
        "employee",
        "experiences",
        "contract",
        "compensation",
        "computer",
        "field",
        "people",
        "family",
        "sets",
        "world",
        "future",
        "shape",
        "solve",
        "drive",
        "define",
        "help",
        "collaborate",
        "technologies",
        "applications",
        "services",
        "research",
        "technical",
        "software",
        "code",
        "design",
        "process",
        "processes",
        "results",
        "impact",
        "performance",
        "knowledge",
        "understanding",
        "proficiency",
        "expertise",
        "environment",
        "stakeholders",
        "leadership",
        "management",
        "communication",
        "presentation",
        "presentations",
        "written",
        "verbal",
        "cross",
        "functional",
        "end",
        "user",
        "users",
        "customer",
        "customers",
        "partner",
        "partners",
        "senior",
        "junior",
        "staff",
        "level",
        "time",
        "day",
        "days",
        "working",
        "work",
        "team",
        "teams",
    }
)


class YakeKeywordExtractor:
    """Unsupervised keyphrase extractor over job title + description."""

    def __init__(
        self,
        *,
        language: str = "en",
        max_ngram_size: int = 2,
        top: int = 20,
        dedup_lim: float = 0.7,
        window_size: int = 2,
    ) -> None:
        if top < 1:
            raise ValueError("top must be >= 1")
        if max_ngram_size < 1:
            raise ValueError("max_ngram_size must be >= 1")
        self._top = top
        self._max_ngram = max_ngram_size
        self._extractor = yake.KeywordExtractor(
            lan=language,
            n=max_ngram_size,
            top=max(top * 4, top),
            dedupLim=dedup_lim,
            windowsSize=window_size,
        )

    def extract(self, job: Job) -> list[str]:
        text = "\n".join(
            part
            for part in (job.title, job.description or "")
            if part and str(part).strip()
        )
        # Company name pollutes YAKE (every Meta JD → "Meta"); keep title+desc.
        if not text.strip():
            return []

        text = _preprocess_jd_text(text)
        company = (job.company or "").strip().lower()

        try:
            ranked = self._extractor.extract_keywords(text)
        except Exception as exc:  # noqa: BLE001 — extraction is best-effort
            logger.warning("YAKE extraction failed for %s: %s", job.url, exc)
            return []

        ranked_sorted = sorted(ranked, key=lambda pair: pair[1])
        kept: list[str] = []
        for raw_kw, _score in ranked_sorted:
            for term in _expand_candidate(raw_kw):
                if not _is_skill_like(term, company=company):
                    continue
                kept.append(term)
        return normalize_jd_terms(kept)[: self._top]


def _preprocess_jd_text(text: str) -> str:
    """Repair common HTML/LinkedIn join artifacts before YAKE runs."""
    text = text.replace("\xa0", " ")
    text = _CAMEL_BOUNDARY.sub(r"\1 \2", text)
    text = _SENTENCE_GLUE.sub(r"\1 \2", text)
    text = re.sub(r"([.!?])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_phrase(raw: str) -> str:
    text = " ".join(str(raw).split()).strip()
    text = text.strip(".,;:()[]{}\"'|/\\")
    return text


def _expand_candidate(raw: str) -> list[str]:
    """Normalize one YAKE hit; salvage short content tails from noisy phrases."""
    term = _normalize_phrase(raw)
    if not term:
        return []
    term = _CAMEL_BOUNDARY.sub(r"\1 \2", term)
    term = " ".join(term.split())
    out = [term]
    words = _WORD_RE.findall(term)
    content = [
        w
        for w in words
        if w.lower() not in _FILLER_TOKENS and w.lower() not in _GENERIC_SINGLE
    ]
    if len(content) >= 2:
        out.append(" ".join(content[-2:]))
    # Only peel a single technical-looking token (e.g. AWS, Python, PyTorch).
    if content and _looks_technical_token(content[-1]):
        out.append(content[-1])
    return out


def _looks_technical_token(token: str) -> bool:
    """Heuristic: acronyms, versioned tools, or non-generic multi-char names."""
    if re.search(r"[\d+#.]", token):
        return True
    if token.isupper() and 2 <= len(token) <= 6:
        return True
    lo = token.lower()
    if lo in _GENERIC_SINGLE or lo in _FILLER_TOKENS or lo in _STOP_PHRASES:
        return False
    # Keep distinctive tokens (python, pytorch, kubernetes, spark, …).
    return len(token) >= 3


def _is_skill_like(term: str, *, company: str = "") -> bool:
    lo = term.lower().strip()
    if len(lo) < _MIN_TERM_LEN:
        return False
    if lo in _STOP_PHRASES:
        return False
    if company and (lo == company or company in lo or lo in company):
        return False
    if re.fullmatch(r"[\d.,%$]+", lo):
        return False

    tokens = _WORD_RE.findall(term)
    if not tokens:
        return False
    if len(tokens) > 3:
        return False

    content = [t for t in tokens if t.lower() not in _FILLER_TOKENS]
    if not content:
        return False
    if len(tokens) == 1:
        return _looks_technical_token(tokens[0])
    # Multi-word: at least one non-generic content token.
    if all(t.lower() in _GENERIC_SINGLE for t in content):
        # Allow established skill bigrams like "machine learning", "data science".
        if lo in {
            "machine learning",
            "deep learning",
            "data science",
            "data scientist",
            "data engineering",
            "data analysis",
            "data analytics",
            "computer science",
            "product analytics",
            "product development",
            "software engineering",
            "large language",
            "language models",
            "natural language",
            "distributed systems",
            "reinforcement learning",
            "feature engineering",
            "a/b testing",
            "ab testing",
            "big data",
            "cloud platform",
            "cloud platforms",
        }:
            return True
        return False
    return True
