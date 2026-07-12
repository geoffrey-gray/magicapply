# MagicApply Operator Runbook

Phase 1 workflow for the config-driven job-application pipeline. Read this after skimming [README.md](../README.md). The authoritative verification plan is [final_dod_plan.md](../final_dod_plan.md).

**Preferred environment:** a local desktop (macOS or Linux GUI) where you can run `uv run magicapply …` and open headed Chromium for `auth login`. An optional Linux dev VM (`docs/VM_DEV.md`) remains supported for headless discover/tailor/apply after auth is bootstrapped.

```bash
# Local (Mac / desktop) — preferred
cd /path/to/magicapply && uv run magicapply …

# Optional Linux VM only
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && …'
```

---

## 1. First-time setup

1. Install toolchain: `uv` + Python 3.11+, then `uv sync` and `uv run playwright install chromium`.
2. Copy `configs/base_config.example.yaml` → `configs/base_config.yaml` (gitignored). Prefer `llm.provider: mock` for Phase 1.
3. Copy `configs/keyword_bank.example.yaml` → `configs/keyword_bank.yaml`; edit profiles under `configs/profiles/`.
4. Place your base resume YAML under `resumes/` with `source_docx_path` pointing at your DOCX (needed for tailor/apply; discover can load with a minimal profile).
5. Copy `.env.example` → `.env`. For LinkedIn set `MAGICAPPLY_LINKEDIN_ACK=1` (do not paste cookies into chat).
6. Run `uv run magicapply doctor --root configs` — config must load, Chromium must be present.

Phase 1 defaults:

- `llm.provider: mock` — no API key; canned LLM responses.
- Cover letters **deferred** (`generate_cover_letter=False` in composition).
- Apply is **dry-run by default** (`--no-submit`).

---

## 1b. Always-on dry-run robot (multi-board)

Operate as a **patient robot**, not a scrape sprint:

- **2–4 dry-run applies/hour** (global throttle `hourly: 4` in operator config)
- **Rotate boards** in 6h windows so one site is not hit 24/7
- **Record every try** (FAILED / NEEDS_INTERVENTION are training data)
- **Do not** chase every job; promote `data/answer_proposals.yaml` → `answer_library.yaml`

**One-time setup**

```bash
# .env
MAGICAPPLY_LINKEDIN_ACK=1
MAGICAPPLY_INDEED_ACK=1
# Glassdoor deferred (browser "not secure" / login blocked). Re-enable later:
# MAGICAPPLY_GLASSDOOR_ACK=1

# Headed login on Mac (re-run when a board goes dark)
uv run magicapply auth login linkedin  --root configs --force --auto-save --timeout 600
uv run magicapply auth login indeed    --root configs --force --auto-save --timeout 600
# uv run magicapply auth login glassdoor --root configs --force --auto-save --timeout 600
uv run magicapply auth status --root configs
```

**Profiles (rotation)** — Glassdoor source is `enabled: false` in `base_config.yaml` for now.

| Profile | Discovery sources | UTC window (robot) |
|---------|-------------------|--------------------|
| `dryrun-li-gh` | LinkedIn + Greenhouse + career pages | 00–06 |
| `dryrun-indeed` | Indeed + Greenhouse + career pages | 06–12 |
| `multi-dryrun` | LinkedIn + Indeed + Greenhouse + careers | 12–18 |
| `dryrun-ats-only` | Greenhouse + career / job_url only | 18–24 |
| `dryrun-glassdoor` | (legacy; currently Indeed-only fallback) | unused |

**Tick script** (every 15 minutes → ≤4 applies/hour):

```bash
chmod +x scripts/robot_dryrun_tick.sh
# cron:
# */15 * * * * cd /path/to/magicapply && ./scripts/robot_dryrun_tick.sh >> data/robot.log 2>&1

# single tick:
./scripts/robot_dryrun_tick.sh
```

Always uses `--no-submit`. Never pass `--yes-submit` until you have ~150–200 dry-runs and a grown answer library.

**Daily human (~10 min):** `magicapply status`, re-auth any `resolved=none` board, promote a few proposals.

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
| LinkedIn | `MAGICAPPLY_LINKEDIN_ACK=1` + `auth login` (or `LINKEDIN_SESSION_COOKIES` / `LINKEDIN_LI_AT`) | Re-run `auth login` / refresh cookies; check parser if HTML drifted |
| Indeed | `MAGICAPPLY_INDEED_ACK=1` | Often bot-blocked; logged and skipped |
| Glassdoor | `MAGICAPPLY_GLASSDOOR_ACK=1` | Often Cloudflare-blocked; optional `GLASSDOOR_SESSION` |

