# MagicApply — AI Assistant Handoff

**Purpose:** enough context for any AI assistant (Claude, GPT, Gemini, local model) to pick up where the previous session left off without re-learning the topology, invocation quirks, or the operator's hard constraints. Written 2026-07-05.

If you are a human reading this, you can too — just skip the "assistant behavior" section.

---

## 1. Repo + VM topology

MagicApply is developed on a **NixOS host** but built and run inside a **libvirt VM** (`magicapply-dev`, Debian 12). The source tree lives on the host and is shared into the VM **via virtiofs** — no sync step, no copy step. A host-side edit is immediately visible inside the VM (verified: same inode on both sides).

| | Host (NixOS) | VM (`magicapply-dev`) |
|---|---|---|
| Repo path | `/mnt/storage/VMs/dev/magicapply/` | `~/magicapply/` (virtiofs mount of the host path) |
| Python | Not present on `PATH` (deliberate — keeps NixOS pure) | `python3.12` via `uv` at `~/.local/share/uv/` |
| uv | Not installed | `~/.local/bin/uv` |
| Playwright / Chromium | Not installed | Installed in the VM's `.venv` |
| Git | Installed, operates on the same repo | Also present, operates on the same repo (virtiofs) |
| Ordinary text edits | Yes — this is where file edits happen | Not preferred — writes propagate but tooling assumes host-side edits |
| `curl`, `grep`, `ripgrep`, `find` | Yes — use these directly on the host | Also present, but no reason to route through the VM |

The VM is accessed exclusively via SSH:

```bash
ssh magicapply-dev  '<command>'    # non-interactive one-shot
ssh magicapply-dev                 # interactive shell
```

`~/.ssh/config` on the host already contains:

```
Host magicapply-dev
  HostName 192.168.122.6
  User ggray
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
```

Full VM setup + rebuild instructions live in `docs/VM_DEV.md`.

---

## 2. Command routing rules

Follow these rules and everything just works. Ignore them and you'll get `python3: command not found` from the host or a `Chromium not found` from the wrong environment.

### Run on the host (directly)

