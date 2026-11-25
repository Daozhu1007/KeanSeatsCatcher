import datetime
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from PyQt6.QtTest import QTest

from auto_catch_worker import AutoCatchWorker, STATE_IDLE, STATE_UNAVAILABLE
from cloud_worker import CloudWorker
from ui_main import InfoBar, KeanSeatsCatcherApp, MonitorWorker
from ui_autocatch import AutoCatchInterface
from i18n import i18n
from test_registration import client, response, snapshot, events, S
from worker_session import recover_session


@pytest.mark.parametrize("worker_class", [AutoCatchWorker, CloudWorker])
def test_polling_workers_verify_timeout_without_waiting_for_seats(tmp_path, worker_class):
    api = client(tmp_path, [response({"Available": 1}), requests.ReadTimeout()],
                 [snapshot(), snapshot(a=S.REGISTERED)])
    worker = worker_class(api, ["a"], interval=1)
    worker._sleep = Mock()
    worker.run()
    assert api.all_registered(["a"])
    assert api.session.request.call_count == 2  # No second availability or Add.
    assert not worker.is_running
    assert events(api)[-1]["event"] == "worker_stopped"


def test_dashboard_shares_verification_and_finishes_only_when_confirmed(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()], [snapshot(), snapshot(a=S.REGISTERED)])
    worker = MonitorWorker(api, ["a"], 0, "")
    worker._sleep = Mock()
    finished = []
    worker.finished_signal.connect(finished.append)
    worker.run()
    assert finished == [True]
    assert api.session.request.call_count == 1
    assert worker._sleep.call_args.args[0] >= 2


@pytest.mark.parametrize("worker_class", [MonitorWorker, AutoCatchWorker])
def test_new_task_submits_again_after_manual_drop(tmp_path, worker_class):
    api = client(tmp_path, [])
    account_status = S.NOT_REGISTERED

    def request(method, url, **kwargs):
        nonlocal account_status
        if url.endswith("/SectionDetails"):
            return response({"Available": int(account_status == S.NOT_REGISTERED)})
        if url.endswith("/RegisterSections"):
            entries = kwargs["json"]["sectionRegistrations"]
            assert [(entry["SectionId"], entry["Action"]) for entry in entries] == [("a", "Add")]
            assert account_status == S.NOT_REGISTERED
        elif url.endswith("/CompleteRegistration"):
            account_status = S.REGISTERED
        else:
            pytest.fail(f"Unexpected request: {method} {url}")
        return response([])

    api.session.request.side_effect = request
    api.registration_reader.read.side_effect = lambda *_: snapshot(a=account_status)
    assert api.execute_full_attack(["a"])[0]
    session, original_history = api.session, api.history.records()
    assert len(original_history) == 1

    # Drop on KeanWISE while KSC still holds the previous confirmation.
    account_status = S.NOT_REGISTERED
    args = (api, ["a"], 0, "") if worker_class is MonitorWorker else (api, ["a"], 1)
    worker = worker_class(*args)
    worker._sleep = lambda _: pytest.fail("The new registration should succeed in one round")
    worker.run()

    assert account_status == S.REGISTERED and api.all_registered(["a"])
    assert sum(call.args[1].endswith("/RegisterSections") for call in session.request.call_args_list) == 2
    assert api.registration_reader.read.call_count == 2
    assert api.session is session
    assert api.history.records() == original_history


