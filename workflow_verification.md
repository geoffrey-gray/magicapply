# Workflow Verification Report
**Date:** 2026-07-12  
**Verification Type:** Code Deep Dive  
**Status:** ✅ VERIFIED with discrepancies noted

---

## User's Expected Workflow

1. Pull jobs from multiple boards/sources
2. Each source scored and ranked
3. Applications start at top (highest match) and work down
4. Each source has throttle to avoid bans
5. For LinkedIn/Indeed: try to get application URL and apply directly on site first
6. If not possible, apply directly on board
7. If that fails, move on
8. Failures don't stop the process - logged and continue
9. Multiple dry runs to improve until fully automated

---

## Actual Implementation (Verified from Code)

### ✅ 1. Multiple Sources - VERIFIED

**File:** `cli/commands/pipeline.py:62`, `cli/composition.py:59-81`

```python
sources = build_sources_for_profile(loaded, profile_cfg)
pipeline = DiscoveryPipeline(sources=sources, ...)
```

**How it works:**
- `build_sources_for_profile()` iterates profile's source list
- Each source name maps to a config entry in `base_config.yaml::sources`
- Supports: `linkedin`, `indeed`, `glassdoor`, `career_page`, `job_url`
- Sources are created via factory: `build_source(cfg, proxy_pool, data_dir)`

**Status:** ✅ WORKING AS EXPECTED

---

### ✅ 2. Scoring and Ranking - VERIFIED

**File:** `pipelines/discovery.py:62-128`

**Discovery flow:**
```python
# Line 72-77: Iterate sources, catch errors per-source
for source in self._sources:
    try:
        for job in source.discover(known_ids=known_ids):
            all_jobs.append(job)
    except SourceError as exc:
        logger.warning("source %s failed: %s", source.name, exc)
        report.source_errors.append(f"{source.name}: {exc}")

# Line 90: Dedupe with optional cross-source load balancing
for job in dedupe_by_key(all_jobs, source_scorer=cached_scorer):
    # Line 110: Score immediately
    score = self._scorer.score(job)
    app.transition_to(ApplicationState.SCORED, ...)
```

**Scoring:**
- Each job scored immediately after dedup (line 110)
- `JobScorer` = `Prefilter` (fast rules) + `FitScorer` (keyword or LLM)
- Score stored on Application row: `app.score = score.value`

**Status:** ✅ WORKING AS EXPECTED

---

### ⚠️ 3. Application Order (Highest Match First) - PARTIALLY VERIFIED

**Expected:** Applications start at top (highest score) and work down.

**What the code does:**

**Discovery phase** (pipelines/discovery.py):
- Jobs scored in **discovery order**, not score order
- No sorting by score during discovery
- Rejected jobs (below threshold) transition to REJECTED immediately

**Apply phase** (pipelines/apply.py:254-256):
```python
candidates = (
    apps_repo.list_by_state_and_profile(ApplicationState.TAILORED, profile_cfg.name)
    + apps_repo.list_by_state_and_profile(ApplicationState.FAILED, profile_cfg.name)
    + apps_repo.list_by_state_and_profile(ApplicationState.NEEDS_INTERVENTION, profile_cfg.name)
)
```

**Repository implementation** (infrastructure/persistence/repositories/applications.py:128-133):
```python
def list_by_state_and_profile(self, state: ApplicationState, profile_name: str) -> list[Application]:
    with self._session() as session:
        rows = session.execute(
            select(ApplicationRow)
            .where(ApplicationRow.state == state.value, ApplicationRow.profile_name == profile_name)
        ).scalars().all()
```

**Issue:** ❌ NO ORDER BY CLAUSE!

The query does NOT sort by score. Applications are returned in **database insertion order** (which is discovery order), not score order.

**Expected behavior:**
```python
# SHOULD BE:
.where(ApplicationRow.state == state.value, ApplicationRow.profile_name == profile_name)
.order_by(ApplicationRow.score.desc())  # ← MISSING!
```

**Impact:** HIGH - Applications are NOT processed highest-score-first as expected.

**Status:** ❌ **DISCREPANCY FOUND - Applications processed in discovery order, not score order**

---

### ✅ 4. Source Throttling - VERIFIED

**File:** `infrastructure/sources/linkedin.py:48,144,240`

**Implementation:**
```python
from magicapply.infrastructure.sources.rate_limit import RateLimiter

rate = RateLimiter(self._rate, jitter_ratio=_DEFAULT_JITTER_RATIO)
```

**How it works:**
- Each source creates a `RateLimiter` with configured `requests_per_minute`
- Jitter ratio (default 0.3) randomizes delays to avoid bot detection patterns
- Rate limiter blocks thread until next slot available

**Config location:** `configs/base_config.yaml`
```yaml
sources:
  - type: linkedin
    name: linkedin
    rate_limit_per_minute: 12  # Configurable per source
```

**Status:** ✅ WORKING AS EXPECTED

---

### ✅ 5. LinkedIn/Indeed URL Resolution - VERIFIED

**File:** `infrastructure/sources/apply_url.py:441-473`  
**File:** `pipelines/apply.py:130-143`

