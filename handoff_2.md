# Handoff 2 — Search-page extractors for Indeed + Glassdoor

**Written:** 2026-07-09
**Author:** Claude (session ending; handing off to next assistant)
**Audience:** The next AI resuming MagicApply work.

Complementary to [`picking_up.md`](picking_up.md) (still valid for environment setup) and [`CLAUDE.md`](CLAUDE.md) (design invariants). Read [`docs/VM_DEV.md`](docs/VM_DEV.md) if you have never SSHed into `magicapply-dev`.

---

## 1. TL;DR — what shipped this session

Three commits landed on `main` (no PR — direct fast-forward merge, same pattern the operator uses):

| SHA | Commit | Files |
|---|---|---|
| `2e51c42` | Apply UA + viewport to shared BrowserContext (fix Indeed CF block) | `src/magicapply/infrastructure/browser/session.py`, `tests/unit/browser/test_session.py` |
| `c1ab0c5` | Indeed: parse jobs from search-page hydration (bypass detail-page wall) | `src/magicapply/infrastructure/sources/indeed.py`, `tests/unit/sources/test_indeed.py` |
| `346fe7c` | Glassdoor: parse jobs from search-page cards (bypass detail-page fetches) | `src/magicapply/infrastructure/sources/glassdoor.py`, `tests/unit/sources/test_glassdoor.py` |

**Result — live-verified 2026-07-09 with no cookies, no proxies, no `.env` beyond `MAGICAPPLY_*_ACK=1`:**

- Indeed `staff data scientist` / Remote → **24 real jobs** (title / company / location / snippet all populated).
- Glassdoor `staff data scientist` → **30 real jobs** (title / company / location / salary / snippet).
- Full unit suite: **674 passed, 10 pre-existing skipped**.

Previous session ended blocked at "Additional Verification Required" walls; this session unblocked the whole discovery flow.

---

## 2. Why — the two problems solved

### 2a. Cloudflare fingerprint block on the shared BrowserContext

`PlaywrightSession` in `session.py` already rotated UA + viewport for **per-proxy** contexts (spun by `session.new_page(proxy=…)`), but the **shared default context** — the one used when no proxy is active — kept Playwright's stock `HeadlessChrome/…` UA. Indeed's Cloudflare classifier detects that fingerprint and returns a 35 KB `Just a moment...` challenge page before we render anything.

**Fix:** apply `_rng.choice(self._user_agent_pool)` + viewport hydration to the shared context on `__enter__`. The same Indeed search that returned 35 KB of block page now returns 1.59 MB of real HTML from a stock IP.

### 2b. Indeed's second-tier "Additional Verification Required" wall on detail pages

Even after clearing Cloudflare, `/viewjob?jk=…` detail pages hit a second bot wall — Indeed's own rate limit / verification page. Empirically: search yields 24 anchor URLs, each detail fetch returns a 38 KB verification page, JSON-LD parser finds zero postings, discover yields zero jobs.

**Fix:** parse `Job` records directly from the search page's hydration blob:

```
window.mosaic.providerData["mosaic-provider-jobcards"] = {
  "mosaicProviderJobCardsModel": {
    "results": [{ jobkey, title, company, formattedLocation,
                  snippet, indeedApplyable, thirdPartyApplyUrl, … }, …]
  }
}
```

Every field the domain `Job` needs is already there. Zero detail-page fetches per search → the wall never fires, and each query costs 1 HTTP round trip instead of 25.

### 2c. Same shape, different mechanism, for Glassdoor

Glassdoor has no equivalent JSON hydration blob but **renders every card into the search-page DOM directly** with clean `data-test` attributes:

```html
<li data-test="jobListing" data-jobid="1010126579844">
  <a data-test="job-title" href="/job-listing/…">QC Microbiology, Sr Analyst</a>
  <div id="job-employer-1010126579844">
    <span class="EmployerProfile_compactEmployerName__9MGcV">Ultragenyx</span>
    <span class="rating-single-star_RatingText__5fdjN">3.4</span>
  </div>
  <div data-test="emp-location">Woburn, MA</div>
  <div data-test="descSnippet">Write, prepare, and present…</div>
  <div data-test="detailSalary">$119K - $147K (Employer provided)</div>
</li>
```

