# MagicApply — End-to-End Dry-Run Test Plan

**Status:** shipped 2026-07-03 across commits `dca2d1d` (Phase A) through `e68cdd3` (Phase J). See `git log --oneline` for the phase-by-phase history; this document is kept as reference for the design decisions, deviations, and rationale that landed along the way.

**Goal:** Exercise the full pipeline (discovery → dedup → scoring → tailoring → cover letter → browser-driven submission) inside the `magicapply-dev` VM, with the LLM stubbed but everything else real. Two harnesses: a hermetic local-fixture E2E, and a live-gated dry-run against real jobs that stops one click short of Submit.

---

## 0. VM environment (already deployed)

The VM `magicapply-dev` is provisioned and running. Details captured for reference; no VM setup work is part of this plan except installing Playwright browsers once.

| Piece | Value |
|---|---|
| libvirt domain | `magicapply-dev` (Debian 12, 2 vCPU / 4 GB RAM) |
| Domain XML | `/mnt/storage/VMs/magicapply-dev-setup/magicapply-dev.xml` |
| Cloud-init | `/mnt/storage/VMs/magicapply-dev-setup/user-data` |
| SSH host | `magicapply-dev` (IP `192.168.122.6`, user `ggray`, ed25519 key) |
| Repo mount (in VM) | `~/magicapply` via **virtiofs** target `magicapply` — live view of host `/mnt/storage/VMs/dev/magicapply` |
| Fallback sync | `/mnt/storage/VMs/magicapply-dev-setup/sync-to-vm.sh` (rsync-over-ssh if virtiofs unavailable) |
| Python toolchain | `uv 0.11.26`, `cpython-3.12.13` under `~/.local/share/uv/` |
| Virtualenv | `~/magicapply/.venv` (already created by `uv sync`) |
| Playwright browsers | **NOT installed** — one-time step: `uv run playwright install chromium` |
| Lifecycle | `virsh -c qemu:///system {start,shutdown,destroy} magicapply-dev` |

**Command invocation pattern used throughout this plan:**

```bash
ssh magicapply-dev 'cd ~/magicapply && uv run <command>'
```

All `pytest`, `magicapply`, and `playwright` invocations run inside the VM. Host-side execution is not supported and would use the wrong Python.

---

## 1. Current code state (from Phase-10 survey)

| Layer | Status | Reference |
|---|---|---|
| CLI `discover`, `run`, `apply`, `status`, `config`, `profiles`, `doctor` | `run` warns "apply not wired"; `apply` raises NotImplementedError | `src/magicapply/cli/main.py:20-54`, `src/magicapply/cli/commands/pipeline.py:71-97` |
| `DiscoveryPipeline` | Fully wired: sources → in-run dedup → repo-upsert → prefilter → LLM score → persist | `src/magicapply/pipelines/discovery.py:38-105` |
| `ApplyPipeline.apply_one` | Implemented but never called from CLI; expects a `PageDriver`, updates state | `src/magicapply/pipelines/apply.py:35-77` |
| `Tailorer` (summary rewrite via LLM, Builder) | Implemented, never invoked | `src/magicapply/domain/resumes/tailor.py:40-105` |
| `NarrativeEngine` (cover letter, screening answers) | Implemented, never invoked | `src/magicapply/domain/resumes/narrative.py:41-98` |
| `GreenhouseHandler` (Template Method, static + dynamic fill, submit) | Cover letter best-effort; per-role custom fields stubbed | `src/magicapply/infrastructure/browser/ats/greenhouse.py:25-52` |
| `ATSHandlerFactory` | Only Greenhouse registered | `src/magicapply/infrastructure/browser/ats/factory.py:11` |
| CAPTCHA detection | Working; called on load and pre-submit inside `BaseATSHandler.apply` | `src/magicapply/infrastructure/browser/captcha.py`, `.../ats/base.py:81-104` |
| LLM factory | Accepts only `"anthropic"` and `"ollama"`; ollama raises `NotImplementedError` | `src/magicapply/infrastructure/llm/factory.py:11`, `.../providers/ollama.py:24` |
| `MockLLMClient` | Test-only; canned responses in order, no shape awareness | `src/magicapply/infrastructure/llm/providers/mock.py:18-49` |
| `Application` state machine | `TAILORED` already exists at line 23 — no state changes needed | `src/magicapply/domain/models/application.py:19-52` |
| `ApplicationRow` schema | No tailored-artifact field yet | `src/magicapply/infrastructure/persistence/tables.py:32-45` |
| Config `LLMConfig.provider` | `Literal["anthropic", "ollama"]` | `src/magicapply/config/models.py:32` |
| Playwright session lifecycle | Does not exist | — |
| Dry-run / no-submit flag | Does not exist | — |
| E2E harness | Does not exist; only unit tests + two live-gated tests | `tests/integration/` |

