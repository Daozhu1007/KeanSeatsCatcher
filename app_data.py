"""Per-user storage paths and migration of legacy settings and logs."""
import filecmp
import json
import os
import shutil
import sys
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from tempfile import NamedTemporaryFile

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def data_directory():
    override = os.environ.get("KSC_DATA_DIR")
    if override:
        return Path(override).expanduser().absolute()

    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        base = local if local.is_absolute() else home / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", ""))
        base = xdg_data if xdg_data.is_absolute() else home / ".local" / "share"
    return base / "KeanSeatsCatcher"


def log_directory():
    override = os.environ.get("KSC_LOG_DIR")
    return Path(override).expanduser().absolute() if override else data_directory() / "logs"


def legacy_data_directory():
    # An explicitly selected store already used that location in older versions.
    if os.environ.get("KSC_DATA_DIR"):
        return None
    legacy = Path.home().resolve() / ".keanseatscatcher"
    return legacy if legacy.resolve() == legacy and legacy != data_directory().resolve() else None


def installation_directory():
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def acquire_file_lock(path, *, wait=False):
    """Return a file whose lock is held until the caller closes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("a+b")
    try:
        if os.name == "nt":
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        lock.close()
        raise
    return lock


@contextmanager
def _atomic_write(path):
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as stream:
        pending = Path(stream.name)
    try:
        yield pending
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _parse_config(contents):
    config = json.loads(contents)
    if not isinstance(config, dict) or not isinstance(config.get("Settings", {}), dict):
        raise ValueError("Invalid config.json structure")
    return config


def load_config():
    try:
        return _parse_config((data_directory() / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    path = data_directory() / "config.json"
    try:
        with acquire_file_lock(path.parent / ".config.lock", wait=True):
            config = load_config()
            config.setdefault("Settings", {}).update(changes)
            with _atomic_write(path) as pending:
                pending.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(f"[ERROR] Failed to save config: {exc}")
        return False
    return True


def migrate_legacy_config():
    target = data_directory() / "config.json"
    source = installation_directory() / target.name
    if target.exists() or not source.is_file():
        return
    with acquire_file_lock(target.parent / ".config.lock", wait=True):
        if not target.exists():
            contents = source.read_text(encoding="utf-8-sig")
            _parse_config(contents)
            with _atomic_write(target) as pending:
                pending.write_text(contents, encoding="utf-8")


def _copy_log(source, directory, *, archive_only=False):
    target = directory / source.name
    if archive_only or target.exists():
        if not archive_only and filecmp.cmp(source, target, shallow=False):
            return
        archive = directory / "legacy"
        archive.mkdir(exist_ok=True)
        for suffix in count(1):
            name = source.name if suffix == 1 else f"{source.stem}-{suffix}{source.suffix}"
            target = archive / name
            if not target.exists():
                break
            if filecmp.cmp(source, target, shallow=False):
                return
    with _atomic_write(target) as pending:
        shutil.copy2(source, pending)


def migrate_legacy_logs(*, include_cli=False):
    legacy = legacy_data_directory()
    directory = log_directory().resolve()
    source = legacy / "logs" if legacy else None
    marker = directory / ".legacy-logs-migrated"
    copy_logs = source and source.is_dir() and source.resolve() != directory and not marker.exists()
    cli_log = Path.cwd() / "cloud_sniper.log"
    copy_cli = include_cli and cli_log.is_file() and cli_log.parent.resolve() != directory
    if not (copy_logs or copy_cli):
        return
    with acquire_file_lock(directory / ".legacy-logs.lock", wait=True):
        if copy_logs and not marker.exists():
            for log in source.iterdir():
                if log.is_file() and not log.is_symlink():
                    _copy_log(log, directory)
            marker.touch()
        if copy_cli:
            _copy_log(cli_log, directory, archive_only=True)
