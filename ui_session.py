from datetime import datetime
from math import ceil, isfinite

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    CaptionLabel, FluentIcon as FIF, IconWidget, ProgressBar, SimpleCardWidget,
    StrongBodyLabel, SubtitleLabel, VerticalSeparator,
)

from i18n import i18n
from registration_state import SessionStatus


MUTED = ("#595959", "#adadad")
SUCCESS = ("#17643c", "#a0d7af")
WARNING = ("#7b4c0d", "#efca83")
ERROR = ("#a22732", "#f4a5af")
INFORMATION = ("#225f79", "#9ac9db")

SESSION_STYLES = {
    None: ("session_unverified", FIF.FINGERPRINT, MUTED),
    SessionStatus.VALID: ("session_alive", FIF.COMPLETED, SUCCESS),
    SessionStatus.EXPIRED: ("session_expired", FIF.CLOSE, ERROR),
    SessionStatus.UNKNOWN: ("session_unknown", FIF.QUESTION, WARNING),
}

OUTCOME_STYLES = {
    "valid": ("session_result_valid", SUCCESS),
    "expired": ("session_result_expired", ERROR),
    "unknown": ("session_result_unknown", WARNING),
    "expired_deferred": ("session_result_deferred", WARNING),
    "manual_required": ("session_result_failed", ERROR),
    "completed": ("session_result_completed", INFORMATION),
    "recovered": ("session_result_recovered", SUCCESS),
    "superseded": ("session_result_discarded", MUTED),
}

AUTOMATIC_LABELS = {
    "disabled": "session_auto_off",
    "waiting": "session_auto_waiting",
    "checking": "session_auto_paused",
    "busy": "session_auto_paused",
    "running": "session_auto_running",
    "recovering": "session_auto_recovering",
    "cooldown": "keepalive_state_cooldown",
    "manual_required": "session_auto_sign_in",
}

COUNTDOWN_LABELS = {
    "scheduled": "session_next_countdown",
    "retry": "session_retry_countdown",
    "cooldown": "session_cooldown_countdown",
}

AGE_UNITS = (("days", 86400, "99+"), ("hours", 3600, 23),
             ("minutes", 60, 59), ("seconds", 1, 59))