Same pattern: parse cards inline via lxml/xpath, skip detail-page fetches, 30× fewer HTTP round trips.

---

## 3. Repository state after this session

- **Branch:** `main` (topic branch `shared_context_ua` merged fast-forward and deleted).
- **Uncommitted:** `configs/curated_proxies.yaml`, `scratch/` (per `picking_up.md`, `scratch/` is intentionally not committed), `.claude/` (session artifacts).
- **Latest commit on `main`:** `346fe7c`. See §1 for the last three shipped.

Every recent commit lineage:

```
346fe7c Glassdoor: parse jobs from search-page cards (bypass detail-page fetches)
c1ab0c5 Indeed: parse jobs from search-page hydration (bypass detail-page wall)
2e51c42 Apply UA + viewport to shared BrowserContext (fix Indeed CF block)
25e04b9 Session cookie support for Indeed + Glassdoor
9d28bb5 Add VpsPoolProvider + curate_proxies.py; deep-dive on free-proxy quality
ce1a796 Phase F: burn-on-timeout + max_entries cap + goto timeout tuning
```

---

## 4. How to run everything

**All commands run inside the dev VM.** The host has neither Chromium nor the right Python. Use the SSH prelude from `picking_up.md`:

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && <command>'
```

Or short-form (works because the SSH host config sets what it needs):

```bash
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run <command>'
```

### 4a. Unit tests (fast, no network, no Chromium — ~2.5 minutes)

```bash
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run pytest tests/unit -q'
```

Expected: `674 passed, 10 skipped`. The 10 skips are pre-existing (live captures + one recipe-execute test).

Targeted:

```bash
# Just the new/changed suites:
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run pytest tests/unit/sources/test_indeed.py tests/unit/sources/test_glassdoor.py tests/unit/browser/test_session.py -q'
```

### 4b. Live-verify Indeed + Glassdoor discovery (real HTTP, no submit)

Prereq: the ToS acknowledgements must be set in `.env`. The dev VM already has them:

```
MAGICAPPLY_INDEED_ACK=1
MAGICAPPLY_GLASSDOOR_ACK=1
MAGICAPPLY_LINKEDIN_ACK=1     # if you want LinkedIn too
```

LinkedIn additionally needs `LINKEDIN_LI_AT=<session cookie value>`. Indeed and Glassdoor now work **without** cookies (the `INDEED_SESSION_COOKIES` and `GLASSDOOR_SESSION_COOKIES` plumbing shipped in the previous session's commit `25e04b9` is still valid — it's just no longer required).

Two smoke scripts in `scratch/` (both are gitignored; write them anew if the dir was cleaned):

```python
# scratch/indeed_live_smoke.py
from magicapply.config.models import IndeedSource
from magicapply.infrastructure.sources.indeed import IndeedAdapter

adapter = IndeedAdapter.from_config(IndeedSource(
    name="indeed-search",
    queries=["staff data scientist"],
    location="Remote",
    rate_limit_per_minute=60,
))
jobs = list(adapter.discover())
print(f"discovered {len(jobs)} jobs")
```

```python
# scratch/glassdoor_live_smoke.py
from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.sources.glassdoor import GlassdoorAdapter

adapter = GlassdoorAdapter.from_config(GlassdoorSource(
    name="glassdoor-search",
    queries=["staff data scientist"],
    rate_limit_per_minute=60,
))
jobs = list(adapter.discover())
print(f"discovered {len(jobs)} jobs")
```

Run:

```bash
ssh magicapply-dev 'cd ~/magicapply && set -a && source .env && set +a && ~/.local/bin/uv run python scratch/indeed_live_smoke.py'
ssh magicapply-dev 'cd ~/magicapply && set -a && source .env && set +a && ~/.local/bin/uv run python scratch/glassdoor_live_smoke.py'
```

Expected: **~24 Indeed jobs**, **~30 Glassdoor jobs**, both with title / company / location populated.

### 4c. Full pipeline (discover → tailor → apply dry-run)

Same CLI surface as before — the extractor changes are internal to the source adapters:

```bash
# Discovery only
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run magicapply discover staff-ds --root configs'

# Status
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run magicapply status'

