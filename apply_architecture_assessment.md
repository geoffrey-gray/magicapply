# Apply Engine Architecture Assessment
**Date:** 2026-07-12  
**Question:** Is the apply engine architecture strong, or does it need rearchitecture?  
**Verdict:** ⚠️ **NEEDS TARGETED REFACTORING** (not full rearchitecture)

---

## Current State Analysis

### File Size & Complexity Metrics

**File:** `pipelines/apply.py` - **454 lines**

**Components:**
- 1 main class: `ApplyPipeline`
- 2 public methods: `apply_one()`, `apply_batch()`
- 2 private methods: `_apply_batch_once()`, `_list_batch_candidates()`
- 4 module-level helpers
- 1 Protocol, 1 dataclass, 1 type alias

**Responsibilities Count:** 11 distinct concerns

---

## Complexity Sources (Good vs Bad)

### ✅ Essential Complexity (Domain-Required)

These complexities are inherent to the problem and well-managed:

1. **State machine transitions** (TAILORED → APPLYING → APPLIED/FAILED/NEEDS_INTERVENTION)
   - Location: `apply_one()` lines 174-195
   - Well-encapsulated in domain model

2. **URL resolution** (board listing → external ATS)
   - Location: `apply_one()` lines 130-143
   - Delegated to `apply_url.py` module ✅

3. **ATS handler selection** (Strategy pattern)
   - Location: `apply_one()` line 145
   - Delegated to `ATSHandlerFactory` ✅

4. **Throttle checking** (per-ATS + global caps)
   - Location: `apply_one()` lines 156-172
   - Delegated to `ApplyThrottle` domain service ✅

5. **Dry-run vs real submission**
   - Location: Template Method in `BaseATSHandler`
   - Clean separation ✅

**Assessment:** These are well-delegated to focused classes. ✅ GOOD

---

### ⚠️ Accidental Complexity (Architecture Leaks)

These add complexity without clear domain justification:

#### 1. **Two Apply Modes with Different Semantics**

**Simple mode** (`apply_batch` with no pacing):
```python
def apply_batch(...):
    if not paced:
        return self._apply_batch_once(...)
```
- Calls `_apply_batch_once()`
- Single pass over TAILORED apps
- No retries, no deadline, no pacing

**Paced mode** (`apply_batch` with max_outcomes/deadline/pace):
```python
def apply_batch(...):
    while True:
        candidates = self._list_batch_candidates(...)
        ordered = _order_candidates_by_score(candidates)
        for app in ordered:
            # Complex logic here...
```
- Custom loop implementation
- Includes FAILED/NEEDS_INTERVENTION retry
- Deadline checking
- Pace sleeping
- Quota management
- Stalled-pass detection

**Problem:** These are TWO different algorithms sharing one method signature.

**Lines of code:**
- Simple mode delegation: 6 lines
- Paced mode logic: ~150 lines

**Recommendation:** ⚠️ Split into separate classes or strategies.

---

#### 2. **Paced Mode Has 6 Nested Concerns**

**Location:** `apply_batch()` lines 242-360

**Nested concerns:**
1. Deadline checking (2 places: outer + inner loop)
2. Max outcomes checking (2 places: outer + inner loop)
3. Candidate fetching & ordering
4. Retry state handling (TAILORED vs FAILED/NEEDS_INTERVENTION)
5. Throttle skip logic (continue to next candidate)
6. Pace sleeping logic (only after counted outcomes)
7. Stalled-pass detection (abort after empty waves)

**Cyclomatic complexity:** HIGH (multiple nested conditionals)

**Recommendation:** ⚠️ Extract to `PacedApplyStrategy` class.

---

#### 3. **Score Ordering Happens in Two Places**

**Problem:** Score ordering is both in-memory AND missing from SQL.

**In-memory ordering:**
```python
# apply.py:443
def _order_candidates_by_score(apps: list[Application]) -> list[Application]:
    return sorted(apps, key=lambda a: (a.score or 0), reverse=True)
```

**SQL query (NO ordering):**
```python
# repositories/applications.py:128
def list_by_state_and_profile(...):
    rows = session.execute(
        select(ApplicationRow)
        .where(...)
        # ← Missing .order_by(score.desc())
    )
```

**Result:**
- Paced mode sorts in memory ✅
- Simple mode (`_apply_batch_once`) does NOT sort ❌
- Repository returns discovery order

**Recommendation:** ⚠️ Fix repository query to ALWAYS order by score.

