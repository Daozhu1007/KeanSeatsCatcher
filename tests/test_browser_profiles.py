from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException

import core_auth
from browser_profiles import BrowserProfileBusy, BrowserProfiles
from core_auth import KeanAuthManager


def fake_browser(**kwargs):
    options = kwargs["options"]
    prefix = "--user-data-dir="
    for argument in options.arguments:
        if argument.startswith(prefix):
            Path(argument[len(prefix):]).mkdir(parents=True, exist_ok=True)
    return Mock()


def test_chrome_fallback_reuses_its_profile_after_restart(monkeypatch, tmp_path):
    edge = Mock(side_effect=WebDriverException("Edge unavailable"))
    chrome = Mock(side_effect=fake_browser)
    monkeypatch.setattr(core_auth.webdriver, "Edge", edge)
    monkeypatch.setattr(core_auth.webdriver, "Chrome", chrome)

    first = KeanAuthManager(persistent_profile=True)
    first.open_login_page(detach=True)
    options = chrome.call_args.kwargs["options"]
    assert f"--user-data-dir={tmp_path / 'browser-profiles' / 'chrome'}" in options.arguments
    assert not options.experimental_options.get("detach")
    driver = first.driver
    first.finish_login()
    driver.quit.assert_called_once()

    restarted = KeanAuthManager(persistent_profile=True)
    restarted.open_login_page()
    edge.assert_called_once()
    assert chrome.call_count == 2
    assert chrome.call_args.kwargs["options"].arguments == options.arguments
    restarted.close_browser()

    edge.side_effect = fake_browser
    temporary = KeanAuthManager()
    temporary.launch_browser(headless=True, detach=True)
    options = edge.call_args.kwargs["options"]
    assert not any(arg.startswith("--user-data-dir=") for arg in options.arguments)
    assert options.experimental_options["detach"]
    temporary.close_browser()


def test_clear_is_limited_to_ksc_profiles_and_checks_both_leases(tmp_path):
    store = BrowserProfiles()
    for browser in store.browsers:
        store.path(browser).mkdir(parents=True)
    unrelated = tmp_path / "catches.sqlite3"
    unrelated.touch()
    store.remember_browser("Chrome")

    with store.acquire("Chrome"):
        with pytest.raises(BrowserProfileBusy):
            store.clear()
        assert store.path("Edge").exists()
        assert store.path("Chrome").exists()

    store.clear()
    assert not store.root.exists()
    assert store.preferred_browser() is None
    assert unrelated.exists()


def test_late_browser_start_keeps_its_lease_until_quit(monkeypatch):
    unblocked = Event()
    quit_called = Event()
    first = Mock()
    first.quit.side_effect = quit_called.set

    def delayed_browser(**kwargs):
        assert unblocked.wait(5)
        return first

    monkeypatch.setattr(core_auth.webdriver, "Edge", delayed_browser)
    monkeypatch.setattr(core_auth.webdriver, "Chrome", fake_browser)
    auth = KeanAuthManager(persistent_profile=True)
    try_browser = auth._try_browser_with_timeout
    monkeypatch.setattr(auth, "_try_browser_with_timeout",
                        lambda name, launch: try_browser(name, launch, .1 if name == "Edge" else 2))

    try:
        auth.open_login_page()
        assert auth.driver is not first
        with pytest.raises(BrowserProfileBusy):
            auth._profiles.acquire("Edge")
    finally:
        unblocked.set()
        auth.close_browser()
    assert quit_called.wait(3)


def test_saved_profile_recovery_uses_redirects_without_submitting_okta(monkeypatch):
    auth = KeanAuthManager(persistent_profile=True)
    login = Mock(current_url="https://keanuniv.okta.com/?target=DegreePlans")
    monkeypatch.setattr(core_auth.webdriver, "Edge", Mock(return_value=login))
    click = Mock()
    monkeypatch.setattr(auth, "_click_okta_login", click)

    def until(condition):
        if not condition(login):
            raise TimeoutException()

    monkeypatch.setattr(core_auth, "WebDriverWait", Mock(return_value=Mock(until=until)))
    with pytest.raises(RuntimeError, match=core_auth.i18n.tr("err_browser_sign_in_required")):
        auth.silent_relogin()
    login.get.assert_called_once_with(auth.login_url)
    login.quit.assert_called_once()
    click.assert_not_called()

    login.reset_mock()
    login.current_url = auth.login_url
    login.execute_script.side_effect = ['{"student_id": "1234567", "token": "fixture"}', "fixture"]
    assert auth.silent_relogin()["student_id"] == "1234567"
    login.quit.assert_called_once()
    click.assert_not_called()
