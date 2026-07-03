# Gang of Four Pattern Mapping

Every non-trivial abstraction in MagicApply should map to a pattern below. If none fits, question whether the abstraction is needed.

Patterns are listed with the concrete responsibility they cover, the module that hosts them, and the reason the pattern was chosen over the obvious alternative.

## Applied in MVP

### Strategy (Behavioral)

**Where:** `infrastructure/browser/ats/` (per-ATS form filling) and `infrastructure/llm/providers/` (per-LLM-backend clients).

- `ATSHandler` Protocol → `greenhouse.py`, later `lever.py`, `workday.py`, `ashby.py`.
- `LLMClient` Protocol → `anthropic.py`, `ollama.py`, plus test/dry-run variants `mock.py` (shape-aware; matches on prompt substrings from the injected `PromptsConfig`) and `replay.py` (SHA256-keyed YAML fixtures with a fallback client).

**Why not conditionals:** the variation is behavioral across a large surface (login, navigate, map fields, submit). Polymorphism replaces branching in the pipeline layer.

### Adapter (Structural)

**Where:** `infrastructure/sources/` (per-source job discovery).

- `JobSource` Protocol; each adapter translates an upstream (LinkedIn HTML, JSON-LD `JobPosting`, a career page's own API) into our internal `Job` model.

**Difference from Strategy:** Strategy varies *behavior* to do the same job the same way; Adapter varies *interface translation* so heterogeneous upstreams look uniform to us. Both apply here — intentionally — but for different reasons.

### Factory Method (Creational)

**Where:** `infrastructure/browser/ats/factory.py`.

- `ATSHandlerFactory.for_url(url) -> ATSHandler`. Handlers self-register a URL predicate; the factory owns the lookup.

**Why:** avoids `if "greenhouse.io" in url` scattered across pipelines.

### Builder (Creational)

**Where:** `domain/resumes/tailor.py`.

- `TailoredResumeBuilder` steps: seed from base → inject keywords → reorder bullets → generate summary. Each step is optional and composable.

**Why:** a tailored resume has ~5 collaborators (base resume, JD, keywords, scoring context, output template). A plain constructor becomes unreadable.

### Template Method (Behavioral)

**Where:** `infrastructure/browser/ats/base.py`.

- `BaseATSHandler.apply()` is the concrete template: `navigate` → CAPTCHA detect → `fill_static` → `fill_dynamic` → CAPTCHA detect → **dry-run short-circuit** → `submit` → `verify`. Subclasses override the hooks; the flow is invariant.
- The CAPTCHA detection and dry-run short-circuit are baked into the template, so every existing and future ATS handler inherits them for free. There is no per-handler dry-run wrapper class (see also *Extend, don't multiply* below).

**Why:** every ATS follows the same broad flow — Template Method captures the invariant so subclasses can't accidentally reorder it, and cross-cutting safety features (CAPTCHA, dry-run) live in the template rather than in each subclass.

### Repository (Fowler, PoEAA — not strictly GoF)

**Where:** `domain/repositories.py` defines Protocols; `infrastructure/persistence/repositories/` implements them.

- `JobsRepository`: `upsert`, `get`, `get_by_dedup_key`, `list_all`.
- `ApplicationsRepository`: `add`, `save`, `get`, `by_job_and_profile`, `list_by_state`, `list_by_state_and_profile`.

Listed for completeness — it's the canonical companion to a layered architecture, though not from the GoF book.

## Meta principle: extend, don't multiply

Before adding a new class, ask:
1. Can an existing class take a new optional kwarg or a new mode? (See `MockLLMClient` — canned vs. shape-aware mode on the same class.)
2. Can I add a method to an existing class? (See `ApplyPipeline.apply_batch` next to `apply_one`, no `ApplyRunner` sibling.)
3. Can I extend an existing Template Method's invariant flow? (See `BaseATSHandler.apply` growing a dry-run branch instead of a `DryRunPageDriver` wrapper.)

Only after all three fail does a new sibling class earn its place. Redundant siblings dilute intent and drift out of sync.

## Deferred (Phase 2+)

### Observer (Behavioral)

For `ApplicationStateChanged` events → notifications, dashboards, webhooks. Introduce when a second subscriber exists. Until then, direct calls are simpler.

### Chain of Responsibility (Behavioral)

For dynamic form-field resolution: config-defined answer → keyword-inferred → LLM-generated → fail. Add if resolution grows past ~3 strategies.

### Composite (Structural)

For hierarchical resume sections if we ever support multi-document tailoring (resume + portfolio + cover letter as one tree).

### Facade (Structural)

Pipeline classes (`pipelines/discovery.py`, `pipelines/apply.py`) already function as facades over the layered stack. Named here to document intent.

## Explicitly Rejected

### Singleton

Never. Composition root wires collaborators in `cli/main.py`.

### Visitor

No fit — data shapes aren't rich ASTs. Reconsider only if resume elements grow many independent operations.

### Prototype, Flyweight, Mediator, Interpreter, Memento, State

No current fit. `ApplicationState` uses an enum + explicit transition table rather than the GoF State (class-per-state) pattern — simpler and adequate.
