# MagicApply — Architecture & Design Document

**Version:** 1.0  
**Date:** July 1, 2026  
**Status:** Draft  
**Author:** Geoffrey

---

## 1. Project Overview

**MagicApply** is a config-driven, terminal-first job application automation tool. It discovers jobs across multiple platforms, scores them, tailors applications using base resumes, pre-fills forms from configuration, and auto-applies to qualifying roles with minimal human intervention.

### Goals

- Automate the repetitive parts of job applications while maintaining quality and control.
- Be heavily configurable so static information and behavior can be changed without modifying code.
- Focus on the most common ATS platforms rather than trying to support everything.
- Provide a good developer experience as both a personal tool and a public GitHub project.
- Run locally on Linux with a terminal-first interface.

### Core Principles

- **Config over code** for static data and behavior.
- **Automation with guardrails** — auto-apply by default, but surface to the user on failure.
- **Focus on the 80%** — prioritize reliability on major ATS over perfect coverage.
- **Multiple search profiles** — each profile can use a different base resume and search criteria.
- **Human-in-the-loop only when necessary** (primarily for CAPTCHAs and failures).

---

## 2. Requirements

### Functional Requirements

| ID     | Requirement                                                                 | Priority |
|--------|-----------------------------------------------------------------------------|----------|
| FR-01  | Support multiple search profiles, each linked to a dedicated base resume    | High     |
| FR-02  | Aggregate jobs from LinkedIn, Indeed, Glassdoor, and custom company URLs    | High     |
| FR-03  | Deduplicate jobs across sources                                             | High     |
| FR-04  | Score jobs and auto-apply only above a configurable threshold               | High     |
| FR-05  | Pre-fill static form fields from configuration (email, work auth, DEI, etc.)| High     |
| FR-06  | Perform minimal bullet rewriting based on job description                   | High     |
| FR-07  | Support Narrative Mode for cover letters and open-ended screening questions | High     |
| FR-08  | Focus on top 4 ATS: Greenhouse, Lever, Workday, Ashby                       | High     |
| FR-09  | Attempt automatic form filling; surface to user only on CAPTCHA or failure  | High     |
| FR-10  | Track application history and status                                        | Medium   |
| FR-11  | Support personal keyword bank with job-specific extraction and injection    | Medium   |

### Non-Functional Requirements

- Everything important should be configurable.
- Must run locally on the user’s machine.
- Terminal-first experience with optional browser surfacing.
- Modular and maintainable codebase.
- Resilient to common ATS changes.

---

## 3. User Flows

### Primary Auto-Apply Flow

1. User configures search profiles and static data.
2. System discovers jobs from configured sources.
3. Jobs are deduplicated and scored.
4. Jobs above the threshold are processed:
   - Select appropriate base resume.
   - Extract keywords from job description.
   - Minimally rewrite bullets.
   - Generate narrative content (cover letter + open questions).
5. Pre-fill application form using config + tailored content.
6. Submit application automatically.
7. Log status and continue.

### Intervention Flow

- The system attempts full automation.
- On CAPTCHA detection or unrecoverable error → opens browser for user to intervene.
- User can also manually trigger review or re-processing of specific jobs.

---

