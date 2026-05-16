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
from PyQt6.QtGui import QPixmap, QIcon, QIntValidator, QDesktopServices
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (FluentWindow, SubtitleLabel, BodyLabel, LineEdit,
                            PushButton, TextEdit, CardWidget, ComboBox,
                            SwitchButton,
                            Theme, setTheme, qconfig, NavigationItemPosition,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            ScrollArea)

from i18n import i18n, save_config, load_config
from core_auth import KeanAuthManager
from core_api import KeanApiClient
from ui_autocatch import AutoCatchInterface


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

        self.layout.addWidget(self.icon_label)
        self.layout.addWidget(self.title_label)

    def setSelected(self, selected: bool):
        pass

    def setCompacted(self, compacted: bool):
        pass


class PingWorker(QThread):
    result_signal = pyqtSignal(int)

    def __init__(self, api_engine):
        super().__init__()
        self.api_engine = api_engine

    def run(self):
        latency = self.api_engine.test_latency()
        self.result_signal.emit(latency)


class MonitorWorker(QThread):
    log_signal = pyqtSignal(str, str)
    countdown_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, api_engine, section_ids, interval, target_time_str, enable_waitlist=False):
        super().__init__()
        self.is_running = True
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.target_time_str = target_time_str
        self.enable_waitlist = enable_waitlist
        self._stop_event = threading.Event()

        self.api_engine.log_callback = lambda msg, level="normal": self.log_signal.emit(msg, level)

    def _sleep(self, seconds: float):
        self._stop_event.wait(seconds)

    def run(self):
        self._stop_event.clear()
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
        else:
            self.countdown_signal.emit(i18n.tr("msg_requesting"))

        if not self.is_running:
            self.finished_signal.emit(False)
            return

        self.log_signal.emit(i18n.tr("msg_target_sections", self.section_ids), "normal")

        attempt = 1
        success_flag = False
        consecutive_timeouts = 0

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
                    if "超时" in msg or "timeout" in msg.lower():
                        consecutive_timeouts += 1
                        if consecutive_timeouts <= 2:
                            self.log_signal.emit(i18n.tr("msg_timeout_retry", attempt), "error")
                            self._sleep(2)
                        else:
                            self.log_signal.emit(
                                i18n.tr("msg_consecutive_timeouts", consecutive_timeouts, self.interval),
                                "error")
                            self._sleep(self.interval)
                    else:
                        consecutive_timeouts = 0
                        self.log_signal.emit(i18n.tr("msg_round_failed", attempt, msg, self.interval), "error")
                        self._sleep(self.interval)

            except Exception as e:
                self.log_signal.emit(i18n.tr("msg_exception", str(e)), "error")
                self._sleep(self.interval)

            attempt += 1

        if not success_flag:
            self.log_signal.emit(i18n.tr("msg_manual_stop"), "normal")

        self.finished_signal.emit(success_flag)


