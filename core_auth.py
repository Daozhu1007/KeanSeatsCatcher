import json
import time
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException

from i18n import i18n


class KeanAuthManager:
    def __init__(self):
        self.driver = None
        self.login_url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"

    def launch_browser(self):
        print("Initializing Edge browser...")
        try:
            options = Options()
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_experimental_option("detach", True)

            self.driver = webdriver.Edge(options=options)

            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
            })

            self.driver.get(self.login_url)
            print("Browser launched. Please complete SSO login manually...")

        except Exception as e:
            print(f"Browser launch failed: {str(e)}")
            raise e

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

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None
