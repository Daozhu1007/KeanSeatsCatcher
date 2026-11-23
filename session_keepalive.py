"""Automatic session maintenance for the lifetime of the application.

The v1.3.3 session study verified that KeanWISE renews the authentication
cookie on any authenticated request (sliding renewal), that an idle session
is rejected after roughly 30-35 minutes, and that Session Check (v1.3.4)
retains the renewed cookies in the main jar. This worker repeats those
verified capabilities on a low-frequency timer:

* A registration worker's own traffic already refreshes the retained
  credentials; the timer skips those periods instead of adding visits.
* When nothing else exercises the session, one Session Check every
  KEEPALIVE_INTERVAL_SECONDS keeps the ticket inside the server's sliding
  window (observed valid after 24-28 minutes of idle; the study's own
  probes ran safely about every 7 minutes).
* On an explicit expiration, one silent recovery is attempted through the
  existing Okta SSO path. When that fails — password, MFA or a cold browser
  profile — the worker says so and waits for a manual re-login instead of
  pretending the session is alive.

The worker is Qt-free and never blocks registration requests: Session Check
runs on an independent cookie snapshot and merges renewals back only when no
main request is in flight (see core_api._read_snapshot / _merge_check_cookies).

Scheduling follows a single-writer rule: only the loop thread decides the
next due time. External updates set a flag and wake the loop, so a refresh
that lands mid-cycle can never be dropped by the cycle's own reschedule.
"""
import threading
import time

from i18n import i18n
from registration_state import SessionStatus
from worker_session import recover_session

KEEPALIVE_INTERVAL_SECONDS = 600
BUSY_RECHECK_SECONDS = 60
UNKNOWN_ALERT_THRESHOLD = 3


class SessionKeepalive:
    def __init__(self, client, auth_manager=None, is_busy=None,
                 interval=KEEPALIVE_INTERVAL_SECONDS,
                 on_log=None, on_recovered=None, on_manual_required=None):
        self.client = client
        self.auth_manager = auth_manager
        self.is_busy = is_busy
        self.interval = interval
        self.on_log = on_log
        self.on_recovered = on_recovered
        self.on_manual_required = on_manual_required
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._refresh_pending = False
        self._consecutive_unknown = 0
        self._manual_required = False
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        with self._lock:
            self._refresh_pending = True
        self._thread = threading.Thread(target=self._run, name="session-keepalive", daemon=True)
        self._thread.start()
        self._wake.set()
        self._report(i18n.tr("keepalive_started", f"{self.interval / 60:g}"), "success")

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def credentials_updated(self):
        """Fresh credentials were extracted or recovered; restart the timer."""
        with self._lock:
            self._manual_required = False
            self._consecutive_unknown = 0
            self._refresh_pending = True
        self._wake.set()

    def _report(self, message, level="normal"):
        if self.on_log:
            self.on_log(message, level)
        else:
            print(f"[{level.upper()}] {message}")

    def _run(self):
        next_due = None
        while not self._stop_event.is_set():
            with self._lock:
                refresh = self._refresh_pending
                self._refresh_pending = False
            if refresh:
                next_due = time.monotonic() + self.interval
                continue
            if next_due is None:
                # Parked: wait for a credential update or shutdown.
                self._wake.wait(60)
                self._wake.clear()
                continue
            remaining = next_due - time.monotonic()
            interrupted = remaining > 0 and self._wake.wait(remaining)
            self._wake.clear()
            if self._stop_event.is_set():
                break
            if interrupted:
                # A credential update wants the schedule re-evaluated.
                continue
            try:
                next_due = self._cycle()
            except Exception as exc:  # Maintenance must never die silently.
                next_due = time.monotonic() + self.interval
                self._report(i18n.tr("keepalive_error", type(exc).__name__), "error")

    def _cycle(self):
        """Run one maintenance pass; return the next due time (None = park)."""
        if self._manual_required:
            return None
        now = time.monotonic()
        last = self.client.last_activity()
        if last is not None and now - last < self.interval:
            # Recent authenticated traffic already refreshed the retained
            # credentials; another visit now would only be a duplicate.
            return last + self.interval
        busy = self.is_busy is not None and self.is_busy()
        revision = self.client.credentials_revision()
        check = self.client.check_session()
        if check.status == SessionStatus.VALID:
            self._consecutive_unknown = 0
            self.client._event("session_keepalive", outcome="valid",
                               elapsed_ms=check.elapsed_ms, reason=check.reason)
            self._report(i18n.tr("keepalive_valid", check.elapsed_ms))
            return now + self.interval
        if check.status == SessionStatus.UNKNOWN:
            # Never claim the session is alive on an unrecognizable response.
            self._consecutive_unknown += 1
            self.client._event("session_keepalive", outcome="unknown",
                               elapsed_ms=check.elapsed_ms, reason=check.reason,
                               consecutive=self._consecutive_unknown)
            if self._consecutive_unknown >= UNKNOWN_ALERT_THRESHOLD:
                self._report(i18n.tr("keepalive_unknown_repeated", self._consecutive_unknown), "error")
            else:
                self._report(i18n.tr("keepalive_unknown", check.reason), "normal")
            return now + self.interval
        if busy or revision != self.client.credentials_revision():
            # A running worker owns recovery, or fresh credentials arrived while
            # the check was in flight and the rejection spoke for the old jar.
            self.client._event("session_keepalive", outcome="expired_deferred",
                               elapsed_ms=check.elapsed_ms, reason=check.reason)
            return now + BUSY_RECHECK_SECONDS
        return self._recover(check, now)

    def _recover(self, check, now):
        self.client._event("session_keepalive", outcome="expired",
                           elapsed_ms=check.elapsed_ms, reason=check.reason)
        self._report(i18n.tr("log_session_recovery"), "error")
        if recover_session(self.client, self.auth_manager):
            self._consecutive_unknown = 0
            self.client._event("session_keepalive", outcome="recovered")
            if self.on_recovered:
                self.on_recovered()
            return now + self.interval
        # One verified attempt per expiry episode. The server (or Okta) wants
        # a human: password, MFA or a cold browser profile. Park until new
        # credentials arrive instead of pretending the session is alive.
        self._manual_required = True
        self.client._event("session_keepalive", outcome="manual_required")
        if self.on_manual_required:
            self.on_manual_required(check.reason)
        return None
