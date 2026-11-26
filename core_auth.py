import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (InvalidArgumentException, NoSuchElementException,
                                         TimeoutException, WebDriverException)

from browser_profiles import BrowserProfiles
from i18n import i18n


class KeanAuthManager:
    def __init__(self, *, persistent_profile=False):
        self.driver = None
        self.login_url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"
        self.persistent_profile = persistent_profile
        self._profiles = BrowserProfiles()
        self._profile_lease = None
        self._browser_name = None
        self._signing_in = False
        self._browser_operation = threading.RLock()

    def open_login_page(self, detach=False):
        with self._browser_operation:
            if self.driver is not None:
                if self.persistent_profile == (self._profile_lease is not None):
                    try:
                        self.driver.get(self.login_url)
                        self._signing_in = True
                        return
                    except WebDriverException:
                        pass
                self.close_browser()
            self.launch_browser(detach=detach)
            self._signing_in = True

    def launch_browser(self, headless: bool = False, detach: bool = False):
        with self._browser_operation:
            if self.driver is None:
                self._browser_name, self.driver, self._profile_lease = self._launch(
                    headless, detach, self.persistent_profile)

    def _launch(self, headless, detach, persistent):
        browsers = list(self._profiles.browsers)
        if persistent:
            self._profiles.migrate_legacy()
            preferred = self._profiles.preferred_browser()
            browsers.sort(key=lambda browser: browser != preferred)
        elif sys.platform == "darwin" and not headless:
            # Safari's built-in driver needs no download.
            browsers = ["Safari", "Chrome", "Edge"]

        last_error = None
        for name in browsers:
            try:
                driver, lease = self._try_browser_with_timeout(
                    name, lambda name=name: self._start_browser(name, headless, detach, persistent))
            except (WebDriverException, TimeoutError) as e:
                print(f"{name} unavailable: {e}")
                last_error = e
            else:
                print(f"[INFO] {name} browser launched successfully.")
                return name, driver, lease

        raise RuntimeError(
            "No supported browser found. Please install Chrome or Edge.\n"
            f"Last error: {last_error}"
        )

    def _start_browser(self, name, headless, detach, persistent):
        lease = self._profiles.acquire(name) if persistent else None
        driver = None
        try:
            if name == "Safari":
                driver = webdriver.Safari()
            else:
                options = EdgeOptions() if name == "Edge" else ChromeOptions()
                switches = ["enable-automation"]
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_experimental_option('useAutomationExtension', False)
                if persistent:
                    options.add_argument(f"--user-data-dir={self._profiles.path(name)}")
                    options.add_argument("--disable-background-mode")
                    # Let the browser use its native credential storage.
                    switches.extend(("password-store", "use-mock-keychain"))
                    if not headless:
                        # Chromium drivers start a BiDi session with a blank tab
                        # even when they use an existing user data directory.
                        options.set_capability("webSocketUrl", True)
                elif detach:
                    options.add_experimental_option("detach", True)
                options.add_experimental_option("excludeSwitches", switches)
                if headless:
                    options.add_argument("--headless" if name == "Edge" else "--headless=new")
                    options.add_argument("--disable-gpu")
                    options.add_argument("--window-size=1920,1080")

                factory = webdriver.Edge if name == "Edge" else webdriver.Chrome
                try:
                    driver = factory(options=options)
                except InvalidArgumentException as exc:
                    if not options.capabilities.get("webSocketUrl") or "webSocketUrl" not in str(exc):
                        raise
                    options.capabilities.pop("webSocketUrl")
                    driver = factory(options=options)
                driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                    'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
                })
            if persistent:
                driver.set_page_load_timeout(15)
            driver.get(self.login_url)
            return driver, lease
        except BaseException:
            self._quit_browser(driver, lease)
            raise

    def _try_browser_with_timeout(self, name, launch, timeout=15):
        print(f"Trying {name} browser (timeout={timeout}s)...")
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(launch)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            # A late driver still owns its profile lease and closes itself
            # without replacing the browser chosen by the fallback chain.
            future.add_done_callback(self._discard_launch)
            raise TimeoutError(f"{name} launch timed out after {timeout}s") from exc
        finally:
            executor.shutdown(wait=False)

    def _discard_launch(self, future):
        if future.exception() is None:
            self._quit_browser(*future.result())

    def _on_degree_plans(self, driver):
        current, target = urlsplit(driver.current_url), urlsplit(self.login_url)
        return (current.scheme == target.scheme and current.netloc == target.netloc
                and current.path.rstrip("/").lower() == target.path.lower())

    def extract_credentials(self) -> dict:
        with self._browser_operation:
            return self._extract_credentials(self.driver)

    def _extract_credentials(self, driver):
        if not driver:
            return {"error": i18n.tr("err_no_browser")}

        print("Checking page state...")
        try:
            if not self._on_degree_plans(driver):
                return {"error": i18n.tr("err_not_on_page")}
        except WebDriverException:
            return {"error": i18n.tr("err_browser_disconnected")}

        print("Extracting API credentials & Student ID...")

        try:
            cookies_list = driver.get_cookies()
        except WebDriverException:
            return {"error": i18n.tr("err_browser_disconnected_cookies")}

        js_script = """
        let token = null;
        let tokenInput = document.querySelector('input[name="__RequestVerificationToken"]');
        if (tokenInput) { token = tokenInput.value; }
        else {
            let forms = document.querySelectorAll('form');
            for (let f of forms) {
                let t = f.querySelector('input[name="__RequestVerificationToken"]');
                if (t) { token = t.value; break; }
            }
        }

        let studentId = null;
        let storages = [window.sessionStorage, window.localStorage];

        for (let storage of storages) {
            for (let i = 0; i < storage.length; i++) {
                let key = storage.key(i);
                let val = storage.getItem(key);
                if (val && typeof val === 'string') {
                    let match = val.match(/"(?:personId|studentId)"\\s*:\\s*"?(\\d{6,8})"?/i);
                    if (match) {
                        studentId = match[1];
                        break;
                    }
                }
            }
            if (studentId) break;
        }

        return JSON.stringify({ token: token, student_id: studentId });
        """

        try:
            result_json = driver.execute_script(js_script)
        except WebDriverException:
            return {"error": i18n.tr("err_browser_disconnected_script")}

        result = json.loads(result_json)

        if not isinstance(result, dict):
            return {"error": i18n.tr("err_no_student_id")}

        verification_token = result.get('token')
        student_id = result.get('student_id')

        if not verification_token:
            return {"error": "Verification token not found on page. Please ensure you are logged in."}
        if not student_id:
            return {"error": i18n.tr("err_no_student_id")}

        try:
            user_agent = driver.execute_script("return navigator.userAgent;")
        except WebDriverException:
            user_agent = "unknown"

        credentials = {
            "cookies": cookies_list,
            "verification_token": verification_token,
            "student_id": student_id,
            "user_agent": user_agent
        }

        print(f"Credentials extracted. Student ID: {student_id}")
        return credentials

    def silent_relogin(self, headless: bool = False) -> dict:
        """Recover through the normal KeanWISE redirect. Okta can require a manual sign-in."""
        if not self._browser_operation.acquire(blocking=False):
            raise RuntimeError(i18n.tr("err_browser_auth_in_progress"))
        try:
            persistent = self.persistent_profile
            if persistent and self._profile_lease is not None:
                try:
                    # Detect a retained browser that the user has since closed.
                    self.driver.current_window_handle
                except WebDriverException:
                    self.close_browser()
                else:
                    if self._signing_in:
                        raise RuntimeError(i18n.tr("err_browser_auth_in_progress"))
                    self.driver.get(self.login_url)
                    return self._recover(self.driver, persistent)

            _, driver, lease = self._launch(headless, False, persistent)
            try:
                return self._recover(driver, persistent)
            finally:
                self._quit_browser(driver, lease)
        finally:
            self._browser_operation.release()

    def _recover(self, driver, persistent):
        if not persistent:
            # Preserve the existing temporary/headless workflow.
            time.sleep(3)
            if "okta" in driver.current_url.lower():
                self._click_okta_login(driver)

        try:
            WebDriverWait(driver, 20 if persistent else 30).until(self._on_degree_plans)
        except TimeoutException as exc:
            raise RuntimeError(i18n.tr("err_browser_sign_in_required")) from exc

        credentials = self._extract_credentials(driver)
        if "error" in credentials:
            raise RuntimeError(credentials["error"])
        return credentials

    def _click_okta_login(self, driver):
        """
        Click the Okta sign-in button using multiple fallback selectors.
        Okta's login page may vary — we try several common patterns.
        """
        selectors = [
            # Okta classic / custom widget
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit'][value*='Sign']"),
            (By.CSS_SELECTOR, "input[type='submit'][value*='登录']"),
            # Okta Identity Engine / modern
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, ".button-primary"),
            (By.CSS_SELECTOR, ".btn-primary"),
            # Generic "next" or "verify" buttons
            (By.XPATH, "//input[@type='submit'][contains(@value,'Sign')]"),
            (By.XPATH, "//input[@type='submit'][contains(@value,'登录')]"),
            (By.XPATH, "//button[contains(text(),'Sign')]"),
            (By.XPATH, "//button[contains(text(),'登录')]"),
            (By.XPATH, "//a[contains(@class,'button')][contains(@href,'login')]"),
        ]

        for by, selector in selectors:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if btn and btn.is_displayed():
                    print(f"[Auto-Recovery] Clicking login button: {selector}")
                    btn.click()
                    return
            except (TimeoutException, NoSuchElementException):
                continue

        raise RuntimeError(
            "Could not locate Okta login button. The page layout may have changed."
        )

    def finish_login(self, keep_open=False):
        with self._browser_operation:
            if self._profile_lease is not None:
                try:
                    self._profiles.remember_browser(self._browser_name)
                except (OSError, RuntimeError) as exc:
                    print(f"[WARN] Could not remember the browser profile: {exc}")
            self._signing_in = False
            if not keep_open:
                self.close_browser()

    def clear_browser_profiles(self):
        with self._browser_operation:
            self.close_persistent_browser()
            self._profiles.clear()

    @property
    def uses_persistent_browser(self):
        return self._profile_lease is not None

    def close_persistent_browser(self):
        if self.uses_persistent_browser:
            self.close_browser()

    def close_browser(self):
        with self._browser_operation:
            self._quit_browser(self.driver, self._profile_lease)
            self.driver = self._profile_lease = self._browser_name = None
            self._signing_in = False

    @staticmethod
    def _quit_browser(driver, lease):
        try:
            if driver is not None:
                driver.quit()
        except Exception:
            pass
        finally:
            if lease is not None:
                lease.close()