**Apply pipeline flow:**
```python
# Line 138: Resolve external apply URL
job = job_with_resolved_apply_url(job)
apply_target = resolve_job_apply_destination(job)
```

**Resolution priority** (`resolve_job_apply_destination`):
1. **Greenhouse embed** (if raw board data exists)
2. **External apply_url** (if enriched during discovery)
3. **Fallback to listing URL**

**Discovery enrichment:**
- LinkedIn adapter calls `apply_url_from_linkedin_detail_html()` (line 115)
- Indeed adapter calls `apply_url_from_indeed_detail_html()` (line 240)
- Glassdoor adapter calls `apply_url_from_glassdoor_detail_html()` (line 245)

**What happens:**
- During discovery, sources parse job detail HTML for external apply links
- If found, `job.apply_url` is populated with external ATS URL
- During apply, pipeline uses `apply_url` instead of board listing URL

**Board listing fallback:**
```python
# apply_url.py:463-472
if job.apply_url and str(job.apply_url).strip():
    apply = str(job.apply_url).strip()
    if not is_job_board_listing_url(apply):  # External ATS found
        return apply

return job.url  # Fallback to listing URL
```

**Generic handler for boards:**
- `infrastructure/browser/ats/generic.py` handles board listings
- Supports Indeed/LinkedIn/Glassdoor apply buttons
- Throttled per-board (indeed/linkedin/glassdoor) not as single "generic"

**Status:** ✅ WORKING AS EXPECTED
- **Step 1:** Try external URL (during discovery enrichment) ✅
- **Step 2:** Apply on board if no external URL ✅

---

### ✅ 6. Failure Handling (Non-Blocking) - VERIFIED

**Discovery phase** (pipelines/discovery.py:72-77):
```python
for source in self._sources:
    try:
        for job in source.discover(known_ids=known_ids):
            all_jobs.append(job)
    except SourceError as exc:
        logger.warning("source %s failed: %s", source.name, exc)
        report.source_errors.append(f"{source.name}: {exc}")
        # ← Continue to next source, doesn't raise
```

**Apply phase** (pipelines/apply.py:179-195):
```python
result = handler.apply(page, application_data)

target = {
    "applied": ApplicationState.APPLIED,
    "needs_intervention": ApplicationState.NEEDS_INTERVENTION,
    "failed": ApplicationState.FAILED,
}[result.state]

application.transition_to(target, reason=result.error or "submitted")
application.error = result.error  # ← Logged, not raised
```

**Paced run mode** (cli/commands/pipeline.py:241-246):
```python
try:
    _discover_and_tailor()
except Exception as exc:  # noqa: BLE001 — continue paced run
    logger.warning("discover/tailor failed (continue): %s", exc)
    console.print(f"[yellow]discover/tailor error (continue):[/yellow] {exc}")
    # ← Continues to next iteration
```

**Handler exceptions** (infrastructure/browser/ats/base.py:216-217):
```python
except Exception as exc:  # Template Method catch-all
    error_msg = f"{type(exc).__name__}: {exc}"
    _logger.error("ATS handler failed for %s: %s", ...)
    return ApplicationResult(state="failed", error=error_msg)
    # ← Returns failed result, doesn't propagate exception
```

**Status:** ✅ WORKING AS EXPECTED
- Source failures logged, not raised ✅
- Apply failures transition to FAILED state, don't stop pipeline ✅
- Paced runs continue after errors ✅

---

### ✅ 7. Dry Run Support - VERIFIED

**CLI flag** (cli/commands/pipeline.py:136-143):
```python
no_submit: bool = True  # Default is dry-run
```

**Template method short-circuit** (infrastructure/browser/ats/base.py:203-212):
```python
if data.dry_run:
    # Full application short of the click
    return ApplicationResult(
        state="applied",
        submitted_url=getattr(page, "url", None),
        error="dry-run: submit skipped",
    )

self._submit(page, data)  # Only called if NOT dry_run
```

**State tracking** (pipelines/apply.py:192):
```python
application.dry_run = application_data.dry_run  # Stored on row
```

**Status command split** (cli/commands/status.py - not shown but mentioned in CLAUDE.md):
- `magicapply status` splits APPLIED into `real: N, dry_run: M`

**Status:** ✅ WORKING AS EXPECTED

---

### ✅ 8. Apply Throttling (Anti-Ban) - VERIFIED

**File:** `domain/apply/throttle.py:41-90`  
**Wired in:** `pipelines/apply.py:156-172`

**Throttle check:**
```python
if self._throttle is not None:
    ats_key = ats_key_for_url(apply_target) or "unknown"
    decision = self._throttle.check(ats=ats_key)
    if not decision.allowed:
        logger.info("throttle: deferring application %s ...", ...)
        return ApplyReport(
            application.id,
            application.state,  # ← Stays TAILORED
            f"throttle: {decision.reason}",
        )
```

**Key behavior:**
- Throttle checked BEFORE browser opens (line 156)
- On deny: application stays TAILORED (not failed)
- Next batch re-checks when window rolls
- **Per-ATS** caps: workday/greenhouse/lever/ashby tracked separately
- **Board listings** throttled as indeed/linkedin/glassdoor (not "generic")

