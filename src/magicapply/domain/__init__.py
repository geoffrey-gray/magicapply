"""MagicApply domain layer — pure Pydantic models and rules, no I/O.

Nothing in `domain/` may import from `infrastructure/`. This lets the domain
be exercised with fast, hermetic unit tests. See docs/GOF_PATTERNS.md.
"""
