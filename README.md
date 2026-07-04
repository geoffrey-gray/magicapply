# MagicApply

Config-driven, terminal-first job application automation. Discovers postings across LinkedIn / Indeed / Glassdoor / custom career pages, scores them against a personal profile, tailors resumes and cover letters with an LLM, and drives real Chromium through the top four ATS forms (Greenhouse, Workday, Lever, Ashby) — stopping one click short of Submit by default so the operator stays in control.

Not affiliated with any of the ATSes or job boards it interacts with. Personal-use tool; scraping LinkedIn / Indeed / Glassdoor requires explicit acknowledgement env vars because their ToS forbids it.

## Status

Phase 1 shipped end-to-end. `git log --oneline` walks the history from ARCHITECTURE.md through the dry-run plan (`dryrun_plan.md`, Phases A–J) and the definition-of-done maturation (`dod_plan.md`, Phases K–U).

**Sources:** career pages / JSON-LD job URLs / LinkedIn / Indeed / Glassdoor.
**ATSes:** Greenhouse / Workday / Lever / Ashby.
**LLM:** Anthropic (production); `mock` (stub for dry runs); `replay` (SHA-keyed YAML fixtures for regenerable goldens); Ollama stub.

## Quickstart

MagicApply is developed and run inside a dedicated libvirt VM. See `docs/VM_DEV.md` for the VM setup; the loop is:

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; \
  cd ~/magicapply && uv sync --extra dev'
```

### Configuration surface

Copy the example config layout and fill in the personal bits:

```
configs/
├── base_config.yaml          # llm / scoring / static_answers / sources
├── prompts.yaml              # LLM instructions per task (ships filled in)
├── keyword_bank.yaml         # optional; personal verified skills
├── resume_template.docx      # ships filled in; edit to change resume style
└── profiles/
    └── senior-swe.yaml       # one file per profile; each linked to a resume

resumes/
└── senior-swe.yaml           # BaseResume in YAML (name, summary, experience, skills)

data/                          # created on first run; SQLite + tailored artifacts
```

`base_config.yaml` minimum:

```yaml
version: 1
llm:
  provider: anthropic          # or mock (no API key needed) / replay / ollama
scoring:
  threshold: 70
  prefilter:
    locations: ["Remote", "Boston"]
    seniority: ["senior", "staff"]
static_answers:
  full_name: Jane Doe
  email: jane@example.com
  phone: "555-0100"
  linkedin_url: https://linkedin.com/in/jane
  authorized_to_work_us: true
  needs_sponsorship_us: false
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: career_page
    name: acme-careers
    urls: ["https://boards.greenhouse.io/anthropic"]
```

`profiles/senior-swe.yaml` minimum:

```yaml
name: senior-swe
base_resume: senior-swe.yaml
sources: [acme-careers]
apply:
  narrative_style: concise
```

`keyword_bank.yaml` (optional but recommended — powers evidence-based bullet injection):

```yaml
version: 1
keywords:
  - term: distributed systems
    synonyms: [microservices, SOA]
    evidence: "Led migration to microservices at Acme, cutting p99 latency 40%"
  - term: python
    evidence: "8 years, primary language across the platform team"
```

### Daily loop

```bash
# What's installed / configured
uv run magicapply doctor --root configs

# One-off: pull + score + tailor + apply for one profile
uv run magicapply run senior-swe                  # dry-run (safe default)
uv run magicapply run senior-swe --yes-submit     # actually submits

# Break it up
uv run magicapply discover senior-swe
uv run magicapply tailor senior-swe
uv run magicapply apply <job-id>                  # dry-run against one job
uv run magicapply apply <job-id> --yes-submit
uv run magicapply apply <job-id> --retry          # re-run a FAILED application

# Report card
uv run magicapply status
uv run magicapply status review                   # NEEDS_INTERVENTION / FAILED list