@pytest.mark.parametrize("worker_class", [MonitorWorker, AutoCatchWorker])
def test_new_task_keeps_unconfirmed_add_and_its_grace_period(tmp_path, monkeypatch, worker_class):
    clock = [0]
    monkeypatch.setattr("core_api.time.monotonic", lambda: clock[0])
    api = client(tmp_path, [requests.ReadTimeout()],
                 [snapshot(a=S.NOT_REGISTERED), snapshot(a=S.NOT_REGISTERED), snapshot(a=S.REGISTERED)])
    assert not api.execute_full_attack(["a"])[0]
    clock[0] = 4

    args = (api, ["a"], 0, "") if worker_class is MonitorWorker else (api, ["a"], 1)
    worker = worker_class(*args)

    def wait_for_late_write(_):
        if clock[0] != 4 or api.session.request.call_count != 1:
            pytest.fail("Unconfirmed Add must not be repeated")
        clock[0] = 6

    worker._sleep = wait_for_late_write
    worker.run()

    assert api.all_registered(["a"])
    api.session.request.assert_called_once()
    assert api.registration_reader.read.call_count == 3
    record, = api.history.records()
    assert record["requests"][0]["reason"] == "ReadTimeout"


def test_autocatch_new_task_waits_for_unconfirmed_drop(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()], [
        snapshot(a=S.NOT_REGISTERED, old=S.REGISTERED),
        snapshot(a=S.REGISTERED),
        snapshot(a=S.REGISTERED, old=S.NOT_REGISTERED),
    ])
    assert not api.execute_full_attack(["a"], drop_section_id="old")[0]

    worker = AutoCatchWorker(api, ["a"], 1, drop_section_id="old")
    worker._sleep = lambda _: pytest.fail("The swap should be confirmed in one poll")
    worker.run()

    assert api.all_registered(["a"])
    api.session.request.assert_called_once()
    assert api.registration_reader.read.call_count == 3


def test_dashboard_deadline_does_not_wait_for_credit_lookup(tmp_path, monkeypatch):
    api = client(tmp_path, [requests.ReadTimeout()], [snapshot(a=S.REGISTERED)] * 2)
    api.verify_registration(["a"])
    clock = iter([datetime.datetime(2026, 11, 19, 12), datetime.datetime(2026, 11, 19, 12, 0, 1)])
    monkeypatch.setattr("ui_main.datetime", SimpleNamespace(datetime=SimpleNamespace(now=lambda: next(clock))))
    lookup_started, release, lookup_finished = threading.Event(), threading.Event(), threading.Event()
    load = api._prefetch_registration_credits

    def load_and_signal(*args):
        try:
            load(*args)
        finally:
            lookup_finished.set()

    def slow_details(session, method, url, **kwargs):
        lookup_started.set()
        if not release.wait(5):
            raise requests.ReadTimeout()
        return response({"Id": "a", "MinimumCredits": 4, "MaximumCredits": None})

    monkeypatch.setattr(api, "_prefetch_registration_credits", load_and_signal)
    monkeypatch.setattr(requests.Session, "request", slow_details)
    try:
        api.prefetch_registration_credits(["a"], include_section_details=True)
        MonitorWorker(api, ["a"], 0, "12:00:01").run()
        assert lookup_started.wait(2) and not lookup_finished.is_set()
        api.session.request.assert_called_once()
        call = api.session.request.call_args
        assert call.args == ("POST", api.base_url + "/RegisterSections")
        assert "Credits" not in call.kwargs["json"]["sectionRegistrations"][0]
    finally:
        release.set()
        assert lookup_finished.wait(3)
    lookup = [e for e in events(api) if e.get("operation") == "section_credit_lookup"]
    assert len(lookup) == 2 and lookup[0]["round_id"] == lookup[1]["round_id"]


@pytest.mark.parametrize("worker_class", [MonitorWorker, AutoCatchWorker, CloudWorker])
def test_workers_stop_cleanly_on_expiration_without_auth_manager(tmp_path, worker_class):
    api = client(tmp_path, [response(status=302, location="/Student/Account/Login")])
    args = (api, ["a"], 1, "") if worker_class is MonitorWorker else (api, ["a"], 1)
    worker = worker_class(*args)
    worker.run()
    assert not worker.is_running
    assert api.session.request.call_count == 1
    assert not api.all_registered(["a"])
    assert events(api)[-1]["event"] == "worker_stopped"