- `git` (status, log, diff, add, commit, mv, checkout, branch, etc.).
- File edits (Read, Write, Edit — via the assistant's file tools).
- `curl`, `grep`, `rg`, `find`, `ls`, `stat`, `mv`, `cp`, `rm`, `cat`.
- HTML fetching for offline probing (does not need Playwright).

### Run inside the VM (route through `ssh magicapply-dev`)

Everything that touches Python or a browser:

- `python3` (any Python invocation, including one-liners).
- `uv` and anything under it: `uv run`, `uv sync`, `uv pip …`.
- `pytest` (always via `uv run pytest …`).
- `magicapply` (the CLI — always via `uv run magicapply …`).
- `playwright` (browser install / drive).
- Anything reading `data/` at pipeline runtime (SQLite lives here, and the VM's Python opens it).

### The non-interactive SSH prelude

Non-interactive SSH sessions do NOT source `~/.bashrc`, so `uv` (installed under `~/.local/bin/`) is NOT on `PATH`. Also, `~/magicapply` lives on virtiofs while the uv cache lives on the VM's local disk, so `uv` can't hardlink between them and warns.

**Every VM-side command starts with this prelude:**

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && <your command>'
```

Interactive shells (`ssh magicapply-dev` with no command) work as expected — `.bashrc` runs and everything is on `PATH`.

### Canonical example invocations

```bash
# Sync deps (idempotent; safe to run every time)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && uv sync --extra dev'

# Unit tests (fast; ~2 s)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit -q'

# Full suite including the hermetic E2E fixture (needs Chromium)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests -q'

# CLI: discover jobs for the "staff-ds" profile
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply discover staff-ds --root configs'

# CLI: drive a real browser at a real posting, safe-by-default (stops one click short of Submit)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply apply <job-id> --root configs --no-headless --no-submit'

# Live-network integration tests (Anthropic + custom URL sources)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && MAGICAPPLY_LIVE_TESTS=1 uv run pytest tests/integration'
```

For `--no-headless` runs, X-forwarding is not configured; the VM has no display. If a visible browser is required for observation, arrange VNC or run Playwright with `xvfb`, or observe via `page.screenshot()` captured to `data/observed_forms/…`. The current DoD uses `--no-submit` + observation-log capture — the "visible browser" language in earlier commits meant an operator watching the browser start in-VM, not literally a windowed session on the host.

---

## 3. Operator constraints — HARD RULES

These come from operator feedback that has already been given and cannot be renegotiated. The previous assistant lost the operator's confidence by violating them.

1. **NEVER propose bypassing, deferring, or seeding-around any part of the pipeline** to "prove one step in isolation." The DoD is that magicapply drives the whole flow — discover → tailor → apply → Submit — end to end on real posts. If an upstream layer doesn't support the URL at hand, extend that layer properly. Do not offer "seed the DB directly" or "skip discovery for now" as options. Ever.
2. **Format-preserving DOCX is a hard requirement.** Tailoring must operate on the operator's original `.docx` file (`resumes/geoffrey.docx`) via `python-docx` at `run.text` level. Bold/italic/font/margins survive byte-for-byte. `docxtpl`-style template regeneration is out.
3. **LLM code path stays via `provider: mock`** in Phase 1. Real Anthropic is Phase 2 additive layering, not a Phase 1 rewrite. Do not modify the LLM Protocol or scoring/tailoring/narrative interfaces significantly.
4. **Cover letters are deferred (kwarg-gated), not deleted** — FR-07 stays in the code path; `TailoringPipeline(generate_cover_letter=False)` in Phase 1.
5. **No submissions in Phase 1.** Every real run uses `--no-submit`. The browser stops one click short of the Submit button.
6. **Remote-only US** for location prefilter. Salary target ~$200k. US citizen, no sponsorship needed, not willing to relocate, EEO = Decline to state.
7. **Cadence:** one job all the way through the pipeline, then the next. Not "wire up N jobs then run them all."
8. **ATSes verified in order:** Greenhouse first, then Workday, then Lever, then Ashby. Sources verified after: Indeed, then Glassdoor, then LinkedIn (LinkedIn last because it needs an `li_at` cookie).
9. **Answer library grows from real observations.** Every narrative-tier or unhandled resolution during a real run appends to `data/answer_proposals.yaml` for later operator review + promotion to `configs/answer_library.yaml`.
10. **Architecture over speed.** When a design question arises ("where should this parser live?"), the answer is whatever matches the existing architecture — usually a first-class per-provider class, not a generic-adapter fallback. `docs/ARCHITECTURE.md` + `docs/GOF_PATTERNS.md` are load-bearing.

---

## 4. Assistant behavior — what worked and what didn't

Feedback captured verbatim from the operator in this session:

- **"start executing the plan"** — direct execution, not proposals or plan-mode.
- **"you should surface [remaining questions] as something I can select"** — use the `AskUserQuestion` selector (or equivalent multiple-choice UX in your tool) rather than dumping text.
- **"NO WHY DID YOU ABANDON greenhouse_html.py instead of updating it?!"** — when refactoring, edit in place / `git mv`. Do not create a duplicate file and plan to delete the original.
- **"THIS IS NOT A FUCKING CUSTOM URL. WHY ARE YOU TOUCHING IT!?!"** — provider-specific parsing belongs in a first-class per-provider source, not stitched into a generic URL adapter.
- **"No 2 is fucking unacceptable period! THE WHOLE POINT OF THIS IS TO MAKE IT WORK. YOUR OPTION IS LAZY AND I DONT EVER WANT A RECOMMENDATION LIKE THAT AGAIN, PERIOD!!!"** — see hard rule #1. Never propose bypassing a pipeline step.
- **"the resumes should be crawled with magicapply, not by you"** — do not prefill the DB by hand. Do not curl-and-parse jobs manually and feed them into the tool. The tool does discovery. If it doesn't work for a URL, fix the tool.

**Things I did that worked:** small commits per phase; committing after tests pass; using SQLite state as the source of truth; using `TaskCreate/Update` to track phase progress; wiring the answer library as a Template Method extension in `BaseATSHandler.apply` rather than duplicating in every handler subclass.

**Things I did that failed:** running `python3` on the host (no interpreter); creating a duplicate file to refactor instead of editing in place; proposing "isolate the ATS handler test by seeding the DB directly"; stitching Greenhouse-specific parsing into `custom_url.py`; a `page_parsers/` Strategy package layered under a generic adapter.

---

## 5. Where things live

### Load-bearing docs (read these first)

- `CLAUDE.md` — codebase state, guardrails, testing invocations. Kept up-to-date per phase.
- `docs/ARCHITECTURE.md` — layered architecture (`cli/` → `services/` → `domain/` → `infrastructure/`), Strategy for ATS handlers, Repository for persistence, config-over-code philosophy.
- `docs/GOF_PATTERNS.md` — the "extend, don't multiply" meta-principle. When in doubt, add a kwarg or a new method to an existing class, not a new sibling class.
- `docs/VM_DEV.md` — full VM setup, rebuild, and daily-loop shell examples.
- `final_dod_plan.md` — the Phase 1 DoD plan and its current status snapshot.

### Code

- `src/magicapply/config/` — Pydantic v2 models and YAML loader.
- `src/magicapply/domain/` — pure domain models (Job, Application, resume/BaseResume, keyword bank).
- `src/magicapply/infrastructure/` — everything that touches the outside world:
  - `sources/` — per-platform search/discovery adapters. Existing: `career_page`, `job_url` (both in `custom_url.py`), `linkedin`, `indeed`, `glassdoor`. Each real platform (LinkedIn/Indeed/Glassdoor) is a first-class class per file, and the base `Source` discriminated union in `config/models.py` gates on the `type` field.
  - `browser/ats/` — per-ATS Playwright handlers implementing the Strategy pattern behind `BaseATSHandler`. `greenhouse.py`, `workday.py`, `lever.py`, `ashby.py`. `base.py` holds the Template Method (`.apply`) and `ApplicationData`.
  - `browser/ats/answer_router.py` — six-tier imperative resolver (static → library → yes/no → dei → narrative → select/check/file/unhandled). CoR-threshold `# NOTE` comment at top marks the deferred refactor.
  - `browser/forms/` — composable forms (`FormComposer`, `FormSchema`, `FormField` variants, `RulesBasedDriver` / `HybridDriver` / `LLMDriver`, `capture_loader.py`).
  - `browser/ats/router_dispatch.py` — `fill_dynamic_fields` → `FormComposer.fill_scanned` (requires `form_composer`; no fallback).
  - `browser/ats/workday_recipes.py` — Workday widget-step recipe schemas for `FormComposer.fill_recipe`.
  - `browser/ats/observed_form_log.py` — post-`_fill_dynamic` yaml writer for `data/observed_forms/<ts>-<host>/form.yaml` and the appender to `data/answer_proposals.yaml`.
  - `persistence/` — SQLite repositories behind repository interfaces.
  - `rendering/docx_inplace.py` — the format-preserving DOCX tailorer.
  - `llm/` — the `LLMClient` Protocol plus `anthropic`, `mock`, `replay`, `ollama` (stub) providers.
- `src/magicapply/pipelines/` — orchestration: `discovery.py`, `tailoring.py`, `apply.py`.
- `src/magicapply/cli/` — Typer commands; `composition.py` is the DI-free composition root.

### Configs (all gitignored unless marked)

- `configs/base_config.yaml` — sources catalog, prefilter thresholds, static answers, paths. Also `configs/base_config.example.yaml` (tracked).
- `configs/profiles/staff-ds.yaml` — the operator's active profile.
- `configs/keyword_bank.yaml` — bank of terms + synonyms + evidence used by the tailorer.
- `configs/answer_library.yaml` — verified (question → answer) pairs; consulted by the router's `library` tier.
- `configs/prompts.yaml` — every LLM prompt lives here; never inline in Python.
- `configs/answer_library.example.yaml`, `configs/keyword_bank.example.yaml`, etc. — tracked examples.

### Resumes

- `resumes/geoffrey.yaml` — `BaseResume` extracted from the operator's DOCX; contains `source_docx_path` pointing at the real DOCX.
- `resumes/geoffrey.docx` — the operator's original DOCX. NOT tracked in git.

### Runtime data (all gitignored)

- `data/jobs.sqlite` — SQLite database of Jobs + Applications.
- `data/tailored/<app_id>/` — per-app tailored resume + (Phase 2) cover letter.
- `data/observed_forms/<ts>-<host>/` — per-run form observation yaml + eventual screenshots/DOMs.
- `data/answer_proposals.yaml` — growing catalog of narrative/unhandled resolutions awaiting human promotion.

---

## 6. Where things left off (2026-07-07)

**Branch:** `feature/composable-forms-refactor` (large uncommitted diff).

**Composable forms (CF.0–CF.6):** shipped. All handlers use `FormComposer`. Legacy Workday DEI/disability imperative fallbacks removed — fill gaps in recipes/scan/drivers instead.

**W.4a Greenhouse:** `GreenhouseSource` wired; discovery works. Live apply + capture pending.

**W.4b Workday:** Circle Staff DS (`b55def1b256b5d48`) dry-run reaches Review + Submit. Capture `20260707-131006` promoted to `tests/fixtures/captured/workday-circle-staff-ds-20260707/`. CF.4 gate: stable Self Identify across 2 consecutive dry-runs.

**W.7 partial:** `tests/fixtures/captured/` + `test_capture_regression.py`.

**Next:** CF.4 stabilize Circle Self Identify → W.4a live GH apply → W.4c/d → W.5–W.8.

---

## 6a. Where W.4a actually left off (2026-07-05)

**URL approved and confirmed live:** `https://job-boards.greenhouse.io/reddit/jobs/7772274` — Reddit, Senior Staff Machine Learning Engineer, GenAI Platform, Remote - United States. Application form is inline (`id="application-form"`). Page does NOT embed JSON-LD, so the existing `JobUrlAdapter` cannot discover it.

**Correct next step (agreed after two rejected approaches):**

Build a first-class `GreenhouseSource` adapter using the public boards-api. Concretely:

1. Add `GreenhouseSource` to `src/magicapply/config/models.py` as a peer of `LinkedInSource / IndeedSource / GlassdoorSource`. Fields: `type: Literal["greenhouse"]`, `enabled: bool = False`, `boards: list[str]` (Greenhouse board slugs — `reddit`, `anthropic`, etc.), `title_keywords: list[str]` (client-side filter over role titles — `["staff data", "principal data", "staff ml", ...]` for the operator's search), `rate_limit_per_minute: int = 30`.
2. Add `GreenhouseAdapter` to `src/magicapply/infrastructure/sources/greenhouse.py` — new file, sibling of `linkedin.py`/`indeed.py`/`glassdoor.py`. It queries `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` per board, filters titles client-side against `title_keywords`, and yields `Job.new(...)` records from each match. No HTML scraping.
3. Register `GreenhouseAdapter` in `src/magicapply/infrastructure/sources/factory.py` next to the other adapters.
4. Extend the discriminated union in `config/models.py:Source` to include `GreenhouseSource`.
5. Update `configs/base_config.yaml` to add a `greenhouse` source (`boards: [reddit]`, `title_keywords: [...]`), and add its name to `configs/profiles/staff-ds.yaml:sources`.
6. Run `magicapply discover staff-ds` — expect the Reddit posting to appear in the DB.
7. Run `magicapply tailor staff-ds` — expect `data/tailored/<app_id>/resume.docx` produced from `resumes/geoffrey.docx`.
8. Run `magicapply apply <job-id> --no-headless --no-submit` — expect the Greenhouse handler to drive Chromium to the Submit button on the real Reddit posting. Iterate on unhandled fields per the plan's W.4 loop.

**Uncommitted files that must be deleted before starting** (they are from the two rejected approaches):

- `scratch/probe_gh.py`
- `src/magicapply/infrastructure/sources/page_parsers/` (whole directory)

`dod_plan.md` at the repo root is a separate older superseded plan; leave it or delete it independently.

**API confirmation** (already run during W.4a probing, quoted here for the next assistant): `boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true` returns HTTP 200 with a ~5.6 MB JSON body enumerating every open Reddit posting. Each posting has `id`, `title`, `absolute_url`, `content` (HTML JD), `location.name`, `updated_at`, and more. Same shape across `anthropic`, `databricks`, `airbnb`, `stripe`, `dropbox`, `mongodb`, `scaleai`, `pinterest`, `robinhood` — every Greenhouse-hosted board tested worked.

**Testing shape for the new source:** unit test with a fixture JSON body (small subset of a real boards-api response) fed to a `httpx.MockTransport`; assert (a) the adapter yields one `Job` per matching posting, (b) client-side title filter drops non-matching roles, (c) rate limiter is invoked between board fetches. Same testing pattern used by `LinkedInAdapter` / `IndeedAdapter` (which use Playwright, so their fixtures are different — but the shape is: adapter takes a config object + a mocked transport/browser, iterates, yields Jobs).

---

## 7. Test suite state

- **Unit + browser + CLI:** `uv run pytest tests/unit -q` — ~2 s, no network, no Chromium. **367 pass, 7 skipped** at HEAD (`35c2a45`).
- **Hermetic E2E fixture:** `uv run pytest tests/integration/e2e/test_e2e_local.py` (marked `slow`) — real Chromium against a threaded HTTP fixture impersonating a Greenhouse form; ~5 s.
- **Live-gated pipeline:** `MAGICAPPLY_LIVE_TESTS=1 MAGICAPPLY_LIVE_APPLY=1 MAGICAPPLY_LIVE_APPLY_PROFILE=<name> uv run pytest tests/integration/e2e/test_e2e_live.py -s` — runs the operator's real configs, always dry-run.
- **Live Anthropic / custom URL sources:** `MAGICAPPLY_LIVE_TESTS=1 ANTHROPIC_API_KEY=… uv run pytest tests/integration` — hits real network / API.
- **Regenerate tailoring goldens:** `MAGICAPPLY_UPDATE_GOLDENS=1 uv run pytest tests/integration/e2e/test_e2e_local.py::TestE2EDryRun -q` and commit `tests/goldens/tailoring/*`.

---

## 8. Contacting / observing the operator

- Operator email: `weathertop.labs@gmail.com`
- Real static answers live in `configs/base_config.yaml` (gitignored). Contact info, location, sponsorship / EEO / salary constants are already there.
- LinkedIn `li_at` cookie: not yet provided; W.5c is where the operator will paste it into `~/magicapply/.env` (gitignored). Do not prompt for it before W.5c.
- When you need operator input (URL approval, feature choice), use a selectable-options UI rather than free-text prompts. The operator has been explicit about this.

---

## 9. One-line handoff to a new assistant

> Repo is on a NixOS host at `/mnt/storage/VMs/dev/magicapply`, virtiofs-mounted into `magicapply-dev` (libvirt VM) at `~/magicapply`. Run git/edits on the host; route all `python`/`uv`/`pytest`/`magicapply`/`playwright` through `ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && <cmd>'`. Read `CLAUDE.md`, `final_dod_plan.md` §0 (status snapshot), and `docs/AI_HANDOFF.md` before touching anything. W.0–W.3 are on `main`; W.4a is next: build a first-class `GreenhouseSource` using `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`, wired the same shape as the existing `LinkedInSource / IndeedSource / GlassdoorSource`. Target posting is `https://job-boards.greenhouse.io/reddit/jobs/7772274`. Delete `scratch/probe_gh.py` and `src/magicapply/infrastructure/sources/page_parsers/` first — they are from two rejected approaches and must not be reused. Never propose skipping a pipeline step to isolate one part.
