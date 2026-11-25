import json
from unittest.mock import Mock

import pytest
import requests
from PyQt6.QtTest import QTest

import cloud_cli
import core_auth
import ui_main
from registration_reader import DegreePlanRegistrationReader
from test_registration import client, response, snapshot, S


CREDENTIALS = {"cookies": {}, "verification_token": "fixture-token", "student_id": "fixture-student"}


@pytest.mark.parametrize("route", ["session_file", "headless", "visible"])
def test_cli_authentication_paths_enable_scoped_reader(tmp_path, monkeypatch, route):
    monkeypatch.setenv("KSC_LOG_DIR", str(tmp_path))
    auth = Mock()
    auth.silent_relogin.return_value = auth.extract_credentials.return_value = CREDENTIALS
    monkeypatch.setattr(core_auth, "KeanAuthManager", lambda: auth)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    session = tmp_path / "fixture-session.json"
    session.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    api, _ = cloud_cli.authenticate(Mock(), route != "visible",
                                   str(session) if route == "session_file" else None)
    assert isinstance(api.registration_reader, DegreePlanRegistrationReader)


def test_gui_extraction_enables_scoped_reader(tmp_path, monkeypatch, qt_app):
    monkeypatch.setenv("KSC_LOG_DIR", str(tmp_path))
    widget = ui_main.AuthInterface()
    widget.auth_manager = Mock()
    widget.auth_manager.extract_credentials.return_value = CREDENTIALS
    monkeypatch.setattr(ui_main.InfoBar, "success", Mock())
    engines = []
    widget.engine_ready_signal.connect(engines.append)
    widget.handle_extract()
    assert widget.extract_worker.wait(1000)
    qt_app.processEvents()
    assert isinstance(engines[0].registration_reader, DegreePlanRegistrationReader)
    assert not widget.findChildren(ui_main.LineEdit)
    widget.close()


def test_dashboard_session_card_and_persisted_keepalive_preferences(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr("i18n.app_dir", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"Settings": {"Language": "en_US", "KeepBrowserOpen": True}}), encoding="utf-8")
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="fixture">'
    send = Mock(return_value=response(text=valid))
    monkeypatch.setattr(requests.Session, "request", send)
    monkeypatch.setattr(ui_main.InfoBar, "success", Mock())

    window = ui_main.KeanSeatsCatcherApp()
    monitor, auth, settings = window.monitor_interface, window.auth_interface, window.settings_interface
    card = monitor.session_card
    auth.auth_manager = Mock()
    try:
        assert settings.keepalive_preferences == (True, 10)
        assert [settings.keepalive_interval.itemData(i) for i in range(3)] == [5, 10, 15]
        assert not card.btn_check.isEnabled() and not card.btn_keepalive.isEnabled()
        assert card.values["next_check"].text() == "—"

        auth.on_credentials_extracted(CREDENTIALS)
        keeper = window.session_keepalive
        first_due = keeper.snapshot().next_due
        assert card.btn_check.isEnabled() and card.btn_keepalive.isEnabled()
        assert card.values["last_check"].text() == card.values["last_renewal"].text() == "—"
        assert card.values["next_check"].text() != "—"
        send.assert_not_called()

        settings.keepalive_interval.setCurrentIndex(2)
        assert keeper.snapshot().next_due == first_due + 300
        settings.keepalive_switch.setChecked(False)
        assert keeper.snapshot().next_due is None
        assert card.values["next_check"].text() == "—"

        card.btn_keepalive.click()
        for _ in range(150):
            if card.values["last_check"].text() != "—":
                break
            QTest.qWait(10)
        assert card.values["last_check"].text() != "—"
        assert card.values["last_renewal"].text() == card.values["next_check"].text() == "—"
        assert card.values["status"].text() == ui_main.i18n.tr("session_state_valid")
        assert card.values["keepalive"].text() == ui_main.i18n.tr("keepalive_state_disabled")
        assert send.call_count == 1
    finally:
        window.close()
        if window.session_keepalive is not None:
            window.session_keepalive._thread.join(3)

    reopened = ui_main.KeanSeatsCatcherApp()
    try:
        assert reopened.settings_interface.keepalive_preferences == (False, 15)
        assert reopened.settings_interface.browser_switch.isChecked()
    finally:
        reopened.close()


def test_cli_verify_only_never_starts_a_registration_worker(tmp_path, monkeypatch):
    api = client(tmp_path, [], [snapshot(a=S.REGISTERED)])
    monkeypatch.setattr(cloud_cli, "authenticate", lambda *args, **kwargs: (api, None))
    monkeypatch.setattr(cloud_cli, "setup_logging", Mock())
    monkeypatch.setattr(cloud_cli, "CloudWorker", lambda *args, **kwargs: pytest.fail("Worker must not start"))
    monkeypatch.setattr("sys.argv", ["cloud_cli.py", "--sections", "a", "--verify-only"])
    cloud_cli.main()
    assert api.all_registered(["a"])
    api.session.request.assert_not_called()
