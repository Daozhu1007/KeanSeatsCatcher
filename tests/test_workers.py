from unittest.mock import Mock

import pytest
import requests

from auto_catch_worker import AutoCatchWorker, STATE_IDLE
from cloud_worker import CloudWorker
from ui_main import MonitorWorker, PingWorker
from registration_state import SessionStatus
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


def test_dashboard_recovery_keeps_unknown_attempt_and_verifies_before_retry(tmp_path):
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken">'
    api = client(tmp_path, [response(status=401), response(text=valid)], [snapshot(a=S.REGISTERED)])
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "fixture-student"}
    worker = MonitorWorker(api, ["a"], 0, "", auth_manager=auth)
    worker.run()
    assert api.all_registered(["a"])
    auth.silent_relogin.assert_called_once()
    assert api.session.request.call_count == 2  # Add, session check; no repeated Add.


@pytest.mark.parametrize("status", [403, 503])
def test_recovery_is_not_claimed_when_session_check_unknown(tmp_path, status):
    api = client(tmp_path, [response(status=status)])
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


def test_repeated_expiration_does_not_loop_reauthentication(tmp_path):
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken">'
    api = client(tmp_path, [response(status=401), response(text=valid)],
                 [__import__("core_api").SessionExpiredError("session_expired")])
    auth = Mock()
    auth.silent_relogin.return_value = {"cookies": {}, "verification_token": "new",
                                      "student_id": "fixture-student"}
    worker = MonitorWorker(api, ["a"], 0, "", auth_manager=auth)
    worker.run()
    assert not worker.is_running
    auth.silent_relogin.assert_called_once()
    assert not api.all_registered(["a"])


def test_session_check_worker_preserves_three_state_result(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()])
    worker = PingWorker(api)
    results = []
    worker.result_signal.connect(results.append)
    worker.run()
    assert results[0].status == SessionStatus.UNKNOWN


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
def test_unknown_reconciliation_requires_a_new_seat_check_before_retry(tmp_path, worker_class):
    api = client(tmp_path, [response({"Available": 1}), requests.ReadTimeout(),
                           response({"Available": 0}), response({"Available": 1}), requests.ReadTimeout()],
                 [snapshot(), snapshot(a=S.NOT_REGISTERED), snapshot(a=S.REGISTERED)])
    worker = worker_class(api, ["a"], interval=1)
    worker._sleep = lambda seconds: api._retry_after.clear()
    worker.run()
    operations = [call.args[1].rsplit("/", 1)[-1] for call in api.session.request.call_args_list]
    assert operations == ["SectionDetails", "RegisterSections", "SectionDetails", "SectionDetails", "RegisterSections"]
    assert api.all_registered(["a"])


@pytest.mark.parametrize("worker_class", [AutoCatchWorker, CloudWorker])
def test_one_open_section_does_not_authorize_add_for_a_full_section(tmp_path, worker_class):
    api = client(tmp_path, [response({"Available": 1}), response({"Available": 0}), requests.ReadTimeout()],
                 [snapshot(a=S.REGISTERED, b=S.NOT_REGISTERED)])
    worker = worker_class(api, ["a", "b"], interval=1)
    worker._sleep = lambda seconds: setattr(worker, "is_running", False)
    worker.run()
    registrations = api.session.request.call_args.kwargs["json"]["sectionRegistrations"]
    assert [item["SectionId"] for item in registrations] == ["a"]