# Full run (safe — dry-run by default, no submissions)
ssh magicapply-dev 'cd ~/magicapply && ~/.local/bin/uv run magicapply run staff-ds --root configs --no-headless=false'
```

---

## 5. What the new code looks like

### 5a. Indeed hydration extractor — `src/magicapply/infrastructure/sources/indeed.py`

**Key symbols to know:**

- `_JOB_CARDS_BLOB_RE` — regex anchoring on `window.mosaic.providerData["mosaic-provider-jobcards"] = {`. Note: NOT `window.mosaic.initialData` — that global exists but only holds page-level metadata (country / ctk / env). We learned this the hard way; the module docstring calls it out.
- `extract_jobs_from_search(html, *, source_name) -> list[Job]` — public. `raw_decode`s the blob, walks it recursively for dicts with a string `jobkey`, requires both `title` (or `displayTitle`) and `company`.
- `_walk_for_job_records(node)` — the recursive walker. Doesn't hard-code paths — future container renames in Indeed's hydration shape are absorbed transparently.
- `_BOT_BLOCK_MARKERS` — extended with `"additional verification required"` and `"security check - indeed.com"` for defense in depth on the search page itself.

**What was removed:** the per-URL detail-page fetch loop in `_search_one_query` and the JSON-LD imports (`extract_jobposting_dicts`, `jsonld_to_job`, `enrich_job_from_detail_html`) — those modules still exist and are used by `linkedin.py`, `custom_url.py`, and `glassdoor.py` (Glassdoor no longer, but the imports stay unbroken).

**What was kept:** `extract_job_urls` (still public, still used by unit tests as a schema-drift canary) and `looks_like_bot_block`/`looks_like_cloudflare` aliases.

### 5b. Glassdoor card extractor — `src/magicapply/infrastructure/sources/glassdoor.py`

**Key symbols:**

- `extract_jobs_from_search(html, *, source_name) -> list[Job]` — public. Runs `//li[@data-test="jobListing"]` xpath, extracts fields via `data-test` selectors and stable partial class names.
- `_card_company(card)` — extracts the employer name. Prefers the stable `span.EmployerProfile_compactEmployerName…` child of `div#job-employer-<jobid>`. Falls back to the `job-employer-<id>` div text with a trailing rating (`Ultragenyx3.4` → `Ultragenyx`) stripped.
- `_card_first_text(card, xpath)` — helper for `emp-location` / `descSnippet` / `detailSalary` / `job-age` lookups. Returns `None` if the field isn't rendered on this card (Glassdoor omits salary on unspecced listings).
- `_clean_text(text)` — whitespace collapse.

**Same removal as Indeed:** per-URL detail-page fetch loop and the JSON-LD imports (which now don't appear anywhere in this file).

**Session-cookie path preserved:** the `GLASSDOOR_SESSION` (legacy) / `GLASSDOOR_SESSION_COOKIES` (multi) support from commit `25e04b9` is unchanged. Cookies + proxy pool + `_effective_proxy_pool()` — all still work; they just aren't needed for the happy path anymore.

### 5c. Shared BrowserContext UA fix — `src/magicapply/infrastructure/browser/session.py`

```python
self._context = self._browser.new_context(
    storage_state=storage_state,
    locale="en-US",
    user_agent=self._rng.choice(self._user_agent_pool),
    viewport={
        "width": (v := self._rng.choice(self._viewport_pool))[0],
        "height": v[1],
    },
    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
)
```

Same UA + viewport pools that per-proxy contexts already used. Deterministic when the RNG is seeded (see `TestSharedContextIdentity.test_shared_context_ua_deterministic_from_rng`).

---

## 6. Environment variables reference

| Variable | Purpose | Required for the flows in §4b? |
|---|---|---|
| `MAGICAPPLY_INDEED_ACK` | ToS acknowledgement gate for `IndeedAdapter` | **Yes** |
| `MAGICAPPLY_GLASSDOOR_ACK` | ToS acknowledgement gate for `GlassdoorAdapter` | **Yes** |
| `MAGICAPPLY_LINKEDIN_ACK` | ToS ack for LinkedIn | Yes if using LinkedIn |
| `LINKEDIN_LI_AT` | LinkedIn `li_at` session cookie | Yes if using LinkedIn |
| `INDEED_SESSION_COOKIES` | Browser Cookie-header string for Indeed | **No** (optional; use when the UA fix stops being enough) |
| `GLASSDOOR_SESSION_COOKIES` | Browser Cookie-header string for Glassdoor | **No** (optional) |
| `GLASSDOOR_SESSION` | Legacy single-cookie `gdSession` | No (kept for backwards compat) |
| `ANTHROPIC_API_KEY` | If using `llm.provider: anthropic` in config | No (Phase 1 default is `mock`) |
| `UV_LINK_MODE=copy` | Required in the SSH prelude — virtiofs + uv cache issue | **Yes** (already in `magicapply-dev` shell profile) |

Cookie extraction workflow (if you ever need cookies): browser DevTools → Network tab → any request to `indeed.com` (or `glassdoor.com`) → Request Headers → copy the entire `Cookie:` value, paste as `INDEED_SESSION_COOKIES=<paste>` in `.env`. See `docs/OPERATOR_RUNBOOK.md` §13.

---

## 7. Design invariants (must preserve)

These come from `CLAUDE.md` and were reinforced by the memory rule `feedback_never_skip_dod.md` — **never bypass, defer, or seed around a pipeline step; extend the layer that needs extending and make the full flow work.** Applies most acutely to:

1. **`_search_one_query` returns `Job` records or `_Blocked` sentinel — never dummy stubs.** During implementation I briefly wrote a "URL-only fallback" that yielded `Job.new(title="(untitled)", company="(unknown company)")` when the hydration blob was missing. That's the anti-pattern the rule guards against — it makes downstream stages silently degrade. Removed before commit. Do the same if you ever feel the pull.

2. **Extend, don't multiply.** `IndeedAdapter` still has one class — the change was internal. Don't add a `IndeedHydrationAdapter` sibling. Same for Glassdoor.

3. **Config over code.** No new hardcoded ATS quirks — the extractor targets stable structural markers (`data-test` attrs, JSON keys) that Indeed / Glassdoor have kept stable across builds. If a marker rotates, the fix is a regex or xpath update in the module, not a config knob.

4. **Cookies-win-over-proxies rule is intact.** Both `_effective_proxy_pool()` methods still return `None` when any session cookie env var is set. The extractor changes are orthogonal to that rule.

5. **Backwards-compat:** `extract_job_urls` (Indeed + Glassdoor) is still exported. Unit tests exercise it as a schema-drift canary — the adapter warns when `extract_jobs_from_search` returns `[]` but `extract_job_urls` finds URLs, so you notice when the hydration/card shape drifts before it silently zeros discovery.

---

## 8. Known follow-ups (nothing shipped this session)

None are blocking, but they're the obvious next moves:

1. **LinkedIn** — still uses `extract_jobposting_dicts` + `jsonld_to_job` on detail pages. It works because LinkedIn's cookie auth bypasses aggressive bot-detection, but the same "parse from search results" pattern *might* apply. Would need a live probe.

2. **Fixture promotion.** The `scratch/indeed_search_live.html` (1.68 MB) and `scratch/glassdoor_search_live.html` (1.03 MB) captures used to probe hydration/card shape are not committed. If drift becomes a recurring concern, promote synthesized minimal fixtures to `tests/fixtures/captured/` (like the ATS handler suite already does) and add a regression test. The current unit tests already build synthetic minimum-shape fixtures inline — that's usually enough.

3. **`enrich_apply_urls` config field.** Now dead in both `IndeedAdapter` and `GlassdoorAdapter` (the detail-page loop it gated is gone). Left in place to preserve config-schema backwards compat. If you touch these adapters again, consider removing the field from `IndeedSource` and `GlassdoorSource` in `config/models.py` — but that's a schema break; check first whether any operator configs set it.

4. **Fair cross-source dedup interaction.** The `source_scorer` in `DiscoveryPipeline` load-balances cross-source collisions. More Glassdoor / Indeed jobs surfacing now (~54 combined vs. ~0 before) will change the observed distribution — worth running one full `magicapply discover` and checking the SQL split by source_name to confirm the scorer's caps don't accidentally starve LinkedIn.

5. **Merge-freeze / release notes.** No project mgmt state was updated. If there's a changelog or release-notes process, note the three commits.

---

## 9. Test artifacts / fixtures generated during the session

**Not committed** (in `scratch/`, per `picking_up.md` policy):

- `scratch/indeed_search_live.html` — 1.68 MB live capture of `indeed.com/jobs?q=staff+data+scientist&l=Remote`
- `scratch/glassdoor_search_live.html` — 1.03 MB live capture
- `scratch/indeed_live_smoke.py`, `scratch/glassdoor_live_smoke.py` — reusable smoke scripts (see §4b)
- Several probe scripts (`indeed_extract*.py`, `glassdoor_card_shape.py`, etc.) — reference material, safe to delete.

**Committed** — new unit-test fixtures inline in the test files:

- `TestSearchPageHydrationExtractor._hydrated_html()` in `tests/unit/sources/test_indeed.py`
- `TestSearchPageCardExtractor._card_html()` in `tests/unit/sources/test_glassdoor.py`

Both mirror the real hydration/card shape captured live 2026-07-09.

---

## 10. If something regresses

**Symptom: Indeed discover returns 0 jobs (fresh env, no cookies).**

Sequence to diagnose:

1. Save a fresh dump: `python scratch/indeed_dump_live.py` (see previous scratch files as templates).
2. Check whether hydration blob is present: `grep -c 'window.mosaic.providerData\["mosaic-provider-jobcards"\]' data/scratch/indeed_search_live.html`. Expected: 2 (one escaped inside the JS bundle, one real).
3. If 0 → Indeed changed the global name; grep for `"jobkey"` and walk backwards to find the parent assignment. Update `_JOB_CARDS_BLOB_RE`.
4. If 2 but the walker still returns 0 → the record shape drifted. `raw_decode` the blob and inspect keys; `_walk_for_job_records` looks for any dict with a string `jobkey` — usually still works.
5. If Cloudflare is blocking the search page itself → the UA fix may need refreshing. Check `HeadlessChrome` isn't in the outgoing UA. If it is, look at `session.py::__enter__`.

**Symptom: Glassdoor discover returns 0 jobs.**

1. Save a fresh dump.
2. `grep -c 'data-test="jobListing"' data/scratch/glassdoor_search_live.html`. Expected: 30 (one per result per page).
3. If present but extractor returns 0 → check `_card_company`. The stable class-name substring `compactEmployerName` may have rotated. Update the xpath. Fallback via `job-employer-<id>` id should still work.
4. If missing entirely → Glassdoor may have moved to SSR-hydrated cards. `grep '__NEXT_DATA__\|__APOLLO_STATE__\|__INITIAL_STATE__'` to find a new hydration blob — the pattern would then mirror Indeed.

**Symptom: unit tests pass but pipeline runs still fail live.**

Most likely: Indeed / Glassdoor served a stripped page because of aggressive rate-limiting from your IP. Wait, or set the (optional) cookie env vars. The composed pipeline's `ApplyThrottle` and rate limiters throttle correctly; the browser's raw fetch does not.

---

## 11. Contact points in the codebase (quick jump)

| What you want | Path |
|---|---|
| Indeed hydration extractor | `src/magicapply/infrastructure/sources/indeed.py::extract_jobs_from_search` |
| Glassdoor card extractor | `src/magicapply/infrastructure/sources/glassdoor.py::extract_jobs_from_search` |
| Shared BrowserContext identity | `src/magicapply/infrastructure/browser/session.py::PlaywrightSession.__enter__` |
| Session cookie parser | `src/magicapply/infrastructure/sources/base.py::parse_cookie_string` |
| Discovery pipeline (composes adapters) | `src/magicapply/pipelines/discovery.py` |
| Composition root (wiring) | `src/magicapply/cli/composition.py` |
| Config models | `src/magicapply/config/models.py` |
| Operator runbook (env vars, cookie extraction, live workflow) | `docs/OPERATOR_RUNBOOK.md` |
| VM SSH / build workflow | `docs/VM_DEV.md` |
| Design invariants + phase 1 status | `CLAUDE.md` |
| Broader "picking up" onboarding | `picking_up.md` |

Good luck. If you find the hydration blob has moved again, the diagnostic pattern in §10 is the shortest path back to green. — Claude
