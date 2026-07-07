# MagicApply

Config-driven, terminal-first job application automation. Discovers postings across Greenhouse boards, job URLs, LinkedIn, Indeed, and Glassdoor; scores them against a profile; tailors resumes with keyword-bank swaps in your original DOCX; and drives Chromium through Greenhouse, Workday, Lever, and Ashby — **dry-run by default** (stops one click before Submit).

**Operator docs:** [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md) · **Verification plan:** [final_dod_plan.md](final_dod_plan.md)

## Phase 1 status (2026-07)

| Area | Status |
|------|--------|
| Four ATS handlers (GH / WD / Lever / Ashby) | Verified live dry-run, composable forms |
| Discovery sources | Greenhouse API + job URLs; Indeed/Glassdoor documented blocked; LinkedIn needs `li_at` |
| LLM | `mock` provider (no API key) |
| Tailoring | In-place DOCX keyword swap; cover letters deferred |
| Tests | Unit + e2e-smoke + promoted W.4 capture regression |

## Quickstart (dev VM)

See [docs/VM_DEV.md](docs/VM_DEV.md). Typical prelude:

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && uv sync --extra dev'
```

Copy `configs/base_config.example.yaml` → `configs/base_config.yaml` (gitignored) and add profiles under `configs/profiles/`.

```bash
uv run magicapply doctor --root configs
uv run magicapply discover <profile> --root configs
uv run magicapply tailor <profile> --root configs
uv run magicapply run <profile> --root configs          # dry-run (default)
uv run magicapply run <profile> --root configs --yes-submit   # real submit
uv run magicapply status --root configs
```

## Configuration layout

```
configs/
├── base_config.yaml       # llm, scoring, static_answers, sources (gitignored — copy from example)
├── base_config.example.yaml
├── prompts.yaml
├── keyword_bank.yaml
├── answer_library.yaml    # verified screening answers (grows from real runs)
└── profiles/
    └── staff-ds.yaml      # links base_resume + source names

resumes/
└── geoffrey.yaml          # BaseResume + source_docx_path

data/                      # SQLite, tailored/, observed_forms/, answer_proposals.yaml
```

Phase 1 uses `llm.provider: mock` and `InPlaceDocxTailorer` (not docxtpl). Personal configs and `base_config.yaml` stay out of git.

## Safety defaults

- **`--no-submit`** on `apply` and `run` — fills forms, does not click Submit; rows land in `APPLIED` with `dry_run=True`.
- **`--yes-submit`** required for real submission.
- Scraping adapters require explicit ToS ack env vars (`MAGICAPPLY_LINKEDIN_ACK`, `MAGICAPPLY_INDEED_ACK`, `MAGICAPPLY_GLASSDOOR_ACK`).

## Supported ATSes

Greenhouse, Workday, Lever, Ashby — handlers under `src/magicapply/infrastructure/browser/ats/`. Dynamic fields fill via `FormComposer` + `AnswerRouter`. Workday widget steps use recipe schemas in `workday_recipes.py`.

## Testing

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration/e2e/test_e2e_local.py -q              # slow; Chromium
uv run pytest tests/integration/e2e/test_e2e_captured_live.py -q      # W.4 live DOM dry-runs
uv run pytest tests/acceptance/test_dod.py -q                         # four-ATS acceptance
```

Promote a live capture into fixtures: `uv run python scripts/promote_w4_captures.py --data-dir data`

## Documentation map

| Doc | Purpose |
|-----|---------|
| [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md) | Daily loop, answer library, failures, sources |
| [final_dod_plan.md](final_dod_plan.md) | Phase 1 verification plan (W.0–W.8) |
| [CLAUDE.md](CLAUDE.md) | Codebase invariants for contributors / LLMs |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Original design |
| [ARCHITECTURE_COMPOSABLE_FORMS.md](ARCHITECTURE_COMPOSABLE_FORMS.md) | Composable forms stack |
| [docs/VM_DEV.md](docs/VM_DEV.md) | VM setup |
| [docs/GOF_PATTERNS.md](docs/GOF_PATTERNS.md) | Pattern map |

Legacy plans (superseded): `dryrun_plan.md`, `dod_plan.md` → see `final_dod_plan.md`.

## License

MIT. See `LICENSE` when it lands.