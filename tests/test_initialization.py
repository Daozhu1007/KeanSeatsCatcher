import json
from unittest.mock import Mock

import pytest
import requests
from PyQt6.QtCore import QAbstractAnimation, QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QPushButton, QWidget
from qfluentwidgets import Flyout

import cloud_cli
import core_auth
import ui_main
from registration_reader import DegreePlanRegistrationReader
from test_registration import client, response, snapshot, S
from ui_session import SessionStatusButton


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


@pytest.mark.parametrize("dismiss", ["button", "shadow", "outside", "escape"])
def test_session_status_flyout_dismissal(qt_app, dismiss):
    window = QWidget()
    window.resize(620, 400)
    button = SessionStatusButton()
    button.setParent(window)
    button.setText("Session: Unchecked")
    button.adjustSize()
    button.move(420, 20)
    outside = QPushButton("Outside", window)
    outside.move(20, 300)
    outside_clicked = QSignalSpy(outside.clicked)
    window.show()
    try:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        flyout = button.findChild(Flyout)
        animation = flyout.aniManager.aniGroup
        animation.setCurrentTime(animation.duration())
        closed = QSignalSpy(flyout.closed)
        settled = flyout.pos()

        if dismiss == "escape":
            QTest.keyClick(window, Qt.Key.Key_Escape)
        elif dismiss == "shadow":
            edge = button.rect().bottomRight() - QPoint(12, 0)
            position = flyout.mapFromGlobal(button.mapToGlobal(edge))
            assert flyout.rect().contains(position)
            QTest.mouseClick(flyout, Qt.MouseButton.LeftButton, pos=position)
        else:
            QTest.mouseClick(button if dismiss == "button" else outside, Qt.MouseButton.LeftButton)
        assert len(outside_clicked) == (dismiss == "outside")
        assert flyout.isVisible() and len(closed) == 0
        assert animation.direction() == QAbstractAnimation.Direction.Backward
        animation.setCurrentTime(animation.duration() // 2)
        assert 0 < flyout.windowOpacity() < 1
        assert flyout.x() == settled.x() and flyout.y() < settled.y()
        assert closed.wait(1000)
        assert button._flyout is None
    finally:
        if button._flyout is not None:
            button._flyout.close()
        window.close()


def test_session_status_flyout_reverses_in_place(qt_app):
    button = SessionStatusButton()
    button.show()
    try:
        button.click()
        flyout = button.findChild(Flyout)
        animation = flyout.aniManager.aniGroup
        animation.setCurrentTime(animation.duration() // 2)
        for direction in (QAbstractAnimation.Direction.Backward, QAbstractAnimation.Direction.Forward):
            before = (animation.currentTime(), flyout.pos(), flyout.windowOpacity())
            button.click()
            assert animation.direction() == direction
            assert (animation.currentTime(), flyout.pos(), flyout.windowOpacity()) == before
            assert button.findChildren(Flyout) == [flyout] and flyout.isVisible()
        animation.setCurrentTime(animation.duration())
        assert flyout.windowOpacity() == 1
    finally:
        if button._flyout is not None:
            button._flyout.close()
        button.close()


def test_dashboard_session_flyout_and_persisted_keepalive_preferences(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr("i18n.app_dir", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"Settings": {"Language": "en_US", "KeepBrowserOpen": True}}), encoding="utf-8")
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="fixture">'
    send = Mock(return_value=response(text=valid))
    monkeypatch.setattr(requests.Session, "request", send)
    monkeypatch.setattr(ui_main.InfoBar, "success", Mock())

    window = ui_main.KeanSeatsCatcherApp()
    monitor, auth, settings = window.monitor_interface, window.auth_interface, window.settings_interface
    auth.auth_manager = Mock()
    flyout = None
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

        QTest.mouseClick(monitor.session_status_button, Qt.MouseButton.LeftButton)
        flyout = monitor.session_status_button.findChild(Flyout)
        assert flyout is not None and flyout.isVisible()
        values = flyout.view.values
        assert values["next_check"].text() == "—"

        auth.on_credentials_extracted(CREDENTIALS)
        keeper = window.session_keepalive
        first_due = keeper.snapshot().next_due
        assert monitor.btn_ping.isEnabled() and monitor.btn_keepalive.isEnabled()
        assert values["last_check"].text() == values["last_renewal"].text() == "—"
        assert values["next_check"].text() != "—"
        send.assert_not_called()

        settings.keepalive_interval.setCurrentIndex(2)
        assert keeper.snapshot().next_due == first_due + 300
        settings.keepalive_switch.setChecked(False)
        assert keeper.snapshot().next_due is None
        assert values["next_check"].text() == "—"

        monitor.btn_keepalive.click()
        expected_status = ui_main.i18n.tr("session_state_valid")
        expected_summary = ui_main.i18n.tr("session_status_summary", expected_status)
        for _ in range(150):
            if monitor.session_status_button.text() == expected_summary:
                break
            QTest.qWait(10)
        assert values["last_check"].text() != "—"
        assert values["last_renewal"].text() == values["next_check"].text() == "—"
        assert values["last_check"].text() == ui_main.i18n.tr("session_time_just_now")
        assert values["last_check"].toolTip() and not values["last_renewal"].toolTip()
        assert values["status"].text() == ui_main.i18n.tr("session_alive")
        assert values["keepalive"].text() == ui_main.i18n.tr("keepalive_state_disabled")
        assert monitor.session_status_button.text() == expected_summary
        assert send.call_count == 1
    finally:
        if flyout is not None:
            flyout.close()
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