class BrowserLaunchWorker(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, auth_manager):
        super().__init__()
        self.auth_manager = auth_manager

    def run(self):
        try:
            self.auth_manager.launch_browser()
            self.finished_signal.emit(True, i18n.tr("msg_browser_launched"))
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class MonitorInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("MonitorInterface")
        self.layout = QVBoxLayout(self)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.layout.setSpacing(16)

        self.api_engine = None
        self.sibling = None

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
        self.log_box.setStyleSheet("font-family: Consolas; font-size: 13px; background: #1e1e1e;")
        self.layout.addWidget(self.log_box, 1)

        self.btn_start.clicked.connect(self.start_attack)
        self.btn_stop.clicked.connect(self.stop_attack)
        self.btn_ping.clicked.connect(self.ping_server)

    def set_api_engine(self, engine):
        self.api_engine = engine
        self.log(i18n.tr("msg_engine_loaded"), "success")

    @property
    def is_running(self):
        return hasattr(self, 'worker') and self.worker.isRunning()

    def lock_start(self):
        self.btn_start.setEnabled(False)

    def unlock_start(self):
        if not self.is_running:
            self.btn_start.setEnabled(True)

    def log(self, msg, state="normal"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = ">> "
        if state == "error": prefix = "[ERROR] "
        if state == "success": prefix = "[SUCCESS] "

        self.log_box.append(f"[{timestamp}] {prefix}{msg}")
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def update_btn_text(self, text):
        self.btn_start.setText(text)

    def on_worker_finished(self, success):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_start.setText(i18n.tr("btn_start"))
        if self.sibling:
            self.sibling.unlock_start()

    def start_attack(self):
        if self.api_engine is None:
            InfoBar.error(i18n.tr("msg_engine_not_ready"), i18n.tr("msg_goto_auth"), duration=3000, parent=self)
            return

        if self.sibling and self.sibling.is_running:
            InfoBar.warning(
                "Busy",
                i18n.tr("msg_autocatch_busy"),
                duration=3000, parent=self)
            return

        raw_sections = self.section_input.text().strip()
        if not raw_sections:
            self.log(i18n.tr("msg_enter_sections"), "error")
            return

        section_list = [s.strip() for s in raw_sections.split(",") if s.strip()]

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

        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.is_running = False
            self.worker._stop_event.set()
            self.worker.quit()
            self.worker.wait(3000)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        waitlist_enabled = self.waitlist_switch.isChecked()
        self.worker = MonitorWorker(self.api_engine, section_list, interval_time, target_time_str,
                                    enable_waitlist=waitlist_enabled)
        self.worker.log_signal.connect(self.log)
        self.worker.countdown_signal.connect(self.update_btn_text)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

        if self.sibling:
            self.sibling.lock_start()

    def stop_attack(self):
        if hasattr(self, 'worker'):
            self.worker.is_running = False
            self.worker._stop_event.set()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.sibling:
            self.sibling.unlock_start()
        self.log(i18n.tr("msg_stopping"))

    def ping_server(self):
        if self.api_engine is None:
            InfoBar.error(i18n.tr("msg_engine_not_ready"), i18n.tr("msg_goto_auth"), duration=3000, parent=self)
            return

        self.btn_ping.setEnabled(False)
        self.ping_worker = PingWorker(self.api_engine)
        self.ping_worker.result_signal.connect(self.on_ping_result)
        self.ping_worker.start()

    def on_ping_result(self, latency):
        self.btn_ping.setEnabled(True)
        if latency >= 0:
            InfoBar.success(
                i18n.tr("msg_ping_title"),
                i18n.tr("msg_ping_result", f"{latency} ms"),
                duration=3000,
                parent=self
            )
        else:
            InfoBar.warning(
                i18n.tr("msg_ping_title"),
                i18n.tr("msg_ping_timeout"),
                duration=3000,
                parent=self
            )


class AuthInterface(QWidget):
    engine_ready_signal = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AuthInterface")
        self.layout = QVBoxLayout(self)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.auth_manager = KeanAuthManager()

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

        card_layout.addWidget(self.btn_login)
        card_layout.addWidget(self.btn_extract)

        self.layout.addWidget(card)
        self.layout.addStretch(1)

        self.btn_login.clicked.connect(self.handle_login)
        self.btn_extract.clicked.connect(self.handle_extract)

    def handle_login(self):
        self.btn_login.setEnabled(False)
        self.btn_extract.setEnabled(False)
        InfoBar.info(i18n.tr("msg_launching_browser"), i18n.tr("msg_please_wait"), duration=3000, parent=self)

        self.launch_worker = BrowserLaunchWorker(self.auth_manager)
        self.launch_worker.finished_signal.connect(self.on_browser_launched)
        self.launch_worker.start()

    def on_browser_launched(self, success, msg):
        if success:
            self.btn_extract.setEnabled(True)
            InfoBar.success(i18n.tr("msg_browser_ready"), i18n.tr("msg_browser_login_hint"), duration=5000, parent=self)
        else:
            self.btn_login.setEnabled(True)
            InfoBar.error(i18n.tr("msg_launch_failed"), i18n.tr("msg_browser_error", msg), duration=8000, parent=self)

    def handle_extract(self):
        credentials = self.auth_manager.extract_credentials()
        if "error" in credentials:
            InfoBar.error(i18n.tr("msg_extract_failed"), credentials["error"], duration=5000, parent=self)
            return

        student_id = credentials["student_id"]

        api_engine = KeanApiClient(
            cookies_dict=credentials["cookies"],
            verification_token=credentials["verification_token"],
            student_id=student_id
        )

        self.auth_manager.close_browser()

        self.btn_extract.setText(i18n.tr("msg_engine_ready", student_id))
        self.btn_extract.setEnabled(False)
        self.btn_login.setEnabled(False)

        self.engine_ready_signal.emit(api_engine)
        InfoBar.success(i18n.tr("msg_auth_success"), i18n.tr("msg_auth_success_detail", student_id),
                        position=InfoBarPosition.TOP, duration=5000, parent=self)


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
        ver_lbl = BodyLabel("Version 1.2 Stable")
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

        btn_github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Daozhu1007")))
        btn_bilibili.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://space.bilibili.com/477852567")))

        top_layout.addWidget(btn_github)
        top_layout.addWidget(btn_bilibili)
        self.layout.addWidget(top_card)

        # Language selector
        lang_card = CardWidget()
        lang_layout = QHBoxLayout(lang_card)
        lang_layout.setContentsMargins(20, 16, 20, 16)
        lang_layout.setSpacing(15)

        lang_label = BodyLabel(i18n.tr("set_lang"))
        lang_label.setStyleSheet("font-size: 14px;")
        self.lang_combo = ComboBox()
        self.lang_combo.addItem(i18n.tr("lang_zh"), userData="zh_CN")
        self.lang_combo.addItem(i18n.tr("lang_en"), userData="en_US")
        current_lang = i18n.locale
        self.lang_combo.setCurrentIndex(0 if current_lang == "zh_CN" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)

        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(self.lang_combo, 1)
        lang_layout.addStretch(1)
        self.layout.addWidget(lang_card)

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

    def _on_lang_changed(self, index):
        lang_code = self.lang_combo.itemData(index)
        if not lang_code:
            return
        config = load_config()
        if config.get("Settings", {}).get("Language") == lang_code:
            return
        config.setdefault("Settings", {})["Language"] = lang_code
        save_config(config)
        i18n.set_language(lang_code)
        InfoBar.success(
            i18n.tr("msg_lang_changed"),
            i18n.tr("msg_lang_restart"),
            duration=5000,
            parent=self
        )


class KeanSeatsCatcherApp(FluentWindow):
    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        qconfig.set(qconfig.themeMode, Theme.DARK)

        self.setWindowTitle("KeanSeatsCatcher")
        self.setWindowIcon(QIcon(os.path.join(data_dir, "assets", "logo.ico")))
        self.resize(1000, 650)
        self.setMinimumSize(900, 600)

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

        self.branding_widget = BrandingWidget(self)
        self.navigationInterface.addWidget(
            routeKey='branding',
            widget=self.branding_widget,
            onClick=None,
            position=NavigationItemPosition.TOP
        )

        self.monitor_interface = MonitorInterface(self)
        self.autocatch_interface = AutoCatchInterface(self)
        self.auth_interface = AuthInterface(self)
        self.about_interface = AboutInterface(self)

        self.auth_interface.engine_ready_signal.connect(self.monitor_interface.set_api_engine)
        self.auth_interface.engine_ready_signal.connect(self.autocatch_interface.set_api_engine)
        self.autocatch_interface.set_auth_manager(self.auth_interface.auth_manager)

        self.monitor_interface.sibling = self.autocatch_interface
        self.autocatch_interface.sibling = self.monitor_interface

        self.addSubInterface(self.monitor_interface, FIF.VIEW, i18n.tr("tab_monitor"))
        self.addSubInterface(self.autocatch_interface, FIF.ZOOM, i18n.tr("tab_autocatch"))
        self.addSubInterface(self.auth_interface, FIF.SETTING, i18n.tr("tab_auth"))
        self.addSubInterface(self.about_interface, FIF.INFO, i18n.tr("tab_about"), position=NavigationItemPosition.BOTTOM)

        self.navigationInterface.expand()


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
