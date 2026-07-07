# MagicApply — Real Definition-of-Done Plan

**Status:** in progress — see §0 for the 2026-07-07 snapshot.
**Scope:** Phase 1 verification against real employer forms + real search sources. LLM plumbing runs but returns canned (mock-provider) outputs — real LLM validation is a separate Phase 2 that layers on top after Phase 1 is proven.

**Document hierarchy:**
- **`ARCHITECTURE.md`** — single source of truth for layered architecture, patterns, and design principles.
- **`docs/GOF_PATTERNS.md`** — concrete pattern map and the **extend, don't multiply** meta-principle.
- **`final_dod_plan.md` (this file)** — single source of truth for the Phase 1 **workflow** (W.0–W.8) and **DoD acceptance criteria**. When architecture guidance here previously conflicted with `ARCHITECTURE.md`, this revision realigns architecture notes only; operator-facing steps and verification targets are unchanged.

---

## Changes in this revision (2026-07-05)

Architecture sections realigned to `ARCHITECTURE.md` and `docs/GOF_PATTERNS.md`. **Workflow steps, targets, exit criteria, and §4 acceptance criteria are unchanged in intent.**

| Area | Before (drifted) | After (aligned) |
|------|------------------|-----------------|
| **Top of plan** | No explicit duplication guard | **ANTI-DUPLICATION RULE** + **LAW OF SIMPLICITY** added (non-negotiable; work together) |
| **§0 W.4a** | Described as "blocked on architecture" | Reflects agreed `GreenhouseSource` Adapter; rejected `page_parsers/` / `custom_url` fallback documented as forbidden |
| **§1 D3** | Said cover letters "**Deleted**" | Corrected to **kwarg-gated / deferred** (FR-07 code path retained) |
| **§2a GoF** | Three notes only | Expanded: layered architecture, Adapter vs Strategy placement, when new siblings are justified, explicit rejections |
| **§3 W.4 step 2** | Always "`job_url` seed" | **Source-type matches upstream**: `GreenhouseSource` for boards-api; `job_url` only when JSON-LD exists; no generic parser layer |
| **§3 W.4 step 3** | "Proves JSON-LD works" | "Proves **discovery** lands the job in SQLite" (outcome unchanged; mechanism corrected) |
| **§3 W.4a** | Generic candidate URLs + `job_url` assumption | Approved Reddit URL + `GreenhouseSource` via `boards-api.greenhouse.io` |
| **Factory naming** | "SourceFactory" | `infrastructure/sources/factory.py:build_source` (existing Factory Method) |
| **New-class language** | Some steps said "New …" without justification | New siblings only where Adapter/Strategy pattern requires a per-platform peer; otherwise extend via kwarg/method/Template Method |

---

## Design rules (non-negotiable)

Two rules that work together. Neither trumps the other in the abstract — apply both, then pick the simpler outcome.

### ANTI-DUPLICATION RULE

- Never create new classes, functions, or files that share **80%+ overlap** with existing code.
- Before adding anything new, ask in order:
  1. Can I add a **kwarg** or **mode** to an existing class?
  2. Can I add a **method** to an existing class?
  3. Can I **extend the Template Method** (`BaseATSHandler.apply`, pipeline hooks)?
  4. Can I add a **new case** to an existing factory/strategy (`build_source`, `ATSHandlerFactory.for_url`, `AnswerRouter` tier)?
- Only after all four fail does a **new sibling class** earn its place.
- Document the justification in the **commit message**. If the pattern recurs, add a one-line note to `docs/GOF_PATTERNS.md`.

**Explicitly forbidden (W.4a lessons):**
- Provider-specific parsing inside `custom_url.py` (`JobUrlAdapter` / `CareerPageAdapter`).
- A `page_parsers/` Strategy package iterated by a generic URL adapter.
- Duplicate adapter/handler files created to refactor — **edit in place** or `git mv`.

### LAW OF SIMPLICITY

- **Goal:** keep the code as clean and simple as possible. Fewer moving parts beats clever indirection.
- Extend-don't-multiply is the *default*, not a mandate to contort existing code. If honoring the four questions above would mean ~100 lines of conditionals, wrappers, or architectural gymnastics to avoid a 15-line sibling that does one clear thing with **no meaningful overlap**, take the 15-line path.
- **This is not an 80% overlap scenario.** Anti-duplication blocks near-copies; simplicity blocks forced extensions that make the tree harder to read.
- **Tie-breaker:** when extend vs. new is ambiguous, ask: *which version would a new contributor understand faster in five minutes?* Prefer that one.
- Simplicity does **not** license bypassing the layered architecture (`ARCHITECTURE.md`), stitching provider logic into generic adapters, or skipping the full pipeline. A simple solution still lives in the right layer and pattern slot.

---

## Architecture alignment (ARCHITECTURE.md + GOF_PATTERNS.md)

### Layered architecture (unchanged invariant)

```
CLI (Typer) → pipelines/ (Facade) → domain/ → infrastructure/
```

- **Config over code:** static data in `configs/`; prompts in `configs/prompts.yaml`; keyword bank in YAML.
- **Composition root:** `cli/composition.py` wires collaborators — no DI framework, no Singleton.
- **Domain must not import infrastructure** (except the documented `LLMClient` Protocol exception).

### Pattern placement

