import threading
from PyQt6.QtCore import QThread, pyqtSignal

from i18n import i18n

STATE_IDLE = 0
STATE_POLLING = 1
STATE_ATTACKING = 2


class AutoCatchWorker(QThread):
    log_signal = pyqtSignal(str, str)
    status_signal = pyqtSignal(int)
    seat_found_signal = pyqtSignal(str, int)
    attack_result_signal = pyqtSignal(bool, str)
    round_signal = pyqtSignal(int)

    def __init__(self, api_engine, section_ids, interval, enable_waitlist=False):
        super().__init__()
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.enable_waitlist = enable_waitlist
        self.is_running = True
        self._stop_event = threading.Event()

        self.api_engine.log_callback = lambda msg, level="normal": self.log_signal.emit(msg, level)

    def _sleep(self, seconds: float):
        self._stop_event.wait(seconds)

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
            for sec_id in self.section_ids:
                if not self.is_running:
                    break
                if self.api_engine.check_section_status(
                        sec_id, enable_waitlist=self.enable_waitlist):
                    found_seats = True
                    self.seat_found_signal.emit(sec_id, 0)
                    break

            if found_seats:
                self.status_signal.emit(STATE_ATTACKING)

                success, msg = self.api_engine.execute_full_attack(
                    self.section_ids,
                    enable_waitlist=self.enable_waitlist)

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
