# MagicApply — Definition-of-Done Maturity Plan

**Status:** proposed 2026-07-03
**Precedent:** `dryrun_plan.md` shipped Phases A–J (`dca2d1d..e68cdd3`) and brought the pipeline to hermetic + live-gated dry-run against Greenhouse. This plan closes the gap to the full user workflow: multi-platform discovery, keyword-bank-driven bullet injection, real file upload, per-role form discovery, three additional ATS handlers, and retry/intervention polish.

## 1. Definition of Done (reference)

1. **Setup** — multiple search profiles with per-profile base resumes, static/personal answers, a personal keyword bank, scoring thresholds.
2. **Discovery & scoring** — aggregate from LinkedIn + Indeed + Glassdoor + custom career URLs; dedupe; score.
3. **Application processing** — pick the profile's base resume, extract JD keywords, pull matching bank entries, minimally rewrite bullets to reflect matches, generate cover letter, answer screening questions.
4. **Submission** — full automation across Greenhouse, Lever, Workday, Ashby.
5. **Intervention** — surface a browser only on CAPTCHA / unrecoverable failure; retry / re-review flows exist.

## 2. Locked design decisions

| # | Decision | Choice |
|---|---|---|
| D1 | LinkedIn discovery | **Full scraper** with authenticated session via `LINKEDIN_LI_AT`. Aggressive rate limits + prominent ToS disclaimer on first use. |
| D2 | ATS priority after Greenhouse-full | **Workday second**, then Lever, then Ashby. Workday is the hardest but the enterprise coverage payoff justifies leading with it. |
| D3 | Keyword bank shape | **Global bank at `configs/keyword_bank.yaml` + optional per-profile overrides** at `configs/profiles/<name>-keywords.yaml`. Override entries extend or replace by `term`. |
| D4 | Resume file rendering | **python-docx-template (docxtpl)** producing `.docx`. Ships one canonical template at `configs/resume_template.docx`; users can drop their own. |
| D5 | Sequencing | Strict Wave 1 → Wave 2 → Wave 3 → Wave 4. Each wave leaves the tree green (`uv run pytest tests -q`) and demonstrably valuable. |

## 3. Current gap (from CLAUDE.md's shipped state)

| DoD area | Status | Reference |
|---|---|---|
| Multiple profiles + base resume selection | ✅ | `config/models.py:Profile`, `cli/composition.py:_load_base_resume` |
| Static identity fields | ✅ | `config/models.py:StaticAnswers` |
| Static DEI / work-auth / sponsorship pre-fill | ⚠️ present in `StaticAnswers` but no ATS handler reads them | `browser/ats/greenhouse.py:_fill_static` (identity fields only) |
| Keyword bank | ❌ | none |
| JD keyword extraction | ❌ | none |
| Cover letter generation | ✅ | `domain/resumes/narrative.py:NarrativeEngine.cover_letter` |
| Screening question generation | ⚠️ engine exists but not called | `NarrativeEngine.answer` |
| Bullet rewriting | ❌ blocked by policy | `domain/resumes/tailor.py:10-13` — MVP-scope comment explicitly forbids bullet rewording |
| Resume file rendering + upload | ❌ | none |
| Greenhouse per-role custom fields | ❌ stub | `browser/ats/greenhouse.py:43-49` |
| LinkedIn discovery | ⚠️ raises `SourceError` | `infrastructure/sources/linkedin.py:38-43` |
| Indeed discovery | ❌ | none |
| Glassdoor discovery | ❌ | none |
| Lever handler | ❌ | not registered in `ATSHandlerFactory` |
| Workday handler | ❌ | not registered |
| Ashby handler | ❌ | not registered |
| CAPTCHA → NEEDS_INTERVENTION | ✅ | `browser/ats/base.py:81-99`, `browser/captcha.py` |
| Surface browser on failure | ⚠️ | state exists; browser is never actually kept open |
| Retry FAILED apps | ❌ | `pipelines/apply.py:47-50` requires TAILORED state |

