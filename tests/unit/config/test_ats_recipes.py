"""Tests for YAML ATS recipe loading (PR4)."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.ats_recipes import (
    bundled_recipes_dir,
    load_ats_recipes,
    resolve_navigation_url,
    resolve_recipe,
)


def test_loads_eightfold_recipe_from_bundled_configs() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    assert "eightfold" in recipes
    recipe = recipes["eightfold"]
    assert recipe.platform == "eightfold"
    assert "eightfold.ai" in recipe.match_hosts
    assert recipe.wizard is not None
    assert recipe.apply_path_template == "/careers/apply?pid={pid}"


def test_resolve_recipe_matches_eightfold_host() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = resolve_recipe(
        "https://symetra.eightfold.ai/careers/job/446718943971",
        recipes,
    )
    assert recipe.platform == "eightfold"


def test_resolve_navigation_url_rewrites_job_to_apply_shell() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = recipes["eightfold"]
    url = resolve_navigation_url(
        "https://symetra.eightfold.ai/careers/job/446718943971",
        recipe,
    )
    assert url == "https://symetra.eightfold.ai/careers/apply?pid=446718943971"


def test_unknown_host_falls_back_to_generic() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = resolve_recipe("https://careers.example-custom.com/jobs/1", recipes)
    assert recipe.platform == "generic"