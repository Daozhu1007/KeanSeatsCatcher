import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CardWidget, FluentIcon as FIF, LineEdit, PrimaryPushButton,
                            SubtitleLabel, TableWidget)

from event_log import course_name
from i18n import i18n
from registration_state import SectionStatus, section_credit_limits


def _display(value):
    return "—" if value is None or value == "" else str(value)


def _course_label(section):
    code, number = section.get("CourseName"), section.get("Number")
    title = section.get("Title") or course_name(section)
    if code and number:
        code = f"{code}-{number}"
    return "\n".join(dict.fromkeys(part for part in (code, title) if isinstance(part, str) and part))


def _term_label(section):
    names = (section.get("TermDisplay"), section.get("TermId"))
    return "\n".join(dict.fromkeys(name for name in names if isinstance(name, str) and name))


def _credits_label(section):
    limits = section_credit_limits(section)
    if limits is None:
        return None
    minimum, maximum = limits
    return f"{minimum:g}" if minimum == maximum else f"{minimum:g}–{maximum:g}"


def _waitlist_label(section):
    if section.get("AllowWaitlist") is False:
        return i18n.tr("lookup_waitlist_disabled")
    count, maximum = section.get("Waitlisted"), section.get("WaitlistMaximum")
    label = f"{_display(count)} / {_display(maximum)}" if count is not None or maximum is not None else ""
    available = section.get("WaitlistAvailable")
    if type(available) is bool:
        status = i18n.tr("lookup_waitlist_open" if available else "lookup_waitlist_closed")
        return f"{label} · {status}" if label else status
    return label


class SectionLookupWorker(QThread):
    results_ready = pyqtSignal(list, dict, object)

    def __init__(self, api_engine, section_ids):
        super().__init__()
        self.api_engine = api_engine
        self.section_ids = section_ids

    def run(self):
        details, snapshot = self.api_engine.lookup_sections(self.section_ids)
        self.results_ready.emit(self.section_ids, details, snapshot)


class SectionLookupInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SectionLookupInterface")
        self.api_engine = None
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 24)
        layout.setSpacing(12)
        title = SubtitleLabel(i18n.tr("tab_lookup"))
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        card = CardWidget()
        inputs = QHBoxLayout(card)
        inputs.setContentsMargins(20, 16, 20, 16)
        inputs.addWidget(BodyLabel(i18n.tr("lbl_section_ids")))
        self.section_input = LineEdit()
        self.section_input.setAccessibleName(i18n.tr("lbl_section_ids"))
        self.section_input.setPlaceholderText(i18n.tr("placeholder_section_ids"))
        self.section_input.setClearButtonEnabled(True)
        inputs.addWidget(self.section_input, 1)
        self.btn_lookup = PrimaryPushButton(FIF.SEARCH, i18n.tr("lookup_button"))
        self.btn_lookup.setEnabled(False)
        inputs.addWidget(self.btn_lookup)
        layout.addWidget(card)

        self.status = BodyLabel(i18n.tr("msg_goto_auth"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.table = TableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([i18n.tr(key) for key in (
            "catches_section", "catches_course", "catches_term", "catches_credits",
            "lookup_seats", "lookup_waitlist", "lookup_registration")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        rows = self.table.verticalHeader()
        rows.setDefaultSectionSize(60)
        rows.hide()
        columns = self.table.horizontalHeader()
        columns.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        columns.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(4).setToolTip(i18n.tr("lookup_seats_hint"))
        self.table.horizontalHeaderItem(5).setToolTip(i18n.tr("lookup_waitlist_hint"))
        layout.addWidget(self.table, 1)

        self.btn_lookup.clicked.connect(self.start_lookup)
        self.section_input.returnPressed.connect(self.start_lookup)

    def set_api_engine(self, engine):
        self.api_engine = engine
        self.status.setText(i18n.tr("lookup_account", engine.student_id))
        self.btn_lookup.setEnabled(True)

    def start_lookup(self):
        if self.api_engine is None or (self.worker is not None and self.worker.isRunning()):
            return
        ids = list(dict.fromkeys(sid for sid in re.split(r"[\s,，]+", self.section_input.text()) if sid))
        if not ids:
            self.status.setText(i18n.tr("msg_enter_sections"))
            self.section_input.setFocus()
            return
        self.status.setText(i18n.tr("lookup_loading", self.api_engine.student_id))
        self.btn_lookup.setEnabled(False)
        self.worker = SectionLookupWorker(self.api_engine, ids)
        self.worker.results_ready.connect(self._show_results)
        self.worker.finished.connect(self._lookup_finished)
        self.worker.start()

    def _lookup_finished(self):
        self.btn_lookup.setEnabled(True)

    def _show_results(self, ids, details, snapshot):
        status = i18n.tr("lookup_account", self.api_engine.student_id)
        if snapshot.reason == "session_expired":
            status = i18n.tr("msg_session_expired")
        elif snapshot.reason != "account_records":
            status += " · " + i18n.tr("lookup_registration_unavailable")
        self.status.setText(status)
        self.table.setRowCount(len(ids))
        for row, sid in enumerate(ids):
            section = details.get(sid, {})
            record = snapshot.records.get(sid)
            registration = record.status if record else SectionStatus.UNKNOWN
            available, capacity = section.get("Available"), section.get("Capacity")
            seats = None
            if section.get("AreSeatCountsAvailable") is not False and (available is not None or capacity is not None):
                seats = f"{_display(available)} / {_display(capacity)}"
            title = _course_label(section) if section else i18n.tr("lookup_details_unavailable")
            values = (sid, title, _term_label(section) or (record.term_id if record else None),
                      _credits_label(section), seats, _waitlist_label(section),
                      i18n.tr("lookup_status_" + registration.value.lower()))
            for column, value in enumerate(values):
                item = QTableWidgetItem(_display(value))
                item.setToolTip(item.text())
                if column in (0, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
