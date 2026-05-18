import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (WebDriverException, InvalidSessionIdException,
                                        TimeoutException, NoSuchElementException)

from i18n import i18n


class KeanAuthManager:
    def __init__(self):
        self.driver = None
        self.login_url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"

    def launch_browser(self, headless: bool = False):
        """
        Per-browser 15-second timeout prevents a hung driver download
        from freezing the entire chain.

        On macOS Safari is tried first — it uses the built-in
        safaridriver (zero download), so it works even on slow/flaky
        networks where the Selenium Manager CDN is unreachable.
        """
        last_error = None

        chains = self._browser_chain(headless)
        for name, fn in chains:
            try:
                self._try_browser_with_timeout(name, fn)
                return
            except (WebDriverException, TimeoutError) as e:
                print(f"{name} unavailable: {e}")
                last_error = e

        raise RuntimeError(
            "No supported browser found. Please install Chrome or Edge.\n"
            f"Last error: {last_error}"
        )

    def _browser_chain(self, headless):
        """Return the ordered list of (name, callable) to try."""
        import sys
        if sys.platform == "darwin" and not headless:
            # Safari uses built-in driver — no download, instant start
            return [
                ("Safari",  lambda: self._try_safari()),
                ("Chrome",  lambda: self._try_chrome(headless)),
                ("Edge",    lambda: self._try_edge(headless)),
            ]
        return [
            ("Edge",    lambda: self._try_edge(headless)),
            ("Chrome",  lambda: self._try_chrome(headless)),
        ]

    def _try_edge(self, headless: bool):
        options = EdgeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_experimental_option("detach", True)
        if headless:
            options.add_argument('--headless')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')

        self.driver = webdriver.Edge(options=options)
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
        })
        self.driver.get(self.login_url)
        print("[INFO] Edge browser launched successfully.")

    def _try_chrome(self, headless: bool):
        options = ChromeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        if headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')

        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
        })
        self.driver.get(self.login_url)
        print("[INFO] Chrome browser launched successfully.")

    def _try_safari(self):
        self.driver = webdriver.Safari()
        self.driver.get(self.login_url)
        print("[INFO] Safari browser launched successfully.")

    def _try_browser_with_timeout(self, name, fn, timeout=15):
        """
        Run *fn* in a daemon thread with a timeout guard.

        CRITICAL: executor.shutdown(wait=False) is used instead of the
        ``with`` statement. The context-manager __exit__ calls
        shutdown(wait=True), which would block until the hung thread
        finishes — defeating the purpose of the timeout.
        """
        print(f"Trying {name} browser (timeout={timeout}s)...")
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(fn)
            future.result(timeout=timeout)
        except FutureTimeoutError:
            raise TimeoutError(f"{name} launch timed out after {timeout}s")
        finally:
            executor.shutdown(wait=False)

    def extract_credentials(self) -> dict:
        if not self.driver:
            return {"error": i18n.tr("err_no_browser")}

        print("Checking page state...")
        try:
            if "DegreePlans" not in self.driver.current_url:
                return {"error": i18n.tr("err_not_on_page")}
        except (WebDriverException, InvalidSessionIdException):
            return {"error": i18n.tr("err_browser_disconnected")}

        print("Extracting API credentials & Student ID...")

        try:
            cookies_list = self.driver.get_cookies()
        except (WebDriverException, InvalidSessionIdException):
            return {"error": i18n.tr("err_browser_disconnected_cookies")}

        cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies_list}

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
            result_json = self.driver.execute_script(js_script)
        except (WebDriverException, InvalidSessionIdException):
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
            user_agent = self.driver.execute_script("return navigator.userAgent;")
        except (WebDriverException, InvalidSessionIdException):
            user_agent = "unknown"

        credentials = {
            "cookies": cookies_dict,
            "verification_token": verification_token,
            "student_id": student_id,
            "user_agent": user_agent
        }

        print(f"Credentials extracted. Student ID: {student_id}")
        return credentials

    def silent_relogin(self, headless: bool = False) -> dict:
        """
        Re-authenticate without user interaction.

        Launches a fresh browser (reusing the local Edge profile so saved
        credentials persist). If redirected to Okta, automatically clicks
        the login button — no 2FA is required when the browser profile's
        session is still warm.

        Set headless=True for cloud/headless deployments.

        Returns the same credential dict as extract_credentials() on success.
        Raises RuntimeError if the automated flow times out.
        """
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

        print("[Auto-Recovery] Launching browser for silent re-authentication...")
        self.launch_browser(headless=headless)

        print("[Auto-Recovery] Waiting for page to stabilize...")
        time.sleep(3)

        # Check if we landed on an Okta login page
        current_url = ""
        try:
            current_url = self.driver.current_url
        except Exception:
            pass

        if "okta" in current_url.lower():
            print("[Auto-Recovery] Detected Okta login page. Attempting auto-click...")
            self._click_okta_login()
            print("[Auto-Recovery] Waiting for post-login redirect...")
        else:
            print("[Auto-Recovery] Not on Okta page, checking if already logged in...")

        # Wait until we reach the DegreePlans page (or timeout)
        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: "DegreePlans" in (d.current_url or "")
            )
        except TimeoutException:
            raise RuntimeError(
                "Silent re-login timed out: did not reach DegreePlans page. "
                "A manual re-login may be required."
            )

        print("[Auto-Recovery] Reached DegreePlans. Extracting credentials...")
        creds = self.extract_credentials()

        if "error" in creds:
            raise RuntimeError(
                f"Credential extraction failed during recovery: {creds['error']}"
            )

        print(f"[Auto-Recovery] Credentials extracted. Student ID: {creds.get('student_id')}")
        return creds

    def _click_okta_login(self):
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
                btn = WebDriverWait(self.driver, 5).until(
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

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None
