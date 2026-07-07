"""Unit tests for WorkdayAccountStore."""

from __future__ import annotations

from pathlib import Path

from magicapply.infrastructure.browser.ats.workday_accounts import WorkdayAccountStore


def test_tenant_from_url_uses_host() -> None:
    url = "https://pluralsight.wd1.myworkdayjobs.com/en-US/Careers/job/Staff-DS_R001"
    assert (
        WorkdayAccountStore.tenant_from_url(url)
        == "pluralsight.wd1.myworkdayjobs.com"
    )


def test_careers_login_url_builds_redirect() -> None:
    page_url = (
        "https://pluralsight.wd1.myworkdayjobs.com/en-US/Careers/job/"
        "Remote---USA/Staff-Data-Scientist_R0014221/apply/applyManually"
    )
    login = WorkdayAccountStore.careers_login_url(page_url)
    assert login is not None
    assert "/en-US/Careers/login" in login
    assert "redirect=" in login
    assert "apply%2FapplyManually" in login


def test_normalize_en_us_url_replaces_italian_locale() -> None:
    url = (
        "https://circle.wd1.myworkdayjobs.com/it-IT/Circle/job/"
        "Staff-Data-Scientist---Digital-Assets_JR101068"
    )
    assert "/en-US/Circle/job/" in WorkdayAccountStore.normalize_en_us_url(url)


def test_url_needs_en_us_for_any_non_english_locale() -> None:
    assert WorkdayAccountStore.url_needs_en_us(
        "https://circle.wd1.myworkdayjobs.com/fr-FR/Circle/job/x"
    )
    assert not WorkdayAccountStore.url_needs_en_us(
        "https://circle.wd1.myworkdayjobs.com/en-US/Circle/job/x"
    )


def test_normalize_en_us_url_is_idempotent() -> None:
    url = (
        "https://circle.wd1.myworkdayjobs.com/en-US/Circle/job/"
        "Staff-Data-Scientist---Digital-Assets_JR101068"
    )
    assert WorkdayAccountStore.normalize_en_us_url(url) == url


def test_careers_login_url_supports_employer_branded_site() -> None:
    page_url = (
        "https://circle.wd1.myworkdayjobs.com/en-US/Circle/job/"
        "Staff-Data-Scientist---Digital-Assets_JR101068/apply/applyManually"
    )
    login = WorkdayAccountStore.careers_login_url(page_url)
    assert login is not None
    assert "/en-US/Circle/login" in login
    assert "redirect=" in login


def test_upsert_and_reload_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "workday_accounts.yaml"
    store = WorkdayAccountStore(path)
    store.upsert(
        "acme.wd1.myworkdayjobs.com",
        email="j@example.com",
        password="secret-pass",
    )
    reloaded = WorkdayAccountStore(path)
    account = reloaded.get("acme.wd1.myworkdayjobs.com")
    assert account is not None
    assert account.email == "j@example.com"
    assert account.password == "secret-pass"
    assert account.created_at
    assert account.last_used_at


def test_touch_updates_last_used_without_new_entry(tmp_path: Path) -> None:
    path = tmp_path / "workday_accounts.yaml"
    store = WorkdayAccountStore(path)
    store.upsert("acme.wd1.myworkdayjobs.com", email="j@example.com", password="p")
    first_used = store.get("acme.wd1.myworkdayjobs.com")
    assert first_used is not None
    store.touch("acme.wd1.myworkdayjobs.com")
    second = WorkdayAccountStore(path).get("acme.wd1.myworkdayjobs.com")
    assert second is not None
    assert second.last_used_at is not None
    assert first_used.last_used_at is not None