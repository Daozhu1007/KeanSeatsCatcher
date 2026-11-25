from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (SubtitleLabel, BodyLabel, CardWidget, ComboBox,
                            SwitchButton,
                            InfoBar, ScrollArea)

from i18n import i18n, load_config, save_config
from session_keepalive import KEEPALIVE_INTERVAL_MINUTES, KEEPALIVE_INTERVAL_SECONDS


class SettingsInterface(ScrollArea):
    keepalive_changed = pyqtSignal(bool, int)

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

        self.layout.addStretch(1)

    def _on_browser_keep_open_changed(self, checked):
        config = load_config()
        config.setdefault("Settings", {})["KeepBrowserOpen"] = checked
        save_config(config)

    @property
    def keepalive_preferences(self):
        return self.keepalive_switch.isChecked(), self.keepalive_interval.currentData()

    def _on_keepalive_changed(self):
        enabled, minutes = self.keepalive_preferences
        config = load_config()
        settings = config.setdefault("Settings", {})
        settings["SessionKeepaliveEnabled"] = enabled
        settings["SessionKeepaliveIntervalMinutes"] = minutes
        save_config(config)
        self.keepalive_changed.emit(enabled, minutes)

    def _on_lang_changed(self, index):
        lang_code = self.lang_combo.itemData(index)
        if not lang_code:
            return
        config = load_config()
        if config.get("Settings", {}).get("Language") == lang_code:
            return
        config.setdefault("Settings", {})["Language"] = lang_code
        save_config(config)
        i18n.set_language(lang_code)
        InfoBar.success(
            i18n.tr("msg_lang_changed"),
            i18n.tr("msg_lang_restart"),
            duration=5000,
            parent=self
        )
