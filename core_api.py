import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, replace
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

from event_log import EventLog, timestamp
from i18n import i18n
from registration_state import (RegistrationRecord, RegistrationSnapshot, SectionResult, SectionStatus,
                                SessionCheck, SessionStatus, SubmissionResult,
                                SubmissionStatus)

WAITLIST_ACTION = "Waitlist"


class SessionExpiredError(Exception):
    """The authenticated session was rejected or redirected to login."""


class _PageEvidence(HTMLParser):
    def __init__(self):
        super().__init__()
        self.login = False
        self.logout = False
        self.token = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.login |= attrs.get("type", "").lower() == "password"
            self.login |= attrs.get("name", "").lower() in ("samlrequest", "wresult")
            self.token |= attrs.get("name", "").lower() == "__requestverificationtoken"
        if tag in ("a", "form"):
            path = urlparse(attrs.get("href", attrs.get("action", ""))).path.lower()
            self.logout |= path.endswith(("/logout", "/logoff"))


class KeanApiClient:
    def __init__(self, cookies_dict: dict, verification_token: str, student_id: str,
                 registration_reader=None, log_dir=None):
        self.session = requests.Session()
        self.session.trust_env = False
        self.student_id = student_id
        self.base_url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"
        self.log_callback = None
        self.session.cookies.update(cookies_dict)
        self.session.headers.update({
            "__requestverificationtoken": verification_token,
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/json; charset=UTF-8",
            "x-requested-with": "XMLHttpRequest",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
        })
        self.registration_reader = registration_reader
        self.results = {}
        self.events = EventLog(log_dir)
        self.round_id = 0
        self.retry_delay = 0
        self._unknown_rounds = 0
        self._retry_after = {}
        self._drop_pending = set()
        self._drop_attempted = set()
        self._log_error_reported = False

    def update_credentials(self, cookies_dict, verification_token, student_id=None):
        if student_id is not None and str(student_id) != str(self.student_id):
            raise ValueError("Recovered account differs from the active registration account")
        self.session.cookies.clear()
        self.session.cookies.update(cookies_dict)
        self.session.headers["__requestverificationtoken"] = verification_token

    def _log(self, msg, level="normal"):
        print(f"[{level.upper()}] {msg}")
        if self.log_callback:
            self.log_callback(msg, level)

    def _event(self, event, **fields):
        self.events.write(event, round_id=self.round_id, **fields)
        if self.events.error and not self._log_error_reported:
            self._log(i18n.tr("log_file_error", self.events.error), "error")
            self._log_error_reported = True

    @staticmethod
    def _login_url(url):
        parsed = urlparse(url)
        return (bool(re.search(r"/(login|signin|sign-in|sso|saml)(/|$)", parsed.path, re.I))
                or "okta" in (parsed.hostname or "").lower())

    def _check_expired(self, resp):
        if resp.status_code == 401:
            raise SessionExpiredError("http_401")
        if self._login_url(resp.headers.get("Location", "")) or self._login_url(resp.url):
            raise SessionExpiredError("login_redirect")
        # An outage page mentioning 'login' is not evidence of expiration.
        if resp.status_code == 200 and resp.text.lstrip().startswith("<"):
            page = _PageEvidence()
            page.feed(resp.text)
            if page.login:
                raise SessionExpiredError("login_page")

    def _request(self, operation, method, url, section_ids=(), **kwargs):
        """All traffic, including future read-only adapters, is audited here."""
        request_id = uuid.uuid4().hex
        start = time.perf_counter()
        self._event("request_started", request_id=request_id, operation=operation,
                    method=method, section_ids=list(section_ids))
        reason, status, response_received_at = "unknown", None, None
        try:
            # Never forward the verification token to an SSO redirect target.
            resp = self.session.request(method, url, allow_redirects=False, **kwargs)
            response_received_at = timestamp()
            status = resp.status_code
            self._check_expired(resp)
            reason = "response_received"
            return resp
        except SessionExpiredError:
            reason = "session_expired"
            raise
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
            raise
        finally:
            self._event("request_finished", request_id=request_id, operation=operation,
                        section_ids=list(section_ids), http_status=status, reason=reason,
                        response_received_at=response_received_at,
                        elapsed_ms=round((time.perf_counter() - start) * 1000, 3))

    def check_session(self):
        start = time.perf_counter()
        status, reason = SessionStatus.UNKNOWN, "unrecognized_authenticated_page"
        try:
            resp = self._request("session_check", "GET", self.base_url, timeout=(1.5, 8))
            if resp.status_code == 200:
                page = _PageEvidence()
                page.feed(resp.text)
                if page.logout and page.token:
                    status, reason = SessionStatus.VALID, "authenticated_page"
            else:
                reason = f"http_{resp.status_code}"
        except SessionExpiredError:
            status, reason = SessionStatus.EXPIRED, "session_expired"
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
        result = SessionCheck(status, int((time.perf_counter() - start) * 1000), reason)
        self._event("session_check", **asdict(result))
        return result

    def test_latency(self):
        """Compatibility alias; returns a tri-state SessionCheck, not a ping."""
        return self.check_session()

    def check_section_status(self, section_id, enable_waitlist=False):
        root = "/".join(self.base_url.split("/")[:3])
        try:
            resp = self._request("section_availability", "POST",
                                 f"{root}/Student/Student/Courses/SectionDetails",
                                 [section_id], json={"sectionId": str(section_id),
                                 "studentId": str(self.student_id)}, timeout=(1.5, 8))
            if resp.status_code != 200:
                return False
            data = resp.json()
            if not isinstance(data, dict):
                return False
            available = data.get("Available")
            waitlisted, maximum = data.get("Waitlisted"), data.get("WaitlistMaximum")
            has_seat = isinstance(available, (int, float)) and available > 0
            has_waitlist = (enable_waitlist and isinstance(waitlisted, (int, float))
                            and isinstance(maximum, (int, float)) and waitlisted < maximum)
            self._event("availability", section_id=str(section_id),
                        has_seat=has_seat, has_waitlist=bool(has_waitlist))
            return bool(has_seat or has_waitlist)
        except (requests.exceptions.RequestException, ValueError):
            return False

    @staticmethod
    def _is_duplicate_registration_msg(msg):
        return bool(re.search(r"\bduplicate\b|\balready registered\b", msg, re.I))

    def _parse_notifications(self, items, section_ids):
        """Notifications describe submission only, never prove enrollment.

        Associate only an explicit SectionId, exact numeric ID in the message,
        or single-target request. Course names in batch messages are ambiguous.
        Store reason codes, not raw server text which may contain secrets.
        """
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            return SubmissionResult(SubmissionStatus.UNKNOWN, "invalid_notifications")
        rejections, duplicates = {}, set()
        has_errors = False
        for item in items:
            msg = item.get("Message", "")
            if (not isinstance(msg, str) or item.get("Type") not in
                    ("Warning", "Error", "Information", "Info", "Success")):
                return SubmissionResult(SubmissionStatus.UNKNOWN, "invalid_notification_message")
            duplicate = self._is_duplicate_registration_msg(msg)
            if item.get("Type") not in ("Warning", "Error") and not duplicate:
                continue
            has_errors = True
            explicit = item.get("SectionId")
            if explicit is not None:
                matched = [str(explicit)] if str(explicit) in section_ids else []
            else:
                matched = [sid for sid in section_ids
                           if re.search(rf"(?<!\w){re.escape(sid)}(?!\w)", msg)]
                if not matched and len(section_ids) == 1:
                    matched = list(section_ids)
            if len(matched) != 1:
                continue
            sid = matched[0]
            if duplicate:
                duplicates.add(sid)
            else:
                reason = ("closed" if "closed" in msg.lower() else
                          "credits_rejected" if "credit" in msg.lower() else "server_rejected")
                rejections[sid] = reason
        status = (SubmissionStatus.REJECTED if len(rejections) == len(section_ids) and section_ids
                  else SubmissionStatus.UNKNOWN if has_errors else SubmissionStatus.ACKNOWLEDGED)
        return SubmissionResult(status, "notifications" if has_errors else "acknowledged",
                                rejections, duplicates)

    def _submission_response(self, resp, section_ids):
        if resp.status_code != 200:
            return SubmissionResult(SubmissionStatus.UNKNOWN, f"http_{resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return SubmissionResult(SubmissionStatus.UNKNOWN, "non_json_response")
        items = data if isinstance(data, list) else data.get("notifications") if isinstance(data, dict) else None
        return self._parse_notifications(items, section_ids)

    def register_multiple_sections(self, section_ids, action="Add", drop_section_id=None):
        section_ids = self.pending_sections(section_ids)
        if not section_ids:
            return SubmissionResult(SubmissionStatus.UNKNOWN, "nothing_to_submit")
        previous = {sid: replace(self.results[sid]) for sid in section_ids}
        previous_retry = self._retry_after.copy()
        previous_drop_pending = self._drop_pending.copy()
        previous_drop_attempted = self._drop_attempted.copy()
        registrations = []
        if drop_section_id:
            registrations.append({"SectionId": str(drop_section_id), "Action": "Drop",
                                  "Credits": None, "DropReasonCode": None, "IntentToWithdrawId": None})
            self._drop_pending.add(str(drop_section_id))
            self._drop_attempted.add(str(drop_section_id))
        # Do not override fixed credits. Omission needs real webpage validation;
        # variable-credit selection remains unsupported.
        registrations.extend({"SectionId": sid, "Action": action,
                              "DropReasonCode": None, "IntentToWithdrawId": None}
                             for sid in section_ids)
        for sid in section_ids:
            result = self.results[sid]
            result.attempted = result.awaiting_verification = True
            result.status, result.reason, result.rejection = SectionStatus.UNKNOWN, "request_sent", ""
            self._retry_after[sid] = time.monotonic() + 5
        try:
            resp = self._request("register_" + action.lower(), "POST",
                                 f"{self.base_url}/RegisterSections", section_ids,
                                 json={"sectionRegistrations": registrations,
                                       "studentId": str(self.student_id)}, timeout=(1.5, 12))
            submission = self._submission_response(resp, section_ids)
        except SessionExpiredError:
            # An explicit authentication rejection did not accept this request.
            # Undo only this submission; a lost CompleteRegistration response
            # still retains its earlier Add's uncertainty and drop tracking.
            for sid, result in previous.items():
                self.results[sid] = result
                result.status, result.reason = SectionStatus.UNKNOWN, "session_expired"
                result.awaiting_verification = False
            self._retry_after = previous_retry
            self._drop_pending = previous_drop_pending
            self._drop_attempted = previous_drop_attempted
            raise
        except requests.exceptions.RequestException as exc:
            submission = SubmissionResult(SubmissionStatus.UNKNOWN, type(exc).__name__)
        for sid in section_ids:
            # The grace period starts when the response/timeout is observed,
            # not when the request began (a read timeout already took 12s).
            self._retry_after[sid] = time.monotonic() + 5
            result = self.results[sid]
            result.reason = submission.reason
            if sid in submission.rejections:
                result.status = SectionStatus.REJECTED
                result.reason = result.rejection = submission.rejections[sid]
            if sid in submission.duplicates:
                result.status, result.reason = SectionStatus.UNKNOWN, "duplicate_requires_verification"
        self._event("submission", action=action, section_ids=section_ids,
                    status=submission.status, reason=submission.reason,
                    rejections=submission.rejections, duplicates=sorted(submission.duplicates),
                    drop_section_id=drop_section_id)
        return submission

    def complete_registration(self):
        try:
            resp = self._request("complete_registration", "POST",
                                 f"{self.base_url}/CompleteRegistration",
                                 json={"studentId": str(self.student_id), "notifications": []},
                                 timeout=(1.5, 12))
            result = self._submission_response(resp, [])
        except requests.exceptions.RequestException as exc:
            result = SubmissionResult(SubmissionStatus.UNKNOWN, type(exc).__name__)
        self._event("completion", status=result.status, reason=result.reason)
        return result

    def _ensure_sections(self, section_ids):
        ids = list(dict.fromkeys(str(s) for s in section_ids))
        for sid in ids:
            self.results.setdefault(sid, SectionResult(sid))
        return ids

    def pending_sections(self, section_ids):
        return [sid for sid in self._ensure_sections(section_ids)
                if self.results[sid].status in (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED,
                                                SectionStatus.REJECTED)
                and not self.results[sid].awaiting_verification]

    def needs_verification(self, section_ids):
        return bool(self._drop_pending) or any(
            self.results[sid].awaiting_verification or self.results[sid].status == SectionStatus.UNKNOWN
            for sid in self._ensure_sections(section_ids))

    def read_registration_status(self, section_ids):
        if self.registration_reader is None:
            return RegistrationSnapshot()
        return self.registration_reader.read(self, section_ids)

    def verify_registration(self, section_ids):
        ids = self._ensure_sections(section_ids)
        query_ids = list(dict.fromkeys(ids + sorted(self._drop_pending)))
        try:
            snapshot = self.read_registration_status(query_ids)
            if (not isinstance(snapshot, RegistrationSnapshot)
                    or not isinstance(snapshot.records, Mapping)
                    or any(not isinstance(record, RegistrationRecord)
                           for record in snapshot.records.values())):
                raise ValueError("Invalid registration reader contract")
        except requests.exceptions.RequestException as exc:
            snapshot = RegistrationSnapshot(reason=type(exc).__name__)
        except (ValueError, TypeError, KeyError):
            snapshot = RegistrationSnapshot(reason="invalid_registration_records")
        now = timestamp()
        for sid in ids:
            result = self.results[sid]
            record = snapshot.records.get(sid)
            # Confirmed enrollment is monotonic for this client; later outages
            # must not re-enable Add. Re-authenticate after an external manual drop.
            if result.status == SectionStatus.REGISTERED:
                continue
            if record and record.status in (SectionStatus.REGISTERED, SectionStatus.WAITLISTED):
                result.status, result.reason = record.status, "account_record"
                result.awaiting_verification = False
                if record.status == SectionStatus.REGISTERED:
                    result.first_confirmed_at = result.first_confirmed_at or now
                    result.server_registered_at = record.server_registered_at
            elif record and record.status in (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED):
                result.status = SectionStatus.REJECTED if result.rejection else record.status
                result.reason = result.rejection or "account_record_not_registered"
                # A timed-out request may still be processing on the server.
                result.awaiting_verification = (sid in self._retry_after
                                               and time.monotonic() < self._retry_after[sid])
            elif result.status != SectionStatus.WAITLISTED:
                result.awaiting_verification = True
                if result.status != SectionStatus.REJECTED:
                    result.status = SectionStatus.UNKNOWN
                result.reason = result.rejection or snapshot.reason
        for sid in list(self._drop_pending):
            record = snapshot.records.get(sid)
            if record and record.status in (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED):
                self._drop_pending.remove(sid)
        self._event("verification", reason=snapshot.reason, sections=self._states(ids),
                    drop_pending=sorted(self._drop_pending))
        return snapshot

    def _states(self, ids):
        return {sid: asdict(self.results[sid]) for sid in ids}

    def all_registered(self, section_ids):
        ids = self._ensure_sections(section_ids)
        return bool(ids) and not self._drop_pending and all(
            self.results[sid].status == SectionStatus.REGISTERED for sid in ids)

    def result_message(self, section_ids):
        message = "; ".join(i18n.tr("log_section_result", sid,
                         i18n.tr("state_" + self.results[sid].status.value.lower()),
                         self.results[sid].reason) for sid in section_ids)
        if self._drop_pending:
            message += "; " + i18n.tr("log_drop_unknown", ", ".join(sorted(self._drop_pending)))
        return message

    def execute_full_attack(self, section_ids, enable_waitlist=False, drop_section_id=None,
                            submission_ids=None):
        """Reconcile first; submission_ids=[] performs a strictly read-only round.

        Polling workers pass only sections whose availability they just checked.
        """
        ids = self._ensure_sections(section_ids)
        self.round_id += 1
        self._event("round_started", section_ids=ids)
        if self.round_id == 1:
            self._log(i18n.tr("log_event_path", str(self.events.path)))
            if self.registration_reader is None:
                self._log(i18n.tr("log_reader_unavailable"), "error")
        try:
            # Includes never-attempted targets: existing registrations must be
            # discovered before the first Add, even if their sections are full.
            if self.needs_verification(ids):
                self.verify_registration(ids)
            pending = self.pending_sections(ids)
            if submission_ids is not None:
                pending = [sid for sid in pending if sid in submission_ids]
            if self._drop_pending:
                pending = []
            # Only a previous Closed rejection plus an authoritative negative
            # registration read can enable Waitlist, never a timeout/duplicate.
            fallback = [sid for sid in pending if enable_waitlist
                        and self.results[sid].rejection == "closed"]
            for action, targets in (("Add", [sid for sid in pending if sid not in fallback]),
                                    (WAITLIST_ACTION, fallback)):
                if targets and not self._drop_pending:
                    drop = drop_section_id if drop_section_id not in self._drop_attempted else None
                    submitted = self.register_multiple_sections(targets, action=action, drop_section_id=drop)
                    if submitted.status == SubmissionStatus.ACKNOWLEDGED:
                        self.complete_registration()
                    self.verify_registration(ids)
            return self.all_registered(ids), self.result_message(ids)
        except SessionExpiredError:
            self._event("session_expired", section_ids=ids)
            raise
        finally:
            unknown = self.needs_verification(ids)
            self._unknown_rounds = self._unknown_rounds + 1 if unknown else 0
            self.retry_delay = min(30, 2 ** min(self._unknown_rounds, 5)) if unknown else 0
            self._event("round_finished", sections=self._states(ids),
                        all_registered=self.all_registered(ids), retry_delay=self.retry_delay,
                        drop_pending=sorted(self._drop_pending))
            self._log(self.result_message(ids), "success" if self.all_registered(ids) else "normal")