**CLAUDE.md is stale** — it still says "pre-implementation." Phase A refreshes it.

---

## 2. Locked design decisions

1. **Tailored artifact storage:** the tailored resume and cover letter are written to disk as human-readable files, and a new `tailored_path` column on `applications` points to their directory. Format matches the base resume (YAML) plus a Markdown cover letter. You can `cat` them for review.
   - `data/tailored/<application_id>/resume.yaml`
   - `data/tailored/<application_id>/cover_letter.md`
2. **Playwright browsers:** `doctor` reports status only; it never runs `playwright install`. The E2E harness itself drives a **full real browser session** and stops exactly at the final Submit click when in dry-run mode — the point of the test is to prove every step short of that click works.
3. **Live-gated URL:** no separate URL. The live-gated E2E consumes the real configured job sources in `configs/base_config.yaml` and applies to whatever qualifies. It is `--no-submit` by default; a real submit requires an explicit second env var.
4. **Application state:** reuse `APPLIED`. Add a new `dry_run: bool` field to `Application` and to `ApplicationRow`. `magicapply status` groups on it so dry-runs don't inflate the real "applied" count.
5. **Delivery:** small commits per phase (below). Each phase leaves the tree green (`uv run pytest tests/unit -q`).

---

## 3. Execution phases

Each phase is one commit. Every phase ends by running the unit suite in the VM before moving on.

### Phase A — Docs & CLAUDE.md refresh (no code)

**Files:**
- **New:** `docs/VM_DEV.md`
- **Edit:** `CLAUDE.md` (root)

`docs/VM_DEV.md` captures the VM connection details from §0, plus the day-to-day loop:

```bash
ssh magicapply-dev 'cd ~/magicapply && uv sync'
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit -q'
ssh magicapply-dev 'cd ~/magicapply && uv run playwright install chromium'   # one-time
ssh magicapply-dev 'cd ~/magicapply && uv run magicapply --help'
```

`CLAUDE.md` diff:
- Remove/replace the "This repository is pre-implementation" block (lines ~7-11) with a Phase-10 summary of what's shipped and what's not.
- Add a "Running in the dev VM" pointer to `docs/VM_DEV.md`.
- Keep the architectural-guardrails section as-is.

**Verify:** none (docs).

---

### Phase B — LLM stub as a first-class provider

Enables running the full pipeline without an `ANTHROPIC_API_KEY`.

**File edits:**

