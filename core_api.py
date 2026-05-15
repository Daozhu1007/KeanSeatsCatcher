import json
import time
import requests
from typing import List, Tuple, Any

from i18n import i18n

# Action string for waitlist fallback. Change this if your Ellucian system
# uses a different action code for waitlisted courses.
WAITLIST_ACTION = "Waitlist"


class KeanApiClient:
    def __init__(self, cookies_dict: dict, verification_token: str, student_id: str):
        self.session = requests.Session()
        self.session.trust_env = False
        self.student_id = student_id
        self.base_url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"

        self.log_callback = None

        for name, value in cookies_dict.items():
            self.session.cookies.set(name, value)

        self.session.headers.update({
            '__requestverificationtoken': verification_token,
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'content-type': 'application/json; charset=UTF-8',
            'x-requested-with': 'XMLHttpRequest',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0'
        })

    def _log(self, msg: str, level: str = "normal"):
        print(f"[{level.upper()}] {msg}")
        if self.log_callback:
            self.log_callback(msg, level)

    @staticmethod
    def _is_duplicate_registration_msg(msg: str) -> bool:
        msg_lower = msg.lower()
        return "duplicate" in msg_lower or "already registered" in msg_lower

    @staticmethod
    def _is_timeout_error(msg) -> bool:
        msg_lower = str(msg).lower()
        return "timeout" in msg_lower or "超时" in msg_lower

    def check_section_status(self, section_id: str, enable_waitlist: bool = False) -> bool:
        """
        Query section availability via the real Ellucian Banner API.

        Returns True if seats are available (Available > 0) or if waitlist
        capacity remains (Waitlisted < WaitlistMaximum) when enable_waitlist is set.
        Returns False on any error — the caller continues polling.
        """
        root = '/'.join(self.base_url.split('/')[:3])
        url = f"{root}/Student/Student/Courses/SectionDetails"
        payload = {"sectionId": str(section_id), "studentId": str(self.student_id)}

        try:
            resp = self.session.post(url, json=payload, timeout=(1.5, 8))

            if resp.status_code != 200:
                self._log(
                    i18n.tr("log_section_check_http", section_id, resp.status_code),
                    "debug")
                return False

            try:
                data = resp.json()
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
                self._log(i18n.tr("log_section_check_non_json", section_id), "debug")
                return False

            available = data.get('Available', 0)
            enrolled = data.get('Enrolled', '?')
            capacity = data.get('Capacity', '?')
            waitlisted = data.get('Waitlisted', 0)
            waitlist_max = data.get('WaitlistMaximum', 0)

            if available > 0:
                self._log(
                    i18n.tr("log_section_open", section_id, available, enrolled, capacity),
                    "success")
                return True

            if enable_waitlist and waitlisted < waitlist_max:
                self._log(
                    i18n.tr("log_section_waitlist_open", section_id, waitlisted, waitlist_max),
                    "normal")
                return True

            self._log(
                i18n.tr("log_section_full", section_id, enrolled, capacity),
                "normal")
            return False

        except requests.exceptions.Timeout:
            self._log(i18n.tr("log_section_check_timeout", section_id), "debug")
            return False
        except requests.exceptions.RequestException as e:
            self._log(i18n.tr("log_section_check_error", section_id, e), "debug")
            return False

    def test_latency(self) -> int:
        try:
            start = time.perf_counter()
            self.session.head(self.base_url, timeout=5)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return elapsed_ms
        except Exception:
            return -1

    def _parse_notifications(self, items: list) -> Tuple[bool, list]:
        errors = []
        for item in items:
            if item.get('Type') in ('Warning', 'Error'):
                msg = item.get('Message', 'Unknown system rejection reason')
                if self._is_duplicate_registration_msg(msg):
                    self._log(i18n.tr("log_already_registered", msg), "success")
                    return True, []
                else:
                    errors.append(msg)
        return False, errors

    def register_multiple_sections(self, section_ids: List[str], action: str = "Add") -> Tuple[bool, Any]:
        url = f"{self.base_url}/RegisterSections"
        registrations = [{"SectionId": str(sec_id), "Credits": 3, "Action": action, "DropReasonCode": None,
                          "IntentToWithdrawId": None} for sec_id in section_ids]
        payload = {"sectionRegistrations": registrations, "studentId": str(self.student_id)}

        try:
            self._log(i18n.tr("log_send_package", section_ids))
            resp = self.session.post(url, json=payload, timeout=(1.5, 12))

            if resp.status_code == 200:
                try:
                    response_data = resp.json()
                except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
                    self._log(i18n.tr("log_non_json_response"), "error")
                    return False, i18n.tr("msg_response_format_error", resp.text[:200])

                if isinstance(response_data, list):
                    notifications = response_data
                elif isinstance(response_data, dict) and 'notifications' in response_data:
                    notifications = response_data['notifications']
                else:
                    self._log(i18n.tr("log_unknown_response", str(response_data)[:300]), "error")
                    return False, i18n.tr("msg_unknown_response")

                is_success, error_messages = self._parse_notifications(notifications)

                if is_success:
                    return True, response_data
                elif error_messages:
                    self._log(i18n.tr("log_blocked", error_messages), "error")
                    return False, i18n.tr("log_block_reason", ', '.join(error_messages))
                else:
                    return True, response_data

            else:
                self._log(i18n.tr("log_http_error", resp.status_code), "error")
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

        except requests.exceptions.ReadTimeout:
            self._log(i18n.tr("log_read_timeout"), "error")
            return False, i18n.tr("log_read_timeout")
        except requests.exceptions.ConnectTimeout:
            self._log(i18n.tr("log_connect_timeout"), "error")
            return False, i18n.tr("log_connect_timeout")
        except requests.exceptions.RequestException as e:
            self._log(i18n.tr("log_fatal_error", e), "error")
            return False, str(e)

    def complete_registration(self) -> bool:
        url = f"{self.base_url}/CompleteRegistration"
        payload = {"studentId": str(self.student_id), "notifications": []}

        try:
            self._log(i18n.tr("log_send_confirm"))
            resp = self.session.post(url, json=payload, timeout=(1.5, 12))
            if resp.status_code == 200:
                self._log(i18n.tr("log_confirm_ok"), "success")
                return True
            self._log(i18n.tr("log_confirm_rejected", resp.status_code, resp.text[:200]), "error")
            return False

        except requests.exceptions.ReadTimeout:
            self._log(i18n.tr("log_confirm_timeout"), "success")
            return True

        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                self._log(i18n.tr("log_confirm_timeout2"), "success")
                return True

            self._log(i18n.tr("log_confirm_error", e), "error")
            return False

    def execute_full_attack(self, section_ids: List[str], enable_waitlist: bool = False) -> Tuple[bool, str]:
        success, result_data = self.register_multiple_sections(section_ids)

        if success:
            confirm_success = self.complete_registration()
            if confirm_success:
                return True, i18n.tr("msg_attack_complete")
            else:
                return True, i18n.tr("msg_attack_locked")

        # Fallback: if waitlist is enabled and the failure is not a timeout, try waitlist
        if enable_waitlist and not self._is_timeout_error(result_data):
            self._log(i18n.tr("log_waitlist_attempt"))
            success, result_data = self.register_multiple_sections(section_ids, action=WAITLIST_ACTION)

            if success:
                confirm_success = self.complete_registration()
                if confirm_success:
                    return True, i18n.tr("msg_attack_complete")
                else:
                    return True, i18n.tr("msg_attack_locked")
            else:
                return False, i18n.tr("msg_waitlist_failed", str(result_data))

        return False, str(result_data)