---

## Wave 1 — Greenhouse production quality (Phases K–N)

Goal: one Greenhouse submission with a real DOCX resume, all custom fields understood, keyword-bank-informed bullets, screening questions answered — all against real Greenhouse jobs, verified by the existing live-gated harness.

### Phase K — DOCX rendering + upload + extended static answers

**New deps:**
- Add `docxtpl>=0.16` to `pyproject.toml`.

**New files:**
- `src/magicapply/infrastructure/rendering/__init__.py`, `docx.py` — one class `DocxResumeRenderer`:
  ```python
  class DocxResumeRenderer:
      def __init__(self, template_path: Path): ...
      def render(self, tailored: TailoredResume, out_path: Path) -> Path: ...
  ```
  Uses `docxtpl.DocxTemplate` with a context dict derived from `TailoredResume.model_dump()`.
- `configs/resume_template.docx` — canonical template shipped with the code (identity block, `{% for exp in experience %}` for jobs, `{% for edu in education %}`, skills line). Users can drop their own at the same path.
- `src/magicapply/config/models.py` — extend `StaticAnswers`:
  ```python
  # already: full_name, email, phone, location, linkedin_url, github_url,
  # portfolio_url, work_authorization, requires_sponsorship, gender,
  # ethnicity, veteran_status, disability_status
  # add:
  authorized_to_work_us: bool | None = None    # explicit yes/no separate from "H1B"/"citizen" text
  needs_sponsorship_us: bool | None = None
  years_of_experience: int | None = None
  desired_salary: str | None = None
  hispanic_latino: bool | None = None
  ```

