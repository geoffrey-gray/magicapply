"""Config-driven regex tables for `AnswerRouter`.

Keeps the model out of `config/models.py` because it holds compiled
`re.Pattern` objects (Pydantic needs `arbitrary_types_allowed`) and it has
its own YAML-loading entry point (`RouterRules.load_default()`).

Two use-sites:

- Package default: `RouterRules.load_default()` loads
  `src/magicapply/infrastructure/browser/ats/data/router_rules.yaml` via
  `importlib.resources` — works in editable installs and packaged wheels.
- Operator override: `config/loader.py::_load_router_rules` reads
  `<config_root>/router_rules.yaml` if present, else falls back to the
  package default.

Both paths compile-once at load time; the router hot-path stays a plain
`re.Pattern.search(...)`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Flag folding -----------------------------------------------------------

_FLAG_ALIASES: dict[str, int] = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
}


def _fold_flags(flags: list[str] | tuple[str, ...] | None) -> int:
    """Turn a list of flag names from YAML into an ORed `re` flag bitmask."""
    if not flags:
        return 0
    bitmask = 0
    for name in flags:
        upper = str(name).strip().upper()
        try:
            bitmask |= _FLAG_ALIASES[upper]
        except KeyError as exc:
            raise ValueError(
                f"unknown re flag {name!r}; supported: {sorted(_FLAG_ALIASES)}"
            ) from exc
    return bitmask


# --- Rule models ------------------------------------------------------------


class RouterRule(BaseModel):
    """One row across identity / yes_no / dei / optional_checkbox / sms_opt_in /
    handler_owned_widget / consent.patterns / single-regex sections.

    Field polymorphism — same model reused across sections that need
    different payloads:
    - identity / yes_no / dei carry ``attr`` (StaticAnswers attribute name)
    - optional_checkbox carries ``check`` (bool)
    - consent / sms_opt_in / handler_owned_widget carry no payload
    - single-regex sections (experience_years, skill_screening) carry no payload

    Extra fields are rejected so a typo in YAML fails fast at load time.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    pattern: re.Pattern[str]
    attr: str | None = None
    check: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Fold ``{pattern, flags, ...}`` into ``{pattern: re.Pattern, ...}``.

        Runs before individual field validation so ``flags`` never reaches
        the strict extra-fields check. Bare regex strings are accepted for
        the ``consent.patterns`` / ``sms_opt_in`` / ``handler_owned_widget``
        style entries; ``{pattern, attr, check}`` dicts are accepted for
        payloaded entries.
        """
        if isinstance(data, (RouterRule, re.Pattern)):
            return data
        if isinstance(data, str):
            return {"pattern": re.compile(data)}
        if isinstance(data, dict):
            out = dict(data)
            raw_pattern = out.get("pattern")
            if raw_pattern is None:
                raise ValueError(f"router rule entry missing 'pattern': {data!r}")
            if isinstance(raw_pattern, re.Pattern):
                out["pattern"] = raw_pattern
            elif isinstance(raw_pattern, str):
                out["pattern"] = re.compile(raw_pattern, _fold_flags(out.get("flags")))
            else:
                raise ValueError(f"router rule 'pattern' must be a string: {raw_pattern!r}")
            out.pop("flags", None)
            return out
        return data


class ConsentSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[RouterRule] = Field(default_factory=list)
    # Ordered — first-match-wins when picking an affirmative option on a
    # radio/select consent widget.
    option_phrases: tuple[str, ...] = ("agree", "accept", "acknowledge", "yes")


class RouterRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    identity: list[RouterRule] = Field(default_factory=list)
    yes_no: list[RouterRule] = Field(default_factory=list)
    dei: list[RouterRule] = Field(default_factory=list)
    consent: ConsentSection = Field(default_factory=ConsentSection)
    optional_checkbox: list[RouterRule] = Field(default_factory=list)
    sms_opt_in: list[RouterRule] = Field(default_factory=list)
    handler_owned_widget: list[RouterRule] = Field(default_factory=list)
    # Variants owned by an ATS handler (Workday multiselect / listbox /
    # date spin). The router short-circuits when field.variant is in
    # this set — variant-based skip replaces label-regex skip for
    # widget-typed fields. Empty in Phase A; populated in Phase B.
    handler_owned_variants: tuple[str, ...] = ()
    experience_years: RouterRule
    skill_screening: RouterRule

    @field_validator("handler_owned_variants", mode="before")
    @classmethod
    def _tuple_variants(cls, v: Any) -> Any:
        if isinstance(v, list):
            return tuple(v)
        return v

    @classmethod
    def load_default(cls) -> RouterRules:
        """Cached load of the package-resource default YAML."""
        return _load_default_cached()

    @classmethod
    def load_from_yaml(cls, text: str) -> RouterRules:
        raw = yaml.safe_load(text)
        if raw is None:
            raise ValueError("empty router rules YAML")
        if not isinstance(raw, dict):
            raise ValueError(
                f"router rules YAML must be a mapping, got {type(raw).__name__}"
            )
        return cls.model_validate(raw)


_PACKAGE_RESOURCE = "router_rules.yaml"
_PACKAGE_ANCHOR = "magicapply.infrastructure.browser.ats.resources"


@lru_cache(maxsize=1)
def _load_default_cached() -> RouterRules:
    text = files(_PACKAGE_ANCHOR).joinpath(_PACKAGE_RESOURCE).read_text(encoding="utf-8")
    return RouterRules.load_from_yaml(text)
