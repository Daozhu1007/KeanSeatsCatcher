import time
from datetime import datetime

from PyQt6.QtCore import QAbstractAnimation, QEvent, QPoint, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QGridLayout, QVBoxLayout, QWidget
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


class SessionStatusFlyout(Flyout):
    def __init__(self, view, parent):
        super().__init__(view, parent)
        # Let an outside click reach its target while the flyout is still fading out.
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def show_at(self, anchor):
        self.setWindowOpacity(0)
        self.exec(anchor, FlyoutAnimationType.DROP_DOWN)
        self.aniManager.setParent(self)
        self.aniManager.aniGroup.finished.connect(self._finish_animation)
        self.aniManager.opacityAni.valueChanged.connect(self.parentWidget().update)
        QApplication.instance().installEventFilter(self)

    def showEvent(self, event):
        # The read-only flyout can leave keyboard focus on the Dashboard.
        QWidget.showEvent(self, event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def toggle(self):
        self._set_expanded(self.aniManager.aniGroup.direction() == QAbstractAnimation.Direction.Backward)

    def _set_expanded(self, expanded):
        animation = self.aniManager.aniGroup
        direction = (QAbstractAnimation.Direction.Forward if expanded
                     else QAbstractAnimation.Direction.Backward)
        # Changing direction preserves the current time of a running animation.
        animation.setDirection(direction)
        animation.start()

    def _finish_animation(self):
        if self.aniManager.aniGroup.direction() == QAbstractAnimation.Direction.Backward:
            self.close()

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type == QEvent.Type.Hide and watched is self.parentWidget().window():
            self.close()
        elif event_type == QEvent.Type.ApplicationDeactivate:
            self._set_expanded(False)
        elif event_type == QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Cancel):
            self._set_expanded(False)
            return True
        elif event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                            QEvent.Type.NonClientAreaMouseButtonPress, QEvent.Type.NonClientAreaMouseButtonDblClick):
            point = event.globalPosition().toPoint()
            trigger = self.parentWidget()
            on_trigger = (event.button() == Qt.MouseButton.LeftButton and trigger.isVisible() and
                          trigger.rect().contains(trigger.mapFromGlobal(point)))
            if on_trigger and isinstance(watched, QWidget) and watched.window() is self:
                # The flyout's shadow can overlap the bottom of the trigger.
                self.toggle()
                return True
            if not on_trigger and not self.geometry().contains(point):
                self._set_expanded(False)
        return super().eventFilter(watched, event)


class SessionStatusButton(TransparentDropDownPushButton):
    def __init__(self):
        super().__init__()
        self._details = {}
        self._flyout = None
        self.clicked.connect(self._toggle_flyout)

    def _toggle_flyout(self):
        if self._flyout is not None:
            self._flyout.toggle()
            return

        view = SessionStatusView()
        view.set_details(self._details)
        flyout = SessionStatusFlyout(view, self)
        self._flyout = flyout
        flyout.closed.connect(self._clear_flyout)
        right_margin = flyout.layout().contentsMargins().right()
        offset = self.width() + right_margin - flyout.sizeHint().width()
        anchor = self.mapToGlobal(QPoint(offset, self.height()))
        flyout.show_at(anchor)

    def _clear_flyout(self):
        self._flyout = None
        self.update()

    def _drawDropDownIcon(self, painter, rect):
        angle = 180 * self._flyout.windowOpacity() if self._flyout is not None else 0
        painter.save()
        painter.translate(rect.center())
        painter.rotate(angle)
        painter.translate(-rect.center())
        super()._drawDropDownIcon(painter, rect)
        painter.restore()

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