def test_dashboard_recovery_keeps_unknown_attempt_and_verifies_before_retry(tmp_path, monkeypatch):
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="fixture">'
    api = client(tmp_path, [response(status=401)], [snapshot(a=S.REGISTERED)])
    check = Mock(return_value=response(text=valid))
    monkeypatch.setattr(requests.Session, "request", check)
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "fixture-student"}
    worker = MonitorWorker(api, ["a"], 0, "", auth_manager=auth)
    worker.run()
    assert api.all_registered(["a"])
    auth.silent_relogin.assert_called_once()
    api.session.request.assert_called_once()  # Initial Add; no repeated Add.
    check.assert_called_once()


@pytest.mark.parametrize("status", [403, 503])
def test_recovery_is_not_claimed_when_session_check_unknown(tmp_path, monkeypatch, status):
    api = client(tmp_path, [])
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=response(status=status)))
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "fixture-student"}
    assert not recover_session(api, auth)


def test_recovery_cannot_mix_account_results(tmp_path):
    api = client(tmp_path, [])
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "different-student"}
    assert not recover_session(api, auth)
    assert api.session.request.call_count == 0


def test_repeated_expiration_does_not_loop_reauthentication(tmp_path, monkeypatch):
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="fixture">'
    api = client(tmp_path, [response(status=401)],
                 [__import__("core_api").SessionExpiredError("session_expired")])
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=response(text=valid)))
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "fixture-student"}
    worker = MonitorWorker(api, ["a"], 0, "", auth_manager=auth)
    worker.run()
    assert not worker.is_running
    auth.silent_relogin.assert_called_once()
    assert not api.all_registered(["a"])


