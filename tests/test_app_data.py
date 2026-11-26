import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app_data
import ui_settings
from browser_profiles import BrowserProfileBusy, BrowserProfiles
from catch_history import CatchHistory
from event_log import EventLog


@pytest.fixture
def upgraded_user(tmp_path, monkeypatch):
    home = tmp_path / "user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.delenv("KSC_DATA_DIR")
    monkeypatch.delenv("KSC_LOG_DIR")
    return home / ".keanseatscatcher"


@pytest.mark.parametrize("platform, parts", [
    ("win32", ("AppData", "Local")),
    ("darwin", ("Library", "Application Support")),
    ("linux", (".local", "share")),
])
def test_platform_directory(upgraded_user, monkeypatch, platform, parts):
    monkeypatch.setattr(app_data, "sys", SimpleNamespace(platform=platform))
    assert app_data.data_directory() == upgraded_user.parent.joinpath(*parts, "KeanSeatsCatcher")


def test_config_is_imported_once_and_updates_stay_in_app_data(upgraded_user, tmp_path, monkeypatch):
    install = tmp_path / "old portable folder"
    install.mkdir()
    monkeypatch.setattr(app_data, "installation_directory", lambda: install)
    source = install / "config.json"
    original = {"Settings": {"Language": "zh_CN", "PersistentBrowserProfile": True}}
    source.write_text(json.dumps(original), encoding="utf-8")

    app_data.migrate_legacy_config()
    assert app_data.load_config() == original
    assert app_data.save_settings(KeepBrowserOpen=True)
    source.write_text('{"Settings": {"Language": "en_US"}}', encoding="utf-8")
    app_data.migrate_legacy_config()
    assert app_data.load_config()["Settings"] == dict(original["Settings"], KeepBrowserOpen=True)

    moved = tmp_path / "new portable folder"
    moved.mkdir()
    monkeypatch.setattr(app_data, "installation_directory", lambda: moved)
    app_data.migrate_legacy_config()
    assert app_data.save_settings(KeepBrowserOpen=False)
    assert not list(moved.iterdir())


def test_history_merges_committed_wal_without_reimporting_deleted_rows(upgraded_user):
    source = CatchHistory(upgraded_user / "catches.sqlite3")
    target = CatchHistory(app_data.data_directory() / "catches.sqlite3")
    record = dict(student_id="student", term_id="term", section_id="existing", confirmed_at="2026-11-01T00:00:00+00:00")
    legacy_only = dict(record, section_id="legacy")
    source.save([record])
    target.save([dict(record, course_name="new entry")])
    with closing(sqlite3.connect(source.path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("BEGIN")
        writer.execute("SELECT id FROM catches").fetchall()
        source.save([legacy_only])
        assert source.path.with_name("catches.sqlite3-wal").stat().st_size > 0
        migrated = CatchHistory()
        assert migrated.records() == [dict(record, course_name="new entry"), legacy_only]
        assert migrated.delete(legacy_only)
    assert CatchHistory().records() == [dict(record, course_name="new entry")]
    assert source.records() == [record, legacy_only]


def test_busy_profile_migration_retries_and_preserves_the_whole_profile(upgraded_user):
    legacy, profiles = BrowserProfiles(upgraded_user), BrowserProfiles()
    old = legacy.path("Chrome")
    (old / "Default").mkdir(parents=True)
    (old / "Local State").write_text("browser encryption state", encoding="utf-8")
    (old / "Default" / "Login Data").write_bytes(b"browser password database")
    (old / "DevToolsActivePort").touch()
    legacy.remember_browser("Chrome")
    profiles.path("Edge").mkdir(parents=True)

    with legacy.acquire("Chrome"):
        with pytest.raises(BrowserProfileBusy):
            profiles.migrate_legacy()
        assert not profiles.path("Chrome").exists()

    profiles.migrate_legacy()
    new = profiles.path("Chrome")
    assert (new / "Local State").read_text(encoding="utf-8") == "browser encryption state"
    assert (new / "Default" / "Login Data").read_bytes() == b"browser password database"
    assert not (new / "DevToolsActivePort").exists()
    assert profiles.preferred_browser() == "Chrome"
    assert old.is_dir()

    profiles.clear()
    assert not profiles.root.exists()
    assert not legacy.root.exists()
    old.mkdir(parents=True)
    profiles.migrate_legacy()
    assert not new.exists()


def test_log_migration_preserves_conflicts_and_honors_the_override(upgraded_user, tmp_path, monkeypatch):
    legacy = upgraded_user / "logs"
    legacy.mkdir(parents=True)
    (legacy / "registration-same.jsonl").write_text("old log", encoding="utf-8")
    output = tmp_path / "custom logs"
    output.mkdir()
    (output / "registration-same.jsonl").write_text("new log", encoding="utf-8")
    monkeypatch.setenv("KSC_LOG_DIR", str(output))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cloud_sniper.log").write_text("old CLI log", encoding="utf-8")

    for _ in range(2):
        app_data.migrate_legacy_logs(include_cli=True)
    assert (output / "registration-same.jsonl").read_text(encoding="utf-8") == "new log"
    assert (output / "legacy" / "registration-same.jsonl").read_text(encoding="utf-8") == "old log"
    assert (output / "legacy" / "cloud_sniper.log").read_text(encoding="utf-8") == "old CLI log"
    assert len(list((output / "legacy").iterdir())) == 2
    assert EventLog().path.parent == output


def test_settings_opens_the_selected_data_folder(tmp_path, monkeypatch, qt_app):
    folder = tmp_path / "my app data"
    monkeypatch.setenv("KSC_DATA_DIR", str(folder))
    open_url = Mock(return_value=True)
    monkeypatch.setattr(ui_settings.QDesktopServices, "openUrl", open_url)
    settings = ui_settings.SettingsInterface()
    try:
        settings.open_data_folder_button.click()
        assert folder.is_dir()
        assert Path(open_url.call_args.args[0].toLocalFile()) == folder
    finally:
        settings.close()
