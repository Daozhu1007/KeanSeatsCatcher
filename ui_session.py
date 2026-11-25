import time
from datetime import datetime

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QGridLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CaptionLabel, Flyout, FlyoutAnimationType, FlyoutViewBase,
    StrongBodyLabel, TransparentDropDownPushButton,
)

from i18n import i18n


class SessionStatusView(FlyoutViewBase):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(408)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel(i18n.tr("session_status_title")))

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(24)
        metrics.setVerticalSpacing(6)
        metrics.setColumnStretch(1, 1)
        self.values = {}
        fields = ("keepalive", "status", "interval", "last_check", "next_check", "last_renewal")
        for row, key in enumerate(fields):
            metrics.addWidget(CaptionLabel(i18n.tr(f"session_{key}")), row, 0)
            value = BodyLabel("—")
            metrics.addWidget(value, row, 1)
            self.values[key] = value
        layout.addLayout(metrics)

    def set_details(self, details):
        for key, text in details.items():
            self.values[key].setText(text)


class SessionStatusButton(TransparentDropDownPushButton):
    def __init__(self):
        super().__init__()
        self._details = {}
        self._flyout = None
        self.clicked.connect(self._show_flyout)

    def _show_flyout(self):
        if self._flyout is not None:
            return

        view = SessionStatusView()
        view.set_details(self._details)
        flyout = Flyout(view, self)
        self._flyout = flyout
        flyout.closed.connect(self._clear_flyout)
        right_margin = flyout.layout().contentsMargins().right()
        offset = self.width() + right_margin - flyout.sizeHint().width()
        anchor = self.mapToGlobal(QPoint(offset, self.height()))
        flyout.exec(anchor, FlyoutAnimationType.DROP_DOWN)

    def _clear_flyout(self):
        self._flyout = None

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
        status_text = i18n.tr(f"session_state_{status}")
        summary = i18n.tr("session_checking") if snapshot and snapshot.action else status_text
        self.setText(i18n.tr("session_status_summary", summary))

        next_check = (time.time() + snapshot.next_due - time.monotonic()
                      if snapshot and snapshot.next_due is not None else None)
        self._details = {
            "keepalive": i18n.tr(f"keepalive_state_{state}"),
            "status": status_text,
            "interval": i18n.tr("session_minutes", minutes),
            "last_check": self._format_time(session.last_successful_check_at if session else None),
            "next_check": self._format_time(next_check),
            "last_renewal": self._format_time(session.last_auth_cookie_renewal_at if session else None),
        }
        if self._flyout is not None:
            self._flyout.view.set_details(self._details)
