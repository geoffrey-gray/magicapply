# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

Phases 1–10 (foundational stack) and the dry-run harness Phases A–J are shipped. Every step of the config-driven pipeline from job discovery through browser-driven submission is wired end-to-end, with a hermetic fixture E2E and a live-gated E2E to prove it.

**End-to-end today:**
- `magicapply discover <profile>` — sources → in-run dedup → repo dedup → prefilter → LLM score → persist to SQLite (`src/magicapply/pipelines/discovery.py`).
- `magicapply tailor <profile>` — SCORED apps → resume summary rewrite + cover letter → files under `data/tailored/<app_id>/{resume.yaml,cover_letter.md}` → transition to TAILORED (`src/magicapply/pipelines/tailoring.py`).
- `magicapply apply <job-id>` — one Application, real Playwright + Chromium, safe by default (`--no-submit` dry-run stops one click short; `--yes-submit` performs a real submission). Powered by `ApplyPipeline.apply_one` (`src/magicapply/pipelines/apply.py`).
- `magicapply run <profile>` — discover → tailor → `ApplyPipeline.apply_batch` in one shared Chromium session (`src/magicapply/cli/commands/pipeline.py`). Same safety flags. Short-circuits the browser if no TAILORED apps exist.
- `magicapply status` — table split by state with an additional `real: N, dry_run: M` note on the APPLIED bucket (`src/magicapply/cli/commands/status.py`).
- `magicapply doctor` — reports Python, package, cwd, Chromium presence, and (when `--root` resolves) `llm.provider`, resolved data dir, and SQLite DB size (`src/magicapply/cli/main.py`).
- `magicapply config validate`, `magicapply profiles list`, `magicapply status review`.

**Design invariants worth preserving:**
- **Prompts live in YAML** (`configs/prompts.yaml`), loaded through a `PromptsConfig` Pydantic model and injected into `LLMScorer`, `Tailorer`, and `NarrativeEngine` on construction. Never inline in Python.
- **`BaseATSHandler.apply` is the Template Method** for the invariant flow — navigate → CAPTCHA check → fill → CAPTCHA check → **`if data.dry_run` short-circuit** → submit → verify. New ATS handlers inherit the CAPTCHA and dry-run guards for free.
- **`ApplicationData.dry_run` + `Application.dry_run` (row)** — the flow terminal state is always `APPLIED`; the flag distinguishes real vs. dry-run so a single query reports both and `status` splits them at display.
- **LLM providers** are selected by config: `anthropic`, `ollama` (Phase 2 stub), `mock` (shape-aware; no API key needed for full pipeline runs), `replay` (SHA-keyed YAML fixtures + record mode).

**Deferred (Phase 2 roadmap):** LinkedIn scraping, Indeed, Glassdoor, Lever/Workday/Ashby handlers, keyword bank, review UI, Ollama provider implementation, Alembic migrations.

## Running in the dev VM

MagicApply builds and runs inside a dedicated libvirt VM — see `docs/VM_DEV.md`. Any `pytest`, `magicapply`, or `playwright` invocation should be routed through `ssh magicapply-dev 'cd ~/magicapply && uv run …'`. Running these on the host uses the wrong Python and misses the Playwright environment.

## Project: MagicApply

Config-driven, terminal-first job application automation tool. Discovers jobs across platforms, scores them, tailors resumes/cover letters with an LLM, and auto-applies via browser automation. Runs locally on Linux (in the dev VM).

## Tech Stack

Actual `pyproject.toml` dependencies:

- Python 3.11+ (dev VM runs 3.12 via uv)
- CLI: Typer + Rich
- Config: Pydantic v2 + PyYAML
- Browser automation: Playwright
- LLM: `anthropic` (primary), Ollama stub (Phase 2), plus `mock` / `replay` providers for testing
- Storage: SQLite via SQLModel
- HTTP: httpx
- Testing: pytest + pytest-asyncio
- Packaging: `pyproject.toml` (hatchling)

## Architectural Guardrails

These principles come from `ARCHITECTURE.md` and shape design decisions:

- **Config over code.** Static data (search profiles, form answers, thresholds, keyword banks, prompt instructions, ATS behavior) lives in YAML under `configs/`, not in source. Adding a new hardcoded value is usually the wrong instinct — put it in config and load it via a Pydantic model.
- **Layered architecture.** Keep the separation strict: `cli/` → `services/` → `domain/` → `infrastructure/`. Domain code must not import from `infrastructure/` directly; use repository/strategy interfaces. (One pre-existing exception: the `LLMClient` Protocol lives under `infrastructure/llm/client.py` and is imported by domain scoring / tailoring / narrative modules. Flagged in `dryrun_plan.md`; out-of-scope to move.)
- **Extend, don't multiply.** When adding capability, first ask whether an existing class can take a new optional kwarg, a new mode, or a new method. New sibling classes only when the capability genuinely does not exist. See `docs/GOF_PATTERNS.md`.
- **Automation with guardrails.** Auto-apply is the default path; only surface a browser to the user on CAPTCHA or unrecoverable failure. Don't add interactive prompts to the happy path.
- **Focus on the top 4 ATS** (Greenhouse, Lever, Workday, Ashby) before broadening. Greenhouse ships today; the rest are Phase 2.
- **Strategy pattern for ATS handlers** under `infrastructure/browser/` — one handler per ATS, selected at runtime.
- **Repository pattern for persistence** — domain code talks to repository interfaces, SQLite lives behind them in `infrastructure/persistence/`.
- **Multiple search profiles** — each profile has its own base resume and search criteria; nothing should assume a single global profile.

## CLI Surface (ARCHITECTURE.md §7)

`magicapply run | discover | tailor | apply <job-id> | review | config validate | profiles list | status | doctor`

When adding a Typer command, wire it into this surface rather than inventing parallel entry points. `run`, `discover`, `tailor`, `apply` register at the top level via `src/magicapply/cli/main.py`; `config`, `profiles`, `status` are sub-typers.

## Composition root

Dependencies are wired explicitly in `src/magicapply/cli/composition.py` (no DI framework). CLI commands call helpers there (`build_repos`, `build_sources_for_profile`, `build_scorer`, `build_tailorer`, `build_narrative`, `build_tailoring_pipeline`, `build_apply_pipeline`, `build_application_data`) rather than constructing infrastructure classes directly. Add new composition helpers here when a command needs a new collaborator.

## Testing

- **Unit + browser + CLI**: `uv run pytest tests/unit -q` (fast; ~2s, no network, no Chromium).
- **Hermetic E2E fixture**: `uv run pytest tests/integration/e2e/test_e2e_local.py` (marked `slow`; real Chromium against a threaded HTTP fixture that impersonates a Greenhouse form; ~5s).
- **Live-gated pipeline**: `MAGICAPPLY_LIVE_TESTS=1 MAGICAPPLY_LIVE_APPLY=1 MAGICAPPLY_LIVE_APPLY_PROFILE=<name> uv run pytest tests/integration/e2e/test_e2e_live.py -s` (runs your real configs, always dry-run — never submits).
- **Live Anthropic / custom-URL sources**: `MAGICAPPLY_LIVE_TESTS=1 ANTHROPIC_API_KEY=… uv run pytest tests/integration` (hits real network / API).
- **Regenerate tailoring goldens**: `MAGICAPPLY_UPDATE_GOLDENS=1 uv run pytest tests/integration/e2e/test_e2e_local.py::TestE2EDryRun -q` and commit `tests/goldens/tailoring/*`.

## MVP Scope

Delivered end-to-end. Everything in `ARCHITECTURE.md` §10 Phase 1 (config system with multiple profiles, LinkedIn + custom-URL discovery, dedup + scoring, resume tailoring, cover-letter generation, Greenhouse form filling, auto-apply with CAPTCHA fallback) ships. LinkedIn scraping and Ollama remain stubs pending Phase 2.

## Open Design Questions

`ARCHITECTURE.md` §11 leaves these unresolved — flag them to the user rather than picking silently:
- Review UI: terminal TUI vs. local web UI.
- How far to push non-LinkedIn sources in Phase 1.
- Automatic retry of failed applications vs. manual trigger only.
