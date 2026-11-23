import sys
import os

if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
    data_dir = sys._MEIPASS
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = app_dir

import time
import threading
import datetime
from PyQt6.QtGui import QPixmap, QIcon, QIntValidator, QDesktopServices, QTextCursor
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QObject
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from qfluentwidgets import (FluentWindow, SubtitleLabel, BodyLabel, LineEdit,
                            PushButton, TextEdit, CardWidget,
                            SwitchButton,
                            Theme, setTheme, qconfig, NavigationItemPosition,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            ScrollArea)

from i18n import i18n, load_config
from core_auth import KeanAuthManager
from core_api import KeanApiClient, SessionExpiredError
from registration_state import SessionStatus
from registration_reader import DegreePlanRegistrationReader
from session_keepalive import SessionKeepalive
from worker_session import recover_session
from ui_autocatch import AutoCatchInterface
from ui_catches import MyCatchesInterface
from ui_lookup import SectionLookupInterface
from ui_settings import SettingsInterface


class BrandingWidget(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(16, 12, 0, 0)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.icon_label = QLabel(self)
        self.icon_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.85); border-radius: 4px; padding: 2px;")
        self.icon_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        logo_path = os.path.join(data_dir, "assets", "logo.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(data_dir, "assets", "logo.jpg")

        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            dpr = self.devicePixelRatioF()
            scaled_pixmap = pixmap.scaledToHeight(int(22 * dpr), Qt.TransformationMode.SmoothTransformation)
            scaled_pixmap.setDevicePixelRatio(dpr)
            self.icon_label.setPixmap(scaled_pixmap)

        self.title_label = QLabel("KeanSeatsCatcher", self)
        self.title_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: white; background: transparent; margin-left: 10px;"
        )
        self.title_label.setWordWrap(False)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        self.layout.addWidget(self.icon_label)
        self.layout.addWidget(self.title_label)

    def setSelected(self, selected: bool):
        pass

    def setCompacted(self, compacted: bool):
        pass


class PingWorker(QThread):
    result_signal = pyqtSignal(object)

    def __init__(self, api_engine):
        super().__init__()
        self.api_engine = api_engine

    def run(self):
        self.result_signal.emit(self.api_engine.check_session())


