"""Case-insensitive substring match of extracted JD terms against a KeywordBank.

Each JD term is matched (a) against the ``term`` field of each entry and
(b) against any of the entry's ``synonyms``. First entry that matches wins;
duplicate bank entries for the same JD term do not count twice.
"""

from __future__ import annotations

from magicapply.config.models import KeywordBank, KeywordEntry


def match_bank(extracted: list[str], bank: KeywordBank) -> list[KeywordEntry]:
    """Return the KeywordBank entries relevant to a set of JD terms.

    Case-insensitive substring match on ``term`` and ``synonyms``. Order of
    the returned list follows the bank's own order (stable + deterministic
    for goldens). Each entry is included at most once.
    """
    if not extracted or not bank.keywords:
        return []

    lowered = [t.strip().lower() for t in extracted if t.strip()]
    hits: list[KeywordEntry] = []
    seen: set[str] = set()
    for entry in bank.keywords:
        if entry.term in seen:
            continue
        candidates = [entry.term.lower()] + [s.lower() for s in entry.synonyms]
        if any(_matches_any(cand, lowered) for cand in candidates):
            hits.append(entry)
            seen.add(entry.term)
    return hits


def _matches_any(bank_term: str, jd_terms: list[str]) -> bool:
    """A bank term matches when it is a substring of a JD term, or vice versa."""
    return any(bank_term in jd or jd in bank_term for jd in jd_terms)