class SessionSummary(SimpleCardWidget):
    def __init__(self):
        super().__init__()
        self.setBorderRadius(8)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)
        center = Qt.AlignmentFlag.AlignVCenter

        self.status_tile = QFrame()
        self.status_tile.setFixedSize(28, 28)
        tile_layout = QHBoxLayout(self.status_tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        self.status_icon = IconWidget()
        self.status_icon.setFixedSize(16, 16)
        tile_layout.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_tile, 0, center)

        live = QVBoxLayout()
        live.setSpacing(0)
        self.status_label = StrongBodyLabel()
        self.automatic_label = self._caption()
        self.automatic_label.setAccessibleDescription(i18n.tr("session_keepalive"))
        self._reserve_width(self.status_label, [i18n.tr(key) for key, _, _ in SESSION_STYLES.values()])
        captions = [i18n.tr(key) for key in (*AUTOMATIC_LABELS.values(), "session_auto_on")]
        captions.extend(i18n.tr(key, "88:88:88") for key in COUNTDOWN_LABELS.values())
        self._reserve_width(self.automatic_label, captions)
        live.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignLeft)
        live.addWidget(self.automatic_label, 0, Qt.AlignmentFlag.AlignLeft)
        live.addSpacing(3)
        self.countdown_progress = ProgressBar(useAni=False)
        self.countdown_progress.setFixedHeight(2)
        self.countdown_progress.setRange(0, 1000)
        self.countdown_progress.setAccessibleName(i18n.tr("session_next_keepalive"))
        self.countdown_progress.setCustomBarColor(*INFORMATION)
        self.countdown_progress.setCustomBackgroundColor(QColor(0, 0, 0, 40), QColor(255, 255, 255, 45))
        policy = self.countdown_progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.countdown_progress.setSizePolicy(policy)
        self.countdown_progress.hide()
        live.addWidget(self.countdown_progress)
        layout.addLayout(live)

        divider = VerticalSeparator()
        divider.setFixedHeight(28)
        layout.addWidget(divider, 0, center)

        activity = QGridLayout()
        activity.setHorizontalSpacing(8)
        activity.setVerticalSpacing(3)
        activity.setAlignment(center)
        self.last_check_label, self.last_check_age = self._add_record(activity, 0, "session_last_check")
        self.last_keepalive_label, self.last_keepalive_age = self._add_record(activity, 1, "session_last_keepalive")
        layout.addLayout(activity)

    @staticmethod
    def _caption(text=""):
        label = CaptionLabel(text)
        label.setTextColor(*MUTED)
        return label

    @staticmethod
    def _reserve_width(label, texts):
        # Size to the longest translated value so status updates keep the same geometry.
        metrics = label.fontMetrics()
        label.setFixedWidth(max(metrics.horizontalAdvance(text) for text in texts) + 1)

    def _add_record(self, layout, row, title):
        caption = self._caption(i18n.tr(title))
        outcome = StrongBodyLabel()
        outcome.pixelFontSize = 12
        outcome.setAccessibleDescription(i18n.tr(title))
        self._reserve_width(outcome, [i18n.tr(key) for key, _ in OUTCOME_STYLES.values()])
        age = self._caption()
        age.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        age.setAccessibleDescription(i18n.tr(title))
        ages = [i18n.tr("session_time_just_now")]
        ages.extend(i18n.tr(f"session_time_{unit}_ago", sample) for unit, _, sample in AGE_UNITS)
        self._reserve_width(age, ages)
        layout.addWidget(caption, row, 0)
        layout.addWidget(outcome, row, 1)
        layout.addWidget(age, row, 2)
        return outcome, age

    def update_status(self, enabled, minutes, snapshot, now, monotonic_now):
        status = snapshot.session.status if snapshot else None
        key, icon, colors = SESSION_STYLES[status]
        text = i18n.tr(key)
        if self.status_label.text() != text:
            self.status_label.setText(text)
            self.status_icon.setIcon(icon.colored(*colors))
            tint = QColor(colors[1])
            tint.setAlpha(35)
            self.status_tile.setStyleSheet(
                f"QFrame {{ background-color: {tint.name(QColor.NameFormat.HexArgb)}; border-radius: 7px; }}"
            )

        self._update_automatic(enabled, minutes, snapshot, now, monotonic_now)
        self._update_history(self.last_check_label, self.last_check_age, snapshot.last_check if snapshot else None,
                             "session_last_check", now)
        self._update_history(self.last_keepalive_label, self.last_keepalive_age,
                             snapshot.last_keepalive if snapshot else None, "session_last_keepalive", now)

    def _update_automatic(self, enabled, minutes, snapshot, now, monotonic_now):
        state = snapshot.state if snapshot else ("waiting" if enabled else "disabled")
        if state == "running" and snapshot.recovering:
            state = "recovering"
        automatic_on = state != "disabled"
        setting = i18n.tr("settings_keepalive_on" if automatic_on else "keepalive_state_disabled")
        text = i18n.tr(AUTOMATIC_LABELS.get(state, "session_auto_on"))
        tooltip = [f"{i18n.tr('session_keepalive')} · {setting}"]
        if automatic_on:
            tooltip.append(f"{i18n.tr('session_interval')} · {i18n.tr('session_minutes', minutes)}")

        deadline = snapshot.next_due if snapshot else None
        counting_down = deadline is not None and isfinite(deadline)
        self.countdown_progress.setVisible(counting_down)
        if counting_down:
            remaining = max(0, deadline - monotonic_now)
            clock = self._countdown_text(remaining)
            text = i18n.tr(COUNTDOWN_LABELS.get(state, "session_next_countdown"), clock)
            if state == "cooldown":
                tooltip.append(i18n.tr("keepalive_state_cooldown"))
            try:
                due = datetime.fromtimestamp(now.timestamp() + remaining).astimezone()
                tooltip.append(f"{i18n.tr('session_next_keepalive')} · {due:%Y-%m-%d %H:%M:%S %z}")
            except (ValueError, OverflowError, OSError):
                pass
            start = snapshot.scheduled_from
            duration = deadline - start if start is not None else 0
            elapsed = 1 - min(1, remaining / duration) if duration > 0 else 0
            self.countdown_progress.setValue(round(elapsed * self.countdown_progress.maximum()))

        self.automatic_label.setText(text)
        details = "\n".join(tooltip)
        self.automatic_label.setToolTip(details)
        self.countdown_progress.setToolTip(details)

    @staticmethod
    def _countdown_text(remaining):
        hours, seconds = divmod(ceil(remaining), 3600)
        minutes, seconds = divmod(seconds, 60)
        if hours >= 24:
            days = hours // 24
            return i18n.tr("session_countdown_days", days if days <= 99 else "99+")
        clock = f"{minutes:02d}:{seconds:02d}"
        return f"{hours}:{clock}" if hours else clock

    @staticmethod
    def _relative_time(seconds):
        if seconds < 5:
            return i18n.tr("session_time_just_now")
        for unit, duration, _ in AGE_UNITS:
            if seconds >= duration:
                amount = int(seconds // duration)
                return i18n.tr(f"session_time_{unit}_ago", amount if amount <= 99 else "99+")

    @staticmethod
    def _update_history(label, age, result, title, now):
        if result is None:
            outcome, colors, relative, tooltip = "—", MUTED, "", ""
        else:
            outcome_key = result.outcome
            if title == "session_last_keepalive" and outcome_key == "valid":
                outcome_key = "completed"
            key, colors = OUTCOME_STYLES[outcome_key]
            outcome = i18n.tr(key)
            when = datetime.fromtimestamp(result.completed_at).astimezone()
            relative = SessionSummary._relative_time(now.timestamp() - result.completed_at)
            tooltip = f"{i18n.tr(title)} · {outcome}\n{when:%Y-%m-%d %H:%M:%S %z}"

        if label.text() != outcome:
            label.setText(outcome)
            label.setTextColor(*colors)
        age.setText(relative)
        label.setToolTip(tooltip)
        age.setToolTip(tooltip)


class _SessionPageTitle(SubtitleLabel):
    def __init__(self, text):
        super().__init__()
        self._full_text = text
        self.setText(text)
        self.setAccessibleName(text)
        self.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.setContentsMargins(0, 8, 0, 8)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.setSizePolicy(policy)

    def resizeEvent(self, event):
        text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width())
        self.setText(text)
        self.setToolTip(self._full_text if text != self._full_text else "")
        super().resizeEvent(event)


def create_session_header(title_text):
    title = _SessionPageTitle(title_text)
    summary = SessionSummary()
    # Reserve the header height when the summary is hidden.
    title.setMinimumHeight(summary.sizeHint().height())

    title_row = QHBoxLayout()
    title_row.setSpacing(12)
    title_row.addWidget(title, 1)
    title_row.addWidget(summary, 0, Qt.AlignmentFlag.AlignVCenter)
    return title_row, summary
