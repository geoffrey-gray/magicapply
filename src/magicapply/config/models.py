"""Pydantic v2 configuration models.

These are the shape of MagicApply's user-facing YAML. Every model uses
`extra="forbid"` so typos in a config file fail fast instead of being silently
ignored.

Design decisions worth recording:

- Sources are a discriminated union on `type`. New source kinds add a class here
  and register an adapter in `infrastructure/sources/`.
- Profiles reference sources by `name` (a foreign key) rather than embedding
  source config, so switching profiles doesn't require duplicating credentials
  or rate limits.
- Paths are stored as strings on the model and resolved against a `config_root`
  in the loader — keeps YAML relative-path-friendly.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_Strict = ConfigDict(extra="forbid", frozen=False, str_strip_whitespace=True)


class LLMConfig(BaseModel):
    """LLM provider configuration. Provider-specific fields live under `options`."""

    model_config = _Strict

    provider: Literal["anthropic", "ollama", "mock", "replay"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    # Provider-specific overrides (e.g. base_url for ollama). Kept loose on
    # purpose — providers own their own validation.
    options: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ScoringPrefilter(BaseModel):
    """Cheap rule-based prefilter run before any LLM scoring call."""

    model_config = _Strict

    locations: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    """Job-scoring behavior.

    `threshold` is applied after LLM scoring. Prefilter rules eliminate jobs
    before an LLM is called; anything they pass is scored 0-100 and compared
    against `threshold` to decide auto-apply.
    """

    model_config = _Strict

    threshold: int = Field(default=70, ge=0, le=100)
    prefilter: ScoringPrefilter = Field(default_factory=ScoringPrefilter)


class StaticAnswers(BaseModel):
    """Answers to routine application questions.

    Everything static enough that it never depends on the specific job goes
    here. Anything dynamic (bullets, cover letter, screening questions) is
    generated per-job by the LLM layer.

    Booleans for authorization / sponsorship / hispanic-latino live alongside
    the free-text ``work_authorization`` field because ATS forms ask both
    shapes: some as an open field, some as a yes/no radio. The AnswerRouter
    (Phase L) picks the right one per field.
    """

    model_config = _Strict

    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    city: str | None = None
    state: str | None = None
    address_line_1: str | None = None
    postal_code: str | None = None
    country_phone_code: str | None = None
    phone_device_type: str | None = None
    how_did_you_hear: str | None = None
    # Two-level source picker on some tenants (raghuboosetty: Job Board → LinkedIn).
    how_did_you_hear_parent: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_employer: str | None = None
    country: str | None = None
    # Explicit yes/no forms — some ATS forms ask a radio "authorized to work
    # in the US?" separately from the free-text visa description.
    authorized_to_work_us: bool | None = None
    needs_sponsorship_us: bool | None = None
    # Workday step 2: "Have you previously worked for <employer>?"
    previously_employed: bool | None = None
    work_authorization: str | None = None
    requires_sponsorship: bool | None = None
    # Practical numbers a lot of forms ask up front.
    years_of_experience: int | None = Field(default=None, ge=0, le=80)
    desired_salary: str | None = None
    # DEI/EEO — omit or fill per your comfort; MagicApply never invents values.
    gender: str | None = None
    ethnicity: str | None = None
    hispanic_latino: bool | None = None
    veteran_status: str | None = None
    disability_status: str | None = None
    # Default password for NEW Workday apply accounts (step 1 Create Account).
    # Once created, credentials are persisted per tenant in
    # ``data/workday_accounts.yaml`` and reused on later runs.
    workday_apply_password: str | None = None
    # Workday phone step SMS opt-in checkbox (phone-sms-opt-in).
    workday_sms_opt_in: bool | None = None


class Paths(BaseModel):
    """Filesystem locations. Resolved relative to the config root at load time."""

    model_config = _Strict

    resumes_dir: str = "resumes"
    data_dir: str = "data"


class _SourceBase(BaseModel):
    model_config = _Strict

    name: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source name must not be blank")
        return v


class CareerPageSource(_SourceBase):
    """A company's careers page.

    Adapter fetches the page and prefers JSON-LD `JobPosting`; falls back to a
    per-host HTML parser only when JSON-LD is absent.
    """

    type: Literal["career_page"] = "career_page"
    urls: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=30, gt=0)


class JobUrlSource(_SourceBase):
    """A hand-curated list of specific job posting URLs to keep watching."""

    type: Literal["job_url"] = "job_url"
    urls: list[str] = Field(default_factory=list)


class LinkedInSource(_SourceBase):
    """Authenticated LinkedIn scraping.

    Disabled by default. LinkedIn's ToS forbids scraping and their bot
    detection is aggressive — the user opts in explicitly per source, and
    provides a `LINKEDIN_LI_AT` cookie via env.
    """

    type: Literal["linkedin"] = "linkedin"
    enabled: bool = False
    queries: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=10, gt=0)
    enrich_apply_urls: bool = True


class IndeedSource(_SourceBase):
    """Playwright-based Indeed scraping.

    Disabled by default. Indeed's ToS also forbids scraping and their bot
    detection uses Cloudflare — the adapter detects the "Just a moment"
    challenge and logs+skips rather than crashing.
    """

    type: Literal["indeed"] = "indeed"
    enabled: bool = False
    queries: list[str] = Field(default_factory=list)
    location: str | None = None
    rate_limit_per_minute: int = Field(default=5, gt=0)
    enrich_apply_urls: bool = True


class GlassdoorSource(_SourceBase):
    """Playwright-based Glassdoor scraping.

    Disabled by default. Glassdoor also uses Cloudflare and often requires
    a session cookie for detail pages; the adapter can pick one up from
    the ``GLASSDOOR_SESSION`` env var but falls back to unauthenticated
    fetching for the public search results.
    """

    type: Literal["glassdoor"] = "glassdoor"
    enabled: bool = False
    queries: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=5, gt=0)
    enrich_apply_urls: bool = True


class GreenhouseSource(_SourceBase):
    """Greenhouse boards-api discovery.

    Queries ``boards-api.greenhouse.io`` per configured board slug and
    filters postings client-side by ``title_keywords``. Disabled by default.
    """

    type: Literal["greenhouse"] = "greenhouse"
    enabled: bool = False
    boards: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=30, gt=0)


Source = Annotated[
    CareerPageSource
    | JobUrlSource
    | LinkedInSource
    | IndeedSource
    | GlassdoorSource
    | GreenhouseSource,
    Field(discriminator="type"),
]


FormDriverName = Literal["rules", "llm", "hybrid"]


class FormDriverFieldOverride(BaseModel):
    """Per-field driver override matched by label regex."""

    model_config = _Strict

    label_regex: str
    driver: FormDriverName


class FormDriversConfig(BaseModel):
    """Composable form driver selection — see ARCHITECTURE_COMPOSABLE_FORMS.md §2.6."""

    model_config = _Strict

    default: FormDriverName = "hybrid"
    ats: dict[str, FormDriverName] = Field(default_factory=dict)
    variants: dict[str, FormDriverName] = Field(default_factory=dict)
    fields: list[FormDriverFieldOverride] = Field(default_factory=list)


class ProxyProviderConfig(BaseModel):
    """One entry in the `proxies.providers` list.

    `type` selects the concrete provider (see
    `infrastructure/browser/proxy_pool.py`). Extra fields are provider-
    specific — currently `sources` for `free_list_scraper` and `entries`
    for `static_list`. Unknown provider types raise at composition time
    rather than at YAML validation, so future provider slots (`vps_pool`,
    `commercial`) can be added without a config-model bump."""

    model_config = ConfigDict(extra="allow")
    type: str
    sources: list[str] = Field(default_factory=list)
    entries: list[str] = Field(default_factory=list)
    # Cap on the raw pool size before health-check. Free lists ship 3000+
    # entries; health-checking all of them stalls the CLI at startup.
    # Default 200 gives ~20 alive proxies at typical hit rates.
    max_entries: int = Field(default=200, ge=0)
    # `vps_pool`-specific config surface (ignored by other provider types).
    # Kept on the same model so the YAML shape is uniform.
    hosts: list[str] = Field(default_factory=list)
    scheme: str = "http"
    username: str | None = None
    password: str | None = None


class ProxyPoolConfig(BaseModel):
    """Rotating-proxy pool config. Missing / `enabled: false` → no pool
    is built; discovery adapters fall back to direct fetches (which is
    what pre-Phase-A behaviour did)."""

    model_config = _Strict

    enabled: bool = False
    providers: list[ProxyProviderConfig] = Field(default_factory=list)
    health_check_url: str = "https://httpbin.org/ip"
    health_check_timeout_seconds: float = Field(default=5.0, gt=0)
    health_check_workers: int = Field(default=20, gt=0)
    cooldown_seconds: int = Field(default=900, gt=0)


class ThrottleCaps(BaseModel):
    """Two-window caps used by both apply and discovery throttles."""

    model_config = _Strict

    hourly: int = Field(default=6, ge=0)
    daily: int = Field(default=25, ge=0)


class ApplyThrottleConfig(BaseModel):
    """Per-ATS caps on apply attempts. `ats_default` covers any ATS not
    listed in `ats_overrides`. `global_cap` bounds the total across
    every ATS in the same window — safeguard against a runaway batch."""

    model_config = _Strict

    ats_default: ThrottleCaps = Field(default_factory=ThrottleCaps)
    ats_overrides: dict[str, ThrottleCaps] = Field(default_factory=dict)
    global_cap: ThrottleCaps = Field(
        default_factory=lambda: ThrottleCaps(hourly=15, daily=60)
    )


class BaseConfig(BaseModel):
    """Top-level config shared across every profile."""

    model_config = _Strict

    version: Literal[1] = 1
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    static_answers: StaticAnswers
    paths: Paths = Field(default_factory=Paths)
    sources: list[Source] = Field(default_factory=list)
    form_drivers: FormDriversConfig = Field(default_factory=FormDriversConfig)
    proxies: ProxyPoolConfig = Field(default_factory=ProxyPoolConfig)
    apply_throttle: ApplyThrottleConfig = Field(default_factory=ApplyThrottleConfig)

    def source_names(self) -> set[str]:
        return {s.name for s in self.sources}


class PromptsConfig(BaseModel):
    """LLM prompt instructions.

    Prompts are static behavior specification (what defines a good score, a
    good summary, a good cover letter, a good screening answer, which JD
    terms count as keywords). Per the "Config over code" guardrail they
    live in `configs/prompts.yaml`, not inline in domain modules. Domain
    classes receive the specific prompt string on construction from the
    composition root.
    """

    model_config = _Strict

    version: Literal[1] = 1
    scoring: str
    summary: str
    cover_letter: str
    answer: str
    keyword_extraction: str = ""
    bullet_rewrite: str = ""

    @field_validator("scoring", "summary", "cover_letter", "answer")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be blank")
        return v


class KeywordEntry(BaseModel):
    """One term the operator has verified they can speak to.

    ``evidence`` is a short factual phrase (metric, project, tenure) the
    bullet-injection prompt can weave into a rewritten bullet without
    inventing anything. ``synonyms`` broadens the JD-term match: a job
    that mentions "microservices" still matches an entry keyed on
    "distributed systems" if that synonym is listed. ``tags`` is
    free-form and used only for grouping / filtering in the CLI.
    """

    model_config = _Strict

    term: str
    synonyms: list[str] = Field(default_factory=list)
    evidence: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("term", "evidence")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class LibraryEntry(BaseModel):
    """One verified (or proposed) answer to a real employer's screening question.

    See ``final_dod_plan.md`` W.3. Grows from ``data/answer_proposals.yaml``
    (auto-appended during real ATS runs) after operator review.
    """

    model_config = _Strict

    question: str
    question_regex: str | None = None
    canonical_answer: str
    seen_on: list[str] = Field(default_factory=list)
    status: Literal["verified", "proposed"] = "verified"

    @field_validator("question", "canonical_answer")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class AnswerLibrary(BaseModel):
    """A growing catalog of (ATS question → verified answer) pairs.

    Consulted by ``AnswerRouter`` between the static and narrative tiers.
    Only ``status == "verified"`` entries are used by the router at runtime;
    ``proposed`` entries live in the file for review + promotion.
    """

    model_config = _Strict

    version: Literal[1] = 1
    answers: list[LibraryEntry] = Field(default_factory=list)


class KeywordBank(BaseModel):
    """Global bank of terms + evidence a profile can draw from.

    Loaded from ``configs/keyword_bank.yaml`` (missing file = empty bank,
    not an error — banks are optional). Per-profile overrides are merged
    via ``extend_with`` — override entries win by ``term``.
    """

    model_config = _Strict

    version: Literal[1] = 1
    keywords: list[KeywordEntry] = Field(default_factory=list)

    def extend_with(self, override: KeywordBank) -> KeywordBank:
        """Return a new bank whose entries are self.keywords updated by override.

        Entries with matching ``term`` are replaced by the override entry;
        new terms in override are appended. Original order of self is
        preserved for stability.
        """
        by_term: dict[str, KeywordEntry] = {e.term: e for e in self.keywords}
        for entry in override.keywords:
            by_term[entry.term] = entry
        # Preserve self order, then append terms new-to-override at the end.
        ordered: list[KeywordEntry] = []
        seen: set[str] = set()
        for entry in self.keywords:
            ordered.append(by_term[entry.term])
            seen.add(entry.term)
        for entry in override.keywords:
            if entry.term not in seen:
                ordered.append(entry)
                seen.add(entry.term)
        return KeywordBank(version=self.version, keywords=ordered)


class ApplyBehavior(BaseModel):
    model_config = _Strict

    auto_apply: bool = True
    narrative_style: Literal["concise", "detailed"] = "concise"


class Profile(BaseModel):
    """A search profile. Multiple profiles = multiple resumes and criteria."""

    model_config = _Strict

    name: str
    base_resume: str  # Filename relative to Paths.resumes_dir
    # A profile can override scoring wholesale. Partial merging is not supported
    # yet — see docs/GOF_PATTERNS.md and README for rationale.
    scoring: ScoringConfig | None = None
    sources: list[str] = Field(default_factory=list)
    apply: ApplyBehavior = Field(default_factory=ApplyBehavior)
    # Optional per-profile keyword bank that extends the global one; see
    # LoadedConfig.effective_bank for merge semantics.
    keyword_bank_override: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("profile name must not be blank")
        return v
