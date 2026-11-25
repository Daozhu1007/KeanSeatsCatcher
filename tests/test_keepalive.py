"""Offline checks for the session maintenance schedule and its HTTP integration."""
import threading
import time
from dataclasses import replace
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

import session_keepalive
from registration_state import SessionCheck, SessionState, SessionStatus
from session_keepalive import NETWORK_RECHECK_SECONDS, SessionKeepalive
from test_registration import client, events, response


VALID_PAGE = ('<a href="/Student/Account/Logout">Out</a>'
              '<input name="__RequestVerificationToken" value="fixture">')


class FakeClient:
    def __init__(self, checks):
        self.checks = iter(checks)
        self.check_calls = 0
        self.state = SessionState()
        self.revision = 1
        self.log_events = []

    def session_state(self):
        return self.state

    def credentials_revision(self):
        return self.revision

    def check_session(self):
        self.check_calls += 1
        check = next(self.checks)
        if callable(check):
            check = check()
        self.state = replace(self.state, status=check.status)
        if check.status == SessionStatus.VALID:
            self.state = replace(self.state, last_activity=session_keepalive.time.monotonic())
        return check

    def _event(self, event, **fields):
        self.log_events.append({"event": event, **fields})


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def clock(monkeypatch):
    value = [200.0]
    monkeypatch.setattr(session_keepalive, "time", SimpleNamespace(monotonic=lambda: value[0]))
    return value


@pytest.fixture
def start_keeper():
    keepers = []

    def start(api, **kwargs):
        keeper = SessionKeepalive(api, on_log=kwargs.pop("on_log", Mock()), **kwargs)
        keeper.start()
        keepers.append(keeper)
        return keeper

    yield start
    for keeper in keepers:
        keeper.stop()
        keeper._thread.join(3)
        assert not keeper._thread.is_alive()


def advance(keeper, clock, target):
    clock[0] = target
    keeper.configure(True, keeper.interval)


def test_recent_registration_traffic_moves_due_time_without_a_duplicate_visit(clock, start_keeper):
    fake = FakeClient([SessionCheck(SessionStatus.VALID, 5, "authenticated_page")])
    keeper = start_keeper(fake)
    assert keeper.snapshot().next_due == 800
    fake.state = replace(fake.state, last_activity=700.0)
    advance(keeper, clock, 800)
    assert keeper.snapshot().next_due == 1300
    assert fake.check_calls == 0

    advance(keeper, clock, 1300)
    wait_until(lambda: len(fake.log_events) == 1)
    assert fake.log_events[-1]["outcome"] == "valid"
    assert keeper.snapshot().next_due == 1900


def test_network_failure_gets_one_early_recheck_per_regular_cycle(clock, start_keeper, monkeypatch):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    failure = SessionCheck(SessionStatus.UNKNOWN, 10, "ReadTimeout", transient_network_error=True)
    fake = FakeClient([failure] * 4)
    logs = Mock()
    keeper = start_keeper(fake, on_log=logs)

    for index, (due, resulting_state, delay) in enumerate([
        (800, "retry", NETWORK_RECHECK_SECONDS),
        (860, "scheduled", 600),
        (1460, "retry", NETWORK_RECHECK_SECONDS),
        (1520, "scheduled", 600),
    ], 1):
        advance(keeper, clock, due)
        wait_until(lambda: len(fake.log_events) == index and logs.call_count == index)
        assert keeper.snapshot().state == resulting_state
        assert keeper.snapshot().next_due == due + delay
        assert fake.state.status == SessionStatus.UNKNOWN
    recover.assert_not_called()
    assert [call.args[1] for call in logs.call_args_list] == ["normal", "normal", "error", "error"]


def test_new_activity_cancels_the_extra_recheck(clock, start_keeper):
    failure = SessionCheck(SessionStatus.UNKNOWN, 10, "ConnectTimeout", transient_network_error=True)
    fake = FakeClient([failure])
    keeper = start_keeper(fake)
    advance(keeper, clock, 800)
    wait_until(lambda: fake.log_events)
    fake.state = replace(fake.state, last_activity=820, status=SessionStatus.VALID)
    advance(keeper, clock, 860)
    assert keeper.snapshot().state == "scheduled"
    assert keeper.snapshot().next_due == 1420
    assert fake.check_calls == 1


