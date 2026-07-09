# Handoff 3 — Scoring, volume discovery, auth login, operator next steps

**Written:** 2026-07-09  
**Author:** Grok (session ending; handing off to next assistant)  
**Audience:** The next AI resuming MagicApply work.

Complementary to [`handoff_2.md`](handoff_2.md) (Indeed/Glassdoor search-page extractors — shipped), [`picking_up.md`](picking_up.md), [`CLAUDE.md`](CLAUDE.md). Read [`docs/VM_DEV.md`](docs/VM_DEV.md) if you have never SSHed into `magicapply-dev`. Operator workflow: [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) §8 (auth).

---

## 1. TL;DR

Large uncommitted body of work from sessions after handoff_2 is now on `main` (see §8). Product is ready for **operator auth bootstrap**, then **safe drip discovery**. Do **not** keep fighting headed Chromium on the headless VM.

| Area | Status |
|------|--------|
| Keyword-alignment scoring (before + after tailor) | Shipped |
| Form-scan / AnswerRouter dry-run fixes | Shipped |
| Volume search URLs + pagination (LI / Indeed / GD) | Shipped (safe defaults) |
| Safe-drip LinkedIn (jitter, max_pages, circuit-break) | Shipped |
| `magicapply auth` (login / status / clear / sites) | Shipped |
| Headed Chromium flags for Xvfb | Shipped (`--use-gl=swiftshader`; no `--disable-software-rasterizer`) |
| LinkedIn / Indeed / Glassdoor `storage_state` on disk | **Not done** — operator must bootstrap |
| Real submissions at scale / cover letters / live Anthropic | Still Phase 2 |

**Immediate operator goal (not more infra):** get auth without VNC theater, then limited discover.

---

## 2. What the operator is blocked on (read this first)

### 2a. The real blocker

Discovery for LinkedIn needs a session (`li_at` / storage_state). Cookie paste into chat was painful. `magicapply auth login` is the intended path — **but the VM has no real display**.

Sessions burned hours on:

1. Host X11 reverse tunnel → blank Chromium  
2. VM Xvfb + openbox + x11vnc + SSH `-L 5901` → black bands / empty desktop / “child process” noise  

**Playwright can load LinkedIn login and screenshot it on `DISPLAY=:1`.** Interactive use through VNC is fragile and **not required**.

### 2b. Operator decision (already accepted in chat)

**Stop making auth harder than it is.** Bootstrap on a real desktop (Mac) **or** put Cookie header in VM `.env` only. Keep the VM for headless discover/tailor/apply after that.

### 2c. Recommended next steps for the *operator* (not the agent inventing more display stacks)

**Option A — Mac (preferred for `auth login`):**

```bash
# On Mac, in a clone of this repo with configs + .env:
uv sync && uv run playwright install chromium
uv run magicapply auth login linkedin --root configs --force --auto-save --timeout 600
uv run magicapply auth status --root configs
# Copy data/auth/*_storage_state.json to the VM data/auth/ if discover runs there
```

**Option B — Cookie env on VM only (fastest, no GUI):**

```bash
# In magicapply-dev ~/magicapply/.env (never paste secrets into chat):
# LINKEDIN_SESSION_COOKIES=<full Cookie header from linkedin.com request>
# or LINKEDIN_LI_AT=<li_at only>
# MAGICAPPLY_LINKEDIN_ACK=1
# Same pattern for INDEED_ / GLASSDOOR_ when needed
```

**Then on VM (headless):**

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy
  cd ~/magicapply && uv run magicapply auth status --root configs'
# Limited discover — respect safe drip (do not crank max_pages / rpm)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy
  cd ~/magicapply && uv run magicapply discover <profile> --root configs'