Compare fresh HTML against promoted fixtures under `tests/fixtures/captured/`. Offline regression:

```bash
uv run pytest tests/unit/browser/test_capture_regression.py -q
uv run pytest tests/integration/e2e/test_e2e_captured_live.py -q  # slow; Chromium
```

---

## 8. Auth login (recommended) + env fallbacks

### 8.1 Mac / local desktop (preferred)

Log in once with headed Chromium — no DevTools cookie dump, no VM tunnel:

```bash
# Opens Chromium on your desktop — log in (+ 2FA), then press Enter
# (or use --auto-save to write when the session cookie appears).
uv run magicapply auth login linkedin --root configs --force --auto-save --timeout 600
uv run magicapply auth login indeed --root configs    # optional
uv run magicapply auth login glassdoor --root configs # optional

uv run magicapply auth status --root configs   # no secret values shown
uv run magicapply auth sites                   # registry of known sites
uv run magicapply auth clear linkedin --root configs
```

Expect `linkedin` → `resolved=storage_state`. Sessions save under
`data/auth/<site>_storage_state.json` (gitignored via `data/`). Discover prefers
storage_state, then Cookie env, then legacy single cookies.

After auth works, limited discover:

```bash
# base_config: linkedin source enabled, max_pages: 1–2, rate_limit_per_minute: 3
uv run magicapply discover <profile> --root configs
```

### 8.2 Linux headless / optional VM notes

On Linux without a display, headed login raises unless `DISPLAY` /
`WAYLAND_DISPLAY` is set — use env cookies (§8.3) or run `auth login` on a
desktop Mac/Linux GUI and copy `data/auth/*_storage_state.json` into the
headless host’s `data/auth/`.

**VM → laptop GUI (libvirt guest):** the domain has no SPICE/VNC device. Use
guest **Xvfb + x11vnc** (full recipe in [VM_DEV.md](VM_DEV.md) § Headed GUI stream):

```bash
# Guest (once per boot)
ssh magicapply-dev 'bash ~/magicapply/scripts/dev_vnc_up.sh'

# Host laptop
ssh -fN -L 5901:127.0.0.1:5901 magicapply-dev
vncviewer 127.0.0.1:5901   # or Remmina / TigerVNC

# Guest — keep this SSH session open for the whole login
ssh magicapply-dev
export DISPLAY=:1 PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy
cd ~/magicapply
uv run magicapply auth login linkedin --root configs --force --auto-save --timeout 900
```

**Easier alternative:** run `auth login` on a real desktop (Mac) and copy
`data/auth/*_storage_state.json` into the VM. Cookie env fallbacks below still work.

Do **not** rely on host X11 reverse tunnels into the guest — paint is flaky.
Hosted Chromium already uses software GL (`--use-gl=swiftshader`) for Xvfb.

**Fallback env vars** (paste Cookie header only into `.env`, never into chat):

```bash
# LinkedIn
LINKEDIN_SESSION_COOKIES=<full Cookie header from linkedin.com request>
LINKEDIN_LI_AT=<li_at only — weaker fallback>
MAGICAPPLY_LINKEDIN_ACK=1

# Indeed / Glassdoor
MAGICAPPLY_INDEED_ACK=1
MAGICAPPLY_GLASSDOOR_ACK=1
INDEED_SESSION_COOKIES=<full Cookie header>
GLASSDOOR_SESSION_COOKIES=<full Cookie header>
GLASSDOOR_SESSION=<optional legacy gdSession>
```

Adding a new auth site later: register it in
`infrastructure/browser/auth_sites.py` and call `resolve_session_auth` from
that adapter — `magicapply auth login <name>` works automatically.

### Safe discovery cadence (LinkedIn / Indeed / Glassdoor)

**Do not** run continuous multi-page scrapes or raise caps aggressively. Goal is
a **drip**, not 600 jobs in one shot (ban risk).

**Shared posture (all three boards):**

1. **SERP first** — title, company, location, listing URL, offsite apply if present  
2. **Offsite** — full JD from destination ATS/career page when external `apply_url` exists  
3. **Capped board detail** — visit listing/detail only when apply URL or description is still incomplete  

