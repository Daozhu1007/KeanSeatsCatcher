"""Run session checks and keepalive cycles on one session worker."""
import threading
import time
from dataclasses import dataclass

from i18n import i18n
from registration_state import SessionCheck, SessionState, SessionStatus
from worker_session import recover_session

KEEPALIVE_INTERVAL_SECONDS = 600
KEEPALIVE_INTERVAL_MINUTES = (5, 10, 15)
NETWORK_RECHECK_SECONDS = 60
UNKNOWN_ALERT_THRESHOLD = 3


@dataclass(frozen=True)
class SessionActionResult:
    """Outcome and wall-clock completion time for one Dashboard action."""

    completed_at: float
    outcome: str


@dataclass(frozen=True)
class KeepaliveSnapshot:
    state: str
    interval: float
    next_due: float | None
    action: str | None
    can_check: bool
    can_keepalive: bool
    session: SessionState
    scheduled_from: float | None
    recovering: bool
    last_check: SessionActionResult | None
    last_keepalive: SessionActionResult | None


class SessionKeepalive:
    def __init__(self, client, auth_manager=None, is_busy=None,
                 interval=KEEPALIVE_INTERVAL_SECONDS,
                 on_log=None, on_recovered=None, on_manual_required=None,
                 *, enabled=True, on_check=None):
        self.client = client
        self.auth_manager = auth_manager
        self.is_busy = is_busy or (lambda: False)
        self.interval = interval
        self.on_log = on_log
        self.on_recovered = on_recovered
        self.on_manual_required = on_manual_required
        self.on_check = on_check
        self._enabled = enabled
        self._changed = threading.Condition()
        self._stopped = False
        self._anchor = time.monotonic()
        self._retry_due = None
        self._consecutive_unknown = 0
        self._manual_required_since = None
        self._credential_updates = 0
        self._action = None
        self._recovering = False
        self._last_check = None
        self._last_keepalive = None
        self._thread = None

    def start(self):
        with self._changed:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopped = False
            self._anchor = time.monotonic()
            self._action = None
            self._recovering = False
            self._thread = threading.Thread(target=self._run, name="session-keepalive", daemon=True)
            self._thread.start()

    def stop(self):
        with self._changed:
            self._stopped = True
            self._changed.notify()

    def configure(self, enabled, interval):
        with self._changed:
            if enabled != self._enabled:
                self._retry_due = None
            self._enabled, self.interval = enabled, interval
            self._changed.notify()

    def credentials_updated(self):
        with self._changed:
            self._credential_updates += 1
            self._manual_required_since = None
            self._consecutive_unknown = 0
            self._retry_due = None
            self._anchor = time.monotonic()
            self._changed.notify()

    def request_now(self):
        return self._request_action("keepalive")

    def request_check(self):
        return self._request_action("check")

    def _needs_sign_in(self, session):
        return self._manual_required_since is not None and (
            session.last_activity is None or session.last_activity <= self._manual_required_since
        )

    def _using_retry(self, session):
        return self._retry_due is not None and (
            session.last_activity is None or session.last_activity <= self._anchor
        )

    def _next_schedule(self, session):
        if not self._enabled or self._needs_sign_in(session):
            return None, None
        if self._using_retry(session):
            start = self._anchor
            due = self._retry_due
        else:
            start = max(self._anchor, session.last_activity or self._anchor)
            due = start + self.interval
        return start, max(due, session.cooldown_until)

    def _can_check(self, session, busy, now):
        return (self._thread is not None and self._thread.is_alive() and not self._stopped and self._action is None
                and not busy and now >= session.cooldown_until)

    def _request_action(self, action):
        with self._changed:
            session = self.client.session_state()
            if not self._can_check(session, self.is_busy(), time.monotonic()):
                return False
            if action == "keepalive" and self._needs_sign_in(session):
                return False
            self._action = action
            self._changed.notify()
            return True

    def snapshot(self):
        with self._changed:
            now, session, busy = time.monotonic(), self.client.session_state(), self.is_busy()
            needs_sign_in = self._needs_sign_in(session)
            can_check = self._can_check(session, busy, now)
            scheduled_from = next_due = None
            if not self._enabled or self._stopped:
                state = "disabled"
            elif self._action is not None:
                state = "checking" if self._action == "check" else "running"
            elif needs_sign_in:
                state = "manual_required"
            elif busy:
                state = "busy"
            else:
                scheduled_from, next_due = self._next_schedule(session)
                if now < session.cooldown_until:
                    state = "cooldown"
                else:
                    state = "retry" if self._using_retry(session) else "scheduled"
            return KeepaliveSnapshot(state, self.interval, next_due, self._action, can_check,
                                     can_check and not needs_sign_in, session, scheduled_from,
                                     self._recovering, self._last_check, self._last_keepalive)

    def _run(self):
        while True:
            with self._changed:
                if self._stopped:
                    return
                session = self.client.session_state()
                if self._action is None:
                    _, due = self._next_schedule(session)
                    if due is None:
                        # A registration worker can restore the session while maintenance is parked.
                        self._changed.wait(1 if self._enabled else None)
                        continue
                    remaining = due - time.monotonic()
                    if remaining > 0:
                        self._changed.wait(min(remaining, 60))
                        continue
                    if self.is_busy():
                        self._changed.wait(1)
                        continue
                    self._action = "automatic"
                action = self._action
                was_retry = self._using_retry(session)
                credential_updates = self._credential_updates

            try:
                check, recovery = self._cycle(action)
            except Exception as exc:
                check, recovery = SessionCheck(SessionStatus.UNKNOWN, 0, type(exc).__name__), None
                key = "session_check_error" if action == "check" else "keepalive_error"
                self._report(i18n.tr(key, type(exc).__name__), "error")

            with self._changed:
                self._action = None
                self._recovering = False
                if self._stopped:
                    return
                credentials_changed = credential_updates != self._credential_updates
                outcome = ("superseded" if credentials_changed or not check.current
                           else recovery or check.status.value.lower())
                result = SessionActionResult(time.time(), outcome)
                if action == "check":
                    self._last_check = result
                else:
                    self._last_keepalive = result
                if not credentials_changed:
                    self._finish_cycle(action, check, recovery, was_retry)
                consecutive_unknown = self._consecutive_unknown
            if action == "check":
                if self.on_check:
                    self.on_check(check)
            elif not credentials_changed and check.current:
                self._report_cycle(check, recovery, consecutive_unknown)

    def _cycle(self, action):
        revision = self.client.credentials_revision()
        check = self.client.check_session()
        if action == "check" or check.status != SessionStatus.EXPIRED or not check.current:
            return check, None
        with self._changed:
            cancelled = self._stopped or (action == "automatic" and not self._enabled)
        if (cancelled or self.is_busy() or revision != self.client.credentials_revision()
                or time.monotonic() < self.client.session_state().cooldown_until):
            return check, "expired_deferred"

        self.client._event("session_keepalive", outcome="expired",
                           elapsed_ms=check.elapsed_ms, reason=check.reason)
        self._report(i18n.tr("log_session_recovery"), "error")
        with self._changed:
            self._recovering = self.auth_manager is not None
        recovered = recover_session(self.client, self.auth_manager, expected_revision=revision)
        if recovered is None:
            return check, "expired_deferred"
        return check, "recovered" if recovered else "manual_required"

    def _finish_cycle(self, action, check, recovery, was_retry):
        self._anchor = time.monotonic()
        # Manual probes share this schedule. A probe during a pending recheck
        # consumes it, and accepted traffic can make the extra visit unnecessary.
        retry = (self._enabled and check.current and action != "check" and not was_retry
                 and check.transient_network_error)
        self._retry_due = self._anchor + NETWORK_RECHECK_SECONDS if retry else None
        if not check.current:
            return
        if recovery == "manual_required":
            self._manual_required_since = self._anchor
        elif check.status == SessionStatus.VALID or recovery == "recovered":
            self._manual_required_since = None
        self._consecutive_unknown = self._consecutive_unknown + 1 if check.status == SessionStatus.UNKNOWN else 0

    def _report_cycle(self, check, recovery, consecutive_unknown):
        self.client._event("session_keepalive", outcome=recovery or check.status.value.lower(),
                           elapsed_ms=check.elapsed_ms, reason=check.reason, consecutive=consecutive_unknown)
        if recovery == "recovered" and self.on_recovered:
            self.on_recovered()
        elif recovery == "manual_required" and self.on_manual_required:
            self.on_manual_required(check.reason)
        elif check.status == SessionStatus.VALID:
            self._report(i18n.tr("keepalive_valid", check.elapsed_ms))
        elif consecutive_unknown >= UNKNOWN_ALERT_THRESHOLD:
            self._report(i18n.tr("keepalive_unknown_repeated", consecutive_unknown), "error")
        elif check.status == SessionStatus.UNKNOWN:
            self._report(i18n.tr("keepalive_unknown", check.reason))

    def _report(self, message, level="normal"):
        if self.on_log:
            self.on_log(message, level)
        else:
            print(f"[{level.upper()}] {message}")
