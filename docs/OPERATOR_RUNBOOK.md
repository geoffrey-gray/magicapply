# MagicApply Operator Runbook

Phase 1 workflow for the config-driven job-application pipeline. Read this after skimming [README.md](../README.md). The authoritative verification plan is [final_dod_plan.md](../final_dod_plan.md).

All commands assume the dev VM unless noted:

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && …'
```

---

## 1. First-time setup

1. Copy `configs/base_config.example.yaml` → `configs/base_config.yaml` (gitignored).
2. Copy `configs/profiles/*.yaml` and `configs/keyword_bank.yaml` as needed.
3. Place your base resume YAML under `resumes/` with `source_docx_path` pointing at your DOCX.
4. Run `uv run magicapply doctor --root configs` — config must load, Chromium must be present.
5. Install browsers once: `uv run playwright install chromium`.

Phase 1 defaults:

- `llm.provider: mock` — no API key; canned LLM responses.
- Cover letters **deferred** (`generate_cover_letter=False` in composition).
- Apply is **dry-run by default** (`--no-submit`).

---

## 2. Daily loop

```bash
# Health check
uv run magicapply doctor --root configs

# Discover new jobs for a profile (sources in profile + base_config)
uv run magicapply discover staff-ds --root configs

# Tailor SCORED applications (keyword bank + in-place DOCX swap)
uv run magicapply tailor staff-ds --root configs

# Apply — dry-run (stops one click before Submit)
uv run magicapply run staff-ds --root configs

# Or one job at a time
uv run magicapply apply <job-id> --root configs --no-submit

# Real submission (operator-gated)
uv run magicapply apply <job-id> --root configs --yes-submit

# Report card (APPLIED splits real vs dry_run)
uv run magicapply status --root configs
```

### Expected discover output

| Situation | Typical output |
|-----------|----------------|
| First run of the day with new postings | `discovered: N`, `scored: N`, `rejected: M` |
| Second run same day | `discovered: 0`, `already_seen: N` |
| Blocked source (Indeed/Glassdoor) | Warning log; 0 jobs from that source |
| Missing ToS ack | `source_errors` lists `MAGICAPPLY_*_ACK` message |

Job IDs come from SQLite (`jobs.id`). `magicapply status` and the discover/score tables show titles; use `sqlite3 data/magicapply.sqlite3 "SELECT id, title FROM jobs ORDER BY discovered_at DESC LIMIT 10;"` when needed.

### Interactive apply (CAPTCHA / manual finish)

```bash
uv run magicapply apply <job-id> --root configs --no-headless --no-submit
```

When the flow lands in `NEEDS_INTERVENTION`, finish in the visible browser, then press Enter at the CLI prompt.

### Retry a failed or dry-run application

```bash
uv run magicapply apply <job-id> --root configs --no-submit --retry
```

---

## 3. Review and promote answer proposals

After each real apply run, screening questions resolved via `narrative` or marked `unhandled` append to `data/answer_proposals.yaml`.

**Workflow:**

1. Open `data/answer_proposals.yaml` after a discover/tailor/apply session.
2. For each proposal, decide the canonical answer you want typed on future forms.
3. Add a verified entry to `configs/answer_library.yaml`:

```yaml
version: 1
answers:
  - question: "Are you willing to participate in on-call rotation?"
    question_regex: null
    canonical_answer: "Yes — I have carried production on-call and am comfortable with rotation."
    seen_on:
      - "ashby:trm-labs:2026-07-07"
    status: verified
```

4. Only `status: verified` entries are used by `AnswerRouter` (library tier).
5. Re-run apply; the library hit skips an LLM call entirely.

Use `question` for exact label match; add `question_regex` when labels vary slightly across employers.

---

## 4. Keyword bank

File: `configs/keyword_bank.yaml` (optional per-profile override: `configs/profiles/<name>-keywords.yaml`).

| Action | When |
|--------|------|
| Add a `term` | JD repeatedly uses a skill you want reflected in tailored bullets |
| Add `synonyms` | Your resume says "microservices" but JDs say "distributed systems" |
| Add `evidence` | Short phrase injected when the term matches — must be truthful |

Tailoring matches bank entries against JD terms from `KeywordExtractor`, then `InPlaceDocxTailorer` swaps synonyms in the source DOCX at run level (formatting preserved).

After editing the bank, re-run `tailor` on SCORED apps (or delete TAILORED rows and re-tailor if already past that state).

---

## 5. Scoring prefilter and static answers

File: `configs/base_config.yaml`

**Prefilter** (`scoring.prefilter`):

- `locations` — substring match on job location (e.g. `Remote`, `United States`).
- `seniority` — substring match on job title.
- `exclude` — reject if keyword appears in title/description.
- `threshold` — LLM score floor after prefilter passes.

**Static answers** (`static_answers`): identity, work authorization booleans, salary, DEI declines, Workday password for guest apply, etc. `AnswerRouter` maps form labels to these fields before narrative/library tiers.

Validate after edits:

```bash
uv run magicapply config validate configs
```

---

## 6. When a form fails or leaves gaps

Every apply run writes an observation bundle:

```
data/observed_forms/<timestamp>-<host-slug>/
  form.yaml      # fields + resolved_strategy per field
  dom.html       # page snapshot at observation time
  screenshot.png # optional
```

**Triage:**

1. Open `form.yaml` — find `resolved_strategy: unhandled` on **required** fields.
2. Check `dom.html` for the real label / widget shape.
3. Fix in order of preference:
   - **Router pattern** — extend `_IDENTITY_PATTERNS` / `_YES_NO_PATTERNS` in `answer_router.py` if it's a common static question.
   - **Answer library** — one-off employer wording → verified entry in `answer_library.yaml`.
   - **Scan label** — Ashby UUID fields, Lever custom questions, Workday widgets → `form_scan.py` or `workday_recipes.py`.
4. Re-run `uv run magicapply apply <job-id> --retry --no-submit`.
5. Promote stable captures into regression fixtures:

```bash
uv run python scripts/promote_w4_captures.py --data-dir data
uv run pytest tests/unit/browser/test_capture_regression.py -q
```

---

## 7. When a source stops returning jobs

| Source | Auth / ack | If blocked |
|--------|------------|------------|
| Greenhouse boards API | none | Check board slug + `title_keywords` |
| `job_url` / career page | none | Confirm JSON-LD on page |
| LinkedIn | `LINKEDIN_LI_AT` + `MAGICAPPLY_LINKEDIN_ACK=1` | Refresh cookie; extend `linkedin.py` parser |
| Indeed | `MAGICAPPLY_INDEED_ACK=1` | Often bot-blocked; logged and skipped |
| Glassdoor | `MAGICAPPLY_GLASSDOOR_ACK=1` | Often Cloudflare-blocked; optional `GLASSDOOR_SESSION` |

Compare fresh HTML against promoted fixtures under `tests/fixtures/captured/`. Offline regression:

```bash
uv run pytest tests/unit/browser/test_capture_regression.py -q
uv run pytest tests/integration/e2e/test_e2e_captured_live.py -q  # slow; Chromium
```

---

## 8. Sources env vars (quick reference)

```bash
# LinkedIn (W.5c — operator provides cookie in ~/magicapply/.env)
LINKEDIN_LI_AT=<paste li_at value>
MAGICAPPLY_LINKEDIN_ACK=1

# Indeed / Glassdoor
MAGICAPPLY_INDEED_ACK=1
MAGICAPPLY_GLASSDOOR_ACK=1
GLASSDOOR_SESSION=<optional session cookie>
```

---

## 9. Testing before you push

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration/e2e/test_e2e_local.py -q   # slow
```

---

## 10. Phase 2 (not in this runbook)

- Flip `llm.provider` to `anthropic` and tighten `scoring.threshold`.
- Re-enable cover letters via `generate_cover_letter=True`.
- Real submissions at scale with operator review gates.
- LinkedIn as primary discovery once `li_at` is wired.

See `final_dod_plan.md` §2 and §4 for scope boundaries.

---

## 11. Custom ATS corpus and 80% gate

The custom (non–big-4) apply stack is measured by a versioned manifest at
[`tests/corpus/custom_ats_manifest.yaml`](../tests/corpus/custom_ats_manifest.yaml).
Each row represents one custom apply URL and its verification status.

### Manifest fields

| Field | Meaning |
|-------|---------|
| `id` | Stable slug (e.g. `symetra-lead-ds-20260707`) |
| `source` | Discovery source (`linkedin-search`, `indeed-search`, `e2e-fixture`, …) |
| `listing_url` | Original listing URL (LinkedIn view page, career page, …) |
| `apply_url` | External apply destination after enrichment |
| `platform` | Sniffed family: `eightfold`, `phenom`, `icims`, `netflix`, `custom_careers`, `generic`, … |
| `capture_dir` | Optional path to promoted fixture (`tests/fixtures/captured/custom-*`) |
| `live_gate` | `true` if the entry participates in the live dry-run test |
| `status` | `pass` / `fail` / `pending` / `skipped` |
| `skip_reason` | Required when `status: skipped` |

### Grow the corpus

```bash
# 1. Discover jobs from all sources — apply URL enrichment runs at discover time.
uv run magicapply discover staff-ds --root configs

# 2. Seed / merge manifest rows for non–big-4 apply URLs. Operator `status`
#    and `capture_dir` on existing rows are preserved.
uv run python scripts/build_custom_ats_corpus.py

#    Preview counts without writing:
uv run python scripts/build_custom_ats_corpus.py --dry-run

# 3. Dry-run apply for a pending entry.
uv run magicapply apply <job-id> --root configs --no-submit

# 4. Promote the observed_forms bundle into a regression fixture.
uv run python scripts/promote_w4_captures.py --data-dir data

# 5. Flip the manifest row to `status: pass` and point `capture_dir` at the
#    new `tests/fixtures/captured/custom-<slug>-<date>/` directory.
```

### Report card

```bash
uv run magicapply custom-ats report --root configs
```

Prints entries grouped by platform and by source, the current offline pass
rate over rows with a real `capture_dir` on disk, and the top unhandled
screening-question labels aggregated from `data/answer_proposals.yaml`.
Exit code `1` when the offline pass rate is below **80%** (see
[`plan.md`](../plan.md) §6).

### The 80% gate

Two enforcement points:

| Where | Kind | Command |
|-------|------|---------|
| CI / `tests/acceptance/` | Offline manifest gate (deterministic) | `uv run pytest tests/acceptance/test_custom_ats_coverage.py -q` |
| Operator ad-hoc | Same rate + top unhandled labels | `uv run magicapply custom-ats report --root configs` |

Both compute `pass / (pass + fail)` over rows whose `capture_dir` resolves to
an existing directory. Pending rows without a capture do **not** count in
the denominator — they represent known custom apply URLs waiting for a live
dry-run.

### Fix order when a row is `fail`

Same triage tree as §6, applied to the specific `capture_dir`:

1. **Router pattern** — extend regex tables in `answer_router.py` (identity /
   yes-no / DEI). Cheapest fix; benefits every ATS.
2. **Answer library** — verified entry in `configs/answer_library.yaml`.
3. **Scan** — extend `form_scan.py` variant detection when a widget slips
   through as `unhandled`.
4. **Recipe** — add or refine a YAML file under `configs/ats_recipes/`
   (platform recipe) or `configs/ats_recipes/employers/<slug>.yaml`
   (employer override). Config over code.
5. **Family handler** — only if ≥3 rows for the same platform share a
   quirk that recipes can't express. Subclass `GenericHandler`; do not
   duplicate the Template Method.

After the fix: re-run apply against the affected `apply_url`, refresh the
capture, run `test_capture_regression.py` + `test_custom_ats_coverage.py`,
flip the manifest row to `pass`.

### Live-gated rows

`live_gate: true` marks rows the operator wants exercised against the real
apply URL. Walk them one at a time with the standard apply command:

```bash
uv run magicapply apply <job-id> --root configs --no-submit
```

Always dry-run in Phase 1. After the run, promote the observed_forms
bundle, flip the manifest row to `pass`, and re-run the acceptance test.
A dedicated live pytest driver is Phase 2. Skipped entries (e.g. LinkedIn
Easy Apply) are excluded from every denominator; document the reason in
`skip_reason`.