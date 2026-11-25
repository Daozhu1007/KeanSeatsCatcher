import time
from datetime import datetime

from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF, PushButton, StrongBodyLabel

from i18n import i18n


class SessionStatusCard(CardWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel(i18n.tr("session_card_title")))

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(22)
        metrics.setVerticalSpacing(2)
        self.values = {}
        for index, key in enumerate(("keepalive", "status", "interval", "last_check", "next_check", "last_renewal")):
            row, column = divmod(index, 3)
            metrics.addWidget(CaptionLabel(i18n.tr(f"session_{key}")), row * 3, column)
            value = BodyLabel("—")
            metrics.addWidget(value, row * 3 + 1, column)
            metrics.setColumnStretch(column, 1)
            self.values[key] = value
        metrics.setRowMinimumHeight(2, 5)
        layout.addLayout(metrics)

        actions = QHBoxLayout()
        actions.addStretch()
        self.btn_check = PushButton(FIF.SYNC, i18n.tr("btn_ping"))
        self.btn_keepalive = PushButton(FIF.PLAY, i18n.tr("btn_keepalive_now"))
        actions.addWidget(self.btn_check)
        actions.addWidget(self.btn_keepalive)
        layout.addLayout(actions)

    @staticmethod
    def _format_time(value):
        if value is not None:
            try:
                return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OverflowError, OSError):
                pass
        return "—"

    def update_status(self, enabled, minutes, snapshot=None):
        state = snapshot.state if snapshot else ("waiting" if enabled else "disabled")
        session = snapshot.session if snapshot else None
        if snapshot:
            minutes = f"{snapshot.interval / 60:g}"
        status = session.status.value.lower() if session and session.status is not None else "unchecked"
        now = time.monotonic()
        next_check = (time.time() + snapshot.next_due - now
                      if snapshot and snapshot.next_due is not None else None)
        values = (
            i18n.tr(f"keepalive_state_{state}"),
            i18n.tr(f"session_state_{status}"),
            i18n.tr("session_minutes", minutes),
            self._format_time(session.last_successful_check_at if session else None),
            self._format_time(next_check),
            self._format_time(session.last_auth_cookie_renewal_at if session else None),
        )
        for value, label in zip(values, self.values.values()):
            label.setText(value)

        action = snapshot.action if snapshot else None
        self.btn_check.setText(i18n.tr("session_checking" if action == "check" else "btn_ping"))
        self.btn_keepalive.setText(i18n.tr("session_checking" if action == "keepalive" else "btn_keepalive_now"))
        self.btn_check.setEnabled(bool(snapshot and snapshot.can_check))
        self.btn_keepalive.setEnabled(bool(snapshot and snapshot.can_keepalive))
        cooling_down = session and now < session.cooldown_until
        self.btn_check.setToolTip(i18n.tr("keepalive_state_cooldown" if cooling_down else "tooltip_ping"))
        self.btn_keepalive.setToolTip(i18n.tr("keepalive_state_cooldown" if cooling_down else "tooltip_keepalive_now"))