---

#### 4. **Unclear Retry State Handling**

**Simple mode:**
```python
def _apply_batch_once(..., include_retry_states: bool = False):
    # Line 378: include_retry_states is IGNORED
    # Always fetches only TAILORED
```

**Paced mode:**
```python
def apply_batch(..., include_retry_states: bool = False):
    # Line 254: Fetches TAILORED + FAILED + NEEDS_INTERVENTION
    if include_retry_states:
        # Uses the extra states
```

**Problem:** Same parameter name, different semantics in two modes.

**Recommendation:** ⚠️ Make retry behavior explicit per mode.

---

#### 5. **Data Builder Indirection**

**Pattern:**
```python
class ApplyPipeline:
    def __init__(self, ..., data_builder: DataBuilder | None = None):
        self._data_builder = data_builder

    def apply_batch(self, ...):
        data = self._data_builder(app, job, dry_run=dry_run)
```

**What it is:** `DataBuilder = Callable[[Application, Job], ApplicationData]`

**Why it exists:** Closes over `LoadedConfig` so pipeline doesn't need config reference.

**Problem:**
- Not a real abstraction (only one implementation)
- Makes code harder to follow (indirection for no polymorphism)
- Could just pass `build_application_data` function directly

**Recommendation:** ✅ ACCEPTABLE - This is dependency injection. Not ideal, but not harmful.

---

## Architectural Strengths

### ✅ What's Working Well

1. **Template Method Pattern** (BaseATSHandler)
   - Clean, extensible
   - Dry-run short-circuit in right place
   - Error handling centralized

2. **Strategy Pattern** (ATS handlers)
   - Each handler focused on one ATS
   - Factory selects at runtime
   - Easy to add new handlers

3. **Composition Pattern** (FormComposer)
   - Complex form logic delegated
   - Router dispatch well-separated
   - Field drivers pluggable

4. **Repository Pattern**
   - Persistence abstracted
   - Domain uses Protocols
   - Testable with fakes

5. **Throttle as Domain Service**
   - Policy in domain layer
   - Injected as collaborator
   - Clock injectable for testing

---

## Architectural Weaknesses

### ❌ What's Breaking Down

1. **ApplyPipeline doing too much**
   - Orchestration (good) ✅
   - Pacing logic (should be extracted) ❌
   - Ordering logic (should be in repository) ❌
   - Retry logic (should be explicit strategy) ❌

2. **No clear "Apply Strategy" abstraction**
   - Simple vs Paced are different algorithms
   - Crammed into one `apply_batch` method
   - No polymorphic interface

3. **Repository missing score ordering**
   - Forces in-memory sorting
   - Inconsistent between modes
   - Should be database responsibility

---

## Recommendations

### Priority 1: Fix Score Ordering (5 minutes) ⚠️ CRITICAL

**File:** `infrastructure/persistence/repositories/applications.py`

**Add to ALL list methods:**
```python
.order_by(ApplicationRow.score.desc().nulls_last())
```

**Impact:** Fixes simple mode, simplifies paced mode.

---

### Priority 2: Extract Paced Apply Strategy (2-3 hours) ⚠️ HIGH

**Create:** `pipelines/apply_strategies.py`

```python
class ApplyStrategy(Protocol):
    def execute(
        self,
        *,
        session: _SessionProto,
        pipeline: ApplyPipeline,
        profile_name: str,
        dry_run: bool,
    ) -> list[ApplyReport]:
        ...

class SimpleApplyStrategy:
    """One-shot pass over TAILORED applications."""
    def execute(...) -> list[ApplyReport]:
        apps = pipeline._list_tailored(profile_name)
        return [pipeline.apply_one(...) for app in apps]

class PacedApplyStrategy:
    """Multi-pass with deadline, quota, pacing, and retries."""
    def __init__(
        self,
        max_outcomes: int | None,
        deadline: datetime | None,
        pace_seconds: float | None,
        include_retry_states: bool,
    ):
        ...

    def execute(...) -> list[ApplyReport]:
        # All the paced logic here
        ...
```

**Refactor:**
```python
class ApplyPipeline:
    def apply_batch(self, *, session, profile_name, dry_run, **pacing_kwargs):
        if _is_paced(**pacing_kwargs):
            strategy = PacedApplyStrategy(**pacing_kwargs)
        else:
            strategy = SimpleApplyStrategy()
        return strategy.execute(session=session, pipeline=self, ...)
```

