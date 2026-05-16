import threading
from PyQt6.QtCore import QThread, pyqtSignal

from i18n import i18n
from core_api import SessionExpiredError

STATE_IDLE = 0
STATE_POLLING = 1
STATE_ATTACKING = 2


class AutoCatchWorker(QThread):
    log_signal = pyqtSignal(str, str)
    status_signal = pyqtSignal(int)
    seat_found_signal = pyqtSignal(str, int)
    attack_result_signal = pyqtSignal(bool, str)
    round_signal = pyqtSignal(int)
    session_expired_signal = pyqtSignal()
    session_recovered_signal = pyqtSignal(bool)

    def __init__(self, api_engine, section_ids, interval, enable_waitlist=False,
                 auth_manager=None):
        super().__init__()
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.enable_waitlist = enable_waitlist
        self.auth_manager = auth_manager
        self.is_running = True
        self._stop_event = threading.Event()

        self.api_engine.log_callback = lambda msg, level="normal": self.log_signal.emit(msg, level)

    def _sleep(self, seconds: float):
        self._stop_event.wait(seconds)

    def _recover_session(self) -> bool:
        """
        Attempt silent re-authentication.

        Returns True if recovery succeeded and the worker should continue.
        Returns False if recovery failed and the worker should stop.
        """
        self.log_signal.emit(
            "[WARNING] Session expired. Initiating automatic re-authentication...",
            "error")
        self.session_expired_signal.emit()

        if self.auth_manager is None:
            self.log_signal.emit(
                "[FATAL] No auth manager available for session recovery. Stopping Auto-Catch.",
                "error")
            self.session_recovered_signal.emit(False)
            self.is_running = False
            return False

        try:
            creds = self.auth_manager.silent_relogin()
            self.api_engine.update_credentials(
                creds["cookies"], creds["verification_token"])
            self.log_signal.emit(
                "[SUCCESS] Session recovered. Resuming Auto-Catch...",
                "success")
            self.session_recovered_signal.emit(True)
            return True
        except Exception as e:
            self.log_signal.emit(
                f"[FATAL] Auto-recovery failed: {e}. Stopping Auto-Catch.",
                "error")
            self.session_recovered_signal.emit(False)
            self.is_running = False
            return False

    def run(self):
        self._stop_event.clear()
        round_count = 0

        while self.is_running:
            round_count += 1
            self.round_signal.emit(round_count)
            self.status_signal.emit(STATE_POLLING)
            self.log_signal.emit(
                i18n.tr("autocatch_round_start", round_count), "normal")

            found_seats = False
            session_lost = False
            for sec_id in self.section_ids:
                if not self.is_running:
                    break
                try:
                    if self.api_engine.check_section_status(
                            sec_id, enable_waitlist=self.enable_waitlist):
                        found_seats = True
                        self.seat_found_signal.emit(sec_id, 0)
                        break
                except SessionExpiredError:
                    session_lost = True
                    break

            if session_lost:
                if not self._recover_session():
                    break
                continue

            if found_seats:
                self.status_signal.emit(STATE_ATTACKING)

                try:
                    success, msg = self.api_engine.execute_full_attack(
                        self.section_ids,
                        enable_waitlist=self.enable_waitlist)
                except SessionExpiredError:
                    self.log_signal.emit(
                        "[WARNING] Session expired during attack. Initiating automatic re-authentication...",
                        "error")
                    if not self._recover_session():
                        break
                    continue

                self.attack_result_signal.emit(success, msg)

                if success:
                    self.is_running = False
                    break
                else:
                    self.log_signal.emit(
                        i18n.tr("autocatch_attack_failed", msg), "error")
            else:
                self.log_signal.emit(
                    i18n.tr("autocatch_no_seats"), "normal")

            if self.is_running:
                self._sleep(self.interval)

        self.status_signal.emit(STATE_IDLE)