@pytest.mark.parametrize("interleaving", ["worker_started", "credentials_changed"])
def test_maintenance_defers_expiry_recovery_when_superseded(clock, start_keeper, monkeypatch, interleaving):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    busy = [False]

    def reject():
        if interleaving == "worker_started":
            busy[0] = True
        else:
            fake.revision += 1
        return SessionCheck(SessionStatus.EXPIRED, 7, "login_redirect")

    fake = FakeClient([reject])
    keeper = start_keeper(fake, is_busy=lambda: busy[0])
    advance(keeper, clock, 800)
    wait_until(lambda: fake.log_events)
    recover.assert_not_called()
    assert fake.log_events[-1]["outcome"] == "expired_deferred"
    if busy[0]:
        assert keeper.snapshot().state == "busy"
        assert keeper.snapshot().next_due is None
    assert fake.check_calls == 1


@pytest.mark.parametrize("recovered", [False, True])
def test_explicit_expiry_attempts_one_recovery(clock, start_keeper, monkeypatch, recovered):
    recover = Mock(return_value=recovered)
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    manual, success = Mock(), Mock()
    fake = FakeClient([SessionCheck(SessionStatus.EXPIRED, 7, "http_401"),
                       SessionCheck(SessionStatus.VALID, 5, "authenticated_page")])
    keeper = start_keeper(fake, auth_manager=Mock(), on_manual_required=manual, on_recovered=success)
    advance(keeper, clock, 800)
    wait_until(lambda: manual.called or success.called)
    recover.assert_called_once()

    if recovered:
        success.assert_called_once_with()
        assert keeper.snapshot().next_due == 1400
    else:
        manual.assert_called_once_with("http_401")
        assert keeper.snapshot().state == "manual_required"
        assert keeper.snapshot().next_due is None
        assert keeper.snapshot().can_check
        assert not keeper.request_now()
        advance(keeper, clock, 2000)
        assert fake.check_calls == 1
        keeper.credentials_updated()
        assert keeper.snapshot().next_due == 2600
        advance(keeper, clock, 2600)
        wait_until(lambda: fake.log_events[-1]["outcome"] == "valid")
        assert keeper.snapshot().state == "scheduled"


def test_manual_actions_share_worker_and_work_with_auto_off(clock, start_keeper):
    entered, release = threading.Event(), threading.Event()

    def slow_check():
        entered.set()
        assert release.wait(3)
        return SessionCheck(SessionStatus.VALID, 5, "authenticated_page")

    fake = FakeClient([slow_check, SessionCheck(SessionStatus.UNKNOWN, 10, "ReadTimeout")])
    fake.state = replace(fake.state, last_activity=195)
    checked = Mock()
    keeper = start_keeper(fake, enabled=False, on_check=checked)
    assert keeper.snapshot().state == "disabled"
    assert keeper.snapshot().next_due is None
    assert keeper.request_now()
    try:
        assert entered.wait(1)
        assert keeper.snapshot().action == "keepalive"
        assert not keeper.request_check()
        assert not keeper.request_now()
    finally:
        release.set()
    wait_until(lambda: fake.log_events)
    assert keeper.snapshot().next_due is None
    assert keeper.request_check()
    wait_until(lambda: checked.called)
    assert checked.call_args.args[0].status == SessionStatus.UNKNOWN


def test_disabling_auto_prevents_recovery_after_pending_automatic_check(clock, start_keeper, monkeypatch):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    entered, release = threading.Event(), threading.Event()

    def slow_rejection():
        entered.set()
        assert release.wait(3)
        return SessionCheck(SessionStatus.EXPIRED, 5, "http_401")

    fake = FakeClient([slow_rejection])
    keeper = start_keeper(fake)
    advance(keeper, clock, 800)
    try:
        assert entered.wait(1)
        keeper.configure(False, 600)
    finally:
        release.set()
    wait_until(lambda: fake.log_events)
    assert fake.log_events[-1]["outcome"] == "expired_deferred"
    assert keeper.snapshot().next_due is None
    recover.assert_not_called()