| Field | Safe default | Meaning |
|-------|--------------|---------|
| `remote_only` | `true` | Remote workplace filter |
| `posted_within_days` | `7` | Past-week style date filter |
| `max_pages` | `1–2` for smoke; `3–6` LI / `5` Indeed/GD max | How deep to paginate **this run** looking for new postings |
| `rate_limit_per_minute` | `3` | Slow serial pacing (+ jitter) |
| `enrich_descriptions` | `true` | Prefer JD from **destination** ATS/career page |
| `enrich_apply_urls` | `true` | Allow board detail to resolve missing offsite apply |
| `board_detail_fallback` (Indeed/GD) / `linkedin_description_fallback` (LI) | `true` | Board listing detail when offsite incomplete |
| `max_board_detail_fetches` (Indeed/GD) / `max_linkedin_detail_fetches` (LI) | `8` | Cap on board detail visits **per discover run** |
| `require_external_apply` | `false` | Keep board-native-only jobs; apply falls back to listing URL via GenericHandler. Set `true` to drop them. |
| `max_jobs_per_run` | `30–50` | Max **new** jobs to add this run (skips corpus IDs while paging) |
| `page_dwell_ms_*` / `pause_*` | LinkedIn only | Human-like dwell + every-N-page pause |

**Cadence:** run `magicapply discover` **1–2× per day**, not in a loop. On
auth walls / redirect storms, **stop and refresh session cookies** — do not
bump `max_pages`. Raise caps only after clean multi-page runs.

Adapters paginate serially until empty page, circuit-break, or caps.
**Prefilter still runs after discover** — second funnel for fit.

### Apply order (paced `run`)

Candidates are ordered by **score (JD fit) high → low**, not by board brand.
`ApplyThrottle` (per-ATS + global) decides whether that destination may fire
**now**. On deny, the pipeline **skips** to the next-best job whose bucket
still has quota (e.g. LinkedIn cap hit → try Greenhouse). Pace sleep runs
only after a counted outcome (applied / failed / needs_intervention).

**Board fallback:** If there is no external `apply_url`, apply still targets
the Indeed/LinkedIn listing via **GenericHandler**. Throttle buckets are
`indeed` / `linkedin` (not a single generic pile). Prefer offsite ATS when
enrich finds it; board apply is the throttled fallback, not skipped.

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

---

## 12. Proxy rotation and application throttle

Two guardrails introduced together on the `proxy_rotation` branch:

### 12.1 Rotating proxy pool (Cloudflare defeat on Indeed / Glassdoor)

Enable in `configs/base_config.yaml`:

```yaml
proxies:
  enabled: true
  providers:
    - type: free_list_scraper
      sources: []          # empty = use the shipped GitHub feeds
    - type: static_list
      entries: []          # your hand-verified proxies, if any
```

Defaults:

- Free-list feeds shipped: `TheSpeedX/PROXY-List`, `roosterkid/openproxylist`,
  `monosans/proxy-list`, `proxyscrape` free tier (US).
- Health check: HEAD `https://httpbin.org/ip` (5 s timeout, 20 parallel).
- Cooldown: burned proxies stay out 15 min.

Providers are walked in first-non-empty fallback order. Add a `vps_pool`
or `commercial` provider later without any code change — just prepend
the entry.

**Retry-on-block:** Indeed and Glassdoor requeue blocked queries with a
fresh proxy (up to `max_query_retries = 3`). The pool goes from
~20–40 % per-proxy success rate to ~90 %+ per-query success across the
rotation. Both adapters bump `RateLimiter.jitter_ratio` to 0.3 so
request timing isn't rhythmically identical.

**Live probe:**

```bash
ssh magicapply-dev '… uv run magicapply discover staff-ds --root configs'
```

Expected shape when proxies work:

```
discovered: N (mix across LinkedIn / Indeed / Glassdoor / greenhouse-boards)
duplicates: M
already seen: K
scored: N
rejected: R
```

No `bot protection` warnings for Indeed/Glassdoor. If they show up
anyway, the free lists were dry that day — either bump
`refresh_interval_minutes` or add hand-curated entries to `static_list`.

### 12.2 Apply throttle

Prevents flooding any single ATS. Config:

```yaml
apply_throttle:
  ats_default:      {hourly: 6, daily: 25}
  ats_overrides:
    workday:        {hourly: 4, daily: 20}
  global_cap:       {hourly: 15, daily: 60}
```