**Benefits:**
- Separates simple from complex
- Each strategy focused on one algorithm
- Easier to test independently
- Clearer what each mode does

---

### Priority 3: Make Retry Explicit (1 hour) ⚠️ MEDIUM

**Current (confusing):**
```python
apply_batch(..., include_retry_states: bool = False)
```

**Proposed:**
```python
class SimpleApplyStrategy:
    # No retry - always fresh TAILORED only
    pass

class PacedApplyStrategy:
    def __init__(self, ..., retry_failed: bool = False, retry_intervention: bool = False):
        self._retry_failed = retry_failed
        self._retry_intervention = retry_intervention
```

**Benefits:**
- Explicit about what gets retried
- No parameter that means different things in different modes

---

### Priority 4: Consider Board Listing Fallback (4-6 hours) ⚠️ LOW

**Only if user wants resilience over simplicity.**

**Pattern:** Chain of Responsibility

```python
class ApplyAttemptChain:
    def __init__(self, attempts: list[ApplyAttempt]):
        self._attempts = attempts

    def execute(self, page, app, job) -> ApplicationResult:
        for attempt in self._attempts:
            if not attempt.should_try(job, previous_failures):
                continue
            result = attempt.execute(page, app, job)
            if result.state == "applied":
                return result
            # Continue to next attempt
        return last_result

# Usage:
chain = ApplyAttemptChain([
    ExternalATSAttempt(),    # Try apply_url if external
    BoardListingAttempt(),   # Fall back to listing URL
])
```

**Complexity vs Benefit:** Unclear. User should decide.

---

## Verdict

### Current Architecture: 6/10

**Strengths:**
- Core domain patterns (Strategy, Template Method, Repository) are solid ✅
- Composition pattern for forms is excellent ✅
- Throttle as domain service is clean ✅

**Weaknesses:**
- Apply pipeline mixing two algorithms in one method ❌
- Score ordering in wrong place (memory instead of SQL) ❌
- Retry logic unclear ❌

### Recommendation: **Targeted Refactoring, Not Full Rearchitecture**

**Why not full rearchitecture:**
- The foundation is SOLID (Strategy, Template Method, Repository)
- The domain model is correct
- The composition pattern works well
- Error handling is robust

**Why refactoring is needed:**
- `ApplyPipeline.apply_batch()` has two algorithms
- Score ordering belongs in repository
- Paced mode is too complex for one method

**Refactoring Plan (4-6 hours total):**

1. **Fix SQL ordering** (5 min) - Priority 1
2. **Extract PacedApplyStrategy** (2-3 hrs) - Priority 2
3. **Make retry explicit** (1 hr) - Priority 3
4. **Board fallback** (optional, 4-6 hrs) - Only if needed

**After refactoring → 8/10 architecture**

---

## Comparison: Current vs Proposed

### Current Structure
```
ApplyPipeline
├── apply_one()          [100 lines] ✅ Good
├── apply_batch()        [150 lines] ❌ Too complex
│   ├── simple mode      [delegated to _apply_batch_once]
│   └── paced mode       [inline, complex loop]
├── _apply_batch_once()  [40 lines]  ✅ OK
└── _list_batch_candidates() [20 lines] ✅ OK
```

### Proposed Structure
```
ApplyPipeline
├── apply_one()          [100 lines] ✅ Good
├── apply_batch()        [20 lines]  ✅ Strategy selection only
└── _list_tailored()     [10 lines]  ✅ Helper

ApplyStrategy (Protocol)
├── SimpleApplyStrategy  [30 lines]  ✅ One-shot focused
└── PacedApplyStrategy   [120 lines] ✅ Complex but isolated
    ├── _check_deadline()
    ├── _check_quota()
    ├── _should_pace()
    └── _detect_stalled()

Repository (fixed)
└── list_by_state_and_profile()  ✅ Now sorts by score
```

**Result:** Same functionality, clearer structure, easier to test and extend.

---

## Final Answer

**Should you rearchitect?** 

**No.** The architecture is fundamentally sound.

**Should you refactor?**

**Yes.** Extract the paced mode into a strategy, fix the SQL ordering, and clarify retry semantics.

**Is it urgent?**

**Medium priority.** The code works, but it will get harder to maintain as you add features. Do it before adding more complexity (like board fallback logic).

**Estimated effort:** 4-6 hours for clean refactoring.

**Risk:** Low - you have 835 passing tests to verify no regressions.
