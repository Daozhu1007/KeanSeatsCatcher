import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (SubtitleLabel, BodyLabel, LineEdit,
                            PushButton, TextEdit, CardWidget,
                            SwitchButton,
                            FluentIcon as FIF, InfoBar, InfoBarPosition)

from i18n import i18n
from auto_catch_worker import AutoCatchWorker, STATE_IDLE, STATE_POLLING, STATE_ATTACKING


class AutoCatchInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AutoCatchInterface")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 32, 24, 24)
        self.layout.setSpacing(16)

        self.api_engine = None
        self.auth_manager = None
        self.sibling = None
        self._is_running = False

        title = SubtitleLabel(i18n.tr("autocatch_title"))
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

        drop_row = QHBoxLayout()
        drop_row.addWidget(BodyLabel(i18n.tr("lbl_drop_section_id")))
        self.drop_section_input = LineEdit()
        self.drop_section_input.setPlaceholderText(i18n.tr("placeholder_drop_section_id"))
        drop_row.addWidget(self.drop_section_input, 1)
        card_layout.addLayout(drop_row)

        input_row2 = QHBoxLayout()
        input_row2.addWidget(BodyLabel(i18n.tr("lbl_interval")))
        self.interval_input = LineEdit()
        self.interval_input.setPlaceholderText(i18n.tr("placeholder_interval_autocatch"))
        self.interval_input.setText("15")
        self.interval_input.setFixedWidth(80)
        input_row2.addWidget(self.interval_input)
        input_row2.addStretch(1)
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
        self.btn_start = PushButton(FIF.PLAY, i18n.tr("btn_start_monitoring"))
        self.btn_stop = PushButton(FIF.PAUSE, i18n.tr("btn_stop"))
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)

        self.layout.addWidget(card)

        self.status_label = BodyLabel(i18n.tr("autocatch_status_idle"))
        self.status_label.setStyleSheet("color: #a0a0a0; margin-top: 4px;")
        self.layout.addWidget(self.status_label)

        log_title = BodyLabel(i18n.tr("lbl_log"))
        log_title.setStyleSheet("color: #a0a0a0;")
        self.layout.addWidget(log_title)

        self.log_box = TextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.document().setMaximumBlockCount(1000)
        self.log_box.setStyleSheet("font-family: Consolas; font-size: 13px; background: #1e1e1e;")
        self.layout.addWidget(self.log_box, 1)

        self.btn_start.clicked.connect(self.start_monitoring)
        self.btn_stop.clicked.connect(self.stop_monitoring)

    @property
    def is_running(self):
        return self._is_running

    def set_api_engine(self, engine):
        self.api_engine = engine
        self.log(i18n.tr("msg_engine_loaded"), "success")

    def set_auth_manager(self, auth_manager):
        self.auth_manager = auth_manager

    def lock_start(self):
        self.btn_start.setEnabled(False)

    def unlock_start(self):
        if not self._is_running:
            self.btn_start.setEnabled(True)

    def log(self, msg, state="normal"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = ">> "
        if state == "error":
            prefix = "[ERROR] "
        if state == "success":
            prefix = "[SUCCESS] "
        self.log_box.append(f"[{timestamp}] {prefix}{msg}")
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())

    def start_monitoring(self):
        if self.api_engine is None:
            InfoBar.error(
                i18n.tr("msg_engine_not_ready"),
                i18n.tr("msg_goto_auth"),
                duration=3000, parent=self)
            return

        if self.sibling and self.sibling.is_running:
            InfoBar.warning(
                "Busy",
                i18n.tr("msg_monitor_busy"),
                duration=3000, parent=self)
            return

        raw_sections = self.section_input.text().strip()
        if not raw_sections:
            self.log(i18n.tr("msg_enter_sections"), "error")
            return

        section_list = [s.strip() for s in raw_sections.split(",") if s.strip()]

        try:
            interval_time = int(self.interval_input.text() or 15)
        except (ValueError, TypeError):
            interval_time = 15
            self.interval_input.setText("15")

        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.is_running = False
            self.worker._stop_event.set()
            self.worker.quit()
            self.worker.wait(3000)

        self._is_running = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.section_input.setEnabled(False)
        self.interval_input.setEnabled(False)
        self.waitlist_switch.setEnabled(False)
        self.drop_section_input.setEnabled(False)

        if self.sibling:
            self.sibling.lock_start()

        waitlist_enabled = self.waitlist_switch.isChecked()
        drop_section_id = self.drop_section_input.text().strip()
        self.worker = AutoCatchWorker(
            self.api_engine, section_list, interval_time,
            enable_waitlist=waitlist_enabled,
            auth_manager=self.auth_manager,
            drop_section_id=drop_section_id)
        self.worker.log_signal.connect(self.log)
        self.worker.status_signal.connect(self.on_status)
        self.worker.seat_found_signal.connect(self.on_seat_found)
        self.worker.attack_result_signal.connect(self.on_attack_result)
        self.worker.round_signal.connect(self.on_round)
        self.worker.session_expired_signal.connect(self.on_session_expired)
        self.worker.session_recovered_signal.connect(self.on_session_recovered)
        self.worker.start()

    def stop_monitoring(self):
        if hasattr(self, 'worker'):
            self.worker.is_running = False
            self.worker._stop_event.set()
        self.btn_stop.setEnabled(False)
        self.status_label.setText(i18n.tr("autocatch_stopping"))

    def on_status(self, status):
        if status == STATE_IDLE:
            self._is_running = False
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.section_input.setEnabled(True)
            self.interval_input.setEnabled(True)
            self.waitlist_switch.setEnabled(True)
            self.drop_section_input.setEnabled(True)
            self.status_label.setText(i18n.tr("autocatch_status_idle"))
            if self.sibling:
                self.sibling.unlock_start()
        elif status == STATE_POLLING:
            pass
        elif status == STATE_ATTACKING:
            self.status_label.setText(i18n.tr("autocatch_status_attacking"))

    def on_seat_found(self, section_code, available):
        self._show_toast(
            i18n.tr("toast_seat_found_title"),
            i18n.tr("toast_seat_found_body", section_code, available))

    def on_attack_result(self, success, msg):
        if success:
            self.log(msg, "success")
            self._show_toast(
                i18n.tr("toast_success_title"),
                i18n.tr("toast_success_body"))
        else:
            self.log(msg, "error")

    def on_round(self, round_count, recovery_count):
        self.status_label.setText(
            i18n.tr("autocatch_status_polling", round_count, recovery_count))

    def on_session_expired(self):
        self.status_label.setText(i18n.tr("autocatch_status_recovering"))
        self.status_label.setStyleSheet("color: #e6a23c; margin-top: 4px;")

    def on_session_recovered(self, success):
        if success:
            self.status_label.setStyleSheet("color: #a0a0a0; margin-top: 4px;")
        else:
            self.status_label.setText(i18n.tr("autocatch_status_idle"))
            self.status_label.setStyleSheet("color: #a0a0a0; margin-top: 4px;")

    def _show_toast(self, title, msg):
        try:
            from winotify import Notification
            toast = Notification(
                app_id="wku.mingtai.keanseatscatcher.v2",
                title=title,
                msg=msg,
                duration="short"
            )
            toast.show()
        except Exception:
            pass
