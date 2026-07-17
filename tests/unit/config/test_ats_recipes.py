"""Tests for YAML ATS recipe loading (PR4)."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.ats_recipes import (
    bundled_recipes_dir,
    load_ats_recipes,
    resolve_navigation_url,
    resolve_recipe,
)


def test_loads_indeed_and_linkedin_board_recipes() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    assert "indeed" in recipes
    assert "linkedin" in recipes
    assert recipes["indeed"].platform == "indeed"
    assert recipes["linkedin"].platform == "linkedin"
    assert recipes["indeed"].wizard is not None
    assert recipes["linkedin"].wizard is not None
    # Guest people-search / login chrome must not be a form_selector fallback.
    assert "form" not in recipes["indeed"].form_selectors
    assert "main form" not in recipes["indeed"].form_selectors
    assert "form" not in recipes["linkedin"].form_selectors
    assert "main form" not in recipes["linkedin"].form_selectors


def test_loads_workable_and_smartrecruiters_recipes() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    assert "workable" in recipes
    assert "smartrecruiters" in recipes
    assert resolve_recipe(
        "https://apply.workable.com/acme/j/ABC", recipes
    ).platform == "workable"
    assert resolve_recipe(
        "https://jobs.smartrecruiters.com/Acme/123", recipes
    ).platform == "smartrecruiters"


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


def test_loads_pr6_platform_and_employer_recipes() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    assert "phenom" in recipes
    assert "icims" in recipes
    assert "custom_careers" in recipes
    assert "netflix" in recipes


def test_resolve_icims_from_paramount_job_path() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    job_url = (
        "https://careers.paramount.com/job/New-York-Senior-Data-Scientist-NY-10036/1394334600/"
    )
    assert resolve_recipe(job_url, recipes).platform == "icims"


def test_resolve_icims_navigation_rewrite() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    job_url = (
        "https://careers.paramount.com/job/New-York-Senior-Data-Scientist-NY-10036/1394334600/"
    )
    url = resolve_navigation_url(job_url, recipes["icims"])
    assert url == (
        "https://careers.paramount.com/talentcommunity/apply/1394334600/?locale=en_US"
    )


def test_resolve_netflix_employer_override() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = resolve_recipe("https://explore.jobs.netflix.net/careers/job/1", recipes)
    assert recipe.platform == "netflix"


def test_resolve_hyatt_custom_careers_host() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = resolve_recipe(
        "https://careers.hyatt.com/en-US/careers/jobdetails/4437468321",
        recipes,
    )
    assert recipe.platform == "custom_careers"


def test_resolve_phenom_host() -> None:
    recipes = load_ats_recipes(bundled_recipes_dir().parent)
    recipe = resolve_recipe(
        "https://careers.acme.phenompeople.com/us/en/job/12345",
        recipes,
    )
    assert recipe.platform == "phenom"