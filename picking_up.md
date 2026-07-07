# Picking up MagicApply

**Written:** 2026-07-07  
**Audience:** You (or another AI assistant) resuming work after a context switch.

This is the practical “how to run everything” guide. Architecture and the full Custom ATS roadmap live in [`plan.md`](plan.md). Operator workflow details live in [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md). VM topology lives in [`docs/VM_DEV.md`](docs/VM_DEV.md) and [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md).

---

## 1. Where the repo lives

| Location | Path |
|----------|------|
| Host (NixOS) | `/mnt/storage/VMs/dev/magicapply` |
| Symlink | `~/src/magicapply` → same directory |
| Inside dev VM | `~/magicapply` (virtiofs mount of the host path) |

**Branch:** `feature/composable-forms-refactor`

**Latest commits (Custom ATS stack):**

| PR | Commit | What shipped |
|----|--------|--------------|
| PR1 | `14aafab` | `Job.apply_url` + DB column + handler routing |
| PR2 | `eaf293e` | LinkedIn apply-url enrichment + platform sniff |
| PR3 | `93a96be` | `GenericHandler` catch-all |
| PR4 | `514e759` | YAML `ats_recipes` + wizard loop + Eightfold |
| PR5 | `fd07252` | Indeed/Glassdoor/job_url enrichment + corpus builder |
| PR6 | `791c004` | Phenom, iCIMS, custom_careers, Netflix recipes + fixtures |
| PR7 | HEAD | 80% gate manifest + acceptance test + `custom-ats report` CLI |

**Uncommitted / untracked right now:** `plan.md`, `picking_up.md`, `scratch/` (do not commit `scratch/`).

---

## 2. The one rule that prevents 90% of failures

**Python, pytest, magicapply, and Playwright run inside the VM — not on the host.**

Git and file edits happen on the host. The VM sees host edits immediately (virtiofs).

### Non-interactive SSH prelude (required)

Non-interactive SSH does not load `~/.bashrc`, so `uv` is not on `PATH`. Also set `UV_LINK_MODE=copy` (virtiofs + local uv cache).

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && <command>'
```

### Interactive shell (optional)

```bash
ssh magicapply-dev
cd ~/magicapply
export PATH=$HOME/.local/bin:$PATH
```

---

## 3. First-time / health check

Run inside the VM:

```bash
# Sync deps (safe to repeat)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && uv sync --extra dev'

# Install Chromium once if doctor says MISSING
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run playwright install chromium'

# Config + Chromium check
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply doctor --root configs'

# Validate YAML configs
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply config validate configs'
```

**Config setup (if missing):** copy `configs/base_config.example.yaml` → `configs/base_config.yaml` (gitignored). Profile example: `configs/profiles/staff-ds.yaml`.

**Phase 1 defaults:** `llm.provider: mock` (no API key), apply is dry-run by default (`--no-submit`).

---

## 4. Running tests

All via VM + `uv run pytest`.

### Fast unit suite (~2 min, no network, no Chromium)

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit -q'
```

**Last known result:** 546 passed, 10 skipped.

### Targeted suites

```bash
# Capture regression (offline DOM fixtures)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit/browser/test_capture_regression.py -q'

# GenericHandler + recipes
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit/browser/test_generic_handler.py tests/unit/config/test_ats_recipes.py -q'

# Apply URL enrichment
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit/sources/test_apply_url.py tests/unit/corpus/test_custom_ats_manifest.py -q'

# Hermetic E2E (slow; threaded HTTP + Chromium)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/integration/e2e/test_e2e_local.py -q'

# Captured live DOMs (slow; Chromium against tests/fixtures/captured/)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/integration/e2e/test_e2e_captured_live.py -q'
```

### Live-gated tests (opt-in)

```bash
# Real configs, always dry-run — never submits
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  MAGICAPPLY_LIVE_TESTS=1 MAGICAPPLY_LIVE_APPLY=1 MAGICAPPLY_LIVE_APPLY_PROFILE=staff-ds \
  uv run pytest tests/integration/e2e/test_e2e_live.py -s'

# Network / Anthropic integration
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  MAGICAPPLY_LIVE_TESTS=1 uv run pytest tests/integration -q'
```

