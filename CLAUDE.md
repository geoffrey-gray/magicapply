# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

**This repository is pre-implementation.** As of writing, the only file present is `ARCHITECTURE.md` — a design document. There is no source code, no `pyproject.toml`, no tests, and no `.git` directory yet. Any request to "run tests," "build," or "install" cannot be satisfied against existing code; the first task is to scaffold the project described in `ARCHITECTURE.md`.

When bootstrapping, follow the project layout, tech stack, and naming from `ARCHITECTURE.md` (§5, §8) rather than inventing new structure.

## Project: MagicApply

Config-driven, terminal-first job application automation tool. Discovers jobs across platforms, scores them, tailors resumes/cover letters with an LLM, and auto-applies via browser automation. Runs locally on Linux.

## Planned Tech Stack (from ARCHITECTURE.md §8)

- Python 3.11+
- CLI: Typer + Rich
- Config: Pydantic v2 + PyYAML
- Browser automation: Playwright
- LLM: Claude API (primary), Ollama (optional)
- Storage: SQLite
- Testing: pytest
- Packaging: `pyproject.toml`

## Architectural Guardrails

These principles come from `ARCHITECTURE.md` and should shape design decisions:

- **Config over code.** Static data (search profiles, form answers, thresholds, keyword banks, ATS behavior) lives in YAML under `configs/`, not in source. Adding a new hardcoded value is usually the wrong instinct — put it in config and load it via a Pydantic model.
- **Layered architecture.** Keep the separation strict: `cli/` → `services/` → `domain/` → `infrastructure/`. Domain code must not import from `infrastructure/` directly; use repository/strategy interfaces.
- **Automation with guardrails.** Auto-apply is the default path; only surface a browser to the user on CAPTCHA or unrecoverable failure. Don't add interactive prompts to the happy path.
- **Focus on the top 4 ATS** (Greenhouse, Lever, Workday, Ashby) before broadening. Phase 1 MVP is Greenhouse only.
- **Strategy pattern for ATS handlers** under `infrastructure/browser/` — one handler per ATS, selected at runtime.
- **Repository pattern for persistence** — domain code talks to repository interfaces, SQLite lives behind them in `infrastructure/persistence/`.
- **Multiple search profiles** — each profile has its own base resume and search criteria; nothing should assume a single global profile.

## Planned CLI Surface (ARCHITECTURE.md §7)

`magicapply run | discover | apply <job-id> | review | config validate | profiles list | status`

When adding a Typer command, wire it into this surface rather than inventing parallel entry points.

## MVP Scope (Phase 1)

Config system with multi-profile support, LinkedIn + custom-URL discovery, dedup + scoring, minimal resume tailoring, cover-letter generation, Greenhouse form filling, auto-apply with CAPTCHA fallback. Defer Indeed/Glassdoor, Lever/Workday/Ashby, keyword bank, and any web review UI to Phase 2 unless the user explicitly asks for them.

## Open Design Questions

`ARCHITECTURE.md` §11 leaves these unresolved — flag them to the user rather than picking silently:
- Review UI: terminal TUI vs. local web UI.
- How far to push non-LinkedIn sources in Phase 1.
- Automatic retry of failed applications vs. manual trigger only.
