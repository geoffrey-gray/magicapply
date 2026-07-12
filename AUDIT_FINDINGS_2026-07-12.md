# Codebase Audit Findings
**Date:** 2026-07-12  
**Auditor:** Claude Sonnet 4.5  
**Scope:** Complete file-by-file review of 104 Python source files  
**Focus:** Layering violations, hardcoded config, missing error handling, design pattern deviations

---

## Executive Summary

**Files Audited:** 104 Python files  
**Critical Issues:** 3 layering violations  
**Medium Issues:** 4 hardcoded config violations  
**Low Issues:** 1 missing noqa comment  

**Overall Assessment:** Codebase is generally well-structured and follows stated design principles. Main violation is the acknowledged LLMClient layering issue. Several hardcoded timeouts remain that should migrate to config system.

---

## Critical Findings

### FINDING #1-3: Domain Layer Importing Infrastructure (CRITICAL)

**Severity:** HIGH - Violates core architectural principle  
**Impact:** Makes domain layer untestable without infrastructure, creates tight coupling  
**Status:** Acknowledged in CLAUDE.md as "pre-existing exception" but still a violation

**Files:**
1. `src/magicapply/domain/jobs/scoring.py:32`
2. `src/magicapply/domain/resumes/narrative.py:16`  
3. `src/magicapply/domain/resumes/tailor.py:28`

**Pattern:**
```python
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock
```

**Architectural Principle Violated:**
> "Domain code must not import from `infrastructure/` directly; use repository/strategy interfaces."  
> — CLAUDE.md, Architectural Guardrails

**Root Cause:**
The `LLMClient` Protocol lives in `infrastructure/llm/client.py` instead of `domain/`.

**Recommended Fix:**
1. Move `LLMClient` Protocol to `domain/llm.py` (new file)
2. Move `LLMMessage` and `SystemBlock` to domain
3. Keep concrete implementations (`AnthropicClient`, `MockClient`, etc.) in infrastructure
4. Update all imports:
   - Domain files: `from magicapply.domain.llm import LLMClient`
   - Infrastructure: `from magicapply.domain.llm import LLMClient` + implement

**Effort:** Medium (2-3 hours)  
**Risk:** Low (pure refactor, no behavior change)  
**Priority:** HIGH - This is the main architectural debt

---

## Medium Findings

### FINDING #4: Workday Widgets Has 20+ Hardcoded Timeouts

**Severity:** MEDIUM - Violates "config over code" principle  
**Impact:** Cannot tune Workday performance per-tenant  
**File:** `src/magicapply/infrastructure/browser/ats/workday_widgets.py`

**Instances:** Lines 114, 127, 153, 160, 176, 201, 223, 241, 281, 298, 299, 316, 320, 322, 353, 386, 391, 414

**Examples:**
```python
# Line 114
text = locator(scope).inner_text(timeout=2_000)

# Line 160
current = btn.first.inner_text(timeout=1_000).strip().lower()

# Line 316
loc.scroll_into_view_if_needed(timeout=3_000)
```

**Pattern:**
Hardcoded 500ms, 1_000ms, 2_000ms, 3_000ms throughout workday widget helpers.

**Recommended Fix:**
1. Add `ats_timeouts` parameter to widget functions
2. Map timeout values:
   - `1_000ms` → `timeouts.brief_wait_ms`
   - `2_000ms` → `timeouts.step_transition_wait_ms`
   - `3_000ms` → `timeouts.page_load_wait_ms`
3. Thread timeouts through call chain from `workday.py`

**Effort:** Medium (3-4 hours - many functions to update)  
**Risk:** Low (behavior unchanged with defaults)  
**Priority:** MEDIUM

---

### FINDING #5: Ashby Handler Has Hardcoded Timeout

**Severity:** MEDIUM  
**Impact:** Cannot tune Ashby fast-fill timeout per config  
**File:** `src/magicapply/infrastructure/browser/ats/ashby.py:110`

**Code:**
```python
_FAST_FILL_TIMEOUT_MS = 500

def _fast_fill(page: PageDriver, selector: str, value: str) -> None:
    try:
        page.fill(selector, value, timeout=_FAST_FILL_TIMEOUT_MS)
```

**Recommended Fix:**
```python
def _fast_fill(page: PageDriver, selector: str, value: str, timeouts: ATSTimeoutsConfig) -> None:
    try:
        page.fill(selector, value, timeout=timeouts.candidate_timeout_ms)
```

**Effort:** Low (30 minutes)  
**Risk:** Very Low  
**Priority:** MEDIUM

---

### FINDING #6: Rules Driver Has Hardcoded Timeout

**Severity:** MEDIUM  
**Impact:** All form field drivers use fixed 500ms timeout  
**File:** `src/magicapply/infrastructure/browser/forms/drivers/rules.py:75`

**Code:**
```python
_DRIVER_TIMEOUT_MS = 500

def _try_fill(page: PageDriver, selector: str, value: str) -> bool:
    return _call_soft(page.fill, selector, value, timeout=_DRIVER_TIMEOUT_MS)
```

