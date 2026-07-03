# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

Phases 1–10 have shipped. The tree is populated: source under `src/magicapply/`, tests under `tests/`, config examples under `configs/`, a working `pyproject.toml`, and a live git history.

**What's wired end-to-end today:**
- `magicapply discover <profile>` — sources → in-run dedup → repo dedup → prefilter → LLM score → persist to SQLite (`src/magicapply/pipelines/discovery.py`).
- Config loading + cross-ref validation + multi-profile support (`src/magicapply/config/`).
- Anthropic LLM provider with prompt caching (`src/magicapply/infrastructure/llm/providers/anthropic.py`).
- Custom-URL and career-page JSON-LD sources (`src/magicapply/infrastructure/sources/custom_url.py`).
- SQLite repositories for jobs + applications (`src/magicapply/infrastructure/persistence/`).
- CLI: `discover`, `run`, `apply`, `status`, `config validate`, `profiles list`, `doctor`, `version`.

**What's implemented but not yet wired:**
- `Tailorer` (resume summary rewrite) and `NarrativeEngine` (cover letter, screening answers) exist under `src/magicapply/domain/resumes/` but no pipeline calls them.
- `ApplyPipeline` + `GreenhouseHandler` exist under `src/magicapply/pipelines/apply.py` and `src/magicapply/infrastructure/browser/ats/` but the `apply` CLI command is a stub; there is no Playwright session lifecycle.
- The Ollama provider raises `NotImplementedError`.

**What's deferred (Phase 2):** LinkedIn scraping, Indeed, Glassdoor, Lever/Workday/Ashby handlers, keyword bank, review UI.

`dryrun_plan.md` (repo root) tracks the current work: an end-to-end dry-run test harness that wires tailoring + browser submission into the pipeline with the LLM stubbed.

## Running in the dev VM

MagicApply builds and runs inside a dedicated libvirt VM — see `docs/VM_DEV.md`. Any `pytest`, `magicapply`, or `playwright` invocation should be routed through `ssh magicapply-dev 'cd ~/magicapply && uv run …'`. Running these on the host uses the wrong Python and misses the Playwright environment.

## Project: MagicApply

Config-driven, terminal-first job application automation tool. Discovers jobs across platforms, scores them, tailors resumes/cover letters with an LLM, and auto-applies via browser automation. Runs locally on Linux (in the dev VM).

## Tech Stack

Actual `pyproject.toml` dependencies (Phase 10):

- Python 3.11+ (dev VM runs 3.12 via uv)
- CLI: Typer + Rich
- Config: Pydantic v2 + PyYAML
- Browser automation: Playwright
- LLM: `anthropic` (primary), Ollama stub (Phase 2)
- Storage: SQLite via SQLModel
- HTTP: httpx
- Testing: pytest + pytest-asyncio
- Packaging: `pyproject.toml` (hatchling)

## Architectural Guardrails

These principles come from `ARCHITECTURE.md` and should shape design decisions:

- **Config over code.** Static data (search profiles, form answers, thresholds, keyword banks, ATS behavior) lives in YAML under `configs/`, not in source. Adding a new hardcoded value is usually the wrong instinct — put it in config and load it via a Pydantic model.
- **Layered architecture.** Keep the separation strict: `cli/` → `services/` → `domain/` → `infrastructure/`. Domain code must not import from `infrastructure/` directly; use repository/strategy interfaces.
- **Automation with guardrails.** Auto-apply is the default path; only surface a browser to the user on CAPTCHA or unrecoverable failure. Don't add interactive prompts to the happy path.
- **Focus on the top 4 ATS** (Greenhouse, Lever, Workday, Ashby) before broadening. Phase 1 MVP is Greenhouse only.
- **Strategy pattern for ATS handlers** under `infrastructure/browser/` — one handler per ATS, selected at runtime.
- **Repository pattern for persistence** — domain code talks to repository interfaces, SQLite lives behind them in `infrastructure/persistence/`.
- **Multiple search profiles** — each profile has its own base resume and search criteria; nothing should assume a single global profile.

## CLI Surface (ARCHITECTURE.md §7)

`magicapply run | discover | apply <job-id> | review | config validate | profiles list | status | doctor`

When adding a Typer command, wire it into this surface rather than inventing parallel entry points. `run`, `discover`, `apply` register at the top level via `src/magicapply/cli/main.py`; `config`, `profiles`, `status` are sub-typers.

## Composition root

Dependencies are wired explicitly in `src/magicapply/cli/composition.py` (no DI framework). CLI commands call helpers there (`build_repos`, `build_sources_for_profile`, `build_scorer`, and so on) rather than constructing infrastructure classes directly. Add new composition helpers here when a command needs a new collaborator.

## MVP Scope (Phase 1)

Config system with multi-profile support, LinkedIn + custom-URL discovery, dedup + scoring, minimal resume tailoring, cover-letter generation, Greenhouse form filling, auto-apply with CAPTCHA fallback. Defer Indeed/Glassdoor, Lever/Workday/Ashby, keyword bank, and any web review UI to Phase 2 unless the user explicitly asks for them.

## Open Design Questions

`ARCHITECTURE.md` §11 leaves these unresolved — flag them to the user rather than picking silently:
- Review UI: terminal TUI vs. local web UI.
- How far to push non-LinkedIn sources in Phase 1.
- Automatic retry of failed applications vs. manual trigger only.
