import json
import sqlite3
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, SubtitleLabel, TableWidget, TextEdit

from catch_history import CatchHistory
from i18n import i18n


def _display(value):
    return i18n.tr("catches_unrecorded") if value is None else str(value)


def _local_time(value):
    if value is None:
        return _display(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().isoformat(
            sep=" ", timespec="milliseconds")
    except ValueError:
        return value


class MyCatchesInterface(QWidget):
    history_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MyCatchesInterface")
        self.history = CatchHistory()
        self.records = []
        self.visible_records = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = SubtitleLabel(i18n.tr("tab_catches"))
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        header.addWidget(title, 1)
        self.accounts = ComboBox()
        self.accounts.setAccessibleName(i18n.tr("catches_student"))
        header.addWidget(self.accounts)
        layout.addLayout(header)
        hint = BodyLabel(i18n.tr("catches_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.empty = BodyLabel(i18n.tr("catches_empty"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, 1)

        self.table = TableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([i18n.tr("catches_" + field) for field in (
            "course", "section", "term", "credits", "student", "confirmed")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        columns = self.table.horizontalHeader()
        columns.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.detail_title = SubtitleLabel(i18n.tr("catches_evidence"))
        layout.addWidget(self.detail_title)
        self.details = TextEdit()
        self.details.setReadOnly(True)
        self.details.setStyleSheet("font-family: Consolas; font-size: 13px;")
        layout.addWidget(self.details, 2)
        self.accounts.currentIndexChanged.connect(self._populate)
        self.table.itemSelectionChanged.connect(self._show_details)
        self.history_changed.connect(self.refresh)

    def bind_engine(self, engine):
        engine.history_callback = self.history_changed.emit

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        previous_account = self.accounts.currentData()
        try:
            self.records = self.history.records()
            empty = i18n.tr("catches_empty")
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.records = []
            empty = i18n.tr("log_history_error", type(exc).__name__)
        self.empty.setText(empty)
        self.accounts.blockSignals(True)
        self.accounts.clear()
        self.accounts.addItem(i18n.tr("catches_all_accounts"))
        accounts = sorted({record["student_id"] for record in self.records if record["student_id"]})
        for account in accounts:
            self.accounts.addItem(account, userData=account)
        if any(record["student_id"] is None for record in self.records):
            self.accounts.addItem(i18n.tr("catches_unrecorded"), userData="")
        self.accounts.setCurrentIndex(max(0, self.accounts.findData(previous_account)))
        self.accounts.blockSignals(False)
        self.accounts.setVisible(self.accounts.count() > 2)
        self._populate()

    def _populate(self):
        selected = self.table.currentRow()
        previous = self.visible_records[selected] if 0 <= selected < len(self.visible_records) else None
        account = self.accounts.currentData()
        self.visible_records = [record for record in self.records
                                if account is None or (record["student_id"] or "") == account]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.visible_records))
        for row, record in enumerate(self.visible_records):
            values = [record.get(field) for field in ("course_name", "section_id", "term_id", "credits", "student_id")]
            confirmed = _local_time(record["confirmed_at"])
            values.append(confirmed.split(".", 1)[0])
            for column, value in enumerate(values):
                item = QTableWidgetItem(_display(value))
                item.setToolTip(confirmed if column == 5 else item.text())
                self.table.setItem(row, column, item)
        if self.visible_records:
            self.table.selectRow(self.visible_records.index(previous) if previous in self.visible_records else 0)
        self.table.blockSignals(False)
        self.empty.setVisible(not self.visible_records)
        for widget in (self.table, self.detail_title, self.details):
            widget.setVisible(bool(self.visible_records))
        self._show_details()

    def _show_details(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.visible_records):
            self.details.clear()
            return
        record = self.visible_records[row]
        credits = record["sent_credits"]
        sent = _display(credits.get("value")) if credits["status"] == "included" else i18n.tr(
            "credits_" + credits["status"])
        source = record.get("credits_source")
        text = [
            " · ".join(map(_display, (record["course_name"], record["section_id"], record["term_id"]))),
            i18n.tr("catches_confirmed_time", _local_time(record["confirmed_at"])),
            i18n.tr("catches_sent_credits", sent),
            i18n.tr("catches_credits_evidence", i18n.tr("credits_source_" + source) if source else _display(None)),
            i18n.tr("catches_server_time", _local_time(record.get("server_registered_at"))),
            i18n.tr("catches_source", record["source_log"]),
        ]
        if record["source"] == "legacy" and record.get("course_name"):
            text.append(i18n.tr("catches_legacy_name"))
        for trace in record["requests"]:
            request = trace.get("request", {})
            response = trace.get("response")
            status = f"HTTP {trace['http_status']}" if trace.get("http_status") is not None else trace.get("reason")
            payload = {key: request[key] for key in ("params", "json") if key in request}
            text.extend((
                "",
                " · ".join((i18n.tr("operation_" + trace["operation"]), _display(trace.get("method")),
                            _display(request.get("path")), _display(status))),
                i18n.tr("catches_request_time", _local_time(trace.get("started_at")),
                        _local_time(trace.get("response_received_at")), _display(trace.get("elapsed_ms"))),
                i18n.tr("catches_request", json.dumps(payload, ensure_ascii=False, indent=2)
                        if payload else _display(None)),
                i18n.tr("catches_response", json.dumps(response, ensure_ascii=False, indent=2)
                        if response is not None else
                        i18n.tr("catches_no_response") if "response" in trace else _display(None)),
            ))
        self.details.setPlainText("\n".join(text))
