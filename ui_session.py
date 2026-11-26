import time
from datetime import datetime
from math import ceil, isfinite

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, StrongBodyLabel, VerticalSeparator

from i18n import i18n
from registration_state import SessionStatus


SESSION_STYLES = {
    None: ("session_unverified", "#8a8a8a"),
    SessionStatus.VALID: ("session_alive", "#6ccb5f"),
    SessionStatus.EXPIRED: ("session_expired", "#ff99a4"),
    SessionStatus.UNKNOWN: ("session_unknown", "#fce100"),
}

OUTCOME_LABELS = {
    "valid": "session_result_valid",
    "expired": "session_result_expired",
    "unknown": "session_result_unknown",
    "expired_deferred": "session_result_deferred",
    "manual_required": "session_result_failed",
    "recovered": "session_result_recovered",
    "superseded": "session_result_discarded",
}


class SessionSummary(QWidget):
    def __init__(self):
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(0)
        center = Qt.AlignmentFlag.AlignVCenter

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.status_dot = QWidget()
        self.status_dot.setFixedSize(8, 8)
        self.status_label = StrongBodyLabel()
        status_font = self.status_label.font()
        status_font.setPixelSize(13)
        self.status_label.setFont(status_font)
        status_row.addWidget(self.status_dot, 0, center)
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row, 0, 0)
        self.automatic_label = self._caption()
        self.automatic_label.setAccessibleDescription(i18n.tr("session_keepalive"))
        layout.addWidget(self.automatic_label, 1, 0)

        divider = VerticalSeparator()
        divider.setFixedHeight(24)
        layout.addWidget(divider, 0, 1, 2, 1, center)

        self.last_check_label = CaptionLabel()
        self.last_check_label.setMinimumWidth(104)
        self.last_check_label.setAccessibleDescription(i18n.tr("session_last_check"))
        self.last_keepalive_label = CaptionLabel()
        self.last_keepalive_label.setMinimumWidth(112)
        self.last_keepalive_label.setAccessibleDescription(i18n.tr("session_last_keepalive"))
        layout.addWidget(self._caption("session_last_check"), 0, 2, center)
        layout.addWidget(self.last_check_label, 1, 2)
        layout.addWidget(self._caption("session_last_keepalive"), 0, 3, center)
        layout.addWidget(self.last_keepalive_label, 1, 3)

    @staticmethod
    def _caption(key=None):
        label = CaptionLabel(i18n.tr(key) if key else "")
        label.setTextColor("#616161", "#a8a8a8")
        return label

    def update_status(self, enabled, minutes, snapshot):
        status = snapshot.session.status if snapshot else None
        key, color = SESSION_STYLES[status]
        text = i18n.tr(key)
        if self.status_label.text() != text:
            self.status_label.setText(text)
            self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")

        now = datetime.now().astimezone()
        self._update_automatic(enabled, minutes, snapshot, now)
        self._update_history(self.last_check_label, snapshot.last_check if snapshot else None,
                             "session_last_check", now)
        self._update_history(self.last_keepalive_label, snapshot.last_keepalive if snapshot else None,
                             "session_last_keepalive", now)

    def _update_automatic(self, enabled, minutes, snapshot, now):
        state = snapshot.state if snapshot else ("waiting" if enabled else "disabled")
        automatic_on = state != "disabled"
        setting = i18n.tr("settings_keepalive_on" if automatic_on else "keepalive_state_disabled")
        text = i18n.tr("session_auto_on" if automatic_on else "session_auto_off")
        tooltip = [f"{i18n.tr('session_keepalive')} · {setting}"]
        if automatic_on:
            tooltip.append(f"{i18n.tr('session_interval')} · {i18n.tr('session_minutes', minutes)}")

        deadline = snapshot.next_due if snapshot else None
        if deadline is not None and isfinite(deadline):
            remaining = max(0, deadline - time.monotonic())
            hours, seconds = divmod(ceil(remaining), 3600)
            minutes, seconds = divmod(seconds, 60)
            clock = f"{minutes:02d}:{seconds:02d}"
            if hours >= 24:
                days = hours // 24
                clock = i18n.tr("session_countdown_days", days if days <= 99 else "99+")
            elif hours:
                clock = f"{hours}:{clock}"
            if state == "cooldown":
                text = i18n.tr("session_cooldown_countdown", clock)
                tooltip.append(i18n.tr("keepalive_state_cooldown"))
            else:
                key = "session_retry_countdown" if state == "retry" else "session_next_countdown"
                text = f"{text} · {i18n.tr(key, clock)}"
            try:
                due = datetime.fromtimestamp(now.timestamp() + remaining).astimezone()
                tooltip.append(f"{i18n.tr('session_next_keepalive')} · {due:%Y-%m-%d %H:%M:%S %z}")
            except (ValueError, OverflowError, OSError):
                pass
        elif automatic_on:
            detail_key = {
                "waiting": "keepalive_state_waiting",
                "checking": "session_auto_paused",
                "busy": "session_auto_paused",
                "running": "session_recovering" if snapshot and snapshot.recovering else "session_auto_running",
                "manual_required": "keepalive_state_manual_required",
            }.get(state)
            if detail_key:
                text = f"{text} · {i18n.tr(detail_key)}"

        self.automatic_label.setText(text)
        self.automatic_label.setToolTip("\n".join(tooltip))

    @staticmethod
    def _update_history(label, result, title, now):
        if result is None:
            label.setText("—")
            label.setToolTip("")
            return

        key = OUTCOME_LABELS[result.outcome]
        if title == "session_last_keepalive" and result.outcome == "valid":
            key = "session_result_completed"
        outcome = i18n.tr(key)
        when = datetime.fromtimestamp(result.completed_at).astimezone()
        timestamp = when.strftime("%H:%M:%S" if when.date() == now.date() else "%m-%d %H:%M")
        label.setText(f"{outcome} · {timestamp}")
        label.setToolTip(f"{i18n.tr(title)} · {outcome}\n{when:%Y-%m-%d %H:%M:%S %z}")