| Concern | Pattern | Location | Extend-before-multiply |
|---------|---------|----------|------------------------|
| ATS form filling | **Strategy** + **Template Method** | `infrastructure/browser/ats/` | Cross-cutting behavior (CAPTCHA, dry-run, observation log) → `BaseATSHandler.apply`, not per-handler copies |
| Job discovery | **Adapter** | `infrastructure/sources/` | One adapter class per upstream platform/API; register via `build_source` |
| ATS selection | **Factory Method** | `infrastructure/browser/ats/factory.py` | New ATS → new handler sibling + factory registration (justified: distinct URL surface) |
| Source selection | **Factory Method** | `infrastructure/sources/factory.py` | New source type → new `*Source` config model + adapter sibling + `build_source` case |
| Persistence | **Repository** | `domain/repositories.py` + `infrastructure/persistence/` | No second jobs DB access path |
| Resume tailoring (Phase 2 path) | **Builder** | `domain/resumes/tailor.py` | Phase 1 DOCX uses `InPlaceDocxTailorer` alongside Builder (audit `resume.yaml` only) |
| Pipelines | **Facade** | `pipelines/discovery.py`, `tailoring.py`, `apply.py` | Extend pipeline kwargs; no parallel runner classes |

### When a new sibling class *is* justified

- **`GreenhouseAdapter`** (W.4a): distinct upstream (`boards-api.greenhouse.io`) — same Adapter pattern as `LinkedInAdapter` / `IndeedAdapter` / `GlassdoorAdapter`. Not an extension of `JobUrlAdapter` (JSON-LD-only).
- **`InPlaceDocxTailorer`** (W.1): distinct rendering strategy (run-level in-place swap); no overlap with removed `docxtpl` path.
- **`log_observed_form`** (W.3): module-level helper invoked once from Template Method — not a handler sibling; avoids duplicating YAML write logic in four ATS classes.

### Deferred patterns (do not introduce in Phase 1)

- **Chain of Responsibility** for `AnswerRouter` — stays imperative until a tier owns independent state (see `# NOTE: CoR threshold` in `answer_router.py`).
- **Observer** for application state — direct calls suffice.
- **Generic page-parser Strategy** under `custom_url` — rejected; violates Adapter-per-platform.

---

## 0. Status snapshot — 2026-07-07

### Done (committed to `main`)

| Phase | Commit | Summary |
|-------|--------|---------|
| W.0 | `7a7d0df` | Baseline: `resumes/geoffrey.yaml`, `configs/base_config.yaml`, `configs/keyword_bank.yaml`, `configs/profiles/staff-ds.yaml`; `BaseResume.source_docx_path` added. |
| W.1 | `3786d86` | `InPlaceDocxTailorer` (format-preserving DOCX keyword swap at run-text level); Builder bypass documented; 8 new unit tests. |
| W.2 | `a8251da` | Cover-letter path kwarg-gated (`generate_cover_letter=False` in Phase 1). Code NOT deleted per ARCHITECTURE.md FR-07. Existing tailoring tests parameterized. |
| W.3 | `35c2a45` | `AnswerLibrary` config models + `configs/answer_library.yaml`; `AnswerRouter` grows a `library` tier; `BaseATSHandler.apply` invokes `log_observed_form` once per run (Template Method extension); handler-specific `_apply_router` duplication consolidated into `router_dispatch.apply_router_to_form`; 14 new unit tests. **367 pass, 7 skipped.** |

### Done on branch `feature/composable-forms-refactor` (uncommitted)

| Phase | Summary |
|-------|---------|
| **CF.0–CF.6** | Composable forms shipped: `infrastructure/browser/forms/` (`FormComposer`, `FormSchema`, `FormField` variants, `RulesBasedDriver` / `HybridDriver` / `LLMDriver`, `DriverRegistry`). All four ATS handlers fill dynamic fields via `fill_dynamic_fields` → `FormComposer`. `use_composable_forms` flag removed; `form_composer` wired in `composition.build_application_data`. Workday widget steps use recipe schemas (`workday_recipes.py`). Capture regression fixtures in `tests/fixtures/captured/`. **463+ unit tests pass.** |
| **W.4a (partial)** | `GreenhouseSource` + `GreenhouseAdapter` wired; `discover staff-ds` returns Reddit board matches. Hermetic GH/Lever/Ashby fixtures load captured DOM. **Remaining:** live Reddit apply → capture loop. |
| **W.4b (partial)** | Circle Staff DS (`b55def1b256b5d48`) dry-run reaches Review + Submit (`20260707-131006` capture promoted to `tests/fixtures/captured/workday-circle-staff-ds-20260707/`). Legacy imperative DEI/disability fallbacks **removed** — composable recipes + scan enrichment only. **Remaining:** stable zero-unhandled on Self Identify (date spin + disability checkbox) across 2 consecutive dry-runs (CF.4 gate). |
| **W.7 (partial)** | `capture_loader.py` + `test_capture_regression.py`; synthetic + promoted live captures for GH/Lever/Ashby/Workday. Circle capture marked `live: true` (snapshot only). |

### Done on branch `feature/composable-forms-refactor` (committed `7bbe961`)

| Phase | Summary |
|-------|---------|
| **W.4a–d** | All four ATS handlers verified live (GH Reddit `7772274`, Circle Workday, FoodSmart Lever, TRM Ashby). Each dry-run reaches Submit with zero required unhandled; captures under `data/observed_forms/`. |

### In progress

- **W.5a–b:** Indeed + Glassdoor wired in `staff-ds` config; live discover attempted 2026-07-07 — **both blocked by bot protection** (Indeed: `<title>Blocked - Indeed.com</title>`; Glassdoor: Cloudflare "Just a moment…"). HTML snapshots: `data/w5_indeed_search.html`, `data/w5_glassdoor_search.html`. Adapters log+skip; 0 jobs ingested. **Acceptable Phase 1 outcome per §4 criterion #5.**
- **W.5c:** LinkedIn — blocked on operator action (`LINKEDIN_LI_AT` not in `~/magicapply/.env`).
- **W.8:** operator runbook + README refresh.