class MonitorWorker(QThread):
    log_signal = pyqtSignal(str, str)
    countdown_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    waiting_changed = pyqtSignal()

    def __init__(self, api_engine, section_ids, interval, target_time_str, enable_waitlist=False,
                 auth_manager=None):
        super().__init__()
        self.is_running = True
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.target_time_str = target_time_str
        self.enable_waitlist = enable_waitlist
        self.auth_manager = auth_manager
        self._stop_event = threading.Event()
        self._schedule_lock = threading.Lock()
        self._waiting_deadline = None

        self.api_engine.log_callback = lambda msg, level="normal": self.log_signal.emit(msg, level)

    @property
    def is_waiting(self):
        deadline = self._waiting_deadline
        return self.is_running and deadline is not None and datetime.datetime.now() < deadline

    def refresh_waiting_session(self, cookies, verification_token, student_id):
        # Only the local credential update shares the deadline handoff.
        with self._schedule_lock:
            if not self.is_waiting:
                return False
            self.api_engine.update_credentials(cookies, verification_token, student_id=student_id)
            return True

    def _sleep(self, seconds: float):
        self._stop_event.wait(seconds)

    def run(self):
        self._stop_event.clear()
        self.api_engine._event("worker_started", worker="dashboard", section_ids=self.section_ids)
        if self.target_time_str:
            now = datetime.datetime.now()
            parts = self.target_time_str.replace("：", ":").strip().split(":")
            try:
                if len(parts) == 2:
                    target_dt = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
                elif len(parts) == 3:
                    target_dt = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=int(parts[2]),
                                            microsecond=0)
                else:
                    target_dt = now

                if target_dt < now:
                    self.log_signal.emit(i18n.tr("msg_target_passed"), "error")
                    target_dt = now
            except ValueError:
                self.log_signal.emit(i18n.tr("msg_time_parse_error"), "error")
                target_dt = now

            if target_dt > now:
                self._waiting_deadline = target_dt
                self.waiting_changed.emit()
                self.log_signal.emit(i18n.tr("msg_scheduled", target_dt.strftime('%H:%M:%S')), "success")

                while self.is_running:
                    now = datetime.datetime.now()
                    if now >= target_dt:
                        self.log_signal.emit(i18n.tr("msg_time_reached"), "success")
                        self.countdown_signal.emit(i18n.tr("msg_requesting"))
                        break

                    diff = int((target_dt - now).total_seconds())
                    h, rem = divmod(diff, 3600)
                    m, s = divmod(rem, 60)
                    if h > 0:
                        self.countdown_signal.emit(i18n.tr("msg_waiting", f"{h:02d}:{m:02d}:{s:02d}"))
                    else:
                        self.countdown_signal.emit(i18n.tr("msg_waiting", f"{m:02d}:{s:02d}"))
                    time.sleep(0.1)
                with self._schedule_lock:
                    self._waiting_deadline = None
                self.waiting_changed.emit()
        else:
            self.countdown_signal.emit(i18n.tr("msg_requesting"))

        if not self.is_running:
            self.api_engine._event("worker_stopped", worker="dashboard", success=False)
            self.finished_signal.emit(False)
            return

        self.log_signal.emit(i18n.tr("msg_target_sections", self.section_ids), "normal")

        attempt = 1
        success_flag = False
        recovery_attempted = False

        while self.is_running:
            self.log_signal.emit(i18n.tr("msg_round_start", attempt), "normal")

            try:
                success, msg = self.api_engine.execute_full_attack(
                    self.section_ids, enable_waitlist=self.enable_waitlist)

                if success:
                    self.log_signal.emit(i18n.tr("msg_success"), "success")
                    success_flag = True
                    self.is_running = False
                    break
                else:
                    pending = self.api_engine.pending_sections(self.section_ids)
                    if not pending and not self.api_engine.needs_verification(self.section_ids):
                        self.is_running = False
                        break
                    retry_delay = self.api_engine.retry_delay
                    delay = max(self.interval, retry_delay) if pending else max(2, retry_delay)
                    self.log_signal.emit(i18n.tr("msg_round_failed", attempt, msg, delay), "normal")
                    self._sleep(delay)

            except SessionExpiredError:
                self.log_signal.emit(i18n.tr("log_session_recovery"), "error")
                if recovery_attempted or not self.is_running:
                    self.log_signal.emit(i18n.tr("log_session_stopped"), "error")
                    self.is_running = False
                    break
                recovery_attempted = True
                if not recover_session(self.api_engine, self.auth_manager, section_ids=self.section_ids):
                    self.log_signal.emit(i18n.tr("log_session_stopped"), "error")
                    self.is_running = False
                    break
            except Exception as e:
                self.log_signal.emit(i18n.tr("msg_exception", type(e).__name__), "error")
                self._sleep(max(1, self.interval, self.api_engine.retry_delay))

            attempt += 1

        if not success_flag:
            self.log_signal.emit(i18n.tr("msg_process_stopped"), "normal")

        self.api_engine._event("worker_stopped", worker="dashboard", success=success_flag)
        self.finished_signal.emit(success_flag)


class KeepaliveSignals(QObject):
    """Thread bridge for the Qt-free background session keepalive."""

    log = pyqtSignal(str, str)
    session_recovered = pyqtSignal()
    manual_login_required = pyqtSignal(str)


class BrowserLaunchWorker(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, auth_manager, detach=False):
        super().__init__()
        self.auth_manager = auth_manager
        self.detach = detach

    def run(self):
        try:
            self.auth_manager.open_login_page(detach=self.detach)
            self.finished_signal.emit(True, i18n.tr("msg_browser_launched"))
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class BrowserExtractWorker(QThread):
    result_signal = pyqtSignal(object)

    def __init__(self, auth_manager):
        super().__init__()
        self.auth_manager = auth_manager

    def run(self):
        try:
            credentials = self.auth_manager.extract_credentials()
        except Exception as exc:
            credentials = {"error": type(exc).__name__}
        self.result_signal.emit(credentials)