On a cap breach, the application stays in TAILORED — the next batch
handles it when the window rolls. No manual intervention. Real
submissions and dry-runs both count against the cap because both
generate ATS traffic. Verify with:

```bash
ssh magicapply-dev '… uv run magicapply run staff-ds --root configs --no-submit'
```

Deferred rows show up as `TAILORED` in `magicapply status`; the deferral
reason is recorded in the Application error field.

### 12.3 Fair cross-source dedup

Cross-source collisions on `dedup_key = f"{company}::{title}"` now pick
the source with the fewest APPLIED rows in the last 24 h. That
distributes future apply attempts across LinkedIn / Indeed / Glassdoor
/ greenhouse-boards. No config knob — always on when a `SqlApplications
Repository` is present (i.e. every production discover run).

---

## 13. Session cookies for Indeed and Glassdoor

Free proxies are 0% effective against Cloudflare on Indeed and Glassdoor
(empirically verified: 40-sample probe across 6 free lists, 0 hits per
target — see commit `9d28bb5`). Authenticated session cookies are the
free path that actually works: Cloudflare mostly skips logged-in traffic
because their bot detection is calibrated for anonymous visitors. This
mirrors what `LINKEDIN_LI_AT` already does for LinkedIn.

### 13.1 Extract cookies from your browser

1. Log into `indeed.com` (and/or `glassdoor.com`) in your regular
   browser. Not incognito — you want a stable, long-lived session.
2. Open DevTools (F12) → **Network** tab → reload the page → click any
   request that hits the target domain (e.g. `indeed.com/jobs`).
3. In the request's **Headers** panel, scroll to **Request Headers**,
   find the `Cookie:` line, and copy the whole value after `Cookie: `.
   That's the semicolon-separated `name=value` string you want.

Alternative: DevTools → **Application** tab → **Cookies** → pick the
domain → copy each `Name=Value` pair by hand. Slower but works when the
Network tab is empty.

### 13.2 Paste into `.env`

```bash
# .env  (host-side, loaded automatically at CLI startup)
MAGICAPPLY_INDEED_ACK=1
INDEED_SESSION_COOKIES=CTK=abc123; PPID=def456; INDEED_CSRF_TOKEN=xyz; SURF=...

MAGICAPPLY_GLASSDOOR_ACK=1
GLASSDOOR_SESSION_COOKIES=gdSession=xyz; ipc=abc; ...
```

**What cookies matter:** for Indeed the primary tokens are `CTK`, `PPID`,
`INDEED_CSRF_TOKEN`, and `SURF` — but you can safely paste the entire
`Cookie:` header value; the server ignores what it doesn't recognise.
For Glassdoor `gdSession` alone often works; use the multi-cookie env
var when the single-cookie fallback isn't enough.

Legacy `GLASSDOOR_SESSION=<gdSession_value>` still works for backwards
compat — the two Glassdoor env vars combine when both are set.

### 13.3 Verify

```bash
ssh magicapply-dev '… uv run magicapply discover staff-ds --root configs'
```

Expected: jobs from `indeed-search` and `glassdoor-search` in addition
to LinkedIn. No `bot protection` warnings. Confirm with:

```bash
sqlite3 data/magicapply.sqlite3 \
  "SELECT source_name, count(*) FROM jobs
   WHERE datetime(discovered_at) > datetime('now', '-10 minutes')
   GROUP BY source_name;"
```

Each of the three sources should contribute ≥ 1 fresh row.

### 13.4 When cookies expire

Auth cookies typically live 1–30 days depending on the site's "keep me
signed in" behavior. When `discover` starts logging `bot protection`
warnings again, log in via browser, extract fresh cookies, replace the
`.env` values. That's the whole refresh cycle — no cronjob, no
automation, just occasional re-paste.

### 13.5 Cookies × proxies

Cookies win. When either `INDEED_SESSION_COOKIES` /
`GLASSDOOR_SESSION_COOKIES` / `GLASSDOOR_SESSION` is set, the adapter
routes every fetch through the shared browser context and ignores the
proxy pool for that source. Per-proxy contexts are anonymous by design
and would break the authenticated session; also, "same account from
multiple IPs" is itself a bot-detection signal. The proxy pool stays
active for any source without cookies (e.g. it's still available for
LinkedIn if you ever wanted it, though `LINKEDIN_LI_AT` follows the
same skip-pool rule).