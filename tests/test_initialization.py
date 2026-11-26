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


def test_auto_catch_layout_matches_dashboard(qt_app):
    window = ui_main.KeanSeatsCatcherApp()
    dashboard, auto = window.monitor_interface, window.autocatch_interface
    window.stackedWidget.setAnimationEnabled(False)
    window.resize(900, 650)
    window.show()
    try:
        qt_app.processEvents()
        window.switchTo(auto)
        qt_app.processEvents()
        assert auto.section_input.parentWidget().geometry() == dashboard.section_input.parentWidget().geometry()
        assert auto.log_box.geometry() == dashboard.log_box.geometry()
        assert auto.drop_section_input.y() == auto.interval_input.y() == dashboard.interval_input.y()
        assert auto.drop_section_input.geometry().right() < auto.interval_input.x()
        assert auto.waitlist_switch.y() == dashboard.waitlist_switch.y()
        assert auto.btn_start.y() == dashboard.btn_start.y()
        log_title = next(label for label in auto.findChildren(ui_main.BodyLabel)
                         if label.text() == ui_main.i18n.tr("lbl_log"))
        assert auto.status_label.y() == log_title.y()
        assert auto.status_label.x() > log_title.x()
        assert auto.status_label.geometry().right() == auto.log_box.geometry().right()
    finally:
        window.close()


def test_dashboard_session_summary_and_persisted_keepalive_preferences(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr("i18n.app_dir", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"Settings": {"Language": "en_US", "KeepBrowserOpen": True}}), encoding="utf-8")
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="fixture">'
    send = Mock(side_effect=[response(status=401), response(text=valid)])
    monkeypatch.setattr(requests.Session, "request", send)
    monkeypatch.setattr(ui_main.InfoBar, "success", Mock())
    monkeypatch.setattr(ui_main.InfoBar, "warning", Mock())

    window = ui_main.KeanSeatsCatcherApp()
    monitor, auth, settings = window.monitor_interface, window.auth_interface, window.settings_interface
    summary = monitor.session_summary
    auth.auth_manager = Mock()
    try:
        window.show()
        qt_app.processEvents()
        nav_panel = window.navigationInterface.panel
        assert nav_panel.menuButton.parentWidget() is nav_panel and nav_panel.menuButton.isHidden()
        assert settings.keepalive_preferences == (True, 10)
        assert [settings.keepalive_interval.itemData(i) for i in range(3)] == [5, 10, 15]
        assert monitor.btn_ping.geometry().right() == monitor.section_input.geometry().right()
        assert monitor.btn_keepalive.y() == monitor.btn_ping.y()
        assert monitor.btn_keepalive.geometry().right() < monitor.btn_ping.x()
        assert not monitor.btn_ping.isEnabled() and not monitor.btn_keepalive.isEnabled()

        tr = ui_main.i18n.tr
        assert summary.isVisible()
        assert summary.status_label.text() == tr("session_unverified")
        assert summary.automatic_label.text() == f"{tr('session_auto_on')} · {tr('keepalive_state_waiting')}"

        auth.on_credentials_extracted(CREDENTIALS)
        keeper = window.session_keepalive
        first_due = keeper.snapshot().next_due
        assert monitor.btn_ping.isEnabled() and monitor.btn_keepalive.isEnabled()
        assert summary.last_check_label.text() == summary.last_keepalive_label.text() == "—"
        assert tr("session_next_keepalive") in summary.automatic_label.toolTip()
        send.assert_not_called()

        settings.keepalive_interval.setCurrentIndex(2)
        assert keeper.snapshot().next_due == first_due + 300
        settings.keepalive_switch.setChecked(False)
        assert keeper.snapshot().next_due is None
        assert summary.automatic_label.text() == tr("session_auto_off")

        monitor.btn_ping.click()
        for _ in range(150):
            if not monitor.is_checking_session:
                break
            QTest.qWait(10)
        assert summary.status_label.text() == tr("session_expired")
        last_check = summary.last_check_label.text()
        last_check_tooltip = summary.last_check_label.toolTip()
        assert last_check.startswith(f"{tr('session_result_expired')} · ") and last_check_tooltip
        assert summary.last_keepalive_label.text() == "—"
        send.assert_called_once()

        monitor.btn_keepalive.click()
        for _ in range(150):
            if summary.last_keepalive_label.text() != "—":
                break
            QTest.qWait(10)
        assert summary.last_keepalive_label.text().startswith(f"{tr('session_result_completed')} · ")
        assert summary.last_keepalive_label.toolTip()
        assert summary.last_check_label.text() == last_check
        assert summary.last_check_label.toolTip() == last_check_tooltip
        assert summary.status_label.text() == tr("session_alive")
        assert summary.automatic_label.text() == tr("session_auto_off")
        assert send.call_count == 2
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
