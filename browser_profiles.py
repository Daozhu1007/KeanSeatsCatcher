"""Local, independent Chromium user data directories for KSC."""
import errno
import os
import shutil
import socket
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from app_data import acquire_file_lock, data_directory, legacy_data_directory
from i18n import i18n


_CHROMIUM_RUNTIME_FILES = {"DevToolsActivePort", "lockfile", "SingletonCookie", "SingletonLock", "SingletonSocket"}


class BrowserProfileBusy(RuntimeError):
    pass


class BrowserProfiles:
    browsers = ("Edge", "Chrome")

    def __init__(self, directory=None):
        self._legacy_directory = legacy_data_directory() if directory is None else None
        base = data_directory() if directory is None else Path(directory)
        self.root = base.resolve() / "browser-profiles"
        self._migration_marker = self.root.parent / ".legacy-profiles-migrated"

    def _owned_path(self, path):
        if path.resolve() != path:
            raise RuntimeError(i18n.tr("err_browser_profile_path"))
        return path

    def path(self, browser):
        return self._owned_path(self.root / browser.lower())

    def acquire(self, browser):
        """Hold a lease through browser shutdown so clearing cannot race a launch."""
        path = self.path(browser)
        lock_path = self._owned_path(self.root.parent / f".browser-profile-{browser.lower()}.lock")
        busy = i18n.tr("err_browser_profile_in_use", browser)
        try:
            lock = acquire_file_lock(lock_path)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise BrowserProfileBusy(busy) from exc
        try:
            self._check_chromium_idle(path, busy)
        except BaseException:
            lock.close()
            raise
        return lock

    @staticmethod
    def _check_chromium_idle(path, busy):
        # Chromium's lock also covers a browser left running after a KSC crash.
        if os.name == "nt":
            try:
                with (path / "lockfile").open("r+b"):
                    pass
            except FileNotFoundError:
                pass
            except PermissionError as exc:
                raise BrowserProfileBusy(busy) from exc
            return

        singleton = path / "SingletonLock"
        if singleton.is_symlink():
            host, _, pid = os.readlink(singleton).rpartition("-")
            if host == socket.gethostname() and pid.isdecimal() and int(pid) > 0:
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    return
                except PermissionError:
                    pass
            raise BrowserProfileBusy(busy)

    def _legacy_profiles(self):
        if self._legacy_directory is not None:
            legacy = BrowserProfiles(self._legacy_directory)
            if legacy._owned_path(legacy.root).is_dir():
                return legacy
        return None

    def _migration_lock(self):
        path = self._owned_path(self.root.parent / ".profile-migration.lock")
        return acquire_file_lock(path, wait=True)

    def migrate_legacy(self):
        marker = self._owned_path(self._migration_marker)
        if marker.exists():
            return
        legacy = self._legacy_profiles()
        if legacy is None:
            return
        with self._migration_lock():
            if marker.exists():
                return
            for browser in self.browsers:
                source, target = legacy.path(browser), self.path(browser)
                if source.is_dir() and not target.exists():
                    with self.acquire(browser), legacy.acquire(browser):
                        if not target.exists():
                            self._copy_profile(source, target)
            preferred = legacy.preferred_browser()
            if preferred and not self.preferred_browser() and self.path(preferred).is_dir():
                self.remember_browser(preferred)
            marker.touch()

    @staticmethod
    def _copy_profile(source, target):
        def ignore_runtime(directory, names):
            return _CHROMIUM_RUNTIME_FILES.intersection(names) if Path(directory) == source else set()

        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".migration-", dir=target.parent) as directory:
            pending = Path(directory) / target.name
            shutil.copytree(source, pending, symlinks=True, ignore=ignore_runtime)
            pending.rename(target)

    def preferred_browser(self):
        try:
            name = self._owned_path(self.root / "last-browser").read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return None
        return name if name in self.browsers else None

    def remember_browser(self, browser):
        marker = self._owned_path(self.root / "last-browser")
        pending = self._owned_path(self.root / f".last-browser-{browser.lower()}")
        pending.write_text(browser, encoding="ascii")
        pending.replace(marker)

    def clear(self):
        with self._migration_lock(), ExitStack() as leases:
            legacy = self._legacy_profiles()
            stores = (self, legacy) if legacy is not None else (self,)
            roots = []
            for store in stores:
                roots.append(store._owned_path(store.root))
                for browser in self.browsers:
                    leases.enter_context(store.acquire(browser))
            self._owned_path(self._migration_marker).touch()
            for root in roots:
                if root.exists():
                    shutil.rmtree(root)