def test_scheduled_dashboard_uses_refreshed_credentials_without_waiting_for_session_check(
    tmp_path, monkeypatch, qt_app
):
    clock = [datetime.datetime(2026, 11, 19, 12)]
    monkeypatch.setattr("ui_main.datetime", SimpleNamespace(datetime=SimpleNamespace(now=lambda: clock[0])))
    monkeypatch.setattr(InfoBar, "success", Mock())
    monkeypatch.setattr(InfoBar, "warning", Mock())
    wrong_account = Mock()
    monkeypatch.setattr(InfoBar, "error", wrong_account)

    window = KeanSeatsCatcherApp()
    monitor, auth = window.monitor_interface, window.auth_interface
    check_button = monitor.btn_ping
    auth.auth_manager = Mock()
    cookie_name = "studentselfservice_production"
    original = dict(cookies={cookie_name: "old"}, verification_token="first-token", student_id="fixture-student")
    refreshed = dict(original, cookies={cookie_name: "new"}, verification_token="new-token")
    auth.on_credentials_extracted(original)
    api = auth.api_engine
    api.prefetch_registration_credits = Mock()
    api.registration_reader = client(tmp_path, [], [snapshot(a=S.REGISTERED, b=S.REGISTERED)]).registration_reader

    check_started, release_check = threading.Event(), threading.Event()
    add_started, release_add = threading.Event(), threading.Event()
    checks, adds = [], []

    def check(session, method, url, **kwargs):
        checks.append((session, method, url))
        if len(checks) == 2:
            check_started.set()
            release_check.wait(5)
            session.cookies.set(cookie_name, "check-cookie", domain="kean-ss.colleague.elluciancloud.com")
            return response(text='<a href="/Student/Account/Logout">Out</a>'
                                 '<input name="__RequestVerificationToken" value="check-token">')
        return response(status=401)

    def send(method, url, **kwargs):
        adds.append((method, url, kwargs["json"], api.session.headers["__requestverificationtoken"],
                     api.session.cookies.get(cookie_name)))
        add_started.set()
        release_add.wait(5)
        raise requests.ReadTimeout()

    monkeypatch.setattr(requests.Session, "request", check)
    monkeypatch.setattr(api.session, "request", send)
    monitor.section_input.setText("a, b")
    monitor.time_input.setText("12:00:10")
    monitor.interval_input.setText("7")
    monitor.waitlist_switch.setChecked(True)

    def wait_for(predicate):
        for _ in range(100):
            if predicate():
                return True
            QTest.qWait(10)
        return predicate()

    try:
        monitor.btn_start.click()
        scheduled = monitor.worker
        assert wait_for(lambda: scheduled.is_waiting and check_button.isEnabled())
        assert not window.autocatch_interface.btn_start.isEnabled()
        check_button.click()
        assert wait_for(lambda: not monitor.is_checking_session)
        InfoBar.warning.assert_called_once()

        auth.on_credentials_extracted(dict(refreshed, student_id="different-student"))
        wrong_account.assert_called_once()
        assert api.session.headers["__requestverificationtoken"] == original["verification_token"]

        auth.auth_manager.extract_credentials.return_value = refreshed
        auth.handle_extract()
        assert auth.extract_worker.wait(1000)
        qt_app.processEvents()
        assert auth.api_engine is monitor.api_engine is scheduled.api_engine is api
        assert monitor.worker is scheduled
        assert (scheduled.section_ids, scheduled.interval, scheduled.target_time_str, scheduled.enable_waitlist) == (
            ["a", "b"], 7, "12:00:10", True)
        assert wait_for(check_button.isEnabled)

        check_button.click()
        assert check_started.wait(1)
        assert not add_started.is_set()
        clock[0] = clock[0].replace(second=10)
        assert wait_for(lambda: add_started.is_set() and not check_button.isEnabled())
        assert not release_check.is_set()
        method, url, payload, token, cookie = adds[0]
        assert (method, url) == ("POST", api.base_url + "/RegisterSections")
        assert [(item["SectionId"], item["Action"]) for item in payload["sectionRegistrations"]] == [
            ("a", "Add"), ("b", "Add")]
        assert (token, cookie) == ("new-token", "new")
        assert all(session is not api.session and method == "GET" for session, method, _ in checks)

        auth.on_credentials_extracted(dict(refreshed, verification_token="late"))
        assert api.session.headers["__requestverificationtoken"] == "new-token"
        release_check.set()
        assert wait_for(lambda: not monitor.is_checking_session)
        assert api.session.cookies.get(cookie_name) == "new"
        assert api.session_state().last_successful_check_at is None
        assert api.session_state().last_auth_cookie_renewal_at is None
    finally:
        release_check.set()
        release_add.set()
        if monitor.worker is not None:
            monitor.stop_attack()
            assert monitor.worker.wait(3000)
        qt_app.processEvents()
        window.close()
        window.session_keepalive._thread.join(3)


def test_stop_during_availability_does_not_send_add(tmp_path):
    api = client(tmp_path, [])
    worker = AutoCatchWorker(api, ["a"], 1)
    def stop_after_poll(*args, **kwargs):
        worker.is_running = False
        return True
    api.check_section_status = stop_after_poll
    worker.run()
    assert api.session.request.call_count == 0


@pytest.mark.parametrize("worker_class", [AutoCatchWorker, CloudWorker])
@pytest.mark.parametrize("unavailable", [response(status=429), response(status=503), requests.ReadTimeout(),
                                         response({"Available": "unknown"})])