1. `src/magicapply/config/models.py:32` — widen the literal:
   ```python
   provider: Literal["anthropic", "ollama", "mock", "replay"] = "anthropic"
   ```
   **Do not add a top-level `fixtures_dir` field.** `LLMConfig.options` at line 38 is explicitly the escape hatch for "provider-specific overrides — providers own their own validation" (mirrors how Ollama's `base_url` was intended to plug in). The replay provider reads `fixtures_dir` out of `options`.

2. `src/magicapply/infrastructure/llm/factory.py:11` — dispatch:
   ```python
   if config.provider == "mock":
       return MockLLMClient()                       # no `responses` arg → shape-aware mode
   if config.provider == "replay":
       fixtures = str(config.options.get("fixtures_dir", "configs/llm-fixtures"))
       return ReplayLLMClient(fixtures_dir=Path(fixtures))
   ```

3. **Move the four prompt-instruction constants out of Python entirely, into `configs/prompts.yaml`.** Prompts are static behavior specification per CLAUDE.md's "Config over code" guardrail — they belong in YAML, not in any Python module (not `infrastructure/llm/prompts/`, not `domain/prompts.py`). The empty `infrastructure/llm/prompts/` subpackage gets deleted since its docstring is now definitively wrong. The four constants currently live as private module-level strings:
   - `_SCORING_INSTRUCTIONS` at `src/magicapply/domain/jobs/scoring.py:27`
   - `_SUMMARY_INSTRUCTIONS` at `src/magicapply/domain/resumes/tailor.py:30`
   - `_COVER_LETTER_INSTRUCTIONS` at `src/magicapply/domain/resumes/narrative.py:23`
   - `_ANSWER_INSTRUCTIONS` at `src/magicapply/domain/resumes/narrative.py:32`

   All four move to `configs/prompts.yaml` under top-level keys `scoring`, `summary`, `cover_letter`, `answer`. A `PromptsConfig` Pydantic model in `config/models.py` validates them; `config/loader.py` reads `<root>/prompts.yaml` and exposes them on `LoadedConfig.prompts`. `LLMScorer`, `Tailorer`, and `NarrativeEngine` accept the specific prompt string as a required kwarg on `__init__`; composition helpers in `cli/composition.py` inject `loaded.prompts.<field>` explicitly. The shape-aware `MockLLMClient` accepts the whole `PromptsConfig` for substring-matching in its detection path.

4. **Extend the existing `MockLLMClient`** at `src/magicapply/infrastructure/llm/providers/mock.py:18` — do not create a `SmartMockLLMClient` sibling. Signature becomes:
   ```python
   def __init__(
       self,
       responses: str | list[str] | None = None,
       *,
       input_tokens_each: int = 100,
   ) -> None:
   ```
   When `responses is None`, the client enters **shape-aware mode**: in `complete()`, inspect the first `SystemBlock.text` and match against the imported prompt constants to pick the response:
   - contains `SCORING_INSTRUCTIONS` → `'{"score": 82, "rationale": "mock: strong keyword overlap"}'`
   - contains `SUMMARY_INSTRUCTIONS` → a 1-2 sentence templated summary using the job title parsed from the user message
   - contains `COVER_LETTER_INSTRUCTIONS` → a 2-paragraph templated letter using company + title
   - contains `ANSWER_INSTRUCTIONS` → a short truthful-sounding answer
   - no match → raise `RuntimeError` with the prompt preview (fail loud so a new prompt shape is caught in dev)

   When `responses` is a str or list, behavior is unchanged — every existing test that passes canned responses keeps working. Also fixes the pre-existing `field(default_factory=list) if False else []` bug at line 24 while we're here (should just be `[]`).

4. **New:** `src/magicapply/infrastructure/llm/providers/replay.py`
   - Constructor: `ReplayLLMClient(fixtures_dir: Path, *, fallback: LLMClient | None = None, record: bool = False)`.
   - Key: `sha256((system_texts + [m.content for m in messages]).joined())[:16]`.
   - Reads `<fixtures_dir>/<key>.yaml` — schema:
     ```yaml
     prompt_preview: "first 200 chars of the concatenated prompt (for humans)"
     response: "canned response body"
     ```
   - Missing key + `record=True` → captures a response from `fallback` (default `MockLLMClient()` in shape-aware mode) and writes the fixture. `record=False` + missing → falls through to `fallback` if present, else raises `MissingFixture`.
   - Environment override: `MAGICAPPLY_LLM_RECORD=1` sets `record=True`.

5. **New dir:** `configs/llm-fixtures/` with a `.gitkeep` and one seed fixture for the local-E2E test (added later in Phase H).

**New tests:**
- Extend `tests/unit/llm/test_mock.py` (or the existing mock tests wherever they live) with a `test_mock_shape_aware_mode` case — asserts scoring shape (valid JSON), summary length, cover-letter structure, and the "unknown prompt shape raises" contract.
- `tests/unit/llm/test_replay.py` (new) — asserts key stability, fallthrough, record mode, missing-fixture error.
- `tests/unit/llm/test_prompts_module.py` (new) — smoke test that `prompts/__init__.py` exports the four constants and that they're bytewise-identical to what the domain callers import.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit/llm -q'
```

---

### Phase C — Persist tailored-artifact reference on `Application`

**File edits:**

1. `src/magicapply/infrastructure/persistence/tables.py:32-45` — add columns:
   ```python
   tailored_path: str | None = None
   dry_run: bool = False
   ```

2. `src/magicapply/domain/models/application.py:81-96` — add the same fields on the domain model with defaults `None` and `False`.

3. `src/magicapply/infrastructure/persistence/repositories/applications.py` — extend the three mapping helpers so both new fields round-trip:
   - `_domain_to_row` (line 77) — copy `tailored_path` and `dry_run` from domain into row.
   - `_copy_domain_into_row` (line 93) — same, for the `save` path.
   - `_row_to_domain` (line 103) — populate the new fields when reconstructing.

   No schema-migration file needed: the DB is auto-created via `SQLModel.metadata.create_all` (`persistence/db.py:create_db`) and the plan targets fresh test DBs. Document in the commit message that pre-existing DBs need to be deleted; not a public product yet.

4. **Add a repo method** `list_by_state_and_profile(state, profile_name) -> list[Application]` on `ApplicationsRepository` Protocol (`domain/repositories.py:31`) and `SqlApplicationsRepository` — needed by Phase D (tailoring loop) and Phase G (apply loop) to iterate SCORED/TAILORED apps for a specific profile. Two-column SELECT, mirrors the existing single-column `list_by_state` at `applications.py:69`.

**New tests:**
- `tests/unit/persistence/test_applications_repo.py` — add cases for round-trip with `tailored_path` and `dry_run`, plus a case for the new `list_by_state_and_profile` query.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit/persistence -q'
```

---

### Phase D — Tailoring pipeline + `magicapply tailor` CLI

**New files:**

1. `src/magicapply/pipelines/tailoring.py`:
   ```python
   class TailoringReport:
       tailored: int
       skipped_wrong_state: int
       errors: list[str]

   class TailoringPipeline:
       def __init__(self, *, apps_repo, jobs_repo, tailorer, narrative,
                    profile_name, data_dir: Path): ...
       def run(self) -> TailoringReport:
           # Query is apps_repo.list_by_state_and_profile(SCORED, profile_name)
           # so re-running is naturally idempotent — already-TAILORED rows are
           # not returned. For each such Application:
           #   job = jobs_repo.get(app.job_id)
           #   tailored = tailorer.tailor_for(job)              # domain: pure
           #   cover = narrative.cover_letter(job)              # domain: pure
           #   dir = data_dir / "tailored" / app.id ; mkdir(parents=True, exist_ok=True)
           #   yaml.safe_dump(tailored.model_dump(mode="json"), dir/"resume.yaml")
           #   (dir / "cover_letter.md").write_text(cover)
           #   app.tailored_path = str(dir)
           #   app.transition_to(TAILORED, reason="tailoring pipeline")
           #   apps_repo.save(app)
   ```
   The file writes are intentionally in the pipeline, not in `Tailorer`/`NarrativeEngine`, so those domain classes stay pure (mirrors how `DiscoveryPipeline` keeps `JobScorer` pure and does the `apps_repo.save` itself).

2. `src/magicapply/cli/commands/pipeline.py` — add:
   ```python
   @app.command()
   def tailor(profile: str, ...): ...
   ```
   Selects SCORED applications for the profile via the new repo method, calls `TailoringPipeline`.

3. `src/magicapply/cli/composition.py` — extend the existing helper, don't add a sibling. Rename `_load_base_resume_text` (line 63) → `_load_base_resume`, changing its return type from `str` to `BaseResume`. Callers that need the text (only `build_scorer` at line 54) serialize inline:
   ```python
   def build_scorer(loaded, profile, scoring) -> JobScorer:
       llm = build_client(loaded.base.llm)
       base = _load_base_resume(loaded, profile)
       base_text = yaml.safe_dump(base.model_dump(), sort_keys=True)   # was inside the helper
       return JobScorer(
           prefilter=Prefilter(scoring),
           llm_scorer=LLMScorer(llm, base_resume_text=base_text),
       )
   ```
   Then add the new tailoring/narrative helpers alongside the existing `build_scorer`/`build_repos`/`build_sources_for_profile`:
   ```python
   def build_tailorer(loaded, profile) -> Tailorer:
       return Tailorer(build_client(loaded.base.llm), _load_base_resume(loaded, profile))

   def build_narrative(loaded, profile) -> NarrativeEngine:
       return NarrativeEngine(
           build_client(loaded.base.llm),
           _load_base_resume(loaded, profile),
           style=profile.apply.narrative_style,               # config/models.py:174
       )

   def build_tailoring_pipeline(loaded, profile) -> TailoringPipeline: ...
   ```
   Single load helper serves all three callers; every `llm.provider: mock` run needs no API key.

**Wire in `run`:** `pipeline.py:71` — after `discover()`, if the report has ≥1 scored application above threshold, call `TailoringPipeline.run()`.

**New tests:**
- `tests/unit/pipelines/test_tailoring.py` — uses `MockLLMClient()` (shape-aware mode), in-memory SQLite, temp `data_dir`; asserts:
  - Tailored resume YAML file written and readable back as `TailoredResume`.
  - Cover letter Markdown file written.
  - `Application.tailored_path` populated; state = `TAILORED`.
  - Idempotent (re-running does not re-tailor a `TAILORED` app).

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit/pipelines -q'
```

---

### Phase E — Playwright session + `magicapply apply <job-id>` wired

**New files:**

1. `src/magicapply/infrastructure/browser/session.py`:
   ```python
   class PlaywrightSession(AbstractContextManager):
       def __init__(self, *, headless: bool = True,
                    storage_state_path: Path | None = None): ...
       def __enter__(self) -> "PlaywrightSession": ...
       def new_page(self) -> Page: ...
       def save_state(self) -> None: ...
       def __exit__(...) -> None: ...
   ```
   Wraps `playwright.sync_api.sync_playwright().start()`, creates a Chromium browser + context with optional `storage_state=`, exposes `.new_page()`. On exit, saves storage state if a path was provided, closes context+browser+playwright.

**File edits:**

2. `src/magicapply/pipelines/apply.py:35-77` — no signature change to `apply_one`; it already takes keyword-only `page`, `application`, `job`, `application_data`. Phase F adds the `dry_run` kwarg below.

3. `src/magicapply/cli/composition.py` — add:
   ```python
   def build_apply_pipeline(loaded, profile) -> ApplyPipeline: ...

   def build_application_data(loaded, profile, app: Application) -> ApplicationData:
       # Load tailored artifacts from disk:
       tailored_dir = Path(app.tailored_path)
       tailored = TailoredResume.model_validate(
           yaml.safe_load((tailored_dir / "resume.yaml").read_text())
       )
       cover = (tailored_dir / "cover_letter.md").read_text().strip() or None
       # Combine with static answers:
       return ApplicationData(
           job_url=job.url,                                # caller must pass `job` too
           static_answers=loaded.base.static_answers,
           tailored_resume=tailored,
           cover_letter=cover,
       )
   ```
   The `application_data` build needs both `app` and `job` — the CLI fetches `job = jobs_repo.get(app.job_id)` first, then hands both to `build_application_data`.

4. `src/magicapply/cli/commands/pipeline.py:87-97` — replace the stub:
   ```python
   @app.command()
   def apply(job_id: str, ..., headless: bool = True, no_submit: bool = True, yes_submit: bool = False):
       # Fetches the Application by job_id, verifies TAILORED state,
       # loads Job, builds ApplicationData,
       # opens PlaywrightSession, builds ApplicationData with dry_run=(not yes_submit)
       # (Phase F adds the field), calls
       # ApplyPipeline.apply_one(page=..., application=..., job=..., application_data=...),
       # prints result table.
   ```
   `--no-submit` defaults to `True`; real submit requires `--yes-submit`. Passing both is an error.

**New tests:**
- `tests/unit/browser/test_session.py` — skipped if Chromium not installed (checks `~/.cache/ms-playwright` presence). Verifies context/page lifecycle and storage-state save.
- `tests/unit/cli/test_apply_command.py` — uses a fake `PageDriver` fixture; asserts CLI happy path and pre-state guard.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run playwright install chromium'
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit -q'
```

---

### Phase F — Dry-run guard (`--no-submit`) + `dry_run` flag on Application

Rather than introducing a `DryRunPageDriver` wrapper, extend the existing Template Method flow so the dry-run stop point is another invariant every ATS handler inherits for free. This is exactly the "extend the base class" shape the codebase already uses for CAPTCHA detection.

**File edits:**

1. `src/magicapply/infrastructure/browser/ats/base.py:37-46` — add a `dry_run: bool = False` field to `ApplicationData` (Pydantic model). Comment: "when True, the template method stops right before `_submit` and returns `state='applied'`."

2. `src/magicapply/infrastructure/browser/ats/base.py:81-104` — extend `BaseATSHandler.apply` (the invariant flow) with one new branch right after the pre-submit CAPTCHA check and before `_submit`:
   ```python
   captcha = detect_captcha(page.content())
   if captcha:
       return ApplicationResult(state="needs_intervention",
                                error=f"CAPTCHA detected before submit: {captcha}")

   if data.dry_run:
       # Full application short of the click. Same terminal shape as a real
       # submit; the Application row's dry_run flag distinguishes.
       return ApplicationResult(state="applied",
                                submitted_url=getattr(page, "url", None),
                                error="dry-run: submit skipped")

   self._submit(page, data)
   return self._verify(page, data)
   ```
   Every existing and future ATS handler (Greenhouse today; Lever/Workday/Ashby later) picks this up automatically — no per-handler code, no wrapper class, no selector-guessing.

3. `src/magicapply/pipelines/apply.py:39-77` — no signature change. Read `application_data.dry_run` and set `application.dry_run = application_data.dry_run` on the terminal `APPLIED` transition before `self._apps.save`. Symmetrical with how `application.error = result.error` is already set at line 76.

4. `src/magicapply/cli/commands/pipeline.py` (Phase E already added `--no-submit`/`--yes-submit` to the `apply` command; here we wire them through) — compute `dry_run = not yes_submit` and set it on `ApplicationData` in `build_application_data` (`composition.py`, added in Phase E).

5. `src/magicapply/cli/commands/status.py:42-47` — the summary table currently groups only by `state`. Extend to group by (state, dry_run) so `magicapply status` shows e.g. `applied: 3 (dry_run: 2, real: 1)`. Filter in Python after `list_by_state(APPLIED)` — no new repo method needed for the display split.

**New tests:**
- Extend `tests/unit/browser/test_ats_flow.py` (existing) with a `test_dry_run_short_circuits_submit` case: the fake page records that `_submit` was never called and the result is `state="applied"` with the "dry-run" error tag.
- Extend `tests/unit/pipelines/test_apply.py` (or whichever apply test exists) with a `test_apply_marks_dry_run` case: `ApplicationData.dry_run=True` → row lands in `APPLIED` state with `dry_run=True`.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit -q'
```

---

### Phase G — `magicapply run` sequences discover → tailor → apply

**File edits:**

1. `src/magicapply/cli/commands/pipeline.py:71-85` — replace the "apply phase is not wired" warning with:
   ```python
   discover_report = DiscoveryPipeline(...).run()
   tailor_report = TailoringPipeline(...).run()
   apply_reports = apply_pipeline.apply_batch(
       session=session, profile_name=profile.name, dry_run=(not yes_submit),
   )
   render_tables(discover_report, tailor_report, apply_reports)
   ```

2. **Extend the existing `ApplyPipeline`** at `src/magicapply/pipelines/apply.py:35`, don't add an `ApplyRunner` sibling. Add one new method next to `apply_one`:
   ```python
   def apply_batch(
       self,
       *,
       session: PlaywrightSession,
       profile_name: str,
       dry_run: bool,
   ) -> list[ApplyReport]:
       """Apply to every TAILORED application for a profile in one session.

       Reuses `apply_one` per job — the session is shared, but each job gets a
       fresh page. Any exception from a single job is captured as a FAILED
       report and the batch keeps going.
       """
       reports: list[ApplyReport] = []
       tailored = self._apps.list_by_state_and_profile(
           ApplicationState.TAILORED, profile_name,
       )
       for app in tailored:
           job = self._jobs.get(app.job_id)                # jobs_repo added to ApplyPipeline in this phase
           if job is None:
               continue
           data = self._data_builder(app, job, dry_run=dry_run)   # composition-injected callable
           page = session.new_page()
           reports.append(self.apply_one(page=page, application=app,
                                          job=job, application_data=data))
       return reports
   ```
   `ApplyPipeline.__init__` gains a `jobs_repo` and a `data_builder` parameter (Callable produced by `composition.build_application_data_factory(loaded, profile)`). One class, two methods, no sibling.

**New tests:**
- `tests/unit/cli/test_run_command.py` — mocks the three pipelines, verifies ordering and reporting.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit -q'
```

---

### Phase H — Local fixture E2E (hermetic; the primary dry-run test)

The centerpiece. Real Playwright drives a real (local) Greenhouse-shaped form; LLM is stubbed via `replay` with a fallback to `SmartMock`.

**New files:**

1. `tests/integration/e2e/fixtures/careers.html` — HTML with three JSON-LD `JobPosting` blocks. One targeted at "senior backend engineer" (should score high), one "iOS designer" (should be prefiltered out on seniority/keywords), one "director of engineering" (borderline). Each references a `/jobs/<id>` detail URL.

2. `tests/integration/e2e/fixtures/job_<id>.html` — job detail pages with richer descriptions. The one with a Greenhouse link points at `/greenhouse/<id>/apply` on the same fixture server.

3. `tests/integration/e2e/fixtures/apply_form.html` — form with the exact selectors used by `GreenhouseHandler` (`greenhouse.py:33-52`): `#first_name`, `#last_name`, `#email`, `#phone`, `input[name='linkedin_url']`, `textarea[name='cover_letter_text']`, `input[type='submit']`. Submit posts to `/submit` and returns a "thank you" page. **Do not** include any of the strings `detect_captcha` looks for (`captcha.py`: `recaptcha`, `hcaptcha`, `funcaptcha`, `arkoselabs`, `turnstile`, `cf-challenge`) — otherwise `BaseATSHandler.apply` short-circuits to `NEEDS_INTERVENTION` and the test never reaches the fill/submit path.

4. `tests/integration/e2e/fixtures/http_server.py` — pytest fixture that launches a threaded `http.server.HTTPServer` on `127.0.0.1:<random-port>`, serves the above files, and exposes a `.submissions: list[dict]` attribute recording every POST to `/submit`. Yields the base URL.

5. `tests/integration/e2e/configs/base_config.yaml` — a dry-run config:
   ```yaml
   version: 1
   llm:
     provider: replay
     fixtures_dir: "../llm-fixtures"
   scoring:
     threshold: 70
     prefilter:
       locations: ["Remote"]
       seniority: ["senior", "staff"]
       exclude: []
   static_answers:
     full_name: "Test User"
     email: "test@example.com"
     phone: "555-0100"
     linkedin_url: "https://linkedin.com/in/testuser"
   sources:
     - name: "fixture-careers"
       type: "career_page"
       urls: ["__FIXTURE_URL__/careers"]     # substituted at test time
   ```
   Plus a `configs/profiles/e2e.yaml` and `resumes/e2e.yaml`.

6. `tests/integration/e2e/llm-fixtures/` — pre-recorded canned responses for the deterministic runs (scoring, summary rewrite, cover letter). Generated once by running the test with `MAGICAPPLY_LLM_RECORD=1` and checked in.

7. `tests/integration/e2e/test_e2e_local.py`:
   ```python
   @pytest.mark.e2e_local
   def test_full_pipeline_local(fixture_server, tmp_path):
       # 1. Copy configs to tmp_path, substituting FIXTURE_URL.
       # 2. Run `magicapply discover e2e --root <tmp_path>` (via runner).
       #    Assert: 3 jobs discovered, 1 rejected by prefilter, 2 scored.
       # 3. Run `magicapply tailor e2e --root <tmp_path>`.
       #    Assert: tailored/ dir exists, resume.yaml + cover_letter.md valid.
       # 4. Install chromium if missing (skip test with clear message otherwise).
       # 5. Run `magicapply run e2e --root <tmp_path> --yes-submit`.
       #    Assert: fixture_server.submissions has one entry with the expected
       #    first_name, last_name, email, cover-letter contents.
       # 6. Run again with default --no-submit against the same setup:
       #    Assert: fixture_server.submissions unchanged; Application row has
       #    state APPLIED and dry_run=True.
   ```

**Marker registration:** reuse the existing `slow` marker at `pyproject.toml:45` for the hermetic E2E; it takes several seconds and marking it `slow` is enough — no new marker needed. The test skips itself if Chromium is missing (checks `~/.cache/ms-playwright`), matching the "no autorun" locked decision.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/integration/e2e/test_e2e_local.py -v'
```

---

### Phase I — Live-gated E2E against real configured sources

Runs the actual pipeline against `configs/base_config.yaml` (your real sources / preferences), stopping short of submission. Real API key optional (replay fixtures still work); if `provider: anthropic` is configured you get real LLM output.

**New file:**

1. `tests/integration/e2e/test_e2e_live.py`:
   ```python
   @pytest.mark.integration          # reuse existing marker; already gated on MAGICAPPLY_LIVE_TESTS=1
   @pytest.mark.slow
   def test_live_pipeline_no_submit():
       # Additional gate: MAGICAPPLY_LIVE_APPLY=1 must also be set — the base
       # `integration` marker covers "hits network" but live apply is a bigger
       # commitment. Skip cleanly if the extra opt-in isn't there.
       if os.environ.get("MAGICAPPLY_LIVE_APPLY") != "1":
           pytest.skip("MAGICAPPLY_LIVE_APPLY not set")
       runner = CliRunner()
       result = runner.invoke(app, ["run", "<real-profile>", "--no-submit", "--headless"])
       # Smoke check: the CLI completed without a Python-side crash. Any of
       # APPLIED (dry_run=True) / NEEDS_INTERVENTION (CAPTCHA) / FAILED
       # (unsupported ATS on a non-Greenhouse URL, missing selector on a
       # non-standard form) are all acceptable — the point is the pipeline
       # ran end-to-end against real sources.
       assert result.exit_code == 0
       print(result.stdout)   # human-visible for review
   ```

**Gate registration:** the existing `integration`-marker skip logic in `tests/integration/conftest.py` already handles `MAGICAPPLY_LIVE_TESTS=1`; this test adds a second in-body gate on `MAGICAPPLY_LIVE_APPLY=1`. No new marker, no new hook. `--yes-submit` is never wired into this test; a real submission requires the operator to run the CLI directly with the flag.

**Verify (opt-in, from the VM):**
```bash
ssh magicapply-dev 'cd ~/magicapply && MAGICAPPLY_LIVE_APPLY=1 uv run pytest tests/integration/e2e/test_e2e_live.py -v'
```

---

### Phase J — `doctor` polish + goldens for tailoring

**File edits:**

1. `src/magicapply/cli/main.py:43-54` — extend `doctor` in place (no new command, no split into subcommands):
   - Add a `root: Path | None = None` option matching every other CLI command in the codebase (`pipeline.py:39`, `status.py:33`) so `magicapply doctor --root <path>` can inspect a specific config; without it, `doctor` falls back to `default_config_root()` and reports what it found.
   - When a config root resolves cleanly, report `base.llm.provider` and the resolved data-dir. If loading fails, print the ConfigError message but don't exit non-zero — `doctor` is diagnostic, not gating.
   - Check `~/.cache/ms-playwright` for Chromium: `PRESENT` / `MISSING (run: uv run playwright install chromium)`. Do **not** run it.
   - Report DB path (from `_load_or_exit`-style resolution) and size if the file exists.

**New goldens:**

2. `tests/goldens/tailoring/` — canned outputs for the fixture job used in Phase H (tailored resume YAML, cover letter Markdown). Assertions in `test_e2e_local.py` compare file bytes against the goldens; `MAGICAPPLY_UPDATE_GOLDENS=1` re-writes them.

**Verify:**
```bash
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/unit -q && uv run magicapply doctor'
```

---

## 4. End-to-end verification (once all phases land)

Run inside the VM:

```bash
# One-time setup (idempotent)
ssh magicapply-dev 'cd ~/magicapply && uv sync && uv run playwright install chromium'

# Hermetic dry-run: full pipeline against fixtures, no network, no API key
ssh magicapply-dev 'cd ~/magicapply && uv run pytest tests/integration/e2e/test_e2e_local.py -v'

# CLI smoke against fixtures (should mirror the pytest above but hits the real CLI):
ssh magicapply-dev 'cd ~/magicapply && \
    uv run magicapply --root tests/integration/e2e/configs run e2e --headless'

# Inspect artifacts
ssh magicapply-dev 'cd ~/magicapply && \
    ls data/tailored/ && \
    cat data/tailored/*/resume.yaml && \
    cat data/tailored/*/cover_letter.md && \
    uv run magicapply --root tests/integration/e2e/configs status'

# Live-gated dry-run (real sources, stops at final click)
ssh magicapply-dev 'cd ~/magicapply && \
    MAGICAPPLY_LIVE_APPLY=1 uv run pytest tests/integration/e2e/test_e2e_live.py -v'
```

Success criteria for the hermetic run:

- 3 jobs discovered from the fixture careers page.
- 1 rejected by the seniority prefilter (`domain/jobs/scoring.py:55`).
- 2 scored via `MockLLMClient` in shape-aware mode; both above threshold.
- 2 tailored artifacts on disk under `data/tailored/`, each containing valid `resume.yaml` and `cover_letter.md`.
- Playwright drives the fixture form; without `--yes-submit`, the fixture server's `.submissions` list stays empty and both Applications land in `state=APPLIED, dry_run=True`.
- With `--yes-submit`, `.submissions` contains one entry with the expected fields.

---

## 5. Risks & rollback notes

| Risk | Mitigation |
|---|---|
| Adding `tailored_path` / `dry_run` columns without a migration story | Delete `data/magicapply.sqlite3` between phases; the schema is auto-created. Not a shipped product — safe to reset. Add an Alembic pass in Phase 2 of the roadmap. |
| A future ATS handler bypasses the dry-run check | The check lives in `BaseATSHandler.apply` (Template Method), so every handler that inherits gets it for free — the same way CAPTCHA detection already works. A handler would have to explicitly override `apply()` to bypass, which the test suite would flag. |
| Live-gated test accidentally submits | `--yes-submit` is not wired into `test_e2e_live.py`; it can only be triggered by hand-invoking the CLI. Two independent guards (`no_submit=True` default + `ApplicationData.dry_run=True` short-circuit inside `BaseATSHandler.apply`) — either alone is sufficient. |
| Playwright browsers absent in the VM | `doctor` reports missing; local-E2E test skips with a message pointing at `uv run playwright install chromium`. |
| Replay fixtures drift from real prompts | Fixture key is a hash of the prompt; a prompt change invalidates the key and the missing-fixture error surfaces immediately. Re-record with `MAGICAPPLY_LLM_RECORD=1`. |

---

## 6. Phase → commit checklist

- [x] **A** `dca2d1d` `docs/VM_DEV.md` + `CLAUDE.md` refresh
- [x] **B** `f0b27b8` prompts-as-config (`configs/prompts.yaml`), `MockLLMClient` shape-aware mode, `ReplayLLMClient`, config + factory dispatch. Deviation from the original plan: prompts landed in YAML rather than a Python module — see notes in the Phase B section.
- [x] **C** `ac4d1d7` `tailored_path` + `dry_run` on `ApplicationRow`/`Application` (all three mapping helpers) + `list_by_state_and_profile` repo method + round-trip tests
- [x] **D** `9b23e36` `TailoringPipeline` + `magicapply tailor` command + `_load_base_resume` (renamed from `_load_base_resume_text`) + `build_tailorer` / `build_narrative` / `build_tailoring_pipeline` composition helpers
- [x] **E** `bcac1d7` `PlaywrightSession` + `magicapply apply <job-id>` wired end-to-end; `ApplicationData.dry_run` deferred to Phase F
- [x] **F** `25112e9` `ApplicationData.dry_run` + `BaseATSHandler.apply` template method with the pre-submit short-circuit + `--no-submit`/`--yes-submit` flags + Application row marking + `status` display split
- [x] **G** `f7cb958` `ApplyPipeline.apply_batch(session, profile_name, dry_run)` + `magicapply run` sequences discover → tailor → apply
- [x] **H** `febb548` local-fixture hermetic E2E under `tests/integration/e2e/` (reuses `slow` marker) + `conftest.py` marker-match fix
- [x] **I** `5ffa967` live-gated E2E under `tests/integration/e2e/` (reuses `integration` marker + in-body `MAGICAPPLY_LIVE_APPLY` gate)
- [x] **J** `e68cdd3` `doctor` extended with `--root` + Playwright/provider/DB reporting + tailoring goldens under `tests/goldens/tailoring/`