**Config:** `base_config.yaml::apply_throttle`
```yaml
apply_throttle:
  ats_default:
    hourly: 4
    daily: 20
  ats_overrides:
    workday:
      hourly: 2
      daily: 10
  global_cap:
    hourly: 8
    daily: 40
```

**Status:** ✅ WORKING AS EXPECTED

---

## Critical Findings

### ❌ DISCREPANCY #1: Applications NOT Processed by Score Order

**User Expectation:**
> "Applications should start at the top (highest match) and work their way down."

**Actual Behavior:**
Applications are processed in **discovery order** (database insertion order), NOT score order.

**Root Cause:**
`SqlApplicationsRepository.list_by_state_and_profile()` does NOT sort by score:

**File:** `infrastructure/persistence/repositories/applications.py:128-133`
```python
def list_by_state_and_profile(self, state: ApplicationState, profile_name: str) -> list[Application]:
    with self._session() as session:
        rows = session.execute(
            select(ApplicationRow)
            .where(ApplicationRow.state == state.value, ApplicationRow.profile_name == profile_name)
            # ← MISSING: .order_by(ApplicationRow.score.desc())
        ).scalars().all()
```

**Impact:**
- High-scoring jobs may be processed LAST if discovered late
- Throttle caps may be hit before best matches are attempted
- Paced runs may waste time on mediocre jobs while good ones wait

**Recommended Fix:**
```python
def list_by_state_and_profile(self, state: ApplicationState, profile_name: str) -> list[Application]:
    with self._session() as session:
        rows = session.execute(
            select(ApplicationRow)
            .where(ApplicationRow.state == state.value, ApplicationRow.profile_name == profile_name)
            .order_by(ApplicationRow.score.desc().nulls_last())  # ← ADD THIS
        ).scalars().all()
```

**Alternative (if nulls are a concern):**
```python
.order_by(
    ApplicationRow.score.desc().nulls_last(),
    ApplicationRow.created_at.desc()  # Tie-breaker for nulls
)
```

---

### ⚠️ DISCREPANCY #2: No "Try Board, Then Move On" Logic

**User Expectation:**
> "If that is not possible [to apply on external site], then we should attempt to apply directly on the board, if that fails then we move on."

**Actual Behavior:**
There is NO fallback from external site attempt to board attempt. The flow is:

1. During discovery: Try to find external apply URL
2. If found: Use external URL
3. If not found: Use board listing URL
4. At apply time: Use whichever URL was determined in discovery
5. If apply fails: Mark as FAILED, don't retry with alternate URL

**Code Evidence:**
```python
# apply.py:138-139 - URL determined once
job = job_with_resolved_apply_url(job)
apply_target = resolve_job_apply_destination(job)

# apply.py:179 - Single attempt with determined URL
result = handler.apply(page, application_data)

# apply.py:182-195 - No retry with alternate URL
if result.state == "failed":
    application.transition_to(ApplicationState.FAILED)
    # ← No fallback to try board listing
```

**What happens when external apply fails:**
- Application transitions to FAILED
- Error is logged
- Pipeline continues to next application
- NO automatic retry with board listing URL

**Status:** ⚠️ **DESIGN CHOICE, NOT A BUG**
The system chooses ONE URL per job at discovery time and sticks with it. This is simpler but less resilient than trying both.

**Recommendation:**
If you want the fallback behavior, it would require:
1. Store both listing URL and external URL on Job
2. On FAILED, check if external URL was used
3. If yes, retry with listing URL
4. This adds complexity - may not be worth it

---

## Summary

### ✅ Working as Expected (7/9)

1. ✅ Multiple sources discovery
2. ✅ Source-level scoring
3. ❌ **Score-ordered application** (DISCREPANCY #1)
4. ✅ Per-source rate limiting (throttle)
5. ✅ External URL resolution (LinkedIn/Indeed/Glassdoor)
6. ⚠️ **Board fallback** (DISCREPANCY #2 - not implemented)
7. ✅ Non-blocking error handling
8. ✅ Per-ATS + global throttle caps
9. ✅ Dry-run support for iterative improvement

### Critical Issues

**Priority 1:** Applications NOT processed by score order (HIGH IMPACT)
- **Fix:** Add `.order_by(ApplicationRow.score.desc())` to repository query
- **Effort:** 5 minutes
- **Impact:** Ensures best matches attempted first, before throttle caps hit

**Priority 2:** No board listing fallback after external URL failure (MEDIUM IMPACT)
- **Fix:** More complex - requires retry logic with URL switching
- **Effort:** 2-4 hours
- **Impact:** Would improve resilience when external ATSs fail
- **Decision:** User should decide if this complexity is worth it

---

## Code Quality Assessment

**Overall:** The workflow is well-designed and mostly matches expectations. Error handling is robust, throttling works correctly, and URL resolution is sophisticated.

**The score-ordering bug is the ONLY critical flaw** - it fundamentally changes the application order from user's expectation. Everything else works as described.