@pytest.mark.parametrize("error", [requests.ConnectTimeout(), requests.ReadTimeout(), requests.ConnectionError()])
def test_transport_failures_schedule_recheck_with_real_client(tmp_path, monkeypatch, start_keeper, error):
    api = client(tmp_path, [])
    send = Mock(side_effect=[error, response(text=VALID_PAGE)])
    monkeypatch.setattr(requests.Session, "request", send)
    keeper = start_keeper(api)
    assert keeper.request_now()
    wait_until(lambda: keeper.snapshot().state == "retry")
    assert keeper.snapshot().next_due == pytest.approx(time.monotonic() + NETWORK_RECHECK_SECONDS, abs=2)
    assert keeper.snapshot().session.status == SessionStatus.UNKNOWN
    assert api.last_activity() is None
    assert keeper.request_now()
    wait_until(lambda: keeper.snapshot().action is None)
    assert api.last_activity() is not None
    assert keeper.snapshot().session.last_successful_check_at is not None
    assert send.call_count == 2


@pytest.mark.parametrize("status, retry_after, seconds", [
    (429, "1800", 1800),
    (503, "date", 1200),
    (429, "invalid", 600),
    (403, None, 600),
])
def test_server_cooldown_blocks_manual_and_automatic_checks(tmp_path, monkeypatch, start_keeper,
                                                            status, retry_after, seconds):
    api = client(tmp_path, [])
    reply = response(status=status)
    if retry_after is not None:
        reply.headers["Retry-After"] = formatdate(time.time() + seconds, usegmt=True) if retry_after == "date" else retry_after
    send = Mock(return_value=reply)
    monkeypatch.setattr(requests.Session, "request", send)
    keeper = start_keeper(api, interval=300)
    assert keeper.request_now()
    wait_until(lambda: keeper.snapshot().state == "cooldown")
    due = keeper.snapshot().next_due
    assert due == pytest.approx(time.monotonic() + seconds, abs=2)
    assert keeper.snapshot().session.status == SessionStatus.UNKNOWN
    assert api.last_activity() is None
    assert not keeper.request_check()
    keeper.configure(False, 900)
    assert not keeper.request_now()
    api.update_credentials({}, "replacement-token")
    keeper.credentials_updated()
    assert not keeper.request_check()
    assert api.check_session().reason == "retry_after"
    assert send.call_count == 1


@pytest.mark.parametrize("status", [302, 400, 403, 429, 500, 503])
def test_main_error_responses_do_not_advance_activity(tmp_path, status):
    api = client(tmp_path, [response([]), response(status=status)])
    api._request("fixture_main_request", "GET", api.base_url)
    accepted = api.last_activity()
    assert accepted is not None
    api._request("fixture_main_request", "GET", api.base_url)
    assert api.last_activity() == accepted
    state = api.session_state()
    assert state.status == SessionStatus.UNKNOWN
    assert state.last_successful_check_at is None
    assert bool(state.cooldown_until) == (status in (403, 429, 503))


def test_concurrent_recovery_attempts_are_serialized(monkeypatch, tmp_path):
    from worker_session import _recovery_lock, recover_session

    release = threading.Event()
    entered = threading.Event()
    api = client(tmp_path, [])
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=response(text=VALID_PAGE)))
    auth = Mock()

    def slow_relogin(headless=False):
        entered.set()
        release.wait(5)
        return {"cookies": {}, "verification_token": "late", "student_id": "fixture-student"}

    auth.silent_relogin.side_effect = slow_relogin
    thread = threading.Thread(target=lambda: recover_session(api, auth))
    thread.start()
    try:
        assert entered.wait(2)
        assert not _recovery_lock.acquire(timeout=0.2)
    finally:
        release.set()
        thread.join(5)
    assert _recovery_lock.acquire(timeout=1)
    _recovery_lock.release()


def test_new_credentials_win_over_a_background_recovery(tmp_path):
    from worker_session import recover_session

    api = client(tmp_path, [])
    auth = Mock()

    def slow_relogin(headless=False):
        api.update_credentials({}, "manually-refreshed")
        return {"cookies": {}, "verification_token": "stale", "student_id": "fixture-student"}

    auth.silent_relogin.side_effect = slow_relogin
    assert recover_session(api, auth, expected_revision=api.credentials_revision()) is None
    assert api.session.headers["__requestverificationtoken"] == "manually-refreshed"
