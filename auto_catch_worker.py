import threading

from PyQt6.QtCore import QThread, pyqtSignal

from i18n import i18n
from core_api import SessionExpiredError
from worker_session import recover_session

STATE_IDLE = 0
STATE_POLLING = 1
STATE_ATTACKING = 2


class AutoCatchWorker(QThread):
    log_signal = pyqtSignal(str, str)
    status_signal = pyqtSignal(int)
    seat_found_signal = pyqtSignal(str, int)
    attack_result_signal = pyqtSignal(bool, str)
    round_signal = pyqtSignal(int, int)
    session_expired_signal = pyqtSignal()
    session_recovered_signal = pyqtSignal(bool)

    def __init__(self, api_engine, section_ids, interval, enable_waitlist=False,
                 auth_manager=None, drop_section_id: str = ""):
        super().__init__()
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.enable_waitlist = enable_waitlist
        self.auth_manager = auth_manager
        self.drop_section_id = drop_section_id
        self.is_running = True
        self.recovery_count = 0
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

        if self.auth_manager is None or self.recovery_count >= 1 or not self.is_running:
            self.log_signal.emit(
                i18n.tr("log_session_stopped"),
                "error")
            self.session_recovered_signal.emit(False)
            self.is_running = False
            return False

        self.recovery_count += 1
        if recover_session(self.api_engine, self.auth_manager, section_ids=self.section_ids):
            self.log_signal.emit(
                "[SUCCESS] Session recovered. Resuming Auto-Catch...",
                "success")
            self.session_recovered_signal.emit(True)
            return True
        else:
            self.log_signal.emit(
                i18n.tr("log_session_stopped"),
                "error")
            self.session_recovered_signal.emit(False)
            self.is_running = False
            return False

    def run(self):
        self._stop_event.clear()
        self.api_engine._event("worker_started", worker="autocatch", section_ids=self.section_ids)
        round_count = 0
        self.api_engine.prefetch_registration_credits(self.section_ids)

        while self.is_running:
            round_count += 1
            self.api_engine._event("poll_started", worker="autocatch", poll_round=round_count,
                                   section_ids=self.section_ids)
            self.round_signal.emit(round_count, self.recovery_count)
            self.status_signal.emit(STATE_POLLING)
            self.log_signal.emit(
                i18n.tr("autocatch_round_start", round_count), "normal")

            found_section = False
            session_lost = False
            pending = self.api_engine.pending_sections(self.section_ids)
            try:
                for sec_id in self.api_engine.poll_available_sections(
                        pending, self.enable_waitlist, lambda: self.is_running):
                    found_section = True
                    self.seat_found_signal.emit(sec_id, 0)
                    self.status_signal.emit(STATE_ATTACKING)
                    self.api_engine.execute_full_attack(
                        self.section_ids, enable_waitlist=self.enable_waitlist,
                        drop_section_id=self.drop_section_id or None, submission_ids=[sec_id],
                        verify_after=False)
            except SessionExpiredError:
                session_lost = True

            self.api_engine._event("poll_finished", worker="autocatch", poll_round=round_count,
                                   session_expired=session_lost, found_section=found_section)
            if session_lost:
                if not self._recover_session():
                    break
                continue

            if not self.is_running:
                break
            try:
                if self.api_engine.needs_verification(self.section_ids):
                    success, msg = self.api_engine.execute_full_attack(self.section_ids, submission_ids=[])
                else:
                    success = self.api_engine.all_registered(self.section_ids)
                    msg = self.api_engine.result_message(self.section_ids)
            except SessionExpiredError:
                if not self._recover_session():
                    break
                continue

            if success or found_section:
                self.attack_result_signal.emit(success, msg)
            elif pending:
                self.log_signal.emit(
                    i18n.tr("autocatch_no_seats"), "normal")

            pending = self.api_engine.pending_sections(self.section_ids)
            if success or (not pending and not self.api_engine.needs_verification(self.section_ids)):
                self.is_running = False
            if self.is_running:
                delay = self.api_engine.retry_delay
                self._sleep(max(1.0, self.interval, delay) if pending else max(2.0, delay))

        self.api_engine._event("worker_stopped", worker="autocatch",
                              success=self.api_engine.all_registered(self.section_ids))
        self.status_signal.emit(STATE_IDLE)