### Done — W.7 E2E fixture migration (2026-07-07)

| Item | Result |
|------|--------|
| E2E-smoke captures | `*-e2e-smoke-20260707` (submittable HTML) replace inline synthetic Workday + renamed acme fixtures |
| Live W.4 captures | Promoted GH Reddit, Lever FoodSmart, Ashby TRM + existing Circle Workday under `tests/fixtures/captured/` |
| `fixture_server.py` | Serves smoke + live capture DOM via registry; no inline form HTML |
| `test_e2e_captured_live.py` | Dry-run apply against all four live captures |
| `test_capture_regression.py` | Offline FormComposer regression (unchanged path) |

### Done — W.6 aggregation (2026-07-07)

| Check | Result |
|-------|--------|
| Multi-source config | 7 active sources (`greenhouse-boards`, 4× `job_url`, Indeed, Glassdoor) |
| In-run dedup | 29 raw postings → 22 unique `dedup_key`s (**7 collapsed**, incl. `lever-foodsmart` + mirror + Reddit title dupes) |
| Daily-update run 1→2 | `discovered: 0`, `already_seen: 22` both runs (stable queue) |
| Rediscover probe | Deleted Ashby TRM row → run 3: `discovered: 1`, `scored: 1`; run 4: `discovered: 0`, `already_seen: 22` |
| Keyword expansion | Added `staff ml engineer` to GH `title_keywords`; 0 new (already ingested) |

### Architecture notes (composable forms)

- **Single fill path:** `fill_dynamic_fields` / `FormComposer.fill_scanned` / `FormComposer.fill_recipe`. No `composer_from_router` fallback.
- **Workday:** recipe schemas for voluntary disclosures + self identify; imperative helpers remain only for experience/widgets/questionnaire (next migration targets).
- **Design doc:** `ARCHITECTURE_COMPOSABLE_FORMS.md` (C.0–C.6 complete).

### Handoff artifact

`docs/AI_HANDOFF.md` — VM topology, command routing, operator hard constraints.

---

**Why this plan exists:** the previous `dryrun_plan.md` (Phases A–J) and `dod_plan.md` (Phases K–U) shipped code + hermetic tests on both sides of the contract. Nothing had touched a real employer's ATS form. This plan defines done as **`magicapply apply <real-job-id> --no-headless --no-submit` drives Chromium all the way to the Submit button on a real employer form** — for each of Greenhouse / Workday / Lever / Ashby — and **`magicapply discover <profile>` returns real jobs from real sources**.

---

## 1. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| D1 | LLM path | Kept as-is. `provider: mock` throughout Phase 1. Real Anthropic gets layered in during a later Phase 2. No changes to the LLM Protocol, no changes to scoring/tailoring/narrative interfaces. |
| D2 | Resume tailoring | **Format-preserving DOCX in-place keyword swap** on the operator's original file. `docxtpl` template rendering is out. `python-docx` walks paragraphs → runs and swaps only text within existing runs, so bold/italic/font/margins survive byte-for-byte. |
| D3 | Cover letters | **Deferred (kwarg-gated), not deleted.** `TailoringPipeline(generate_cover_letter=False)` in Phase 1. `NarrativeEngine.cover_letter()` and FR-07 code path remain; Phase 2 re-enables via flag flip. |
| D4 | Prefilter location | Remote-only (US timezones). |
| D5 | Verification order | **ATS-first, source-second.** Prove each ATS handler drives real Chromium to Submit on one real posting per ATS. THEN prove each source returns real jobs for a real query. THEN combine. |
| D6 | LinkedIn cookie timing | Last. When we reach W.5c I re-send the retrieval instructions and the operator pastes the `li_at` value into `~/magicapply/.env`. |
| D7 | Career-page source | Deferred to Phase 2 unless a specific ATS handler proves to need career-page URLs as its entry point (unlikely). Phase 1 covers career pages implicitly because ATS URLs like `boards.greenhouse.io/<company>/…` are surfaced by LinkedIn/Indeed/Glassdoor search. |
| D8 | Real personal data | Extract as much as possible from the resume DOCX. Fill in missing bits per operator's Phase 1 answers: US citizen, no sponsorship needed, not willing to relocate, desired salary ~$200k, EEO = Decline to state. Real static answers get typed into real forms; browser stops one click short of Submit. |
| D9 | Screening question policy | Router tier: `static` (from `StaticAnswers`) → `library` (from `configs/answer_library.yaml`) → `narrative` (LLM via mock). Every field the router resolves via `narrative` OR marks `unhandled` gets logged to `data/answer_proposals.yaml` for later human review + promotion to the permanent library. Speed of mock now, fidelity via library growth later. |
| D10 | Failure discipline | If any real employer's form blocks progress via CAPTCHA / account creation / unbypassable challenge, document + move on. Do not fake around it. Skipping is a documented outcome, not a hidden failure. |
| D11 | Architecture changes | Follow **Design rules** (Anti-Duplication + Law of Simplicity). `ARCHITECTURE.md` + `GOF_PATTERNS.md` govern *how*; this plan governs *what to verify*. |

---

## 2. What Phase 1 does NOT do

- Does NOT validate LLM output quality. Mock canned responses only.
- Does NOT generate cover letters at pipeline run time (code path stays; wire-up disabled via kwarg).
- Does NOT actually click Submit anywhere. Every real run is `--no-submit`.
- Does NOT test career-page sources (deferred).
- Does NOT rewrite bullets via LLM into the uploaded DOCX. Only bank-synonym-driven in-place keyword swaps.

---

## 2a. GoF pattern compliance notes

