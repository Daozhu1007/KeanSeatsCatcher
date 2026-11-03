import json
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QApplication

import cloud_cli
import core_auth
import ui_main
from registration_reader import DegreePlanRegistrationReader
from test_registration import client, snapshot, S


CREDENTIALS = {"cookies": {}, "verification_token": "fixture-token", "student_id": "fixture-student"}


@pytest.mark.parametrize("route", ["session_file", "headless", "visible"])
def test_cli_authentication_paths_enable_scoped_reader(tmp_path, monkeypatch, route):
    monkeypatch.setenv("KSC_LOG_DIR", str(tmp_path))
    auth = Mock()
    auth.silent_relogin.return_value = auth.extract_credentials.return_value = CREDENTIALS
    monkeypatch.setattr(core_auth, "KeanAuthManager", lambda: auth)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    session = tmp_path / "fixture-session.json"
    session.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    api, _ = cloud_cli.authenticate(Mock(), route != "visible",
                                   str(session) if route == "session_file" else None, "2027SPW")
    assert isinstance(api.registration_reader, DegreePlanRegistrationReader)
    assert api.registration_reader.term_code == "2027SPW"


def test_gui_extraction_enables_scoped_reader(tmp_path, monkeypatch):
    monkeypatch.setenv("KSC_LOG_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    widget = ui_main.AuthInterface()
    widget.auth_manager = Mock()
    widget.auth_manager.extract_credentials.return_value = CREDENTIALS
    monkeypatch.setattr(ui_main.InfoBar, "success", Mock())
    widget.term_input.setText("2027SPW")
    engines = []
    widget.engine_ready_signal.connect(engines.append)
    widget.handle_extract()
    assert isinstance(engines[0].registration_reader, DegreePlanRegistrationReader)
    assert engines[0].registration_reader.term_code == "2027SPW"
    assert not widget.term_input.isEnabled()
    widget.close()


def test_cli_verify_only_never_starts_a_registration_worker(tmp_path, monkeypatch):
    api = client(tmp_path, [], [snapshot(a=S.REGISTERED)], initially_absent=False)
    monkeypatch.setattr(cloud_cli, "authenticate", lambda *args, **kwargs: (api, None))
    monkeypatch.setattr(cloud_cli, "setup_logging", Mock())
    monkeypatch.setattr(cloud_cli, "CloudWorker", lambda *args, **kwargs: pytest.fail("Worker must not start"))
    monkeypatch.setattr("sys.argv", ["cloud_cli.py", "--sections", "a", "--verify-only"])
    cloud_cli.main()
    assert api.all_registered(["a"])
    api.session.request.assert_not_called()