### Regenerate tailoring goldens (only when intentionally changing output)

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  MAGICAPPLY_UPDATE_GOLDENS=1 uv run pytest tests/integration/e2e/test_e2e_local.py::TestE2EDryRun -q'
```

---

## 5. Running the pipeline (operator loop)

All commands use `--root configs`. Replace `staff-ds` with your profile name.

```bash
# 1. Discover jobs from configured sources
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply discover staff-ds --root configs'

# 2. Tailor SCORED applications (DOCX in-place swap)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply tailor staff-ds --root configs'

# 3. Full pipeline in one Chromium session (discover → tailor → apply batch)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply run staff-ds --root configs'

# 4. Apply one job (dry-run — stops one click before Submit)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply apply <job-id> --root configs --no-submit'

# 5. Retry a failed/dry-run application
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply apply <job-id> --root configs --no-submit --retry'

# 6. Status / review
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply status --root configs'

ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply review --root configs'
```

### Finding job IDs

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  sqlite3 data/magicapply.sqlite3 "SELECT id, title, apply_url FROM jobs ORDER BY discovered_at DESC LIMIT 10;"'
```

### Real submission (operator-gated only)

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run magicapply apply <job-id> --root configs --yes-submit'
```

Phase 1 norm is `--no-submit`. Do not batch real submissions.

---

## 6. Custom ATS work — what’s done vs what’s next

**Goal:** 80% of non–big-4 apply URLs pass dry-run apply with zero required `unhandled` fields. See [`plan.md`](plan.md).

### Shipped (PR1–PR6)

- **`Job.apply_url`** separate from listing `url` — handler routes on `apply_url or url`.
- **Apply URL enrichment** at discovery (LinkedIn safety-go decode, Indeed/Glassdoor/job_url parsers).
- **`GenericHandler`** catch-all registered last in `ATSHandlerFactory`.
- **YAML recipes** in `configs/ats_recipes/` (Eightfold, Phenom, iCIMS, custom_careers, `employers/netflix.yaml`).
- **Offline fixtures** under `tests/fixtures/captured/custom-*-e2e-smoke-20260707/`.
- **Corpus builder** script (seeds manifest from SQLite after discover).

### PR7 — shipped

1. [`tests/corpus/custom_ats_manifest.yaml`](tests/corpus/custom_ats_manifest.yaml) seeded with 10 entries.
2. [`tests/acceptance/test_custom_ats_coverage.py`](tests/acceptance/test_custom_ats_coverage.py) — 80% offline-eligible gate + corpus-hygiene assertions.
3. `magicapply custom-ats report --root configs` — platform/source tables, offline pass rate, top unhandled labels; exits 1 below gate.
4. [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) §11 covers corpus growth + fix order.
5. Live pytest driver deferred; operator drives live_gate rows via `magicapply apply <job-id> --no-submit`.

### Custom ATS commands that work today

```bash
# Seed manifest from discovered jobs (after discover)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run python scripts/build_custom_ats_corpus.py'

# Dry-run only — see counts without writing
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run python scripts/build_custom_ats_corpus.py --dry-run'

# Promote observed forms to regression fixtures (after a real apply run)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && \
  uv run python scripts/promote_w4_captures.py --data-dir data'