## 4. System Architecture

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         CLI (Typer)                          │
└────────────────────────────┬─────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Config      │    │   Jobs        │    │ Applications  │
│   Layer       │    │   Layer       │    │   Tracker     │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│ Resume &      │    │   LLM Layer   │    │   Browser     │
│ Keyword       │    │ (Tailoring +  │    │ Automation    │
│ Engine        │    │  Narrative)   │    │ (Playwright)  │
└───────────────┘    └───────────────┘    └───────────────┘
```

### Architectural Style

- **Layered Architecture** with clear separation of concerns:
  - Presentation (CLI)
  - Application Services
  - Domain Logic
  - Infrastructure

### Recommended Design Patterns (GoF)

| Pattern          | Usage                                      | Location                  |
|------------------|--------------------------------------------|---------------------------|
| **Strategy**     | Different ATS form-filling behaviors       | `infrastructure/browser/` |
| **Factory**      | Creating ATS handlers and tailorers        | Domain / Infrastructure   |
| **Builder**      | Constructing tailored resumes & cover letters | `domain/resumes/`       |
| **Repository**   | Abstracting data access (jobs, applications) | `infrastructure/persistence/` |
| **Observer**     | Optional — for tracking application state changes | `domain/applications/` |

---

## 5. Project Structure

```bash
magicapply/
├── magicapply/
│   ├── cli/                    # Typer commands
│   ├── config/                 # Pydantic config models + loaders
│   ├── domain/
│   │   ├── models/             # Core Pydantic domain models
│   │   ├── jobs/               # Scoring, deduplication logic
│   │   ├── resumes/            # Tailoring + narrative engine
│   │   ├── keywords/           # Keyword extraction & matching
│   │   └── applications/       # Application state & tracking
│   ├── infrastructure/
│   │   ├── browser/            # Playwright + ATS strategies
│   │   ├── llm/                # LLM client and prompting logic
│   │   ├── sources/            # Job source adapters
│   │   └── persistence/        # SQLite repository
│   ├── services/               # High-level orchestration
│   └── utils/
├── configs/
│   ├── profiles/               # One YAML per search profile
│   └── base_config.yaml
├── resumes/                    # Base resume files
├── data/                       # SQLite database
├── tests/
├── pyproject.toml
├── README.md
├── ARCHITECTURE.md
└── LICENSE
```

---

## 6. Key Components

| Component                | Responsibility                                      | Key Technologies      |
|--------------------------|-----------------------------------------------------|-----------------------|
| **CLI**                  | Primary user interface                              | Typer + Rich          |
| **Config Layer**         | Load and validate all settings                      | Pydantic + YAML       |
| **Job Sources**          | Fetch jobs from LinkedIn, Indeed, Glassdoor, custom URLs | Playwright + APIs   |
| **Job Processor**        | Deduplication + scoring                             | Domain logic          |
| **Resume Engine**        | Base resume selection + minimal rewriting           | LLM + templates       |
| **Narrative Engine**     | Cover letter + open-ended question generation       | LLM prompting         |
| **Keyword Engine**       | Extract and match keywords                          | LLM + simple NLP      |
| **Form Filling**         | ATS-specific form automation                        | Playwright + Strategy |
| **Application Tracker**  | Persist and query application history               | SQLite + Repository   |
| **Intervention Handler** | Detect failures and surface browser to user         | Playwright            |

---

## 7. CLI Commands (Initial Set)

| Command                        | Description                                      |
|--------------------------------|--------------------------------------------------|
| `magicapply run`               | Full discovery + scoring + auto-apply cycle      |
| `magicapply discover`          | Discover and score jobs only                     |
| `magicapply apply <job-id>`    | Manually apply to a specific job                 |
| `magicapply review`            | Review failed or pending applications            |
| `magicapply config validate`   | Validate configuration files                     |
| `magicapply profiles list`     | List available search profiles                   |
| `magicapply status`            | Show recent application activity                 |

---

## 8. Technology Stack

- **Language**: Python 3.11+
- **CLI Framework**: Typer + Rich
- **Configuration**: Pydantic v2 + PyYAML
- **Browser Automation**: Playwright
- **LLM Integration**: Claude API (primary) + Ollama support
- **Database**: SQLite
- **Testing**: pytest
- **Packaging**: `pyproject.toml`

---

## 9. Configuration Philosophy

MagicApply is designed to be **highly configurable**. The following should live in configuration rather than code:

- Search profiles and their associated base resumes
- Static form answers (email, work authorization, visa status, DEI responses, etc.)
- Scoring thresholds and filters
- Keyword banks
- ATS-specific behavior preferences
- **Router regex tables** — identity / yes-no / DEI / consent / handler-owned patterns live in `src/magicapply/infrastructure/browser/ats/resources/router_rules.yaml` (package default). Operators override by dropping a `router_rules.yaml` into their config root.
- **Rotating proxy pool** — `configs/base_config.yaml::proxies` declares `enabled`, `providers` (fallback list), health-check target + workers, and cooldown. Providers ship as `static_list` (operator-curated YAML) and `free_list_scraper` (community proxy feeds); a `FallbackProvider` wraps the list in first-non-empty priority order. Cloudflare-adjacent adapters (Indeed / Glassdoor) burn dead proxies and requeue blocked queries.
- **Apply throttle caps** — `configs/base_config.yaml::apply_throttle` sets per-ATS hourly + daily caps (`ats_default` + `ats_overrides`) and a `global_cap`. On breach, `ApplyPipeline` defers the application (stays TAILORED) so the next batch handles it when the window rolls.

---

## 10. MVP Scope & Roadmap

### Phase 1 (MVP) — shipped

- Configuration system with multiple search profiles
- Job discovery from LinkedIn (source stubbed; opt-in via `LINKEDIN_LI_AT`) + custom company URLs (JSON-LD)
- Basic deduplication and scoring
- Minimal resume tailoring
- Narrative generation for cover letters
- Form filling for Greenhouse (initial ATS)
- Auto-apply with CAPTCHA fallback and a `--no-submit`/`--yes-submit` safety guard

See `CLAUDE.md` for the current wired state and `docs/GOF_PATTERNS.md` for the pattern mapping. LinkedIn scraping and Ollama implementations remain stubs pending Phase 2.

### Phase 2

- Add Indeed and Glassdoor
- Expand to Lever, Workday, and Ashby
- Keyword bank system
- Improved application tracking
- Optional local web review interface

---

## 11. Open Questions & Decisions

- Should the review step be terminal-based (TUI) or a local web UI by default?
- How aggressively should we pursue job sources beyond LinkedIn initially?
- Should failed applications be retried automatically, or require manual trigger?

---

**End of Document**
```