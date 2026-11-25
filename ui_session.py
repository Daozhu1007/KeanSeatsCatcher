import time
from datetime import datetime
from math import ceil, isfinite

from PyQt6.QtCore import QAbstractAnimation, QEvent, QPoint, Qt
from PyQt6.QtGui import QColor, QKeySequence
from PyQt6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, Flyout, FlyoutAnimationType, FlyoutViewBase,
    IconWidget, InfoBarIcon, ProgressBar, StrongBodyLabel, SubtitleLabel,
    TransparentDropDownPushButton,
)

from i18n import i18n


STATUS_ICONS = {
    "valid": InfoBarIcon.SUCCESS,
    "unknown": InfoBarIcon.WARNING,
    "expired": InfoBarIcon.ERROR,
}


class SessionStatusView(FlyoutViewBase):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(408)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QGridLayout()
        header.setHorizontalSpacing(16)
        header.setVerticalSpacing(4)
        header.setColumnStretch(0, 1)
        right = Qt.AlignmentFlag.AlignRight
        header.addWidget(CaptionLabel(i18n.tr("session_status_title")), 0, 0)
        header.addWidget(CaptionLabel(i18n.tr("session_keepalive")), 0, 1, right)
        self.status_icon = IconWidget(InfoBarIcon.INFORMATION)
        self.status_icon.setFixedSize(16, 16)
        self.values = {
            "status": SubtitleLabel("—"),
            "keepalive": StrongBodyLabel("—"),
            "next_check": StrongBodyLabel("—"),
        }
        headline = QHBoxLayout()
        headline.setSpacing(8)
        headline.addWidget(self.status_icon)
        headline.addWidget(self.values["status"], 1)
        header.addLayout(headline, 1, 0)
        header.addWidget(self.values["keepalive"], 1, 1, right | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        countdown = QVBoxLayout()
        countdown.setSpacing(4)
        next_check = QHBoxLayout()
        next_check.addWidget(CaptionLabel(i18n.tr("session_next_check")), 1)
        next_check.addWidget(self.values["next_check"])
        countdown.addLayout(next_check)
        self.progress = ProgressBar(useAni=False)
        self.progress.setRange(0, 1000)
        self.progress.setAccessibleName(i18n.tr("session_next_check"))
        self.progress.setCustomBackgroundColor(QColor(0, 0, 0, 36), QColor(255, 255, 255, 48))
        policy = self.progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(policy)
        self.progress.hide()
        countdown.addWidget(self.progress)
        layout.addLayout(countdown)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(24)
        metrics.setVerticalSpacing(6)
        metrics.setColumnStretch(1, 1)
        fields = ("interval", "last_check", "last_renewal")
        for row, key in enumerate(fields):
            metrics.addWidget(CaptionLabel(i18n.tr(f"session_{key}")), row, 0)
            value = BodyLabel("—")
            value.setAlignment(right | Qt.AlignmentFlag.AlignVCenter)
            metrics.addWidget(value, row, 1)
            self.values[key] = value
        layout.addLayout(metrics)

    def update_status(self, status, status_text, keepalive, minutes, snapshot):
        self.values["status"].setText(i18n.tr("session_alive") if status == "valid" else status_text)
        icon = STATUS_ICONS.get(status, InfoBarIcon.INFORMATION)
        self.status_icon.setIcon(icon)

        if keepalive == "disabled":
            automatic = i18n.tr("keepalive_state_disabled")
        else:
            automatic = i18n.tr("settings_keepalive_on")
            if keepalive not in ("scheduled", "checking"):
                automatic = i18n.tr("keepalive_state_detail", automatic, i18n.tr(f"keepalive_state_{keepalive}"))
        self.values["keepalive"].setText(automatic)
        self.values["interval"].setText(i18n.tr("session_minutes", minutes))

        now = time.time()
        self._update_countdown(snapshot, now, time.monotonic())
        session = snapshot.session if snapshot else None
        self._update_past_time("last_check", session.last_successful_check_at if session else None, now)
        self._update_past_time("last_renewal", session.last_auth_cookie_renewal_at if session else None, now)

    def _update_countdown(self, snapshot, now, monotonic_now):
        deadline = snapshot.next_due if snapshot else None
        counting_down = deadline is not None and isfinite(deadline)
        self.progress.setVisible(counting_down)
        label = self.values["next_check"]
        if not counting_down:
            label.setText("—")
            label.setToolTip("")
            return

        remaining = max(0, deadline - monotonic_now)
        hours, seconds = divmod(ceil(remaining), 3600)
        minutes, seconds = divmod(seconds, 60)
        clock = f"{minutes:02d}:{seconds:02d}"
        label.setText(f"{hours}:{clock}" if hours else clock)
        label.setToolTip(self._format_time(now + deadline - monotonic_now))

        duration = deadline - snapshot.scheduled_from
        fraction = 1 - min(1, remaining / duration) if duration > 0 else 1
        self.progress.setValue(round(fraction * self.progress.maximum()))

    @staticmethod
    def _format_time(value):
        if value is not None:
            try:
                return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            except (ValueError, OverflowError, OSError):
                pass
        return ""

    def _update_past_time(self, key, value, now):
        label = self.values[key]
        full_time = self._format_time(value)
        label.setToolTip(full_time)
        if not full_time:
            label.setText("—")
            return
        elapsed = int(now - value)
        for unit, seconds in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1)):
            if elapsed >= max(seconds, 5):
                label.setText(i18n.tr(f"session_time_{unit}_ago", elapsed // seconds))
                return
        label.setText(i18n.tr("session_time_just_now"))


class SessionStatusFlyout(Flyout):
    def __init__(self, view, parent):
        super().__init__(view, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
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
        self._details = None
        self._flyout = None
        self.clicked.connect(self._toggle_flyout)

    def _toggle_flyout(self):
        if self._flyout is not None:
            self._flyout.toggle()
            return

        view = SessionStatusView()
        if self._details is not None:
            view.update_status(*self._details)
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

    def update_status(self, enabled, minutes, snapshot=None):
        state = snapshot.state if snapshot else ("waiting" if enabled else "disabled")
        status = "unchecked"
        if snapshot:
            minutes = f"{snapshot.interval / 60:g}"
            if snapshot.recovering:
                status = "recovering"
            elif snapshot.action:
                status = "checking"
            elif snapshot.session.status is not None:
                status = snapshot.session.status.value.lower()
        status_text = i18n.tr("session_checking" if status == "checking" else f"session_state_{status}")
        self.setText(i18n.tr("session_status_summary", status_text))

        self._details = status, status_text, state, minutes, snapshot
        if self._flyout is not None:
            self._flyout.view.update_status(*self._details)