Audited against `docs/GOF_PATTERNS.md` and `ARCHITECTURE.md` §4. Deliberate calls worth flagging:

- **Builder bypass in W.1 (intentional, Phase 1 only).** `TailoredResumeBuilder` documents the Builder pattern for tailored resumes. The Phase 1 DOCX path uses `InPlaceDocxTailorer` which takes `(source_docx, matched_bank, jd_terms)` directly — it does not consume `TailoredResumeBuilder.build()` output. Rationale: keyword-only in-place swaps preserve formatting byte-for-byte; the Builder was designed for the LLM-rewrites-and-renders path that Phase 2 restores. `TailoredResumeBuilder` still runs to populate `resume.yaml` (audit trail) but its output does not drive the DOCX. Phase 2 wires Builder → renderer back together when LLM output actually influences resume content beyond synonym swaps.
- **Template Method extension over per-handler duplication in W.3.** `log_observed_form` is a cross-cutting concern (every handler needs it) — same shape as CAPTCHA detection and the dry-run guard. Per extend-don't-multiply, the log call lives inside `BaseATSHandler.apply`, not in each of Greenhouse / Workday / Lever / Ashby. Handlers append to `ApplicationData.resolutions_log`; the template method writes the observation once after `_fill_dynamic`. The `observed_form_log.py` module is a single helper, not a fifth ATS handler.
- **Router dispatch consolidation (W.3).** Shared `router_dispatch.apply_router_to_form` extended existing handlers instead of four copies of router-walk logic — prefer method extraction over new handler classes.
- **Chain-of-Responsibility deliberately deferred despite crossing the threshold.** `AnswerRouter` grows a sixth strategy (`library`) between `static` and `narrative`. `GOF_PATTERNS.md` deferred CoR "if resolution grows past ~3 strategies." The router remains imperative because all six branches evaluate in one priority ladder inside one method — they are not independently registered handlers competing for the same input. A CoR refactor becomes warranted when a strategy grows its own state (e.g., an LLM-memoization tier that caches by question hash). W.3 leaves a `# NOTE: CoR threshold` comment at the top of `answer_router.py` so future contributors see the escape hatch.
- **Greenhouse discovery (W.4a) — Adapter sibling, not URL-adapter extension.** `GreenhouseAdapter` is justified: boards-api is a distinct upstream from JSON-LD pages. Extending `JobUrlAdapter` would mix HTTP+JSON API translation with HTML JSON-LD extraction (80%+ overlap risk without shared benefit). Register via existing `build_source` factory — no second source factory.

---

## 3. Phase W plan

Each phase leaves the tree green (`uv run pytest tests/unit -q` passes) unless explicitly noted otherwise.

### W.0 — Baseline config from the real resume

**Input:** `/home/ggray/Downloads/staff_plus/Geoffrey_Gray_Resume_Cluster1_Agentic_Edge_Staff_Plus.docx`.

**Files:**
- `resumes/geoffrey.yaml` (new) — `BaseResume` shape. Fields extracted from the DOCX via `python-docx`: name, contact info (email / phone / LinkedIn / GitHub if present), summary, experience entries (company / title / start / end / bullets), education, skills. Plus a new field `source_docx_path: /home/ggray/Downloads/staff_plus/Geoffrey_Gray_Resume_Cluster1_Agentic_Edge_Staff_Plus.docx` so the format-preserving tailorer knows which file to modify.
- `src/magicapply/domain/models/resume.py` — `BaseResume` grows `source_docx_path: Path | None = None`.
- `configs/base_config.yaml` (new; replaces the toy example):
  - `llm.provider: mock`
  - `static_answers`: full_name / email / phone / linkedin_url / github_url / portfolio_url / location extracted from the resume. `authorized_to_work_us: true`, `needs_sponsorship_us: false`, `desired_salary: "$200k"`, `years_of_experience` estimated from the resume's earliest role, DEI = null.
  - `scoring.prefilter`: `locations: [Remote]`, `seniority: [staff, principal]`, `must_have: []`, `exclude: [security clearance, hardware, embedded]`.
  - `scoring.threshold: 60` (loose during verification; tighten in Phase 2).
  - `paths`: `resumes_dir: ../resumes`, `data_dir: ../data`.
  - `sources: []` (populated per phase).
- `configs/profiles/staff-ds.yaml` (new):
  - `name: staff-ds`
  - `base_resume: geoffrey.yaml`
  - `sources: []` (populated per phase)
- `configs/keyword_bank.yaml` (new) — seeded from the resume's Skills section + verified terms extracted from bullets. Includes `term`, `synonyms` I infer from context, `evidence`.

**Deliverable:** I present `resumes/geoffrey.yaml` + `configs/keyword_bank.yaml` back to the operator for correction before W.1 starts.

**Exit criterion:** `magicapply doctor --root configs` reports the config valid; the extracted resume + bank have operator sign-off.

---

### W.1 — Format-preserving DOCX tailorer

> **GoF note:** this phase intentionally bypasses `TailoredResumeBuilder` for the DOCX output path. See §2a for rationale. `TailoredResumeBuilder` continues to populate `resume.yaml` as an audit trail. New class justified: no existing renderer performs run-level in-place keyword swap.

**Files:**
- `src/magicapply/infrastructure/rendering/docx_inplace.py`:
  ```python
  class InPlaceDocxTailorer:
      def render(
          self,
          source_docx: Path,
          matched_bank: list[KeywordEntry],
          jd_terms: list[str],
          out_path: Path,
      ) -> Path:
          ...
  ```
  Behavior:
  1. Open `source_docx` via `python-docx`.
  2. For each `KeywordEntry` in `matched_bank`: if any of `entry.synonyms` appears anywhere in the resume AND `entry.term` appears in `jd_terms`, swap the synonym for the canonical term in-place.
  3. The swap operates on `run.text` at the run level so all font/bold/italic/color/spacing/margins survive byte-for-byte.
  4. Save to `out_path`.
