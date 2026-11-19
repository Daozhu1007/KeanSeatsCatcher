import os
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qt_app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch, tmp_path):
    monkeypatch.setenv("KSC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KSC_LOG_DIR", str(tmp_path / "logs"))

    def fail(*args, **kwargs):
        pytest.fail("Tests must never contact a real registration server")
    monkeypatch.setattr(requests.sessions.Session, "request", fail)
