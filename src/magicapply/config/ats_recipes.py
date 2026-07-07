"""YAML-driven ATS apply recipes for GenericHandler (custom ATS / PR4)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

_Strict = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

_BUILTIN_RECIPES_DIR = (
    Path(__file__).resolve().parents[3] / "configs" / "ats_recipes"
)


class RecipeStep(BaseModel):
    """Pre-navigation interaction (cookie banner, Apply CTA, etc.)."""

    model_config = _Strict

    action: Literal["click", "wait"]
    selector: str
    timeout_ms: int = Field(default=500, gt=0)


class WizardRecipe(BaseModel):
    """Multi-step Next/Continue loop configuration."""

    model_config = _Strict

    next_selectors: tuple[str, ...] = (
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "[data-testid='next-button']",
        "button[id*='next']",
    )
    max_steps: int = Field(default=10, gt=0, le=25)
    review_selectors: tuple[str, ...] = (
        "button:has-text('Submit')",
        "button[type='submit']",
        "[data-testid*='submit']",
    )


class ATSRecipe(BaseModel):
    """Platform- or host-specific apply behavior for GenericHandler."""

    model_config = _Strict

    platform: str = "generic"
    match_hosts: tuple[str, ...] = ()
    form_selectors: tuple[str, ...] = (
        "form#application-form",
        "form[action*='apply']",
        "form",
        "main form",
    )
    submit_selectors: tuple[str, ...] = (
        "input[type='submit']",
        "button[type='submit']",
        "button[name='submit']",
        "input[name='submit']",
        "button[id*='submit']",
        "[data-testid*='submit']",
        "[data-automation-id*='submit']",
    )
    resume_selectors: tuple[str, ...] = (
        "input[type='file'][name='resume']",
        "input[type='file'][id*='resume']",
        "input[type='file'][name*='cv']",
        "input[type='file']",
    )
    pre_steps: tuple[RecipeStep, ...] = ()
    wizard: WizardRecipe | None = None
    # Rewrite listing URLs to the apply shell (Eightfold: /careers/apply?pid=…).
    apply_path_template: str | None = None
    pid_regex: str | None = None
    # Path substring fallbacks when the host is a vanity domain (iCIMS, Phenom).
    match_paths: tuple[str, ...] = ()


def default_recipe() -> ATSRecipe:
    return ATSRecipe(platform="generic")


def bundled_recipes_dir() -> Path:
    return _BUILTIN_RECIPES_DIR


def load_ats_recipes(root: Path | None = None) -> dict[str, ATSRecipe]:
    """Load platform + employer recipes from ``ats_recipes/`` (incl. ``employers/``)."""
    recipes: dict[str, ATSRecipe] = {}
    for directory in _recipe_search_dirs(root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            recipe = _load_recipe_file(path)
            recipes[recipe.platform] = recipe
        employers = directory / "employers"
        if employers.is_dir():
            for path in sorted(employers.glob("*.yaml")):
                recipe = _load_recipe_file(path)
                recipes[recipe.platform] = recipe
    if not recipes:
        recipes["generic"] = default_recipe()
    return recipes


def resolve_recipe(url: str, recipes: dict[str, ATSRecipe] | None = None) -> ATSRecipe:
    """Pick the best recipe — longest host marker wins, then path markers."""
    catalog = recipes if recipes is not None else load_ats_recipes()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    best: ATSRecipe | None = None
    best_score = 0
    for recipe in catalog.values():
        if recipe.platform == "generic":
            continue
        for marker in recipe.match_hosts:
            if marker in host:
                score = len(marker) + 100
                if score > best_score:
                    best = recipe
                    best_score = score
        for marker in recipe.match_paths:
            if marker in path:
                score = len(marker)
                if score > best_score:
                    best = recipe
                    best_score = score
    if best is not None:
        return best
    return catalog.get("generic", default_recipe())


def resolve_navigation_url(job_url: str, recipe: ATSRecipe) -> str:
    """Map a discovery/job URL to the apply shell when a recipe declares a template."""
    if not recipe.apply_path_template or not recipe.pid_regex:
        return job_url
    parsed = urlparse(job_url)
    match = re.search(recipe.pid_regex, f"{parsed.path}?{parsed.query}")
    if not match:
        return job_url
    pid = match.group(1)
    path = recipe.apply_path_template.format(pid=pid)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


@lru_cache(maxsize=1)
def cached_recipes() -> dict[str, ATSRecipe]:
    """Process-wide recipe catalog (bundled + optional cwd configs)."""
    from magicapply.config.paths import default_config_root

    return load_ats_recipes(default_config_root())


def _recipe_search_dirs(root: Path | None) -> tuple[Path, ...]:
    dirs: list[Path] = []
    if root is not None:
        dirs.append(root / "ats_recipes")
    dirs.append(_BUILTIN_RECIPES_DIR)
    # De-dupe while preserving order.
    seen: set[Path] = set()
    ordered: list[Path] = []
    for d in dirs:
        resolved = d.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return tuple(ordered)


def _load_recipe_file(path: Path) -> ATSRecipe:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pre_steps = tuple(RecipeStep.model_validate(s) for s in payload.get("pre_steps", ()))
    wizard_raw = payload.get("wizard")
    wizard = WizardRecipe.model_validate(wizard_raw) if wizard_raw else None
    selectors = payload.get("form_selectors") or payload.get("form_selector")
    if isinstance(selectors, str):
        selectors = [selectors]
    submit = payload.get("submit_selectors")
    if isinstance(submit, str):
        submit = [submit]
    resume = payload.get("resume_selectors")
    if isinstance(resume, str):
        resume = [resume]
    hosts = payload.get("match_hosts") or ()
    if isinstance(hosts, str):
        hosts = [hosts]
    paths = payload.get("match_paths") or ()
    if isinstance(paths, str):
        paths = [paths]
    return ATSRecipe(
        platform=str(payload.get("platform", path.stem)),
        match_hosts=tuple(hosts),
        match_paths=tuple(paths),
        form_selectors=tuple(selectors or default_recipe().form_selectors),
        submit_selectors=tuple(submit or default_recipe().submit_selectors),
        resume_selectors=tuple(resume or default_recipe().resume_selectors),
        pre_steps=pre_steps,
        wizard=wizard,
        apply_path_template=payload.get("apply_path_template"),
        pid_regex=payload.get("pid_regex"),
    )