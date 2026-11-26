from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (SubtitleLabel, BodyLabel, CardWidget, ComboBox, PushButton,
                            SwitchButton, InfoBar, ScrollArea, FluentIcon as FIF)

from app_data import data_directory, load_config, save_settings
from i18n import i18n
from session_keepalive import KEEPALIVE_INTERVAL_MINUTES, KEEPALIVE_INTERVAL_SECONDS


class SettingsInterface(ScrollArea):
    keepalive_changed = pyqtSignal(bool, int)
    session_status_visibility_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SettingsInterface")
        self.view = QWidget(self)
        self.layout = QVBoxLayout(self.view)

        self.layout.setContentsMargins(24, 32, 24, 24)
        self.layout.setSpacing(20)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea{background: transparent; border: none}")

        title = SubtitleLabel(i18n.tr("settings_title"))
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.layout.addWidget(title)

        # --- Keep Browser Open ---
        browser_card = CardWidget()
        browser_layout = QHBoxLayout(browser_card)
        browser_layout.setContentsMargins(20, 16, 20, 16)
        browser_layout.setSpacing(15)

        browser_text_layout = QVBoxLayout()
        browser_text_layout.setSpacing(4)
        browser_title = BodyLabel(i18n.tr("settings_keep_browser_open"))
        browser_title.setStyleSheet("font-size: 14px;")
        browser_desc = BodyLabel(i18n.tr("settings_keep_browser_open_desc"))
        browser_desc.setStyleSheet("color: #a0a0a0; font-size: 12px;")
        browser_text_layout.addWidget(browser_title)
        browser_text_layout.addWidget(browser_desc)

        self.browser_switch = SwitchButton()
        settings = load_config().get("Settings", {})
        self.browser_switch.setChecked(settings.get("KeepBrowserOpen", False))
        self.browser_switch.checkedChanged.connect(self._on_browser_keep_open_changed)

        browser_layout.addLayout(browser_text_layout, 1)
        browser_layout.addWidget(self.browser_switch)
        self.layout.addWidget(browser_card)

        keepalive_card = CardWidget()
        keepalive_layout = QVBoxLayout(keepalive_card)
        keepalive_layout.setContentsMargins(20, 16, 20, 16)
        keepalive_layout.setSpacing(12)

        keepalive_row = QHBoxLayout()
        keepalive_row.addWidget(BodyLabel(i18n.tr("session_keepalive")), 1)
        self.keepalive_switch = SwitchButton()
        self.keepalive_switch.setAccessibleName(i18n.tr("session_keepalive"))
        self.keepalive_switch.setOnText(i18n.tr("settings_keepalive_on"))
        self.keepalive_switch.setOffText(i18n.tr("keepalive_state_disabled"))
        self.keepalive_switch.setChecked(settings.get("SessionKeepaliveEnabled") is not False)
        keepalive_row.addWidget(self.keepalive_switch)
        keepalive_layout.addLayout(keepalive_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(BodyLabel(i18n.tr("session_interval")), 1)
        self.keepalive_interval = ComboBox()
        self.keepalive_interval.setAccessibleName(i18n.tr("session_interval"))
        for minutes in KEEPALIVE_INTERVAL_MINUTES:
            self.keepalive_interval.addItem(i18n.tr("session_minutes", minutes), userData=minutes)
        interval = settings.get("SessionKeepaliveIntervalMinutes")
        if interval not in KEEPALIVE_INTERVAL_MINUTES:
            interval = KEEPALIVE_INTERVAL_SECONDS // 60
        self.keepalive_interval.setCurrentIndex(KEEPALIVE_INTERVAL_MINUTES.index(interval))
        interval_row.addWidget(self.keepalive_interval)
        keepalive_layout.addLayout(interval_row)
        self.layout.addWidget(keepalive_card)

        self.keepalive_switch.checkedChanged.connect(self._on_keepalive_changed)
        self.keepalive_interval.currentIndexChanged.connect(self._on_keepalive_changed)

        session_status_card = CardWidget()
        session_status_layout = QHBoxLayout(session_status_card)
        session_status_layout.setContentsMargins(20, 16, 20, 16)
        session_status_layout.setSpacing(15)
        session_status_title = i18n.tr("settings_show_session_status")
        session_status_layout.addWidget(BodyLabel(session_status_title), 1)
        self.session_status_switch = SwitchButton()
        self.session_status_switch.setAccessibleName(session_status_title)
        self.session_status_switch.setOnText(i18n.tr("settings_keepalive_on"))
        self.session_status_switch.setOffText(i18n.tr("keepalive_state_disabled"))
        self.session_status_switch.setChecked(settings.get("ShowSessionStatus") is not False)
        self.session_status_switch.checkedChanged.connect(self._on_session_status_visibility_changed)
        session_status_layout.addWidget(self.session_status_switch)
        self.layout.addWidget(session_status_card)

        # --- Language ---
        lang_card = CardWidget()
        lang_layout = QHBoxLayout(lang_card)
        lang_layout.setContentsMargins(20, 16, 20, 16)
        lang_layout.setSpacing(15)

        lang_text_layout = QVBoxLayout()
        lang_text_layout.setSpacing(4)
        lang_title = BodyLabel(i18n.tr("settings_language"))
        lang_title.setStyleSheet("font-size: 14px;")
        lang_desc = BodyLabel(i18n.tr("settings_language_desc"))
        lang_desc.setStyleSheet("color: #a0a0a0; font-size: 12px;")
        lang_text_layout.addWidget(lang_title)
        lang_text_layout.addWidget(lang_desc)

        self.lang_combo = ComboBox()
        self.lang_combo.addItem(i18n.tr("lang_zh"), userData="zh_CN")
        self.lang_combo.addItem(i18n.tr("lang_en"), userData="en_US")
        current_lang = i18n.locale
        self.lang_combo.setCurrentIndex(0 if current_lang == "zh_CN" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)

        lang_layout.addLayout(lang_text_layout, 1)
        lang_layout.addWidget(self.lang_combo)
        self.layout.addWidget(lang_card)

        self.open_data_folder_button = PushButton(FIF.FOLDER, i18n.tr("settings_open_data_folder"))
        self.open_data_folder_button.clicked.connect(self._open_data_folder)
        self.layout.addWidget(self.open_data_folder_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.layout.addStretch(1)

    def _open_data_folder(self):
        directory = data_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                return
        except OSError:
            pass
        InfoBar.error(i18n.tr("settings_title"), i18n.tr("settings_open_data_folder_error", directory),
                      duration=5000, parent=self)

    def _on_browser_keep_open_changed(self, checked):
        save_settings(KeepBrowserOpen=checked)

    @property
    def keepalive_preferences(self):
        return self.keepalive_switch.isChecked(), self.keepalive_interval.currentData()

    def _on_keepalive_changed(self):
        enabled, minutes = self.keepalive_preferences
        save_settings(SessionKeepaliveEnabled=enabled, SessionKeepaliveIntervalMinutes=minutes)
        self.keepalive_changed.emit(enabled, minutes)

    @property
    def show_session_status(self):
        return self.session_status_switch.isChecked()

    def _on_session_status_visibility_changed(self, checked):
        save_settings(ShowSessionStatus=checked)
        self.session_status_visibility_changed.emit(checked)

    def _on_lang_changed(self, index):
        lang_code = self.lang_combo.itemData(index)
        if not lang_code:
            return
        if load_config().get("Settings", {}).get("Language") == lang_code:
            return
        save_settings(Language=lang_code)
        i18n.set_language(lang_code)
        InfoBar.success(
            i18n.tr("msg_lang_changed"),
            i18n.tr("msg_lang_restart"),
            duration=5000,
            parent=self
        )
