"""Local, independent Chromium user data directories for KSC."""
import errno
import os
import shutil
from contextlib import ExitStack

from event_log import data_directory
from i18n import i18n

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class BrowserProfileBusy(RuntimeError):
    pass


class BrowserProfiles:
    browsers = ("Edge", "Chrome")

    def __init__(self):
        self.root = data_directory().resolve() / "browser-profiles"

    def _owned_path(self, path):
        if path.resolve() != path:
            raise RuntimeError(i18n.tr("err_browser_profile_path"))
        return path

    def path(self, browser):
        return self._owned_path(self.root / browser.lower())

    def acquire(self, browser):
        """Hold a lease through browser shutdown so clearing cannot race a launch."""
        path = self.path(browser)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._owned_path(self.root.parent / f".browser-profile-{browser.lower()}.lock")
        lock = lock_path.open("a+b")
        try:
            try:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                raise BrowserProfileBusy(i18n.tr("err_browser_profile_in_use", browser)) from exc

            if os.name == "nt":
                # Chromium holds this file open while its profile is in use, even
                # if a KSC process exited unexpectedly and lost its own lease.
                try:
                    with (path / "lockfile").open("r+b"):
                        pass
                except FileNotFoundError:
                    pass
                except PermissionError as exc:
                    raise BrowserProfileBusy(i18n.tr("err_browser_profile_in_use", browser)) from exc
        except BaseException:
            lock.close()
            raise
        return lock

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
        with ExitStack() as leases:
            for browser in self.browsers:
                leases.enter_context(self.acquire(browser))
            root = self._owned_path(self.root)
            if root.exists():
                shutil.rmtree(root)