- `src/magicapply/pipelines/tailoring.py` — swap `DocxResumeRenderer` for `InPlaceDocxTailorer`. Reads `base_resume.source_docx_path`, the matched-bank list already computed for bullet-rewriting, and the extractor's JD terms; passes all three to the renderer.
- `src/magicapply/cli/composition.py:build_tailoring_pipeline` — construct the new tailorer; drop the `DocxResumeRenderer` construction.
- Delete `configs/resume_template.docx`, `src/magicapply/infrastructure/rendering/docx.py`, `scripts/generate_resume_template.py`, `tests/unit/rendering/test_docx.py`. `docxtpl` stays in `pyproject.toml` for now (small footprint; harmless), or gets removed at commit time — my preference is remove.

**Tests:**
- `tests/unit/rendering/test_docx_inplace.py`: given a small fixture DOCX with two bold + one italic + one hyperlink paragraph, verify:
  - The keyword swap replaces the synonym in exactly the run that contains it.
  - Every other run's `run.text`, `run.bold`, `run.italic`, `run.font.color.rgb`, and `run.font.size` are byte-identical to the input.
  - When no bank entry matches, the output DOCX bytes equal the input bytes.

**Exit criterion:** unit tests pass; when I run the tailorer against your real resume with a small hand-authored JD text, the produced DOCX opens cleanly, shows the intended keyword swap, and is byte-identical everywhere else.

---

### W.2 — Defer cover letter (do NOT delete)

> ARCHITECTURE.md §2 FR-07 lists Narrative Mode (cover letters + screening answers) as High priority. This phase defers the wire-up for Phase 1 without deleting the code — Phase 2 re-enables it via a single flag flip. No functional-requirement regression on the code surface. **Extend** `TailoringPipeline` with a kwarg; do not add a parallel pipeline class.

