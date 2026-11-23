"""Offline tests for the global background session keepalive.

HTTP never leaves the process: scripted SessionChecks drive the pure logic,
and the end-to-end cases reuse the mocked-transport fixtures from
test_registration so the keepalive exercises the real KeanApiClient paths.
"""
import threading
import time
from unittest.mock import Mock

import pytest
import requests

import session_keepalive
from registration_state import SessionCheck, SessionStatus
from session_keepalive import (BUSY_RECHECK_SECONDS, UNKNOWN_ALERT_THRESHOLD,
                               SessionKeepalive)
from test_registration import client, events, response


def check(status, reason="fixture_reason", elapsed=5):
    return SessionCheck(status, elapsed, reason)


class FakeClient:
    def __init__(self, checks=()):
        self.checks = list(checks)
        self.check_calls = 0
        self.activity = None
        self.revision = 1
        self.log_events = []

    def last_activity(self):
        return self.activity

    def credentials_revision(self):
        return self.revision

    def check_session(self):
        self.check_calls += 1
        return self.checks.pop(0)

    def _event(self, event, **fields):
        self.log_events.append({"event": event, **fields})


@pytest.fixture
def clock(monkeypatch):
    """Deterministic monotonic clock for keepalive scheduling."""
    value = [200.0]
    monkeypatch.setattr(session_keepalive.time, "monotonic", lambda: value[0])
    return value


def test_recent_registration_traffic_skips_a_duplicate_visit(clock):
    fake = FakeClient([check(SessionStatus.VALID)])
    fake.activity = 100.0  # A worker response 100s ago; interval is 600s.
    keepalive = SessionKeepalive(fake, interval=600)
    assert keepalive._cycle() == 700.0  # due measured from the traffic, not now.
    assert fake.check_calls == 0


def test_valid_check_renews_and_stays_on_schedule(clock):
    fake = FakeClient([check(SessionStatus.VALID)])
    logs = []
    keepalive = SessionKeepalive(fake, interval=600,
                                 on_log=lambda msg, level: logs.append((msg, level)))
    assert keepalive._cycle() == 800.0
    assert fake.check_calls == 1
    outcomes = [e for e in fake.log_events if e["event"] == "session_keepalive"]
    assert outcomes == [{"event": "session_keepalive", "outcome": "valid",
                         "elapsed_ms": 5, "reason": "fixture_reason"}]
    assert logs[-1][1] == "normal"


def test_busy_worker_owns_recovery_after_an_expired_check(clock, monkeypatch):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    fake = FakeClient([check(SessionStatus.EXPIRED, "login_redirect")])
    keepalive = SessionKeepalive(fake, auth_manager=Mock(), is_busy=lambda: True, interval=600)
    assert keepalive._cycle() == 200.0 + BUSY_RECHECK_SECONDS
    recover.assert_not_called()
    assert fake.log_events[-1]["outcome"] == "expired_deferred"


def test_unknown_results_never_claim_valid_or_trigger_recovery(clock, monkeypatch):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    fake = FakeClient([check(SessionStatus.UNKNOWN, "ReadTimeout")] * 4)
    logs = []
    keepalive = SessionKeepalive(fake, interval=600,
                                 on_log=lambda msg, level: logs.append((msg, level)))
    for _ in range(4):
        assert keepalive._cycle() == 800.0
    recover.assert_not_called()
    assert fake.check_calls == 4
    assert [level for _, level in logs] == ["normal", "normal", "error", "error"]
    assert fake.log_events[-1]["consecutive"] == 4


def test_failed_recovery_parks_until_new_credentials(clock, monkeypatch):
    monkeypatch.setattr(session_keepalive, "recover_session", Mock(return_value=False))
    fake = FakeClient([check(SessionStatus.EXPIRED, "http_401")])
    manual = []
    keepalive = SessionKeepalive(fake, auth_manager=Mock(), interval=600,
                                 on_manual_required=manual.append)
    assert keepalive._cycle() is None
    assert manual == ["http_401"]
    assert keepalive._manual_required
    # Parked cycles stay silent even if the loop wakes for another pass.
    fake.checks.append(check(SessionStatus.VALID))
    assert keepalive._cycle() is None
    assert fake.check_calls == 1
    # Re-extracted credentials restart maintenance on a fresh timer.
    clock[0] = 900.0
    keepalive.credentials_updated()
    assert not keepalive._manual_required
    assert keepalive._cycle() == 1500.0
    assert fake.check_calls == 2