class MonitorInterface(QWidget):
    authentication_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("MonitorInterface")
        self.layout = QVBoxLayout(self)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.layout.setSpacing(16)

        self.api_engine = None
        self.auth_manager = None
        self.sibling = None
        self.worker = None
        self.ping_worker = None
        self._ping_is_current = False

        title = SubtitleLabel(i18n.tr("monitor_title"))
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.layout.addWidget(title)

        card = CardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(15)

        input_row1 = QHBoxLayout()
        input_row1.addWidget(BodyLabel(i18n.tr("lbl_section_ids")))
        self.section_input = LineEdit()
        self.section_input.setPlaceholderText(i18n.tr("placeholder_section_ids"))
        input_row1.addWidget(self.section_input, 1)

        card_layout.addLayout(input_row1)

        input_row2 = QHBoxLayout()
        input_row2.addWidget(BodyLabel(i18n.tr("lbl_schedule")))
        self.time_input = LineEdit()
        self.time_input.setPlaceholderText(i18n.tr("placeholder_schedule"))
        input_row2.addWidget(self.time_input, 1)

        input_row2.addSpacing(20)

        input_row2.addWidget(BodyLabel(i18n.tr("lbl_interval")))
        self.interval_input = LineEdit()
        self.interval_input.setPlaceholderText(i18n.tr("placeholder_interval"))
        self.interval_input.setText("5")
        self.interval_input.setFixedWidth(80)

        validator = QIntValidator(0, 3600, self)
        self.interval_input.setValidator(validator)
        input_row2.addWidget(self.interval_input)

        card_layout.addLayout(input_row2)

        waitlist_row = QHBoxLayout()
        self.waitlist_label = BodyLabel(i18n.tr("lbl_waitlist"))
        self.waitlist_switch = SwitchButton()
        self.waitlist_switch.setChecked(False)
        self.waitlist_switch.setToolTip(i18n.tr("tooltip_waitlist"))
        waitlist_row.addWidget(self.waitlist_label)
        waitlist_row.addWidget(self.waitlist_switch)
        waitlist_row.addStretch(1)
        card_layout.addLayout(waitlist_row)

        btn_row = QHBoxLayout()
        self.btn_start = PushButton(FIF.PLAY, i18n.tr("btn_start"))
        self.btn_stop = PushButton(FIF.PAUSE, i18n.tr("btn_stop"))
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        self.btn_ping = PushButton(FIF.SYNC, i18n.tr("btn_ping"))
        self.btn_ping.setToolTip(i18n.tr("tooltip_ping"))
        btn_row.addWidget(self.btn_ping)
        card_layout.addLayout(btn_row)

        self.layout.addWidget(card)

        log_title = BodyLabel(i18n.tr("lbl_log"))
        log_title.setStyleSheet("color: #a0a0a0;")
        self.layout.addWidget(log_title)

        self.log_box = TextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.document().setMaximumBlockCount(1000)
        self.log_box.setStyleSheet("font-family: Consolas; font-size: 13px; background: #1e1e1e;")
        self.layout.addWidget(self.log_box, 1)

        self.btn_start.clicked.connect(self.start_attack)
        self.btn_stop.clicked.connect(self.stop_attack)
        self.btn_ping.clicked.connect(self.ping_server)

        self.section_input.editingFinished.connect(self._prefetch_credits)

    def set_api_engine(self, engine):
        self.api_engine = engine
        self.log(i18n.tr("msg_engine_loaded"), "success")
        self._prefetch_credits()

    def _prefetch_credits(self):
        if self.api_engine and not self.is_running and not (self.sibling and self.sibling.is_running):
            ids = self._target_sections()
            if ids:
                self.api_engine.prefetch_registration_credits(ids, include_section_details=True)

    def _target_sections(self):
        return [sid for sid in map(str.strip, self.section_input.text().split(",")) if sid]

    @property
    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    @property
    def is_checking_session(self):
        return self.ping_worker is not None and self.ping_worker.isRunning()

    def can_refresh_session(self):
        return (not (self.sibling and self.sibling.is_running)
                and (not self.is_running or self.worker.is_waiting))

    def refresh_session(self, cookies, verification_token, student_id):
        if self.sibling and self.sibling.is_running:
            return False
        if self.is_running:
            if not self.worker.refresh_waiting_session(cookies, verification_token, student_id):
                return False
        else:
            self.api_engine.update_credentials(cookies, verification_token, student_id=student_id)
        self._ping_is_current = False
        if self.sibling:
            self.sibling.clear_relogin_status()
        return True

    def lock_start(self):
        self.btn_start.setEnabled(False)
        self.btn_ping.setEnabled(False)

    def unlock_start(self):
        self._refresh_controls()

    def _refresh_controls(self):
        running = self.is_running
        checking = self.is_checking_session
        sibling_busy = self.sibling and self.sibling.is_running
        self.btn_start.setEnabled(not (running or checking or sibling_busy))
        self.btn_ping.setEnabled(not (checking or sibling_busy) and (not running or self.worker.is_waiting))
        if self.sibling:
            if running or checking:
                self.sibling.lock_start()
            else:
                self.sibling.unlock_start()

    def log(self, msg, state="normal"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = ">> "
        if state == "error": prefix = "[ERROR] "
        if state == "success": prefix = "[SUCCESS] "

        self.log_box.moveCursor(QTextCursor.MoveOperation.End)
        separator = "" if self.log_box.document().isEmpty() else "\n"
        self.log_box.insertPlainText(f"{separator}[{timestamp}] {prefix}{msg}")
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def update_btn_text(self, text):
        self.btn_start.setText(text)

    def on_worker_finished(self):
        self.btn_stop.setEnabled(False)
        self.btn_start.setText(i18n.tr("btn_start"))
        self._refresh_controls()

    def start_attack(self):
        if self.is_running or self.is_checking_session:
            return
        if self.api_engine is None:
            InfoBar.error(i18n.tr("msg_engine_not_ready"), i18n.tr("msg_goto_auth"), duration=3000, parent=self)
            return

        if self.sibling and self.sibling.is_running:
            InfoBar.warning(
                "Busy",
                i18n.tr("msg_autocatch_busy"),
                duration=3000, parent=self)
            return

        section_list = self._target_sections()
        if not section_list:
            self.log(i18n.tr("msg_enter_sections"), "error")
            return

        try:
            interval_time = int(self.interval_input.text())
            if interval_time < 0:
                raise ValueError
        except (ValueError, TypeError):
            InfoBar.warning(i18n.tr("msg_invalid_param"), i18n.tr("msg_invalid_interval"), duration=5000,
                            parent=self)
            self.interval_input.setText("5")
            interval_time = 5

        target_time_str = self.time_input.text().strip()

        self.btn_stop.setEnabled(True)

        waitlist_enabled = self.waitlist_switch.isChecked()
        self.worker = MonitorWorker(self.api_engine, section_list, interval_time, target_time_str,
                                    enable_waitlist=waitlist_enabled, auth_manager=self.auth_manager)
        self.worker.log_signal.connect(self.log)
        self.worker.countdown_signal.connect(self.update_btn_text)
        self.worker.waiting_changed.connect(self._refresh_controls)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

        self._refresh_controls()
        self.api_engine.prefetch_registration_credits(section_list, include_section_details=True)

    def stop_attack(self):
        if self.worker is not None:
            self.worker.is_running = False
            self.worker._stop_event.set()
        self.btn_stop.setEnabled(False)
        self._refresh_controls()
        self.log(i18n.tr("msg_stopping"))

    def ping_server(self):
        if not self.can_refresh_session() or self.is_checking_session:
            return
        if self.api_engine is None:
            InfoBar.error(i18n.tr("msg_engine_not_ready"), i18n.tr("msg_goto_auth"), duration=3000, parent=self)
            return

        self.ping_worker = PingWorker(self.api_engine)
        self._ping_is_current = True
        self.ping_worker.result_signal.connect(self.on_ping_result)
        self.ping_worker.finished.connect(self._refresh_controls)
        self.ping_worker.start()
        self._refresh_controls()

    def on_ping_result(self, result):
        if not self._ping_is_current:
            return
        if result.status == SessionStatus.VALID:
            InfoBar.success(
                i18n.tr("msg_ping_title"),
                i18n.tr("msg_ping_result", f"{result.elapsed_ms} ms"),
                duration=3000,
                parent=self
            )
        else:
            bar = InfoBar.warning(
                i18n.tr("msg_ping_title"),
                i18n.tr("msg_session_expired" if result.status == SessionStatus.EXPIRED
                        else "msg_ping_timeout"),
                duration=6500,
                parent=self
            )
            if result.status == SessionStatus.EXPIRED and self.can_refresh_session():
                button = PushButton(i18n.tr("tab_auth"))
                button.clicked.connect(self.authentication_requested)
                bar.addWidget(button)


class AuthInterface(QWidget):
    engine_ready_signal = pyqtSignal(object)
    credentials_refreshed = pyqtSignal()

    def __init__(self, parent=None, monitor=None):
        super().__init__(parent=parent)
        self.setObjectName("AuthInterface")
        self.layout = QVBoxLayout(self)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.auth_manager = KeanAuthManager()
        self.monitor = monitor
        self.api_engine = None

        title = SubtitleLabel(i18n.tr("auth_title"))
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.layout.addWidget(title)

        card = CardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(15)

        info = BodyLabel(i18n.tr("auth_info"))
        info.setStyleSheet("color: #a0a0a0;")
        card_layout.addWidget(info)

        self.btn_login = PushButton(FIF.GLOBE, i18n.tr("btn_launch_browser"))
        self.btn_extract = PushButton(FIF.DOWNLOAD, i18n.tr("btn_extract"))
        self.btn_extract.setEnabled(False)
        self.account_label = BodyLabel()
        self.account_label.hide()

        card_layout.addWidget(self.account_label)
        card_layout.addWidget(self.btn_login)
        card_layout.addWidget(self.btn_extract)

        self.layout.addWidget(card)
        self.layout.addStretch(1)

        self.btn_login.clicked.connect(self.handle_login)
        self.btn_extract.clicked.connect(self.handle_extract)

    def handle_login(self):
        if self.monitor and not self.monitor.can_refresh_session():
            self._show_busy()
            return
        self.btn_login.setEnabled(False)
        self.btn_extract.setEnabled(False)
        InfoBar.info(i18n.tr("msg_launching_browser"), i18n.tr("msg_please_wait"), duration=3000, parent=self)

        config = load_config()
        detach = config.get("Settings", {}).get("KeepBrowserOpen", False)
        self.launch_worker = BrowserLaunchWorker(self.auth_manager, detach=detach)
        self.launch_worker.finished_signal.connect(self.on_browser_launched)
        self.launch_worker.start()

    def on_browser_launched(self, success, msg):
        self.btn_login.setEnabled(True)
        if success:
            self.btn_extract.setEnabled(True)
            InfoBar.success(i18n.tr("msg_browser_ready"), i18n.tr("msg_browser_login_hint"), duration=5000, parent=self)
        else:
            InfoBar.error(i18n.tr("msg_launch_failed"), i18n.tr("msg_browser_error", msg), duration=8000, parent=self)

    def handle_extract(self):
        if self.monitor and not self.monitor.can_refresh_session():
            self._show_busy()
            return
        self.btn_login.setEnabled(False)
        self.btn_extract.setEnabled(False)
        self.extract_worker = BrowserExtractWorker(self.auth_manager)
        self.extract_worker.result_signal.connect(self.on_credentials_extracted)
        self.extract_worker.start()

    def _show_busy(self):
        InfoBar.warning(i18n.tr("auth_title"), i18n.tr("msg_auth_busy"), duration=5000, parent=self)

    @property
    def is_browser_busy(self):
        return any(worker is not None and worker.isRunning()
                   for worker in (getattr(self, "launch_worker", None),
                                  getattr(self, "extract_worker", None)))

    def on_credentials_extracted(self, credentials):
        self.btn_login.setEnabled(True)
        self.btn_extract.setEnabled(True)
        if "error" in credentials:
            InfoBar.error(i18n.tr("msg_extract_failed"), credentials["error"], duration=5000, parent=self)
            return

        student_id = credentials["student_id"]
        cookies = credentials["cookies"]
        token = credentials["verification_token"]
        refreshing = self.api_engine is not None
        if not refreshing:
            self.api_engine = KeanApiClient(cookies, token, student_id, DegreePlanRegistrationReader())
            self.engine_ready_signal.emit(self.api_engine)
        elif str(student_id) != str(self.api_engine.student_id):
            InfoBar.error(i18n.tr("msg_extract_failed"),
                          i18n.tr("msg_auth_same_account", self.api_engine.student_id),
                          duration=6000, parent=self)
            return
        elif self.monitor:
            if not self.monitor.refresh_session(cookies, token, student_id):
                self._show_busy()
                return
        else:
            self.api_engine.update_credentials(cookies, token, student_id=student_id)

        config = load_config()
        if not config.get("Settings", {}).get("KeepBrowserOpen", False):
            self.auth_manager.close_browser()

        self.btn_extract.setEnabled(False)
        self.btn_login.setText(i18n.tr("btn_relogin"))
        self.account_label.setText(i18n.tr("msg_engine_ready", student_id))
        self.account_label.show()

        status = "msg_auth_refreshed" if refreshing else "msg_auth_success"
        InfoBar.success(i18n.tr(status), i18n.tr("msg_engine_ready", student_id),
                        position=InfoBarPosition.TOP, duration=5000, parent=self)
        self.credentials_refreshed.emit()


class AboutInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AboutInterface")
        self.view = QWidget(self)
        self.layout = QVBoxLayout(self.view)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.layout.setSpacing(20)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea{background: transparent; border: none}")

        top_card = CardWidget()
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(20, 20, 20, 20)
        top_layout.setSpacing(15)

        logo_label = QLabel()
        logo_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.85); border-radius: 8px; padding: 4px;")

        logo_path = os.path.join(data_dir, "assets", "logo.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(data_dir, "assets", "logo.jpg")

        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            dpr = self.devicePixelRatioF()
            scaled_pixmap = pixmap.scaledToHeight(int(60 * dpr), Qt.TransformationMode.SmoothTransformation)
            scaled_pixmap.setDevicePixelRatio(dpr)
            logo_label.setPixmap(scaled_pixmap)
        top_layout.addWidget(logo_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(5)
        name_lbl = SubtitleLabel("KeanSeatsCatcher")
        name_lbl.setStyleSheet("font-size: 22px; font-weight: bold;")
        ver_lbl = BodyLabel("Version 1.3.5")
        ver_lbl.setStyleSheet("color: #a0a0a0;")
        info_layout.addWidget(name_lbl)
        info_layout.addWidget(ver_lbl)
        info_layout.addStretch(1)
        top_layout.addLayout(info_layout)
        top_layout.addStretch(1)

        if os.path.exists(os.path.join(data_dir, "assets", "github.png")):
            btn_github = PushButton(QIcon(os.path.join(data_dir, "assets", "github.png")), "GitHub")
        else:
            btn_github = PushButton(FIF.LINK, "GitHub")
        if os.path.exists(os.path.join(data_dir, "assets", "bilibili.png")):
            btn_bilibili = PushButton(QIcon(os.path.join(data_dir, "assets", "bilibili.png")), "Bilibili")
        else:
            btn_bilibili = PushButton(FIF.VIDEO, "Bilibili")

        btn_github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Daozhu1007/KeanSeatsCatcher")))
        btn_bilibili.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://space.bilibili.com/477852567")))

        top_layout.addWidget(btn_github)
        top_layout.addWidget(btn_bilibili)
        self.layout.addWidget(top_card)

        author_title = SubtitleLabel(i18n.tr("about_title"))
        author_title.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px;")
        self.layout.addWidget(author_title)

        author_card = CardWidget()
        author_layout = QVBoxLayout(author_card)
        author_layout.setContentsMargins(20, 20, 20, 20)
        author_layout.setSpacing(10)

        intro_lbl = BodyLabel("Developer: Limitime")
        intro_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        desc_lbl = BodyLabel(i18n.tr("about_desc"))
        desc_lbl.setStyleSheet("color: #a0a0a0; font-size: 14px;")

        email_lbl = BodyLabel(i18n.tr("about_email"))
        qq_lbl = BodyLabel(i18n.tr("about_qq"))

        author_layout.addWidget(intro_lbl)
        author_layout.addWidget(desc_lbl)
        author_layout.addSpacing(10)
        author_layout.addWidget(email_lbl)
        author_layout.addWidget(qq_lbl)
        self.layout.addWidget(author_card)

        warn_container = QVBoxLayout()
        warn_container.setSpacing(6)
        warn_container.setContentsMargins(0, 20, 0, 0)

        warn1 = BodyLabel(i18n.tr("about_warn1"))
        warn1.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        warn2 = BodyLabel(i18n.tr("about_warn2"))
        warn2.setStyleSheet("color: #a0a0a0; font-size: 13px;")

        warn_container.addWidget(warn1)
        warn_container.addWidget(warn2)
        self.layout.addLayout(warn_container)
        self.layout.addStretch(1)

class KeanSeatsCatcherApp(FluentWindow):
    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        qconfig.set(qconfig.themeMode, Theme.DARK)

        self.setWindowTitle("KeanSeatsCatcher")
        self.setWindowIcon(QIcon(os.path.join(data_dir, "assets", "logo.ico")))
        self.resize(1000, 650)
        self.setMinimumSize(900, 650)

        self.navigationInterface.setReturnButtonVisible(False)
        self.navigationInterface.setExpandWidth(207)

        if hasattr(self.titleBar, 'titleLabel'):
            self.titleBar.titleLabel.hide()
        if hasattr(self.titleBar, 'iconLabel'):
            self.titleBar.iconLabel.hide()

        try:
            nav_panel = self.navigationInterface.panel
            nav_panel.vBoxLayout.removeWidget(nav_panel.menuButton)
            nav_panel.menuButton.hide()
            nav_panel.menuButton.setParent(None)
        except Exception:
            pass

        self.navigationInterface.panel.setMinimumExpandWidth(800)

        self.branding_widget = BrandingWidget(self)
        self.navigationInterface.addWidget(
            routeKey='branding',
            widget=self.branding_widget,
            onClick=None,
            position=NavigationItemPosition.TOP
        )

        self.monitor_interface = MonitorInterface(self)
        self.autocatch_interface = AutoCatchInterface(self)
        self.lookup_interface = SectionLookupInterface(self)
        self.catches_interface = MyCatchesInterface(self)
        self.auth_interface = AuthInterface(self, monitor=self.monitor_interface)
        self.settings_interface = SettingsInterface(self)
        self.about_interface = AboutInterface(self)

        self.auth_interface.engine_ready_signal.connect(self.monitor_interface.set_api_engine)
        self.auth_interface.engine_ready_signal.connect(self.autocatch_interface.set_api_engine)
        self.auth_interface.engine_ready_signal.connect(self.lookup_interface.set_api_engine)
        self.auth_interface.engine_ready_signal.connect(self.catches_interface.bind_engine)
        self.monitor_interface.authentication_requested.connect(lambda: self.switchTo(self.auth_interface))
        recovery_auth = KeanAuthManager()
        self.autocatch_interface.set_auth_manager(recovery_auth)
        self.monitor_interface.auth_manager = recovery_auth

        self.session_keepalive = None
        self.keepalive_signals = KeepaliveSignals()
        self.keepalive_signals.log.connect(self.monitor_interface.log)
        self.keepalive_signals.session_recovered.connect(self.on_session_auto_recovered)
        self.keepalive_signals.manual_login_required.connect(self.on_session_manual_login_required)
        self.auth_interface.engine_ready_signal.connect(self.start_session_keepalive)
        self.auth_interface.credentials_refreshed.connect(self.notify_keepalive_credentials)

        self.monitor_interface.sibling = self.autocatch_interface
        self.autocatch_interface.sibling = self.monitor_interface

        self.addSubInterface(self.monitor_interface, FIF.VIEW, i18n.tr("tab_monitor"))
        self.addSubInterface(self.autocatch_interface, FIF.ZOOM, i18n.tr("tab_autocatch"))
        self.addSubInterface(self.lookup_interface, FIF.SEARCH, i18n.tr("tab_lookup"))
        self.addSubInterface(self.catches_interface, FIF.HISTORY, i18n.tr("tab_catches"))
        self.addSubInterface(self.auth_interface, FIF.FINGERPRINT, i18n.tr("tab_auth"))
        self.addSubInterface(self.settings_interface, FIF.SETTING, i18n.tr("tab_settings"), position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.about_interface, FIF.INFO, i18n.tr("tab_about"), position=NavigationItemPosition.BOTTOM)

        self.navigationInterface.expand()

    def start_session_keepalive(self, engine):
        """Own global session maintenance once credentials exist."""
        if self.session_keepalive is not None:
            self.notify_keepalive_credentials()
            return
        monitor = self.monitor_interface

        def busy():
            # Attack rounds and Auto-Catch polls exercise the session themselves
            # and own their recovery; a scheduled countdown is NOT busy here.
            return ((monitor.is_running and not monitor.worker.is_waiting)
                    or self.autocatch_interface.is_running
                    or monitor.is_checking_session
                    or self.auth_interface.is_browser_busy)

        signals = self.keepalive_signals
        self.session_keepalive = SessionKeepalive(
            engine, monitor.auth_manager, busy,
            on_log=signals.log.emit,
            on_recovered=signals.session_recovered.emit,
            on_manual_required=signals.manual_login_required.emit)
        self.session_keepalive.start()

    def notify_keepalive_credentials(self):
        if self.session_keepalive is not None:
            self.session_keepalive.credentials_updated()

    def on_session_auto_recovered(self):
        InfoBar.success(i18n.tr("keepalive_title"), i18n.tr("keepalive_recovered"),
                        position=InfoBarPosition.TOP, duration=5000, parent=self)

    def on_session_manual_login_required(self, reason):
        bar = InfoBar.error(i18n.tr("keepalive_title"), i18n.tr("keepalive_manual_required"),
                            duration=0, parent=self)
        button = PushButton(i18n.tr("tab_auth"))
        button.clicked.connect(lambda: self.switchTo(self.auth_interface))
        bar.addWidget(button)
        self._show_keepalive_toast()

    def _show_keepalive_toast(self):
        try:
            from winotify import Notification
            toast = Notification(
                app_id="wku.mingtai.keanseatscatcher.v2",
                title=i18n.tr("toast_session_title"),
                msg=i18n.tr("keepalive_manual_required"),
                duration="long"
            )
            toast.show()
        except Exception:
            pass

    def closeEvent(self, event):
        if self.session_keepalive is not None:
            self.session_keepalive.stop()
        super().closeEvent(event)


if __name__ == '__main__':
    import ctypes

    try:
        myappid = 'wku.mingtai.keanseatscatcher.v2'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(data_dir, "assets", "logo.ico")))
    window = KeanSeatsCatcherApp()
    window.show()
    sys.exit(app.exec())
