import sys
import os
import json

from app_data import load_config

resource_dir = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))


class I18nManager:
    def __init__(self, locale_code="zh_CN"):
        self.locale = locale_code
        self.texts = {}
        self.load_language()

    def load_language(self):
        lang_file = os.path.join(resource_dir, "locales", f"{self.locale}.json")
        try:
            with open(lang_file, 'r', encoding='utf-8') as f:
                self.texts = json.load(f)
        except Exception:
            self.texts = {}

    def tr(self, key, *args):
        text = self.texts.get(key, key)
        if args:
            try:
                text = text.format(*args)
            except Exception:
                pass
        return text

    def set_language(self, locale_code):
        self.locale = locale_code
        self.load_language()


i18n = I18nManager(load_config().get("Settings", {}).get("Language") or "en_US")