```

### Custom ATS key files

| File | Role |
|------|------|
| `src/magicapply/infrastructure/sources/apply_url.py` | Enrichment + platform sniff |
| `src/magicapply/infrastructure/browser/ats/generic.py` | Catch-all handler |
| `src/magicapply/infrastructure/browser/ats/generic_wizard.py` | Multi-step Next/Continue |
| `src/magicapply/config/ats_recipes.py` | Recipe loader |
| `configs/ats_recipes/*.yaml` | Platform behavior (config over code) |
| `src/magicapply/infrastructure/corpus/custom_ats.py` | Manifest helpers |
| `tests/corpus/custom_ats_manifest.yaml` | Corpus + CI gate (PR7) |
| `scripts/build_custom_ats_corpus.py` | Manifest seeder |

### Offline custom fixtures (for regression)

- `custom-e2e-smoke-20260707` — generic single-page
- `custom-eightfold-wizard-20260707` — Eightfold wizard
- `custom-phenom-e2e-smoke-20260707`
- `custom-icims-e2e-smoke-20260707`
- `custom-netflix-e2e-smoke-20260707`

---

## 7. Environment variables

Put secrets in `~/magicapply/.env` on the VM (loaded at CLI startup).

```bash
# LinkedIn discovery (W.5c)
LINKEDIN_LI_AT=<cookie value>
MAGICAPPLY_LINKEDIN_ACK=1

# Indeed / Glassdoor (often bot-blocked; adapter logs+skips)
MAGICAPPLY_INDEED_ACK=1
MAGICAPPLY_GLASSDOOR_ACK=1
GLASSDOOR_SESSION=<optional>

# Live test gates
MAGICAPPLY_LIVE_TESTS=1
MAGICAPPLY_LIVE_APPLY=1
MAGICAPPLY_LIVE_APPLY_PROFILE=staff-ds

# Future PR7 live custom-ATS gate (not wired yet)
# MAGICAPPLY_CUSTOM_ATS_LIVE=1
```

---

## 8. After an apply run — triage artifacts

Every apply writes observation bundles:

```
data/observed_forms/<timestamp>-<host-slug>/
  form.yaml       # fields + resolved_strategy
  dom.html        # page snapshot
  screenshot.png  # optional
```

**Fix order:** router pattern → `configs/answer_library.yaml` → scan/recipe → `configs/ats_recipes/`.

Unhandled screening questions also append to `data/answer_proposals.yaml` for operator review.

---

## 9. Git workflow

**On the host** (not via VM):

```bash
cd /mnt/storage/VMs/dev/magicapply   # or ~/src/magicapply

git status
git diff
git add <files>
git commit -m "PR7: ..."
```

Commit style so far: `PRn: <short description>`.

**Before committing PR7:** run unit suite on VM, then commit `plan.md` and `picking_up.md` if you want them tracked.

---

## 10. Hard operator constraints (do not violate)

1. **Never bypass the pipeline** — no hand-seeding jobs, no “skip discover for now.” Fix the adapter/handler.
2. **Dry-run by default** — `--no-submit` for Phase 1 verification.
3. **Config over code** — new ATS behavior goes in `configs/ats_recipes/`, not hardcoded selectors in handlers.
4. **Extend, don’t multiply** — add kwargs/methods/recipes before new sibling classes.
5. **One job at a time** through the full pipeline when doing live verification.
6. **Run pytest/magicapply in the VM** — host Python/Playwright are wrong.

---

## 11. Quick “resume PR7” checklist

1. Read [`plan.md`](plan.md) § PR7.
2. `ssh magicapply-dev` + `uv run pytest tests/unit -q` — confirm green.
3. Populate `tests/corpus/custom_ats_manifest.yaml` (map offline fixtures to `status: pass`, add LinkedIn-derived rows as `pending`).
4. Implement `tests/acceptance/test_custom_ats_coverage.py`.
5. Add `src/magicapply/cli/commands/custom_ats.py` + wire in `cli/main.py`.
6. Add runbook section §11 (custom ATS corpus + 80% gate).
7. Run tests, commit as `PR7: …`.

---

## 12. Other load-bearing docs

| Doc | When to read |
|-----|--------------|
| [`CLAUDE.md`](CLAUDE.md) | Codebase guardrails + test commands |
| [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md) | VM routing + operator hard rules |
| [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) | Daily operator workflow |
| [`final_dod_plan.md`](final_dod_plan.md) | Phase 1 DoD status |
| [`plan.md`](plan.md) | Custom ATS 80% plan + PR breakdown |