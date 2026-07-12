# The Apply Batch Problem and Solution Direction

## What MagicApply Does

Job application automation: discovers jobs → scores them → tailors resumes → auto-applies via browser automation (Playwright).

**Three pipelines:**
1. **Discovery:** Fetch jobs from sources → dedupe → score → persist Applications in SCORED state
2. **Tailoring:** Take SCORED apps → extract keywords → tailor resume → write DOCX → transition to TAILORED
3. **Apply:** Take TAILORED apps → fill forms via ATS handlers → submit → transition to APPLIED/FAILED/NEEDS_INTERVENTION

## The Core Issue

**Discovery and Tailoring are CLEAN:**
- Simple `run()` method
- Iterate candidates once
- Process each one
- No strategy classes, no while loops
- Natural flow: get list → process → done

**Apply is BROKEN:**
- Two modes: "simple" (one pass) vs "paced" (multi-pass with throttling, deadlines, quotas)
- Currently uses Strategy classes (SimpleApplyStrategy, PacedApplyStrategy)
- PacedApplyStrategy has `while candidates:` loop that NEVER actually loops (line 137)
- Split between "orchestrator" (re-listing) and "strategy" (processing)
- Instance state across calls (_stalled_passes, _hit_stall_limit)
- Confusing: when to re-list? when to stop? who decides?

**The nonsensical while loop:**
```python
while candidates:  # candidates is a parameter, NEVER changes
    for app in candidates:
        process(app)
    break  # ALWAYS breaks - loop is meaningless
```

## Why Strategy Classes Are Wrong Here

**GOF_PATTERNS.md explicitly says:** "ApplyPipeline.apply_batch next to apply_one, no ApplyRunner sibling" - meaning NO strategy classes for batch logic.

**Strategy pattern is for:** Different IMPLEMENTATIONS of same interface (Greenhouse vs Workday vs Lever handlers).

**Paced apply is NOT that:** It's orchestration/scheduling logic, not behavioral variation. Same operations (list → apply → check), different TIMING and CONTINUATION rules.

## What the User Wants

"A clear state machine that the architecture supports executing naturally. No while loops, no poorly coded branches."

## The Key Insight: Applications Already ARE the State Machine

**Application states:**
- TAILORED → candidates ready to apply
- APPLYING → in-flight (between transition and terminal state)
- APPLIED → success (terminal)
- FAILED → retry candidate
- NEEDS_INTERVENTION → retry candidate

**Paced batch behavior:**
1. List TAILORED (+ optional FAILED/NEEDS_INTERVENTION) apps, score-ordered
2. For each: try apply
3. On throttle deny: app stays TAILORED (not counted, will be re-listed)
4. On success/fail: app transitions to terminal state (counted)
5. After batch: if more candidates exist AND limits not hit AND not stalled → re-list and loop

**Current problem:** This logic is split across orchestrator (while True) and strategy (while candidates + stall tracking).

## The Natural Solution Direction

**Single cohesive flow in apply_batch(), no strategy classes:**

```python
def apply_batch(self, *, session, profile_name, dry_run, 
                max_outcomes=None, deadline=None, pace_seconds=None,
                include_retry_states=False):
    
    reports = []
    stalled_passes = 0  # LOCAL, not instance state
    
    # Natural progression: keep processing until we can't
    while True:
        # Re-list candidates (score-ordered, TAILORED + optional retries)
        candidates = self._list_batch_candidates(profile_name, include_retry_states)
        if not candidates:
            break
        
        # Check exit conditions BEFORE processing
        if deadline and now() >= deadline:
            break
        if max_outcomes and count_outcomes(reports) >= max_outcomes:
            break
        
        # Process batch
        counted_this_pass = 0
        skipped_quota = 0
        
        for app in candidates:
            # Check limits mid-batch
            if deadline and now() >= deadline:
                break
            if max_outcomes and count_outcomes(reports) >= max_outcomes:
                break
            
            job = self._jobs.get(app.job_id)
            if not job:
                continue
            
            report = self.apply_one(...)
            reports.append(report)
            
            # Throttle deny: app stayed TAILORED, will re-list
            if is_throttle_defer(report):
                skipped_quota += 1
                continue
            
            # Counted outcome: sleep if pacing
            if is_counted_outcome(report):
                counted_this_pass += 1
                if pace_seconds and not limit_hit:
                    sleep(pace_seconds)
        
        # Simple mode: one pass only
        if not (max_outcomes or deadline or pace_seconds or include_retry_states):
            break
        
        # Exit on limits
        if max_outcomes and count_outcomes(reports) >= max_outcomes:
            break
        if deadline and now() >= deadline:
            break
        
        # Stall detection: no progress made
        if counted_this_pass == 0:
            if skipped_quota > 0:
                # All candidates throttled - wait and retry once more
                stalled_passes += 1
                if stalled_passes >= 2:
                    break  # Still blocked after wait
                sleep(pace_seconds or 900)
                continue  # Re-list after sleep
            else:
                # No candidates or all jobs missing - can't make progress
                break
        
        # Progress made - reset stall counter and continue
        stalled_passes = 0
    
    return reports
```

**Key differences from broken version:**
1. **ONE loop** in apply_batch, not two (orchestrator + strategy)
2. **NO strategy classes** - just conditional logic (simple vs paced)
3. **Local state** - stalled_passes is a local variable, not instance state
4. **Natural flow** - the while True loop IS the state machine: list → process → check → continue or exit
5. **Clear exit conditions** - no confusing should_continue() callback
6. **Applications drive progression** - TAILORED apps get re-listed, APPLIED/FAILED don't (unless include_retry_states)

## Why This Works

**The state machine is the Application states + the re-listing loop:**
- Applications in TAILORED are "ready to process"
- Applications that get throttled STAY in TAILORED (deferred, not failed)
- Applications that succeed/fail LEAVE TAILORED (won't be re-listed)
- The loop naturally continues while TAILORED apps exist and limits allow
- No complex state tracking needed - just count outcomes and detect stalls

**This matches Discovery/Tailoring pattern:**
- Discovery: while jobs from sources → process → done
- Tailoring: for apps in SCORED → process → done
- Apply: while apps in TAILORED and limits allow → process → done (with re-listing and stall detection)

## What Needs to Happen

1. **Delete** apply_strategies.py entirely
2. **Delete** strategy selection logic in apply_batch
3. **Implement** single cohesive loop in apply_batch as shown above
4. **Keep** apply_utils.py (helper functions) and apply_types.py (ApplyReport)
5. **Update** tests to not use strategy classes

**The architecture naturally supports this:** Applications ARE the state machine. The batch just needs to keep listing TAILORED apps and processing them until limits hit or no more candidates.