**Files:**
- `src/magicapply/pipelines/tailoring.py` — `TailoringPipeline.__init__` grows `generate_cover_letter: bool = False`. The `narrative.cover_letter(job)` call + `(app_dir / "cover_letter.md").write_text(cover)` write become guarded on `self._generate_cover_letter`. Default False in Phase 1.
- `src/magicapply/cli/composition.py:build_tailoring_pipeline` — passes `generate_cover_letter=False`. A short docstring note points at "Phase 2 sets True."
- `src/magicapply/cli/composition.py:build_application_data` — reads `cover_letter.md` from disk **only if** it exists on disk. `cover_letter=None` when the file is absent (i.e., Phase 1 tailoring path didn't write one). No change to `ApplicationData.cover_letter: str | None = None`.
- `src/magicapply/infrastructure/browser/ats/greenhouse.py`, `workday.py`, `lever.py`, `ashby.py` — existing `if data.cover_letter:` guard already handles `None` cleanly. No handler-side change needed.
- `NarrativeEngine.cover_letter()` — unchanged; still callable and still tested by unit tests.

**Tests:**
- Existing `tests/unit/pipelines/test_tailoring.py` tests that assert `cover_letter.md` exists get parameterised: `generate_cover_letter=True` → assert file present; `generate_cover_letter=False` → assert file absent.
- Existing handler tests that verify the cover-letter textarea gets filled continue to pass with a non-None `data.cover_letter` fixture — they exercise the Phase 2 wire-up path, not the pipeline default.

**Exit criterion:** `uv run pytest tests -q` green. Composition wires `generate_cover_letter=False` for the shipped Phase 1 CLI; `NarrativeEngine.cover_letter()` code path continues to exist and remains unit-tested.

---

### W.3 — Answer library infrastructure + Template-Method observation log

> **GoF notes:** the observation log is a cross-cutting concern; it lives inside `BaseATSHandler.apply` (Template Method), not per handler. Extend `AnswerRouter` with a `library` tier; do not introduce a parallel router class. The router's new library tier crosses the deferred-CoR threshold (§2a); the router stays imperative and gets a comment marker so future contributors see the escape hatch.

**Files (config layer):**
- `configs/answer_library.yaml`:
  ```yaml
  version: 1
  answers: []
  ```
- `AnswerLibrary` + `LibraryEntry` Pydantic models in `src/magicapply/config/models.py`. `LibraryEntry` fields:
  - `question: str` — canonical form of the question (as pasted from a real form's label).
  - `question_regex: str | None` — optional secondary regex match.
  - `canonical_answer: str` — verified answer to type.
  - `seen_on: list[str]` — audit trail of where this question appeared (`<ats>:<company>:<yyyy-mm-dd>`).
  - `status: Literal["verified", "proposed"]` — only `verified` entries are used by the router; `proposed` is for grep-and-review workflow.
- `src/magicapply/config/loader.py` — loads `configs/answer_library.yaml` (missing file → empty `AnswerLibrary`). Exposes on `LoadedConfig.answer_library`.

**Files (router — imperative library tier):**
- `src/magicapply/infrastructure/browser/ats/answer_router.py`:
  - Add a module-level comment marker: `# NOTE: CoR threshold — router currently has 6 imperative priority tiers. If any tier grows independent state (e.g. LLM answer memoization), refactor to formal Chain of Responsibility per docs/GOF_PATTERNS.md.`
  - New tier order: `static` (identity) → `library` (exact-match on question, then regex) → `narrative` (mock) → `select` / `check` / `file` / `unhandled`.
  - Any field the router resolves via `narrative` OR marks `unhandled` gets appended to `data/answer_proposals.yaml` with `{question, tentative_answer, seen_on: [<url>]}`. Duplicate questions get their `seen_on` list extended, not re-appended.

**Files (Template Method — observation log lives in the invariant flow):**
- `src/magicapply/infrastructure/browser/ats/base.py:ApplicationData`:
  - Grows `resolutions_log: list[ResolvedField] = Field(default_factory=list)` — a mutable list that handlers append to as each field resolves. `ResolvedField` is a small dataclass carrying `(FormField, ResolvedAnswer)` pairs so we do not thread the private `Resolved…` types through Pydantic serialisation.
- `src/magicapply/infrastructure/browser/ats/base.py:BaseATSHandler.apply`:
  - After `_fill_dynamic` (before the pre-submit CAPTCHA check), the template calls `log_observed_form(...)` **once**. Every handler subclass — Greenhouse / Workday / Lever / Ashby, and any future ATS — inherits the observation log for free. Same shape as the CAPTCHA + dry-run invariants already living in the template.
- Each handler's `_fill_dynamic` (`greenhouse.py`, `workday.py`, `lever.py`, `ashby.py`) appends to `data.resolutions_log` as it walks the router — one line per resolved field. **No handler calls `log_observed_form` directly.**

**Files (observation-log helper — single module, not a handler sibling):**
- `src/magicapply/infrastructure/browser/ats/observed_form_log.py`:
  ```python
  def log_observed_form(
      app_id: str,
      job_url: str,
      resolutions: list[ResolvedField],
      data_dir: Path,
  ) -> Path:
      ...
  ```
  Writes YAML to `data/observed_forms/<yyyymmdd-hhmmss>-<host-slug>/form.yaml`. Contents:
  - `app_id`, `job_url`, `timestamp`
  - For each field: `label`, `kind`, `selector`, `options`, `required`, `resolved_strategy`, `resolved_value` (redacted for identity fields), `unhandled_reason` if applicable.

**Tests:**
- `tests/unit/config/test_answer_library.py` — model validation; `AnswerRouter` prefers library over narrative when a matching entry exists; unknown question falls through cleanly.
- `tests/unit/browser/test_observed_form_log.py` — writing an observed-form yaml with a small resolution list produces the expected on-disk structure.
- `tests/unit/browser/test_ats_flow.py` — new case: a minimal `BaseATSHandler` subclass runs through the template method; `data.resolutions_log` accumulates during `_fill_dynamic`; the template method writes exactly one observation YAML at the expected path. Verifies the Template-Method extension pattern.

**Exit criterion:** unit tests pass; a synthetic form-scan test shows a written `data/observed_forms/…/form.yaml` and grows `data/answer_proposals.yaml`; the observation log write happens exactly once per handler run (invariant of the template method, not per subclass).

---

### W.4 — ATS handler verification against real employer forms

Four sub-phases (a/b/c/d), one per ATS. **Same loop for each:**

1. I find one real currently-open Staff Data Scientist posting on that ATS. I present the URL to the operator before driving Chromium at it — no live navigation without approval.
2. Configure a **first-class source** in `configs/base_config.yaml` and reference it from `configs/profiles/staff-ds.yaml` so `discover` can ingest the approved posting through the full pipeline — **never** seed the DB by hand, **never** bypass discovery.
   - **Greenhouse (W.4a):** `GreenhouseSource` — `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`; `boards: [<slug>]`, `title_keywords: [...]`.
   - **Workday / Lever / Ashby (W.4b–d):** use `job_url` **only if** the approved page embeds JSON-LD `JobPosting`; otherwise add or extend the appropriate per-platform Adapter (same pattern as W.4a) before proceeding.
   - **Forbidden:** provider-specific logic in `custom_url.py`; `page_parsers/` Strategy under a generic adapter.
3. `magicapply discover staff-ds --root configs` — verify Job row lands in DB with the correct title / company / URL.
4. `magicapply tailor staff-ds --root configs` — verify `data/tailored/<app_id>/resume.docx` is the original DOCX with keyword swaps applied. Spot-check by opening the DOCX.
5. `magicapply apply <job-id> --root configs --no-headless --no-submit` — the operator watches visible Chromium drive the real form.
6. Capture:
   - Final DOM: `data/observed_forms/<...>/dom.html` (`page.content()` at the moment before Submit).
   - Observed-forms log: field list + resolutions.
   - Screenshot at the Submit button: `data/observed_forms/<...>/screenshot.png` via `page.screenshot(path=...)`.
   - Every unhandled question already flowing to `data/answer_proposals.yaml` via W.3.
7. Fix loop:
   - Selector miss (identity field not found) → widen candidate list in the **existing** ATS handler (`greenhouse.py`, etc.) — extend selectors, do not fork a handler class.
   - Router `unhandled` on required field → extend `AnswerRouter` regex tables OR add a library entry (`configs/answer_library.yaml`) once operator verifies the desired answer.
   - Handler crash → fix the crash.
   - Multi-step wizard stall (Workday) → improve Next-button logic + landmark waits in the existing handler.
   - Re-run apply until: browser lands on Submit button with **zero unhandled required fields**.

**W.4a — Greenhouse handler**
- **Approved target:** `https://job-boards.greenhouse.io/reddit/jobs/7772274` (Reddit — Senior Staff Machine Learning Engineer, GenAI Platform).
- **Discovery:** `GreenhouseSource` with `boards: [reddit]` and staff-level `title_keywords` (see §0).
- **Apply:** existing `GreenhouseATSHandler` (Strategy) — extend selectors/router patterns per observation; no new handler class.
- Expected fixes: per-role custom fields (education dropdowns, "How did you hear about us?", location dropdowns) that the current `AnswerRouter` regex table doesn't cover.

**W.4b — Workday handler**
- Target: one Workday tenant Staff DS URL. Must allow guest apply — no forced account creation before the file-upload step. If the first target requires an account, substitute.
- Expected fixes: `data-automation-id` variants, step ordering that differs from my assumption, autofill-vs-manual branch selection, multi-step Next-button navigation with landmark waits.

**W.4c — Lever handler**
- Target: one `jobs.lever.co/<company>/<id>` Staff DS URL. Cleanest shape; expected to work with minimal fixes.

**W.4d — Ashby handler**
- Target: one `jobs.ashbyhq.com/<company>/<id>` Staff DS URL. Drop-zone file upload is the specific risk — the underlying `<input type="file">` may be nested in a way `set_input_files` doesn't reach.

**Exit criterion for each sub-phase:** browser reaches Submit button, zero unhandled required fields, DOM + observed-forms captured, screenshot on disk. Any employer-specific selector fix committed with a comment linking to the observation directory.

---

### W.5 — Source verification against real infrastructure

**W.5a — Indeed**
- Config:
  ```yaml
  sources:
    - type: indeed
      name: indeed-search
      queries: ["Staff Data Scientist"]
      location: "Remote"
  ```
- `MAGICAPPLY_INDEED_ACK=1` in env.
- Run `magicapply discover staff-ds --root configs`.
- Expected: near-certain Cloudflare challenge on first fetch. Adapter surfaces "Cloudflare hit, skipping query".
- Iteration options:
  1. Extend **existing** `IndeedAdapter` (stealth UA, viewport, retry) — no parallel Indeed client class.
  2. If still blocked → document Indeed as blocked, disable the adapter for real usage with a clear operator-facing message, move on.

**W.5b — Glassdoor**
- Same shape as Indeed. Optionally provide `GLASSDOOR_SESSION` cookie.
- Same iteration + documented-outcome tree.

**W.5c — LinkedIn (last)**
- I re-send `li_at` retrieval instructions to the operator.
- Operator pastes the value into `~/magicapply/.env` (git-ignored) as `LINKEDIN_LI_AT=...`.
- Config:
  ```yaml
  sources:
    - type: linkedin
      name: linkedin-search
      queries: ["Staff Data Scientist"]
  ```
- `MAGICAPPLY_LINKEDIN_ACK=1`.
- Run `magicapply discover staff-ds --root configs`.
- Iterate parser against captured real LinkedIn search HTML until ≥ 5 real jobs come back with plausible apply URLs. LinkedIn search markup uses React with hashed class names; extend **existing** `LinkedInAdapter` / `extract_job_urls` — do not add a second LinkedIn source class.
- Then pipeline one of those LinkedIn-discovered jobs through tailor + apply as a smoke check.

**Exit criterion:** each source that isn't intractably blocked returns ≥ 5 real Staff Data Scientist jobs with plausible URLs.

---

### W.6 — Multi-source aggregation + daily-update semantics

- Real config combines all sources that survived W.5.
- `magicapply discover staff-ds --root configs` → verify dedup collapses duplicates when the same posting shows up on multiple sources (same URL after canonicalization, or same `company::title` slug).
- Re-run the same command 30 seconds later → verify existing jobs get counted as `already_seen`, no duplicates added. Proves the daily-update queue works: run tomorrow, only tomorrow's new postings enter the queue.
- Change `queries` list (add "Staff ML Engineer"), re-run → verify new query results merge cleanly with existing rows.

**Exit criterion:** two consecutive `discover` runs against the same config yield `discovered: N` then `discovered: 0, already_seen: N`. Multi-source dedup collapses at least one confirmed duplicate.

---

### W.7 — Real captures → offline regression tests

- Real DOMs saved during W.4 become fixture HTML in `tests/integration/e2e/captured/<ats>-<company>-<yyyymmdd>.html`.
- Existing synthetic fixture HTML in `tests/integration/e2e/fixture_server.py` gets deleted OR marked `_synthetic_smoke.html` and de-emphasised (my preference: delete; the synthetic tests provided false confidence).
- Update `tests/integration/e2e/test_e2e_*.py` and `tests/integration/e2e/test_e2e_workday.py`, `test_e2e_lever.py`, `test_e2e_ashby.py` to serve the captured DOMs instead of the synthetic strings.
- When an employer's markup drifts in the future, the tests break with a diff pointing at the exact handler that needs a fix.

**Exit criterion:** every fixture E2E test either loads a real captured DOM or gets explicitly renamed to indicate synthetic-smoke status.

---

### W.8 — Runbook + README refresh

- `docs/OPERATOR_RUNBOOK.md`:
  - **Daily loop** — commands and expected output.
  - **Review + promote answer proposals** — how to walk `data/answer_proposals.yaml`, decide on a canonical answer, and promote to `configs/answer_library.yaml` with `status: verified`.
  - **Add / tune keyword bank** — editing `configs/keyword_bank.yaml`. When to add a term, when to add a synonym.
  - **Tune the skills / prefilter** — editing `configs/base_config.yaml`.
  - **When a form fails** — read `data/observed_forms/<...>/form.yaml`, identify unhandled fields, add router patterns or library entries.
  - **When a source stops returning jobs** — parser drift; open `tests/integration/e2e/captured/<source>-*.html` for the current test snapshot; compare against a fresh capture.
- `README.md` — refresh to reflect Phase 1 workflow: mock provider, no cover letters, format-preserving DOCX, capture-driven answer library. Point at this doc.
- Refresh `CLAUDE.md` Current-State to reflect Phase 1 reality.
- `dryrun_plan.md`, `dod_plan.md` — add a `## Superseded` banner at the top of each pointing at `final_dod_plan.md`.

**Exit criterion:** a fresh reader can walk from `README.md` → `docs/OPERATOR_RUNBOOK.md` → this file and understand what to do.

---

## 4. Phase 1 acceptance criteria (measurable, not aspirational)

The plan is complete when all of the following hold on the dev VM:

1. **`resumes/geoffrey.yaml`** exists, was extracted from the operator's real DOCX, and has operator sign-off.
2. **`configs/base_config.yaml`, `configs/profiles/staff-ds.yaml`, `configs/keyword_bank.yaml`, `configs/answer_library.yaml`** exist and load cleanly via `magicapply doctor --root configs`.
3. **Format-preserving DOCX tailorer** produces a byte-identical-except-for-keyword-swaps output on the operator's real resume against a hand-authored JD text — verified by unit test.
4. **Four ATS handlers verified** — for each of Greenhouse, Workday, Lever, Ashby, one real currently-open Staff Data Scientist posting has been driven by `magicapply apply --no-headless --no-submit` to the Submit button with zero unhandled required fields. Each verification has a captured DOM + observed-forms YAML + screenshot on disk.
5. **Three sources verified** (or documented blocked):
   - LinkedIn — one query returns ≥ 5 real jobs.
   - Indeed — either returns ≥ 5 real jobs OR is documented as Cloudflare-blocked with adapter disabled.
   - Glassdoor — same tree as Indeed.
6. **Aggregation + dedup verified** — two consecutive `magicapply discover` runs yield `discovered: N` then `discovered: 0, already_seen: N`; multi-source dedup collapses at least one confirmed duplicate.
7. **Offline regression tests** — every fixture E2E test loads either a real captured DOM from W.4 or is explicitly marked synthetic-smoke.
8. **Answer library** — `configs/answer_library.yaml` starts empty; `data/answer_proposals.yaml` grows with each real form run so the operator can review + promote entries.
9. **Runbook** — `docs/OPERATOR_RUNBOOK.md` walks the operator through the daily loop end-to-end.

**Not in scope of this plan (Phase 2 targets):**
- Real Anthropic LLM validation for scoring / narrative / bullet rewriting / cover letters.
- LLM-driven cover letter generation reinstated.
- Real submissions (Submit button actually clicked) — always operator-gated.
- Career-page source verification (deferred; only if surfaced by W.4 need).
- Screening-question memory (LLM-based caching keyed on question hash).

---

## 5. Order of work

Each row = one commit. Small, reviewable, tree stays green.

- [x] **W.0** Extract resume + baseline config; operator sign-off. — `7a7d0df`
- [x] **W.1** Format-preserving DOCX tailorer + unit tests. — `3786d86`
- [x] **W.2** Defer cover-letter step (kwarg-gated, not deleted) + green suite. — `a8251da`
- [x] **W.3** Answer library + observed-forms log + tests. — `35c2a45`
- [x] **W.4a** Greenhouse: Reddit `7772274` — discover → tailor → dry-run apply; 0 required unhandled.
- [x] **W.4b** Workday: Circle Staff DS — dry-run Submit; CF.4 closed (0 required unhandled).
- [x] **CF.0–CF.6** Composable forms migration complete on branch; legacy fallbacks removed.
- [x] **W.4c** Lever: FoodSmart — dry-run Submit; 0 required unhandled.
- [x] **W.4d** Ashby: TRM Labs — dry-run Submit; 0 required unhandled.
- [x] **W.5a** Indeed: **documented blocked** — bot protection (`Blocked - Indeed.com`); adapter logs+skips.
- [x] **W.5b** Glassdoor: **documented blocked** — Cloudflare challenge; adapter logs+skips.
- [ ] **W.5c** LinkedIn cookie retrieval → source verify against real search HTML.
- [x] **W.6** Multi-source aggregation + daily-update semantics verified (2026-07-07).
- [x] **W.7** Real captures → E2E + offline regression; synthetic inline HTML retired (`*-e2e-smoke-*` for yes-submit only).
- [ ] **W.8** Runbook + README + CLAUDE.md refresh; supersede banners on old plan files.

---

## 6. Failure discipline

At any point when a real employer form / real source blocks progress:

- **Do not fabricate around it.** Do not fill fake data. Do not click through error modals blindly.
- **Document what happened.** Capture the failure state: DOM, screenshot, observed-forms log, error message.
- **Decide explicitly.** Fix in code (if the fix is scoped), substitute the target (find another employer's posting on the same ATS), or accept as a documented Phase 1 limitation (rare — should be justified in the runbook).
- **Never regress the "reach Submit button" claim** without an equivalent capture proving it worked on a different real employer.

---

## 7. What operator action unblocks each phase

| Phase | Operator action required |
|-------|--------------------------|
| W.0 | Approve extracted `resumes/geoffrey.yaml` + `configs/keyword_bank.yaml`. |
| W.1 | None (unit tests). |
| W.2 | None. |
| W.3 | None. |
| W.4a–d | Approve the specific real URL I present before I drive Chromium at it. Watch the visible Chromium session; confirm the browser reached Submit. |
| W.5a | None (adapter attempts; documents blocked). |
| W.5b | None (or provide `GLASSDOOR_SESSION` if available). |
| W.5c | Retrieve `li_at` cookie from a logged-in browser session using the instructions I resend, paste value. |
| W.6 | None. |
| W.7 | None. |
| W.8 | Review the runbook. |

---

**End of plan. Save location: `/mnt/storage/VMs/dev/magicapply/final_dod_plan.md`.**