**File edits:**
- `src/magicapply/pipelines/tailoring.py` — after writing `resume.yaml` and `cover_letter.md`, invoke `DocxResumeRenderer.render(tailored, app_dir / "resume.docx")`. `app.tailored_path` still points at the dir; `resume.docx` sits next to the YAML.
- `src/magicapply/infrastructure/browser/ats/greenhouse.py:43-49` — `_fill_dynamic` grows a resume-upload step:
  ```python
  resume_path = data.static_answers.get_resume_path(data)   # helper reading data.tailored_resume.job_id -> data_dir path
  with contextlib.suppress(Exception):
      page.set_input_files("input[type='file'][name*='resume']", str(resume_path))
  ```
  Selector list is best-effort: `input[type='file'][name*='resume']`, `input[type='file']#s3_upload_for_resume` (Greenhouse's legacy id), and `input[type='file']` as a last resort — first successful call wins.
- `src/magicapply/cli/composition.py:build_tailoring_pipeline` — inject the renderer; wire a `DocxResumeRenderer(template_path=…)` reading from `loaded.resumes_dir().parent / "configs" / "resume_template.docx"` with a graceful "template missing" error.

**New tests:**
- `tests/unit/rendering/test_docx.py` — round-trip a fixture `TailoredResume` through the renderer, assert the produced .docx opens (via `docx.Document`) and contains the tailored summary + at least one experience bullet.
- `tests/unit/pipelines/test_tailoring.py` — extend the happy-path assertion: `resume.docx` exists next to `resume.yaml` and is a valid DOCX (magic bytes: `PK\x03\x04`).
- `tests/unit/browser/test_ats_flow.py` — extend the Greenhouse handler flow with a `set_input_files` recording assertion (fake page grows a `set_input_files` method).

**E2E extension (updates goldens):**
- `tests/integration/e2e/fixture_server.py` — the apply form gains `<input type="file" name="resume">` and the POST handler stores the file bytes; parsing multipart/form-data instead of urlencoded. The recorded submission now includes a `_files` sub-dict.
- `tests/integration/e2e/test_e2e_local.py::TestE2EYesSubmit` — asserts each submission contains a non-empty `_files.resume` blob whose bytes look like a DOCX.

**Verify:**
```
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests -q'
```

---

### Phase L — Greenhouse form discovery + screening question flow

Goal: every field on a real Greenhouse form is either filled from `StaticAnswers`, filled with an LLM-generated answer via `NarrativeEngine.answer`, or explicitly reported as "unhandled" without failing the submission.

**New files:**
- `src/magicapply/infrastructure/browser/ats/form_scan.py` — a small utility that reads the page DOM and returns a structured field list:
  ```python
  @dataclass
  class FormField:
      selector: str
      label: str
      kind: Literal["text", "textarea", "select", "checkbox", "radio", "file", "hidden"]
      options: list[str] | None = None    # for select / radio / checkbox groups
      required: bool = False

  def scan_form(page: PageDriver, form_selector: str = "form") -> list[FormField]: ...
  ```
  Uses Playwright's `page.query_selector_all` to walk labels and inputs.

- `src/magicapply/infrastructure/browser/ats/answer_router.py` — one class:
  ```python
  class AnswerRouter:
      """Route a scanned form field to (a) a StaticAnswers value, (b) a
      NarrativeEngine.answer call, or (c) unhandled."""

      def __init__(self, static_answers: StaticAnswers, narrative: NarrativeEngine):
          ...

      def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
          # 1. Label -> StaticAnswers key via a compiled regex table.
          # 2. If open-ended (textarea, or `select` with "Other" open-text),
          #    dispatch to narrative.answer(job, field.label).
          # 3. Yes/no select: LLM answer post-processed to match an option.
          # 4. Anything left is logged as UNHANDLED; handler continues.
  ```

**File edits:**
- `src/magicapply/infrastructure/browser/ats/greenhouse.py` — `_fill_dynamic` rewrites:
  ```python
  fields = scan_form(page, form_selector="#application_form")
  router = _router  # composition-injected
  unhandled: list[str] = []
  for field in fields:
      resolved = router.resolve(field, data.job)
      if resolved.strategy == "static":
          page.fill(field.selector, resolved.value)
      elif resolved.strategy == "select":
          page.select_option(field.selector, resolved.value)
      elif resolved.strategy == "check":
          if resolved.value:
              page.check(field.selector)
      elif resolved.strategy == "narrative":
          page.fill(field.selector, resolved.value)
      elif resolved.strategy == "file":
          page.set_input_files(field.selector, resolved.value)
      else:
          unhandled.append(field.label)
  if unhandled:
      logger.warning("Greenhouse unhandled fields: %s", unhandled)
  ```
- `ApplicationData` gains `job: Job` so the router can hand JD context to the narrative engine. Composition already has `job`; propagate through the existing `build_application_data`.
- `src/magicapply/infrastructure/browser/ats/base.py` — `BaseATSHandler.__init__` grows an optional `answer_router: AnswerRouter | None`. Handlers that want form discovery accept one; simpler handlers can ignore it.

**Playwright surface (`PageDriver`):**
- Currently the Protocol has `goto`, `fill`, `click`, `content`. Extend to include `select_option`, `check`, `set_input_files`, `query_selector_all` (as a very thin wrapper). Fakes in tests grow the same methods.

**New tests:**
- `tests/unit/browser/test_form_scan.py` — feed the scanner a raw HTML string via a fake page; verify field extraction and required detection.
- `tests/unit/browser/test_answer_router.py` — every routing branch:
  - Static: `first_name` label → routed to `static_answers.full_name` split.
  - Yes/no select: "Are you authorized to work?" → routed to `authorized_to_work_us` and mapped to the exact option string.
  - Open-ended: "Why do you want to work here?" → routed to `NarrativeEngine.answer` (mocked; assert the label was passed as the question).
  - Unhandled: opaque label → returns `UNHANDLED`.

**E2E extension:**
- Fixture apply form gains one Yes/No select ("Are you authorized to work in the US?") and one open-ended textarea ("Why do you want to work at Acme?"). Yes-submit test asserts both were filled.

---

### Phase M — Keyword bank system

Goal: `configs/keyword_bank.yaml` (global) + optional per-profile overrides at `configs/profiles/<name>-keywords.yaml`. A JD keyword extractor identifies which bank entries are relevant per job.

**New files:**
- `src/magicapply/config/models.py`:
  ```python
  class KeywordEntry(BaseModel):
      model_config = _Strict
      term: str
      synonyms: list[str] = Field(default_factory=list)
      evidence: str          # a short phrase the tailorer may weave into a bullet
      tags: list[str] = Field(default_factory=list)

  class KeywordBank(BaseModel):
      model_config = _Strict
      version: Literal[1] = 1
      keywords: list[KeywordEntry] = Field(default_factory=list)

      def extend_with(self, override: "KeywordBank") -> "KeywordBank":
          """Merge override on top of self; override entries win by term."""
  ```
- `configs/keyword_bank.example.yaml` — a small seed (5–10 entries) covering common backend/frontend terms.

**File edits:**
- `src/magicapply/config/loader.py`:
  - `_load_keyword_bank(root: Path) -> KeywordBank` reading `<root>/keyword_bank.yaml` (missing file → empty bank, not an error — banks are optional).
  - `LoadedConfig` gains `keyword_bank: KeywordBank`.
  - Per-profile override applied inside `load_config`: if `profile.keyword_bank_override` is set, resolve, validate, `bank.extend_with(override)`. Each profile ends up with its own effective bank; store as `loaded.effective_bank(profile)`.
- `src/magicapply/config/models.py:Profile` — add optional `keyword_bank_override: str | None = None` (path relative to config root).

- `src/magicapply/domain/keywords/` (new package):
  - `extractor.py` — `KeywordExtractor(llm, prompt)` returns `list[str]` of JD-mentioned terms. LLM prompt lives in `configs/prompts.yaml` under a new `keyword_extraction:` key; `PromptsConfig` grows the field. Extraction returns a JSON array.
  - `matcher.py` — `match_bank(extracted: list[str], bank: KeywordBank) -> list[KeywordEntry]`. Case-insensitive substring match on `term` and `synonyms`.

**New tests:**
- `tests/unit/config/test_keyword_bank.py` — model validation, extend_with semantics.
- `tests/unit/config/test_loader.py` — `keyword_bank.yaml` loads; missing file is fine (empty bank); per-profile override merges correctly.
- `tests/unit/keywords/test_extractor.py` — with `MockLLMClient` seeded on the new prompt shape.
- `tests/unit/keywords/test_matcher.py` — term match, synonym match, no double-count.

---

### Phase N — Evidence-based bullet injection

Goal: `Tailorer` picks matched keyword-bank entries and rewrites specific existing bullets to fold the evidence phrase in. Policy is strict: only bank evidence + base-resume facts may appear. Nothing is invented.

**File edits:**
- `src/magicapply/domain/resumes/tailor.py`:
  - Remove the "no bullet rewording" line from the module docstring (kept the intent, weakened the letter — bullets are now touched only through the bank).
  - `Tailorer.__init__` grows a `matched_bank: list[KeywordEntry]` optional; if empty, current summary-only behavior is preserved (regression-safe).
  - New private method `_rewrite_bullets(experience, matched_bank) -> list[ExperienceEntry]`:
    - Sends the LLM a bullet + a subset of relevant bank entries; asks for a rewritten bullet that must (a) use ONLY facts already in the bullet or in the bank entry's `evidence`, (b) not invent employers, dates, tools, or metrics, (c) return the bullet unchanged if no bank entry naturally applies.
    - Prompt lives in `configs/prompts.yaml:bullet_rewrite`; `PromptsConfig` gains the field.
- `src/magicapply/cli/composition.py:build_tailorer` — pull `matched_bank = match_bank(extractor.extract(job), loaded.effective_bank(profile))` per-job, pass it into `Tailorer`.
- `src/magicapply/pipelines/tailoring.py` — record per-app which bank entries matched into `TailoredResume.changes` so the audit trail is real.

**New tests:**
- `tests/unit/resumes/test_tailor.py`:
  - No matched bank → bullets untouched (regression).
  - Matched bank + relevant bullet → LLM invoked; verify bank evidence was in the prompt.
  - Golden bullets in `tests/goldens/tailoring/<slug>/resume.yaml` regenerated.

**E2E extension:**
- Fixture E2E seeds a small `keyword_bank.yaml`, runs discover → tailor → run, and asserts a specific bank evidence phrase shows up in one of the tailored resume bullets and the cover letter.

**Wave 1 acceptance criterion:** run `magicapply run senior-swe --yes-submit` against three real Greenhouse jobs from a curated `job_url` list; all three land in `APPLIED` (or `NEEDS_INTERVENTION` if a CAPTCHA fires), each with a real DOCX uploaded and each with all form fields either filled or logged as unhandled.

---

## Wave 2 — Multi-source discovery (Phases O–Q)

### Phase O — LinkedIn full scraper

**Locked design (D1):** authenticated Playwright session; `LINKEDIN_LI_AT` cookie. First-run prints a ToS notice and requires `MAGICAPPLY_LINKEDIN_ACK=1` to proceed. Aggressive rate limit (default 10 requests/minute, tunable per source).

**New files:**
- `src/magicapply/infrastructure/sources/linkedin.py` — replace the current stub:
  - `LinkedInAdapter.discover()` opens a shared `PlaywrightSession` (headless), loads `LINKEDIN_LI_AT` cookie into context, iterates each configured query URL like `https://linkedin.com/jobs/search/?keywords=<q>`, extracts job cards with `data-job-id` attributes, then visits each job page and pulls JSON-LD (LinkedIn does expose it on job detail).
  - Rate limiter: `RateLimiter(config.rate_limit_per_minute)` — already exists.
  - Session persistence: uses `session.save_state()` on a per-source `storage_state.json` under `data/linkedin/`.

**File edits:**
- `src/magicapply/config/models.py:LinkedInSource` — clarify docstring; add `search_url_template: str = "https://linkedin.com/jobs/search/?keywords={query}"` for future flexibility.
- `.env.example` — add `MAGICAPPLY_LINKEDIN_ACK`.
- `docs/VM_DEV.md` — env-var table gains the ack.

**New tests:**
- `tests/unit/sources/test_linkedin.py` — parser tests only; feed a saved LinkedIn search HTML fixture and one job detail HTML fixture; assert the produced `Job` list. No network.
- `tests/integration/sources/test_linkedin_live.py` — new file marked `@pytest.mark.integration @pytest.mark.linkedin`, gated on `MAGICAPPLY_LINKEDIN_TESTS=1` + a valid `LINKEDIN_LI_AT`. Asserts the adapter completes without an auth error and returns ≥1 job for a broad query. Skips otherwise.

**Risk callout in commit:** LinkedIn actively prosecutes scrapers; the built-in rate limiter is the operator's responsibility to leave conservative. Documented ack gate is the mitigation.

### Phase P — Indeed source

- Playwright-based adapter under `infrastructure/sources/indeed.py`.
- Handles Cloudflare-style challenges with a `state="needs_intervention"` return per source — a first-class variant of the SourceError path so the pipeline can surface it in `magicapply review` alongside failed applications.
- Live test gated similarly to LinkedIn.
- Rate limit conservative (5/min default).

### Phase Q — Glassdoor source

- Same shape as Indeed. Login often required — session cookie via `GLASSDOOR_SESSION` env.
- Small parser test (saved HTML fixture) + live-gated test.

**Wave 2 acceptance criterion:** `magicapply discover senior-swe` runs against a config with one source per platform (LinkedIn queries + Indeed queries + Glassdoor queries + custom URLs) and returns a merged, deduplicated job list. No cross-source duplicates.

---

## Wave 3 — Additional ATS handlers (Phases R–T)

Each phase: selector research, handler subclass, fixture E2E, golden diffs.

### Phase R — Workday handler (chosen: D2)

Workday is a multi-step wizard:
1. Sign-in / create account (skip if session persists)
2. My Information (identity + contact)
3. My Experience (paste resume text OR upload → autofill)
4. Application Questions (screening + DEI)
5. Voluntary Disclosures
6. Review & Submit

**Approach:**
- `src/magicapply/infrastructure/browser/ats/workday.py` — new `WorkdayHandler(BaseATSHandler)`:
  - `matches`: substring match on `myworkdayjobs.com` and `wd*.myworkday.com`.
  - Uses Workday's `data-automation-id` selector convention.
  - Multi-step navigation: helper `_click_next(page)` waits for the next step's landmark.
  - Resume upload in step 3 via `page.set_input_files` on the file input inside `#drop-zone`.
  - Screening questions in step 4 routed through the Phase L `AnswerRouter`.
- Fixture E2E: `tests/integration/e2e/fixture_server.py` gains a `/workday/<slug>/apply` route serving a multi-step HTML shell that mimics Workday's step landmarks. Tests it end-to-end.
- Live-gated test with a real Workday job URL from a config env var.

### Phase S — Lever handler
- Cleaner than Workday; single-page form, `posting-form` container. Handler ships in one commit.
- Fixture E2E + goldens.

### Phase T — Ashby handler
- Modern React form; selectors are stable (`data-testid` attributes). Handler ships in one commit.
- Fixture E2E + goldens.

**Wave 3 acceptance criterion:** `magicapply run senior-swe --yes-submit` against a mixed list of one Greenhouse + one Workday + one Lever + one Ashby job URL succeeds on all four (or reports NEEDS_INTERVENTION with a clear reason).

---

## Wave 4 — Operational polish (Phase U)

### Phase U.1 — Retry command

- `magicapply apply <job-id> --retry` accepts a `FAILED` application (state transition already legal: `FAILED → APPLYING`).
- `apply_one` relaxes its state guard: accepts `TAILORED` or `FAILED` when `retry=True`.
- CLI adds the flag.

### Phase U.2 — Interactive intervention

- `magicapply apply <job-id> --headed` (or default when the flow lands in `NEEDS_INTERVENTION`) opens Chromium in **headed** mode, prints the URL, and waits for a `press-enter-when-done` prompt on stdin.
- After the operator finishes manually, the CLI records the outcome (`APPLIED` if the operator confirms; `SKIPPED` otherwise).

### Phase U.3 — Docs + acceptance suite

- `README.md` — new; user-facing quickstart, config wizard, keyword-bank primer, per-ATS notes.
- `tests/acceptance/test_dod.py` — one exhaustive scenario that mirrors the DoD workflow start-to-finish against a fixture (Wave 1 + 2 + 3 machinery all in-scope). Marked `slow`.
- Update `CLAUDE.md` current-state section as each phase lands.

**Wave 4 acceptance criterion:** running through the README's quickstart in the VM ends with a real DOCX submitted (or dry-run APPLIED) against a real job posting on each of the four supported ATSes.

---

## 4. Order of work (checklist)

Each row is a single commit that leaves the tree green.

- [ ] **K.1** Add `docxtpl` dep + `DocxResumeRenderer` + template file
- [ ] **K.2** Extend `StaticAnswers` with DEI/authorization fields + tests
- [ ] **K.3** `TailoringPipeline` writes `resume.docx`; renderer smoke tests
- [ ] **K.4** Greenhouse resume upload via `set_input_files`; extend `PageDriver`, fake page, E2E fixture form (multipart), golden diffs
- [ ] **L.1** `FormField` model + `scan_form()` utility + tests
- [ ] **L.2** `AnswerRouter` (static / narrative / select / check / file / unhandled) + tests
- [ ] **L.3** Greenhouse `_fill_dynamic` rewritten to use the router; extend fixture form with yes/no + open-ended question
- [ ] **M.1** `KeywordEntry` / `KeywordBank` config models + loader
- [ ] **M.2** Per-profile override merging + `LoadedConfig.effective_bank(profile)`
- [ ] **M.3** `KeywordExtractor` + prompt in `configs/prompts.yaml`
- [ ] **M.4** `match_bank` + tests
- [ ] **N.1** `Tailorer._rewrite_bullets` (evidence-only) + prompt
- [ ] **N.2** Composition wires the extractor + matched bank into `build_tailorer` per-job
- [ ] **N.3** E2E fixture asserts bank evidence in an output bullet; goldens regenerated
- [ ] **Wave 1 acceptance:** three real Greenhouse jobs `--yes-submit`
- [ ] **O.1** LinkedIn adapter: session + cookie load + search page fetch + parser
- [ ] **O.2** Job detail extraction (JSON-LD + fallback)
- [ ] **O.3** ToS ack gate (`MAGICAPPLY_LINKEDIN_ACK`) + rate limit + docs
- [ ] **P.1** Indeed adapter (Playwright-based) + Cloudflare-challenge surfacing
- [ ] **Q.1** Glassdoor adapter + session env var
- [ ] **Wave 2 acceptance:** unified discover across four source kinds, no duplicates
- [ ] **R.1** Workday handler skeleton + fixture E2E (multi-step shell)
- [ ] **R.2** Workday step 2 (identity) + step 3 (resume upload) filled via router
- [ ] **R.3** Workday step 4 (screening/DEI) via router; goldens
- [ ] **R.4** Workday live-gated test
- [ ] **S.1** Lever handler + fixture E2E + goldens + live-gated test
- [ ] **T.1** Ashby handler + fixture E2E + goldens + live-gated test
- [ ] **Wave 3 acceptance:** four mixed-ATS jobs `--yes-submit`
- [ ] **U.1** Retry command + relaxed `apply_one` state guard
- [ ] **U.2** Interactive `--headed` NEEDS_INTERVENTION loop
- [ ] **U.3** README + acceptance test
- [ ] **Wave 4 acceptance:** README quickstart walkthrough in the VM

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LinkedIn / Indeed / Glassdoor ToS enforcement | ack env vars, conservative default rate limits, prominent disclaimer in `.env.example` and `README.md`, session-cookie auth requires the operator to obtain their own |
| Workday selector drift across employers | test against multiple employer subdomains in the live-gated suite; `data-automation-id` is Workday-standard and reasonably stable |
| Bullet rewriting hallucinates | strict "only bank + base-resume facts" prompt, plus a post-check that no numeric literals appear in the rewrite that weren't in the source, plus goldens |
| DOCX template pain | ship one canonical template that just works; operators only touch it if they want branding |
| Resume file paths embedded in DB | `tailored_path` is already a directory, `resume.docx` sits inside — no schema change needed |
| Live-gated tests are flaky against real sites | keep the live-gated tests off the default run; document as opt-in in `docs/VM_DEV.md` |
| Cost of running LLM on hundreds of jobs | prefilter cuts most; caching is already wired (`SystemBlock.cacheable`); goldens keep tests off the wire |

## 6. Open decisions

- **Screening-question memory.** When the same screening question appears across employers, should we cache the previous LLM answer keyed on the question hash? Cheap win, deferred to Wave 4 unless you want it in Wave 1.
- **Resume template customization surface.** One template ships; per-profile template swap-out is trivial to add. Deferring unless the acceptance suite reveals a real need.
- **PDF alongside DOCX.** D4 chose DOCX; some ATSes will refuse anything else. If a Workday live test fails on file-type, revisit and add PDF as a secondary rendered output.