def test_polling_workers_distinguish_unavailable_from_confirmed_full(tmp_path, caplog, worker_class, unavailable):
    full = response({"Available": 0})
    api = client(tmp_path, [full, unavailable, full, full])
    auth = Mock()
    worker = worker_class(api, ["a", "b"], interval=7, auth_manager=auth)
    sleeps, logs, states = [], [], []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            worker.is_running = False

    if worker_class is AutoCatchWorker:
        worker.log_signal.connect(lambda text, level: logs.append(text))
        worker.status_signal.connect(states.append)
    caplog.set_level("DEBUG", logger="CloudSniper")
    worker._sleep = sleep
    worker.run()

    polls = [event for event in events(api) if event["event"] == "poll_finished"]
    assert [event["unavailable_sections"] for event in polls] == [["b"], []]
    assert not any(event["session_expired"] or event["found_section"] for event in polls)
    assert sleeps == [7, 7]
    auth.silent_relogin.assert_not_called()
    assert [call.args[1].rsplit("/", 1)[-1] for call in api.session.request.call_args_list] == ["SectionDetails"] * 4
    if worker_class is AutoCatchWorker:
        assert states.count(STATE_UNAVAILABLE) == 1
        assert logs.count(i18n.tr("autocatch_no_seats")) == 1
        assert i18n.tr("autocatch_poll_unavailable", "b") in logs
    else:
        assert caplog.messages.count("No open seats found in this poll.") == 1
        assert any("temporarily unavailable for b" in message for message in caplog.messages)


def test_autocatch_recovery_failure_leaves_sign_in_prompt(tmp_path, qt_app):
    api = client(tmp_path, [response(status=401)])
    auth = Mock()
    auth.silent_relogin.side_effect = RuntimeError()
    ui = AutoCatchInterface()
    ui.set_api_engine(api)
    ui.set_auth_manager(auth)
    ui.section_input.setText("a")

    ui.start_monitoring()
    assert ui.worker.wait(2000)
    qt_app.processEvents()
    auth.silent_relogin.assert_called_once()
    assert ui.worker.recovery_count == 1
    assert ui.btn_start.isEnabled() and not ui.btn_stop.isEnabled()
    assert ui.status_label.text() == i18n.tr("autocatch_status_relogin")
    assert i18n.tr("log_session_stopped") in ui.log_box.toPlainText()
    ui.close()


@pytest.mark.parametrize("worker_class", [AutoCatchWorker, CloudWorker])
def test_unknown_reconciliation_requires_a_new_seat_check_before_retry(tmp_path, monkeypatch, worker_class):
    clock = [0]
    monkeypatch.setattr("core_api.time.monotonic", lambda: clock[0])
    api = client(tmp_path, [response({"Available": 1}), requests.ReadTimeout(),
                           response({"Available": 0}), response({"Available": 1}), requests.ReadTimeout()],
                 [snapshot(a=S.NOT_REGISTERED), snapshot(a=S.NOT_REGISTERED), snapshot(a=S.REGISTERED)])
    worker = worker_class(api, ["a"], interval=1)
    worker._sleep = lambda seconds: clock.__setitem__(0, clock[0] + 5)
    worker.run()
    operations = [call.args[1].rsplit("/", 1)[-1] for call in api.session.request.call_args_list]
    assert operations == ["SectionDetails", "RegisterSections", "SectionDetails", "SectionDetails", "RegisterSections"]
    assert api.all_registered(["a"])


@pytest.mark.parametrize("worker_class", [AutoCatchWorker, CloudWorker])
def test_open_section_is_sent_without_waiting_to_check_the_next_section(tmp_path, worker_class):
    api = client(tmp_path, [response({"Id": "a", "Available": 1, "MinimumCredits": 4, "MaximumCredits": None}),
                           requests.ReadTimeout(), response({"Available": 0})],
                 [snapshot(a=S.REGISTERED, b=S.NOT_REGISTERED)])
    worker = worker_class(api, ["a", "b"], interval=1)
    worker._sleep = lambda seconds: setattr(worker, "is_running", False)
    worker.run()
    operations = api.session.request.call_args_list
    assert [call.args[1].rsplit("/", 1)[-1] for call in operations] == [
        "SectionDetails", "RegisterSections", "SectionDetails"]
    registrations = operations[1].kwargs["json"]["sectionRegistrations"]
    assert [(item["SectionId"], item["Credits"]) for item in registrations] == [("a", 4)]