```

### 2d. What *not* to do next session unless operator insists

- Rebuild X11 host forwarding  
- Treat VNC black bands as a product bug (empty openbox desktop = no window)  
- Concurrent multi-page scraping or `max_pages` → 40 on LinkedIn  
- Paste cookies into chat  
- Commit `.env`, `data/`, `configs/base_config.yaml`, `configs/keyword_bank.yaml`  

---

## 3. What shipped (code themes)

### 3a. Keyword alignment scoring + before/after tailor

- **Default** `scoring.mode: keyword` (config) → `KeywordAlignmentScorer` uses `KeywordBank` + resume/job text (not mock “always 82”).  
- Module: `src/magicapply/domain/keywords/alignment.py`.  
- Tailoring writes `score_after_tailor` / `score_after_rationale` (+ alignment artifacts under tailored app dir where wired).  
- Persistence columns on applications for after-score; status CLI shows them.  
- `llm` mode remains for Phase 2 Anthropic-style scoring.

### 3b. Dry-run apply / form fixes

- `form_scan`: safer XPath/CSS (`*=` quoting, label normalization).  
- `answer_router` + `router_rules.yaml`: free-text yes/no, DEI, how-did-you-hear, sponsorship-style identity; fewer wrong strategies.  
- Generic handler: fail if no form instead of false APPLIED.  
- Router dispatch / fixture tweaks for e2e hermetic server.

### 3c. Volume discovery (safe drip defaults)

Config models + example YAML: `remote_only`, `posted_within_days`, `max_pages`, rate limits.  
Adapters paginate search pages (LinkedIn / Indeed / Glassdoor).  
LinkedIn: jitter, page dwell, circuit-break on auth wall / redirect storms; **do not** combine auth cookies with proxy pool for that session.  
**Safe defaults matter** — high `max_pages` + high rpm is ban-bait. Runbook § safe discovery cadence.

### 3d. Generic Playwright auth

| Piece | Path |
|-------|------|
| Site registry | `infrastructure/browser/auth_sites.py` |
| Resolve + interactive login | `infrastructure/browser/auth_session.py` |
| CLI | `cli/commands/auth.py` → `magicapply auth login|status|clear|sites` |
| Composition / factory | pass `data_dir`; adapters call `resolve_session_auth` |

**Resolution order:**  
`data/auth/<site>_storage_state.json` → `{SITE}_SESSION_COOKIES` → legacy single cookie (`LINKEDIN_LI_AT`, etc.) → none.

State filenames: `linkedin_storage_state.json`, `indeed_storage_state.json`, `glassdoor_storage_state.json`.

### 3e. Headed session flags (`session.py`)

For headed launches:

- Use `--use-gl=swiftshader`, `--enable-unsafe-swiftshader`, `--no-sandbox`, `--disable-dev-shm-usage`, `--ozone-platform=x11`, `--disable-gpu`  
- **Do not** pass `--disable-software-rasterizer` (blanks Chromium on Xvfb)

Still not a substitute for a real desktop for human login.

### 3f. Keyword bank / operator configs (local only)

`configs/keyword_bank.yaml` and `configs/base_config.yaml` are **gitignored** operator data. Bank was expanded on the VM for real scoring; next agent should not assume repo contains the operator’s full bank — only `configs/*.example` / package defaults if present.

---

## 4. Repository / environment facts

| Item | Value |
|------|--------|
| Dev VM | `ssh magicapply-dev` — repo at `~/magicapply` (virtiofs mount of host tree) |
| Host path | `/mnt/storage/VMs/dev/magicapply` (same files as VM) |
| Commands | Always via VM: `uv run …` with `PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy` |
| Python / Chromium | On VM only — host is wrong for pytest/playwright |
| Secrets | `.env` gitignored; never log cookie values |
| Auth dir | `data/auth/` (under gitignored `data/`) — empty until bootstrap |

### VNC stack (optional; leave alone unless operator wants it)

If already running on VM:

- Xvfb `:1` 1400×900  
- openbox  
- x11vnc `-rfbport 5901 -localhost`  
- Host: `ssh -L 5901:127.0.0.1:5901 magicapply-dev` → viewer `127.0.0.1:5901`  

Black bands / blank = often **no mapped app window**, not dead VNC. Prefer Mac/auth env over repairing this.

---

## 5. How to verify code (agent checklist)

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy
  cd ~/magicapply && uv run pytest tests/unit -q'
```

Prior full unit run in this workstream was on the order of **~720+ passed** (exact count may drift). Targeted:

```bash
uv run pytest tests/unit/keywords tests/unit/jobs/test_scoring.py \
  tests/unit/browser/test_auth_session.py tests/unit/cli/test_auth.py \
  tests/unit/sources/test_volume_search_urls.py \
  tests/unit/browser/test_answer_router.py tests/unit/browser/test_form_scan.py -q
```

Auth without secrets:

```bash
uv run magicapply auth sites
uv run magicapply auth status --root configs
uv run magicapply doctor --root configs   # includes auth summary lines when wired
```

---

## 6. Suggested next engineering tasks (after auth works)

Priority order for a *new* instance once operator has session:

1. Confirm `auth status` shows `storage_state` or `env_cookies` / `legacy` for LinkedIn.  
2. One **safe** `discover <profile>` — inspect counts, auth-wall logs, no redirect storms.  
3. `tailor` + sample dry-run `apply` (`--no-submit`) on a TAILORED app.  
4. Promote useful answers from `data/answer_proposals.yaml` → `configs/answer_library.yaml` (operator).  
5. W.5c LinkedIn cookie/session verification if still open in DoD.  
6. Phase 2 only when asked: Anthropic scoring, cover letters, real submit cadence.

Do **not** start: review web UI, Alembic, new ATS, aggressive scraper scaling.

---

## 7. Design invariants to preserve

- Config over code (YAML prompts, keyword bank, router rules).  
- CLI → pipelines → domain → infrastructure; composition root in `cli/composition.py`.  
- Extend-don’t-multiply (auth sites = registry entries, not parallel CLIs).  
- `BaseATSHandler.apply` template method: CAPTCHA + dry_run short-circuit.  
- Never log cookie values; auth status is names/sources only.  
- Safe discovery drip > volume vanity metrics.

---

## 8. Commits landed with this handoff

| SHA | Summary |
|-----|---------|
| `9307f26` | Keyword scoring, volume drip discovery, auth login, form fixes (+ handoff_2/3, runbook §8) |

Untracked intentionally left out:

- `scratch/` — probes only  
- `configs/curated_proxies.yaml` — ephemeral free-proxy list  
- Operator secrets / `data/` / local `base_config.yaml` / `keyword_bank.yaml`

---

## 9. One-line state

**Pipeline code for score → volume-drip discover → tailor → dry-run apply + generic auth is in-repo; operator still must bootstrap LinkedIn (and optionally Indeed/GD) session without headed-VM heroics, then run limited discover.**