def test_successful_recovery_resumes_maintenance(clock, monkeypatch):
    monkeypatch.setattr(session_keepalive, "recover_session", Mock(return_value=True))
    fake = FakeClient([check(SessionStatus.EXPIRED, "login_redirect")])
    recovered = Mock()
    keepalive = SessionKeepalive(fake, auth_manager=Mock(), interval=600,
                                 on_recovered=recovered)
    assert keepalive._cycle() == 800.0
    recovered.assert_called_once_with()
    assert not keepalive._manual_required
    assert [e["outcome"] for e in fake.log_events] == ["expired", "recovered"]


def test_credential_refresh_during_check_defers_recovery(clock, monkeypatch):
    recover = Mock()
    monkeypatch.setattr(session_keepalive, "recover_session", recover)
    fake = FakeClient([check(SessionStatus.EXPIRED, "login_redirect")])
    original = fake.check_session

    def refresh_while_checking():
        fake.revision += 1  # A concurrent re-extract or worker recovery.
        return original()

    fake.check_session = refresh_while_checking
    keepalive = SessionKeepalive(fake, auth_manager=Mock(), interval=600)
    assert keepalive._cycle() == 200.0 + BUSY_RECHECK_SECONDS
    recover.assert_not_called()
    assert fake.log_events[-1]["outcome"] == "expired_deferred"


def test_keepalive_runs_the_real_session_check_without_duplicate_visits(tmp_path, monkeypatch):
    api = client(tmp_path, [])
    valid = ('<a href="/Student/Account/Logout">Out</a>'
             '<input name="__RequestVerificationToken" value="fixture">')
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=response(text=valid)))
    keepalive = SessionKeepalive(api, interval=600)
    assert keepalive._cycle() == pytest.approx(time.monotonic() + 600, abs=10)
    assert api.last_activity() is not None
    assert events(api)[-1]["event"] == "session_keepalive"
    assert events(api)[-1]["outcome"] == "valid"
    assert len([e for e in events(api) if e["event"] == "session_check"]) == 1
    # The valid check refreshed the retained credentials: an immediate second
    # cycle is a skip measured from that traffic, not another server visit.
    assert keepalive._cycle() == pytest.approx(api.last_activity() + 600, abs=10)
    assert len([e for e in events(api) if e["event"] == "session_check"]) == 1


def test_main_registration_response_marks_activity(tmp_path):
    api = client(tmp_path, [response([])])
    assert api.last_activity() is None
    api.register_multiple_sections(["a"])
    assert api.last_activity() is not None
    keepalive = SessionKeepalive(api, interval=600)
    assert keepalive._cycle() == pytest.approx(api.last_activity() + 600, abs=10)
    assert api.session.request.call_count == 1  # No keepalive visit added.


def test_loop_fires_repeatedly_and_stops_cleanly():
    fake = FakeClient([check(SessionStatus.VALID)] * 50)
    keepalive = SessionKeepalive(fake, interval=0.05, on_log=Mock())
    keepalive.start()
    deadline = time.monotonic() + 5
    while fake.check_calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    keepalive.stop()
    keepalive._thread.join(2)
    assert not keepalive._thread.is_alive()
    assert fake.check_calls >= 2


def test_loop_parks_after_failed_recovery_and_revives_on_credentials(monkeypatch):
    monkeypatch.setattr(session_keepalive, "recover_session", Mock(return_value=False))
    fake = FakeClient([check(SessionStatus.EXPIRED, "http_401"),
                       check(SessionStatus.VALID)])
    manual = threading.Event()
    keepalive = SessionKeepalive(fake, auth_manager=Mock(), interval=0.05,
                                 on_log=Mock(), on_manual_required=lambda reason: manual.set())
    keepalive.start()
    try:
        assert manual.wait(5)
        # Parked: no further checks even though the loop keeps waking.
        time.sleep(0.3)
        assert fake.check_calls == 1
        keepalive.credentials_updated()
        deadline = time.monotonic() + 5
        while fake.check_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        keepalive.stop()
        keepalive._thread.join(2)
    assert fake.check_calls >= 2


def test_concurrent_recovery_attempts_are_serialized(monkeypatch, tmp_path):
    from worker_session import _recovery_lock, recover_session

    release = threading.Event()
    entered = threading.Event()
    api = client(tmp_path, [])
    valid = '<a href="/Student/Account/Logout">Out</a><input name="__RequestVerificationToken" value="x">'
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=response(text=valid)))
    auth = Mock()

    def slow_relogin(headless=False):
        entered.set()
        release.wait(5)
        return {"cookies": {}, "verification_token": "late",
                "student_id": "fixture-student"}

    auth.silent_relogin.side_effect = slow_relogin
    thread = threading.Thread(target=lambda: recover_session(api, auth))
    thread.start()
    assert entered.wait(2)
    # While the first recovery holds the lock, a second attempt cannot start.
    assert not _recovery_lock.acquire(timeout=0.2)
    release.set()
    thread.join(5)
    assert _recovery_lock.acquire(timeout=1)
    _recovery_lock.release()