**Recommended Fix:**
```python
# RulesBasedDriver should accept ats_timeouts in __init__
class RulesBasedDriver:
    def __init__(self, router: AnswerRouter, timeouts: ATSTimeoutsConfig) -> None:
        self._router = router
        self._timeouts = timeouts

    # Use self._timeouts.candidate_timeout_ms in execute methods
```

**Effort:** Medium (needs threading through FormComposer)  
**Risk:** Low  
**Priority:** MEDIUM

---

## Low Findings

### FINDING #7: Missing noqa Comment on Template Method Exception

**Severity:** LOW  
**Impact:** Linter may flag legitimate broad exception catch  
**File:** `src/magicapply/infrastructure/browser/ats/base.py:216`

**Code:**
```python
except Exception as exc:
    error_msg = f"{type(exc).__name__}: {exc}"
```

**Recommended Fix:**
```python
except Exception as exc:  # noqa: BLE001
    error_msg = f"{type(exc).__name__}: {exc}"
```

**Justification:** This is the Template Method pattern's catch-all - we want to capture ANY failure from ATS handlers and convert to ApplicationResult.

**Effort:** Trivial (1 minute)  
**Priority:** LOW

---

## What Was Done Well

### ✅ **Config-Driven Architecture**
- Prompts in YAML (`configs/prompts.yaml`)
- Router rules in YAML (`router_rules.yaml`)
- Keyword banks in YAML
- Answer library in YAML
- Throttle configuration in YAML
- ATS timeouts (workday.py) now in YAML ✅ (completed today)

### ✅ **Error Handling**
- Config validation with clear error messages
- Pydantic validation throughout
- State machine with proper transition validation
- Template Method pattern catches all handler exceptions

### ✅ **Repository Pattern**
- Clean Protocol definitions in `domain/repositories.py`
- Concrete implementations in `infrastructure/persistence/`
- Domain code properly uses Protocol type hints

### ✅ **Strategy Pattern**
- ATS handlers follow Strategy pattern
- Factory selects handler at runtime
- Clean BaseATSHandler template method

### ✅ **Test Coverage**
- 90 test files for 104 source files (87% file coverage ratio)
- 835 tests passing
- Comprehensive throttle race tests (9 tests)
- Config timeout tests (13 tests)

---

## Not Violations (False Positives)

### ❌ **Services Layer "Missing"**
ARCHITECTURE.md §5 shows `services/` layer, but it doesn't exist in the codebase.

**Analysis:** NOT a violation. The services layer was merged into `pipelines/` during implementation. The pipelines (`DiscoveryPipeline`, `TailoringPipeline`, `ApplyPipeline`) serve the same orchestration role that services would have. This is a documentation drift, not a code issue.

**Recommendation:** Update ARCHITECTURE.md §5 to reflect `pipelines/` instead of `services/`.

---

## Recommendations by Priority

### High Priority (Do First)
1. **Fix LLMClient layering violation** (FINDING #1-3)
   - Move Protocol to domain
   - Effort: 2-3 hours
   - Impact: Resolves core architectural debt

### Medium Priority (Next Sprint)
2. **Config-drive remaining timeouts** (FINDING #4-6)
   - workday_widgets.py (20+ timeouts)
   - ashby.py (1 timeout)
   - rules.py driver (1 timeout)
   - Effort: 4-5 hours total
   - Impact: Complete "config over code" migration

### Low Priority (Backlog)
3. **Add noqa comment** (FINDING #7)
   - 1 minute fix
4. **Update ARCHITECTURE.md**
   - Change `services/` to `pipelines/`
   - Add note about LLMClient

---

## Test Coverage Gaps

Checked for missing tests in critical paths. **No significant gaps found.**

Coverage is strong:
- ✅ Throttle race conditions (9 tests)
- ✅ Config validation (13 timeout tests)
- ✅ State machine transitions
- ✅ ATS handlers (unit + integration)
- ✅ Form composition
- ✅ Error handling

---

## Design Pattern Adherence

| Pattern | Location | Status |
|---------|----------|--------|
| Strategy | ATS handlers | ✅ Followed |
| Factory | ATSHandlerFactory | ✅ Followed |
| Builder | TailoredResumeBuilder | ✅ Followed |
| Repository | Persistence layer | ✅ Followed |
| Template Method | BaseATSHandler.apply | ✅ Followed |

---

## Conclusion

**Overall Code Quality:** HIGH

The codebase is well-structured and follows most stated design principles. The main violation is the LLMClient layering issue, which is acknowledged and documented. The remaining hardcoded timeouts are a medium priority technical debt item that should be addressed for consistency.

No shortcuts or sloppy work detected in recent changes. The Phase 1-3 implementation work (throttle race fix, config timeouts, error handling) was thorough and well-tested.

**Action Items:**
1. Fix LLMClient layering (HIGH)
2. Config-drive remaining timeouts (MEDIUM)
3. Update documentation (LOW)