# Interactive intervention when a CAPTCHA / weird form blocks the flow
uv run magicapply apply <job-id> --no-headless    # visible browser + prompt
```

## Safety design

Two defaults matter:

- **`--no-submit`** is the default on both `apply` and `run`. The flow drives real Chromium through every step (identity fields, screening questions, resume upload) but stops before the final Submit click. Applications land in `APPLIED` state with `dry_run=True` so `status` can split real submissions from dry runs at display time.
- **`--yes-submit`** is required for a real click. Belt-and-braces: even under `--yes-submit`, the `BaseATSHandler.apply` template method's CAPTCHA and dry-run guards inherit for every ATS handler — a Workday or Ashby handler cannot accidentally submit past a CAPTCHA.

Scraping the three big job boards uses per-source ToS acks:

| Source | Auth | Ack env var |
|---|---|---|
| Career pages / job URLs | none | — |
| LinkedIn | `LINKEDIN_LI_AT` | `MAGICAPPLY_LINKEDIN_ACK=1` |
| Indeed | none | `MAGICAPPLY_INDEED_ACK=1` |
| Glassdoor | opt: `GLASSDOOR_SESSION` | `MAGICAPPLY_GLASSDOOR_ACK=1` |

Missing any ack means the adapter refuses to run with a clear `SourceError`.

## Supported ATSes

| ATS | Match hosts | Form shape | Notes |
|---|---|---|---|
| Greenhouse | `greenhouse.io`, `boards.greenhouse.io`, `job-boards.greenhouse.io` | Single-page `#id` selectors | Per-role custom fields resolved via `AnswerRouter` — screening questions dispatch to `NarrativeEngine.answer` |
| Workday | `myworkdayjobs.com`, `.myworkday.com` | Multi-step wizard, `data-automation-id` | Fast-fail candidate-selector loops so missing selectors don't burn Playwright's 30 s default timeout |
| Lever | `jobs.lever.co`, `lever.co` | Single-page, standard HTML input names | Cleanest of the four; full name in one `input[name='name']` field |
| Ashby | `jobs.ashbyhq.com` | Single-page, `_systemfield_*` input names | Real Ashby uses drop-zone widgets; `set_input_files` still targets the underlying `<input type="file">` |

Adding a fifth is an `infrastructure/browser/ats/<name>.py` subclass of `BaseATSHandler` + one line in `factory.py`. The template method handles CAPTCHA + dry-run for free.

## Providers

- **`anthropic`** — production LLM via `anthropic` SDK. Requires `ANTHROPIC_API_KEY`. Prompt caching enabled by default on scoring / tailoring / narrative.
- **`mock`** — shape-aware stub. Detects which of the four prompts the caller sent (scoring / summary / cover letter / screening answer / keyword extraction / bullet rewrite) and returns a plausible response. No API key needed. Powers the entire dry-run test harness.
- **`replay`** — SHA-256-keyed YAML fixtures under `configs/llm-fixtures/`. Missing key falls through to the `mock` fallback client. `MAGICAPPLY_LLM_RECORD=1` records new fixtures. Deterministic; good for goldens.
- **`ollama`** — stub; Phase 2 roadmap.

## Where things live

```
src/magicapply/
├── cli/            # Typer commands + composition root
├── config/         # Pydantic models + YAML loader
├── domain/
│   ├── jobs/       # Prefilter + LLMScorer + JobScorer
│   ├── keywords/   # KeywordExtractor + match_bank
│   ├── models/     # Job / Application / BaseResume / TailoredResume
│   ├── repositories.py   # Repository Protocols
│   └── resumes/    # Tailorer (summary + bullet injection) + NarrativeEngine
├── infrastructure/
│   ├── browser/
│   │   ├── ats/    # BaseATSHandler + Greenhouse/Workday/Lever/Ashby + form_scan + answer_router
│   │   └── session.py    # PlaywrightSession context manager
│   ├── llm/        # LLMClient Protocol + Anthropic/Ollama/Mock/Replay providers
│   ├── persistence/      # SQLModel tables + SqlJobsRepository + SqlApplicationsRepository
│   ├── rendering/  # DocxResumeRenderer (docxtpl)
│   └── sources/    # CareerPageAdapter, JobUrlAdapter, LinkedInAdapter, IndeedAdapter, GlassdoorAdapter
└── pipelines/      # DiscoveryPipeline / TailoringPipeline / ApplyPipeline
```

## Testing

```bash
# Unit + fast integration (default). ~24 s.
uv run pytest tests -q

# Live-gated integration (network-hitting; opt-in per source).
MAGICAPPLY_LIVE_TESTS=1 ANTHROPIC_API_KEY=... \
  MAGICAPPLY_LINKEDIN_TESTS=1 LINKEDIN_LI_AT=... MAGICAPPLY_LINKEDIN_ACK=1 \
  MAGICAPPLY_INDEED_ACK=1 MAGICAPPLY_GLASSDOOR_ACK=1 \
  uv run pytest tests

# Regenerate tailoring goldens after intentional prompt changes.
MAGICAPPLY_UPDATE_GOLDENS=1 \
  uv run pytest tests/integration/e2e/test_e2e_local.py::TestE2EDryRun -q
```

## Docs

- `CLAUDE.md` — invariants and layered architecture for anyone (or any LLM) working on the codebase.
- `ARCHITECTURE.md` — original design doc.
- `docs/VM_DEV.md` — dev VM setup, env vars, virtiofs quirks.
- `docs/GOF_PATTERNS.md` — pattern mapping (Strategy / Template Method / Repository / …) and the "extend, don't multiply" meta principle.
- `dryrun_plan.md` — Phase A–J dry-run harness, post-facto reference with commit shas.
- `dod_plan.md` — Phase K–U DoD maturation plan.

## License

MIT. See `LICENSE` when it lands.
