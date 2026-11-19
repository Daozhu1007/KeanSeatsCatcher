import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

from catch_history import CatchHistory, sent_credits
from event_log import (EventLog, Redactor, course_name, credential_values, request_details,
                       response_details, response_messages, timestamp)
from i18n import i18n
from registration_state import (RegistrationRecord, RegistrationSnapshot, SectionResult, SectionStatus,
                                SessionCheck, SessionStatus, SubmissionResult,
                                SubmissionStatus, section_credit_limits, valid_credits)

WAITLIST_ACTION = "Waitlist"
REGISTRATION_GRACE_SECONDS = 5


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
            name = (attrs.get("name") or "").lower()
            self.login |= (attrs.get("type") or "").lower() == "password"
            self.login |= name in ("samlrequest", "wresult")
            self.token |= name == "__requestverificationtoken" and bool(attrs.get("value"))
        if tag in ("a", "form"):
            try:
                path = urlparse(attrs.get("href") or attrs.get("action") or "").path.lower()
            except ValueError:
                return
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
        self.section_terms = {}
        self._section_credit_limits = {}
        self._planned_credits = {}
        self._credit_lookups = set()
        self._credit_lookup_lock = threading.Lock()
        self.results = {}
        self.events = EventLog(log_dir)
        self.history = CatchHistory()
        self.history_callback = None
        self._catalog_metadata = {}
        self._account_metadata = {}
        self._catch_attempts = {}
        self._last_registration_read = None
        self.round_id = 0
        self.retry_delay = 0
        self._unknown_rounds = 0
        self._retry_after = {}
        self._negative_reads = set()
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

    def _event(self, event, *, round_id=None, **fields):
        self.events.write(event, round_id=self.round_id if round_id is None else round_id,
                          student_id=str(self.student_id), **fields)
        if self.events.error and not self._log_error_reported:
            self._log(i18n.tr("log_file_error", self.events.error), "error")
            self._log_error_reported = True

    @staticmethod
    def _login_url(url):
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        return (bool(re.search(r"/(login|signin|sign-in|sso|saml)(/|$)", parsed.path, re.I))
                or (parsed.hostname or "").lower().endswith((".okta.com", ".okta-emea.com")))

    def _check_expired(self, resp):
        if resp.status_code == 401:
            raise SessionExpiredError("http_401")
        if 300 <= resp.status_code < 400 and self._login_url(resp.headers.get("Location")):
            raise SessionExpiredError("login_redirect")
        if resp.status_code == 200:
            if resp.content.lstrip().startswith(b"<"):
                page = _PageEvidence()
                page.feed(resp.text)
                if page.login and not (page.logout and page.token):
                    raise SessionExpiredError("login_page")
            elif b'"InvalidSession"' in resp.content:
                try:
                    data = resp.json()
                except ValueError:
                    return
                if isinstance(data, dict) and data.get("InvalidSession") in (True, "true"):
                    raise SessionExpiredError("invalid_session_response")
        elif resp.status_code == 400 and b"antiforgery token" in resp.content:
            # Self-Service explicitly rejected the token before processing the action.
            try:
                data = resp.json()
            except ValueError:
                return
            if data == "Could not validate this request's antiforgery token.":
                raise SessionExpiredError("invalid_verification_token")

    def _request(self, operation, method, url, section_ids=(), *, session=None, capture=None, **kwargs):
        """Audit the actual payload and the credential-filtered response."""
        request_session = self.session if session is None else session
        secrets = credential_values(request_session)
        request_id = uuid.uuid4().hex
        round_id = self.round_id
        ids = list(section_ids)
        started = dict(request_id=request_id, operation=operation, method=method,
                       section_ids=ids, background=session is not None, started_at=timestamp(),
                       request=request_details(url, kwargs, Redactor(secrets)))
        self._event("request_started", round_id=round_id, **started)
        reason, detail = "unknown", None
        resp, response_received_at, elapsed_ms = None, None, None
        start = time.perf_counter()
        try:
            # Never forward the verification token to an SSO redirect target.
            resp = request_session.request(method, url, allow_redirects=False, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            response_received_at = timestamp()
            self._check_expired(resp)
            reason = "response_received"
            return resp
        except SessionExpiredError as exc:
            reason, detail = "session_expired", str(exc)
            self._event("session_rejected", round_id=round_id, request_id=request_id, operation=operation,
                        reason=detail)
            raise
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
            raise
        finally:
            if elapsed_ms is None:
                elapsed_ms = (time.perf_counter() - start) * 1000
            secrets.update(credential_values(request_session))
            if resp is not None:
                secrets.update(cookie.value for cookie in resp.cookies)
            try:
                response = response_details(resp, operation, ids, self.student_id, Redactor(secrets))
            except (ValueError, TypeError, RecursionError) as exc:
                response = {"format": "unavailable", "audit_error": type(exc).__name__}
            finished = dict(http_status=resp.status_code if resp is not None else None,
                            reason=reason, error_detail=detail, response_received_at=response_received_at,
                            elapsed_ms=round(elapsed_ms, 3), response=response)
            trace = {**started, **finished, "round_id": round_id}
            if capture is not None:
                capture.update(trace)
            if operation == "registration_read":
                self._last_registration_read = trace
            self._event("request_finished", round_id=round_id, request_id=request_id, operation=operation,
                        section_ids=ids, background=session is not None, **finished)
            if operation in ("register_add", "register_waitlist", "complete_registration", "registration_read"):
                self._log_request(trace)

    def _log_request(self, trace):
        operation = trace["operation"]
        ids = trace["section_ids"]
        status = trace["http_status"]
        outcome = f"HTTP {status}" if status is not None else trace["reason"]
        if trace["error_detail"]:
            outcome += f" · {trace['error_detail']}"
        level = "normal" if status == 200 and trace["reason"] == "response_received" else "error"
        self._log(i18n.tr("log_request_summary", i18n.tr("operation_" + operation),
                          ", ".join(ids), outcome, trace["elapsed_ms"]), level)
        for sid in ids:
            credits = sent_credits([trace], sid)
            if credits["status"] != "unrecorded":
                self._log(i18n.tr("log_credits_sent", self._section_label(sid),
                                  credits.get("value", i18n.tr("credits_omitted"))))
        for kind, message, sid in response_messages(trace["response"]):
            reference = f" · {sid}" if sid is not None else ""
            self._log(f"[{kind}{reference}] {message[:2000]}",
                      "error" if level == "error" or kind in ("Error", "Warning") else "normal")

    def check_session(self):
        start = time.perf_counter()
        status, reason = SessionStatus.UNKNOWN, "unrecognized_authenticated_page"
        try:
            resp = self._request("session_check", "GET", self.base_url,
                                 headers={"Cache-Control": "no-cache"}, timeout=(1.5, 8))
            if resp.status_code == 200:
                page = _PageEvidence()
                page.feed(resp.text)
                if page.logout and page.token:
                    status, reason = SessionStatus.VALID, "authenticated_page"
            else:
                reason = f"http_{resp.status_code}"
        except SessionExpiredError as exc:
            status, reason = SessionStatus.EXPIRED, str(exc)
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
        result = SessionCheck(status, int((time.perf_counter() - start) * 1000), reason)
        self._event("session_check", **asdict(result))
        return result

    def test_latency(self):
        """Compatibility alias; returns a tri-state SessionCheck, not a ping."""
        return self.check_session()

    def _section_details(self, section_id, operation, timeout, *, session=None):
        root = "/".join(self.base_url.split("/")[:3])
        sid = str(section_id)
        path = "/Student/Student/Courses/SectionDetails" if session is None else "/Student/Courses/SectionDetails"
        audit = {}
        resp = self._request(operation, "POST", f"{root}{path}",
                             [sid], json={"sectionId": sid, "studentId": str(self.student_id)},
                             timeout=timeout, session=session, capture=audit)
        if resp.status_code != 200:
            if session is None:
                self._log(i18n.tr("log_section_check_http", sid, resp.status_code), "normal")
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        returned_id = data.get("Id")
        if returned_id is not None and str(returned_id) != sid:
            return None
        term = data.get("TermId")
        if session is None and returned_id is not None and isinstance(term, str) and term.strip():
            self.section_terms[sid] = term
        limits = section_credit_limits(data)
        if returned_id is not None and limits is not None:
            if session is None:
                self._section_credit_limits[sid] = limits
            else:
                self._section_credit_limits.setdefault(sid, limits)
        if returned_id is not None:
            metadata = audit["response"].get("body")
            if isinstance(metadata, dict):
                audit_term = metadata.get("TermId")
                details = {
                    "course_name": course_name(metadata),
                    "term_id": audit_term if isinstance(audit_term, str) else None,
                    "credits": limits[0] if limits is not None and limits[0] == limits[1] else None,
                }
                if session is None:
                    self._catalog_metadata[sid] = details
                else:
                    self._catalog_metadata.setdefault(sid, details)
        return data

    def _credits_for(self, section_id):
        planned = self._planned_credits.get(section_id)
        limits = self._section_credit_limits.get(section_id)
        if limits is None:
            return planned
        minimum, maximum = limits
        if minimum == maximum:
            return minimum
        if planned is not None and minimum <= planned <= maximum:
            return planned
        return None

    def prefetch_registration_credits(self, section_ids, *, include_section_details=False):
        # Preparatory reads use a separate session and never gate a registration POST.
        read_credits = getattr(self.registration_reader, "read_credits", None)
        if read_credits is None and not include_section_details:
            return
        with self._credit_lookup_lock:
            ids = [sid for sid in dict.fromkeys(map(str, section_ids))
                   if sid not in self._credit_lookups and self._credits_for(sid) is None]
            self._credit_lookups.update(ids)
        if ids:
            try:
                threading.Thread(
                    target=self._prefetch_registration_credits,
                    args=(ids, self.session.headers.copy(), self.session.cookies.copy(),
                          read_credits, include_section_details), daemon=True).start()
            except RuntimeError:
                with self._credit_lookup_lock:
                    self._credit_lookups.difference_update(ids)

    def _prefetch_registration_credits(self, ids, headers, cookies, read_credits, include_section_details):
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.headers.update(headers)
                session.cookies.update(cookies)
                if read_credits is not None:
                    try:
                        for sid, credits in read_credits(self, ids, session).items():
                            self._planned_credits.setdefault(sid, credits)
                    except SessionExpiredError:
                        return  # The worker owns session recovery.
                    except (requests.exceptions.RequestException, ValueError):
                        pass  # A catalog response may still resolve fixed credits.
                if include_section_details:
                    for sid in ids:
                        if self._credits_for(sid) is None:
                            try:
                                self._section_details(sid, "section_credit_lookup", (1.5, 3), session=session)
                            except SessionExpiredError:
                                break
                            except (requests.exceptions.RequestException, ValueError):
                                continue
        finally:
            with self._credit_lookup_lock:
                self._credit_lookups.difference_update(ids)

    def lookup_section_term(self, section_id):
        if section_id not in self.section_terms:
            try:
                self._section_details(section_id, "section_term_lookup", (1.5, 3))
            except (requests.exceptions.RequestException, SessionExpiredError, ValueError):
                # Keep any positive account records from this same reconciliation.
                # The next authenticated request can handle a session rejection.
                return None
        return self.section_terms.get(section_id)

    def check_section_status(self, section_id, enable_waitlist=False):
        try:
            data = self._section_details(section_id, "section_availability", (1.5, 8))
            if data is None:
                return False
            available = data.get("Available")
            enrolled, capacity = data.get("Enrolled", "?"), data.get("Capacity", "?")
            waitlisted, maximum = data.get("Waitlisted"), data.get("WaitlistMaximum")
            has_seat = isinstance(available, (int, float)) and available > 0
            has_waitlist = (enable_waitlist and isinstance(waitlisted, (int, float))
                            and isinstance(maximum, (int, float)) and waitlisted < maximum
                            and data.get("AllowWaitlist") is not False)
            self._event("availability", section_id=str(section_id),
                        has_seat=has_seat, has_waitlist=bool(has_waitlist), **self._course_details(str(section_id)))
            if has_seat:
                self._log(i18n.tr("log_section_open", section_id, available, enrolled, capacity), "success")
            elif has_waitlist:
                self._log(i18n.tr("log_section_waitlist_open", section_id, waitlisted, maximum))
            elif isinstance(available, (int, float)):
                self._log(i18n.tr("log_section_full", section_id, enrolled, capacity))
            return bool(has_seat or has_waitlist)
        except requests.exceptions.Timeout:
            self._log(i18n.tr("log_section_check_timeout", section_id))
            return False
        except (requests.exceptions.RequestException, ValueError) as exc:
            self._log(i18n.tr("log_section_check_error", section_id, type(exc).__name__))
            return False

    def poll_available_sections(self, section_ids, enable_waitlist, running):
        for sid in section_ids:
            if not running() or self._drop_pending:
                break
            if (self.pending_sections([sid]) and self.check_section_status(sid, enable_waitlist=enable_waitlist)
                    and running()):
                yield sid

    @staticmethod
    def _is_duplicate_registration_msg(msg):
        return bool(re.search(r"\bduplicate\b|\balready registered\b", msg, re.I))

    def _parse_notifications(self, items, section_ids):
        """Notifications describe submission only, never prove enrollment.

        Associate only an explicit SectionId, exact numeric ID in the message,
        or single-target request. Course names in batch messages are ambiguous.
        The request audit records the redacted server text.
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
        should_complete = any(sid not in rejections and sid not in duplicates for sid in section_ids)
        return SubmissionResult(status, "notifications" if has_errors else "acknowledged",
                                rejections, duplicates, should_complete=should_complete)

    def _submission_response(self, resp, section_ids):
        if resp.status_code != 200:
            return SubmissionResult(SubmissionStatus.UNKNOWN, f"http_{resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return SubmissionResult(SubmissionStatus.UNKNOWN, "non_json_response")
        items = data.get("notifications", data.get("Notifications")) if isinstance(data, dict) else data
        return self._parse_notifications(items, section_ids)

    def register_multiple_sections(self, section_ids, action="Add", drop_section_id=None):
        section_ids = self.pending_sections(section_ids)
        if not section_ids:
            return SubmissionResult(SubmissionStatus.UNKNOWN, "nothing_to_submit")
        previous = {sid: replace(self.results[sid]) for sid in section_ids}
        registrations = []
        if drop_section_id:
            drop_section_id = str(drop_section_id)
            registrations.append({"SectionId": drop_section_id, "Action": "Drop",
                                  "Credits": None, "DropReasonCode": None, "IntentToWithdrawId": None})
            self._drop_pending.add(drop_section_id)
            self._drop_attempted.add(drop_section_id)
        unresolved_credits = []
        for sid in section_ids:
            registration = {"SectionId": sid, "Action": action,
                            "DropReasonCode": None, "IntentToWithdrawId": None}
            credits = self._credits_for(sid)
            if credits is None:
                unresolved_credits.append(sid)
            else:
                registration["Credits"] = credits
            registrations.append(registration)
        for sid in section_ids:
            result = self.results[sid]
            result.attempted = result.awaiting_verification = True
            result.status, result.reason, result.rejection = SectionStatus.UNKNOWN, "request_sent", ""
            self._negative_reads.discard(sid)
        audit = {}
        try:
            resp = self._request("register_" + action.lower(), "POST",
                                 f"{self.base_url}/RegisterSections", [entry["SectionId"] for entry in registrations],
                                 json={"sectionRegistrations": registrations,
                                       "studentId": str(self.student_id)}, timeout=(1.5, 12), capture=audit)
            submission = self._submission_response(resp, section_ids)
        except (SessionExpiredError, requests.exceptions.ConnectTimeout) as exc:
            # Explicit auth rejection and failure to connect leave the action unsent.
            self.results.update(previous)
            if drop_section_id:
                self._drop_pending.discard(drop_section_id)
                self._drop_attempted.discard(drop_section_id)
            if isinstance(exc, SessionExpiredError):
                raise
            for sid in section_ids:
                self.results[sid].attempted = True
                self.results[sid].reason = "ConnectTimeout"
            return SubmissionResult(SubmissionStatus.UNKNOWN, "ConnectTimeout")
        except requests.exceptions.RequestException as exc:
            submission = SubmissionResult(SubmissionStatus.UNKNOWN, type(exc).__name__)
        retry_after = time.monotonic() + REGISTRATION_GRACE_SECONDS
        for sid in section_ids:
            result = self.results[sid]
            result.reason = submission.reason
            if sid in submission.duplicates:
                self._catch_attempts.pop(sid, None)
                result.reason = "duplicate_requires_verification"
                self._retry_after[sid] = retry_after
            elif sid in submission.rejections:
                result.status = SectionStatus.REJECTED
                result.reason = result.rejection = submission.rejections[sid]
                self._retry_after.pop(sid, None)
            else:
                self._retry_after[sid] = retry_after
            if sid not in submission.duplicates and audit:
                self._catch_attempts[sid] = [audit]
        self._event("submission", action=action, section_ids=section_ids,
                    request_id=audit.get("request_id"), sections=self._states(section_ids),
                    sent_credits={sid: sent_credits([audit], sid) for sid in section_ids},
                    status=submission.status, reason=submission.reason,
                    rejections=submission.rejections, duplicates=sorted(submission.duplicates),
                    drop_section_id=drop_section_id, unresolved_credits=unresolved_credits)
        return submission

    def complete_registration(self, section_ids=()):
        audit = {}
        try:
            resp = self._request("complete_registration", "POST",
                                 f"{self.base_url}/CompleteRegistration", section_ids,
                                 json={"studentId": str(self.student_id), "notifications": []},
                                 timeout=(1.5, 12), capture=audit)
            result = self._submission_response(resp, [])
        except requests.exceptions.RequestException as exc:
            result = SubmissionResult(SubmissionStatus.UNKNOWN, type(exc).__name__)
        finally:
            retry_after = time.monotonic() + REGISTRATION_GRACE_SECONDS
            for sid in section_ids:
                if sid in self._retry_after:
                    self._retry_after[sid] = retry_after
                if audit and sid in self._catch_attempts:
                    self._catch_attempts[sid].append(audit)
        self._event("completion", status=result.status, reason=result.reason, section_ids=section_ids,
                    request_id=audit.get("request_id"))
        return result

    def _ensure_sections(self, section_ids):
        ids = list(dict.fromkeys(str(s) for s in section_ids))
        for sid in ids:
            self.results.setdefault(sid, SectionResult(sid))
        return ids

    def pending_sections(self, section_ids):
        if self._drop_pending or self.registration_reader is None:
            return []
        return [sid for sid in self._ensure_sections(section_ids)
                if self.results[sid].status not in (SectionStatus.REGISTERED, SectionStatus.WAITLISTED)
                and not self.results[sid].awaiting_verification]

    def needs_verification(self, section_ids):
        return bool(self._drop_pending) or any(
            self.results[sid].awaiting_verification or self.results[sid].status == SectionStatus.UNKNOWN
            for sid in self._ensure_sections(section_ids))

    def read_registration_status(self, section_ids):
        if self.registration_reader is None:
            return RegistrationSnapshot()
        return self.registration_reader.read(self, section_ids)

    def verify_registration(self, section_ids, extra_section_ids=()):
        ids = self._ensure_sections(section_ids)
        query_ids = list(dict.fromkeys(ids + sorted(self._drop_pending) + list(extra_section_ids)))
        observed = time.monotonic()
        self._last_registration_read = None
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
        if isinstance(snapshot.planned_credits, Mapping):
            self._planned_credits.update((sid, credits) for sid, credits in snapshot.planned_credits.items()
                                         if sid in query_ids and valid_credits(credits))
        now = timestamp()
        self._remember_account_metadata(snapshot)
        new_catches = []
        for sid in ids:
            result = self.results[sid]
            record = snapshot.records.get(sid)
            # Confirmed enrollment is monotonic for this client; later outages
            # must not re-enable Add. Re-authenticate after an external manual drop.
            if result.status == SectionStatus.REGISTERED:
                continue
            if record and record.term_id:
                result.term_id = record.term_id
                if record.status != SectionStatus.UNKNOWN:
                    self.section_terms[sid] = record.term_id
            if record and record.status in (SectionStatus.REGISTERED, SectionStatus.WAITLISTED):
                result.status, result.reason = record.status, "account_record"
                result.awaiting_verification, result.rejection = False, ""
                self._retry_after.pop(sid, None)
                self._negative_reads.discard(sid)
                if record.status == SectionStatus.REGISTERED:
                    result.first_confirmed_at = result.first_confirmed_at or now
                    result.server_registered_at = record.server_registered_at
                    if result.attempted and sid in self._catch_attempts:
                        new_catches.append(self._confirmed_catch(sid, result))
            elif record and record.status in (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED):
                if result.status == SectionStatus.WAITLISTED:
                    continue
                if sid in self._retry_after:
                    # A timeout can precede a late write or a stale account read.
                    ready = sid in self._negative_reads and observed >= self._retry_after[sid]
                    self._negative_reads.add(sid)
                    if not ready:
                        result.status, result.reason = SectionStatus.UNKNOWN, "waiting_for_registration_records"
                        continue
                    self._retry_after.pop(sid)
                    self._negative_reads.discard(sid)
                result.status = SectionStatus.REJECTED if result.rejection else record.status
                result.reason = result.rejection or "account_record_not_registered"
                result.awaiting_verification = False
            elif result.awaiting_verification or result.status == SectionStatus.UNKNOWN:
                if result.status != SectionStatus.REJECTED:
                    result.status = SectionStatus.UNKNOWN
                result.reason = result.rejection or (record.reason if record else "") or snapshot.reason
        for sid in list(self._drop_pending):
            record = snapshot.records.get(sid)
            if record and record.status in (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED):
                self._drop_pending.remove(sid)
                self.results.pop(sid, None)
        self._event("verification", reason=snapshot.reason, observed_at=now, sections=self._states(ids),
                    request_id=(self._last_registration_read or {}).get("request_id"),
                    account_records={sid: asdict(record) for sid, record in snapshot.records.items()},
                    drop_pending=sorted(self._drop_pending))
        observed_at = datetime.fromisoformat(now).astimezone().isoformat(timespec="milliseconds")
        self._log(i18n.tr("log_verification_time", observed_at, snapshot.reason))
        if new_catches:
            try:
                if self.history.save(new_catches) and self.history_callback:
                    self.history_callback()
            except (OSError, sqlite3.Error) as exc:
                self._log(i18n.tr("log_history_error", type(exc).__name__), "error")
        return snapshot

    def _remember_account_metadata(self, snapshot):
        read = self._last_registration_read or {}
        body = (read.get("response") or {}).get("body")
        plan = body.get("DegreePlan", {}) if isinstance(body, dict) else {}
        for term in plan.get("Terms", []):
            matches = {}
            for course in term.get("PlannedCourses", []):
                history = course["AcademicHistory"]
                section = course["Section"] or history["Section"]
                sid = str(section.get("Id", ""))
                record = snapshot.records.get(sid)
                if record and record.term_id == term.get("Code") and record.status != SectionStatus.UNKNOWN:
                    matches.setdefault(sid, []).append((section, course, history))
            for sid, rows in matches.items():
                if len(rows) != 1:
                    continue
                section, course, history = rows[0]
                credits = history.get("Credits")
                if not valid_credits(credits):
                    credits = course.get("Credits")
                self._account_metadata[sid] = {
                    "course_name": course_name(section, course, history["Section"]),
                    "term_id": term["Code"],
                    "credits": credits if valid_credits(credits) else None,
                }

    def _course_details(self, sid):
        account = self._account_metadata.get(sid, {})
        catalog = self._catalog_metadata.get(sid, {})
        result = self.results.get(sid)
        term = (result.term_id if result else None) or account.get("term_id") or catalog.get("term_id")
        if account.get("term_id") != term:
            account = {}
        if catalog.get("term_id") not in (term, None):
            catalog = {}
        credits = account.get("credits")
        source = "account"
        if credits is None:
            credits, source = catalog.get("credits"), "catalog"
        return {"course_name": account.get("course_name") or catalog.get("course_name"),
                "term_id": term, "credits": credits, "credits_source": source if credits is not None else None}

    def _confirmed_catch(self, sid, result):
        requests = self._catch_attempts.pop(sid)
        credits = sent_credits(requests, sid)
        if self._last_registration_read:
            requests.append(self._last_registration_read)
        details = self._course_details(sid)
        if details["credits"] is None and "value" in credits:
            details.update(credits=credits["value"], credits_source="submitted")
        return {
            **details, "section_id": sid, "student_id": str(self.student_id),
            "confirmed_at": result.first_confirmed_at, "server_registered_at": result.server_registered_at,
            "source": "live", "run_id": self.events.run_id, "source_log": self.events.path.name,
            "requests": requests, "sent_credits": credits,
        }

    def _section_label(self, sid):
        details = self._course_details(sid)
        name, term = details["course_name"], details["term_id"]
        return " · ".join(part for part in (name, sid, term) if part)

    def _states(self, ids):
        return {sid: {**asdict(self.results[sid]), **self._course_details(sid)} for sid in ids}

    def all_registered(self, section_ids):
        ids = self._ensure_sections(section_ids)
        return bool(ids) and not self._drop_pending and all(
            self.results[sid].status == SectionStatus.REGISTERED for sid in ids)

    def result_message(self, section_ids):
        parts = []
        for sid in section_ids:
            result = self.results[sid]
            state = i18n.tr("state_" + result.status.value.lower())
            reason_key = "reason_" + result.reason
            reason = i18n.tr(reason_key)
            parts.append(i18n.tr("log_section_result", self._section_label(sid), state,
                                 result.reason if reason == reason_key else reason))
        message = " | ".join(parts)
        if self._drop_pending:
            message += " | " + i18n.tr("log_drop_unknown", ", ".join(sorted(self._drop_pending)))
        return message

    def execute_full_attack(self, section_ids, enable_waitlist=False, drop_section_id=None,
                            submission_ids=None, *, verify_after=True):
        """Submit eligible sections promptly, then reconcile account records.

        The polling workers submit as soon as they find an opening, then verify
        all written sections together at the end of that poll.
        """
        ids = self._ensure_sections(section_ids)
        selected = set(map(str, submission_ids)) if submission_ids is not None else None
        connect_timeout = False
        self.round_id += 1
        self._event("round_started", section_ids=ids)
        if self.round_id == 1:
            self._log(i18n.tr("log_event_path", str(self.events.path)))
            if self.registration_reader is None:
                self._log(i18n.tr("log_reader_unavailable"), "error")
        try:
            pending = self.pending_sections(ids)
            if selected is not None:
                pending = [sid for sid in pending if sid in selected]
            if verify_after and not pending and self.needs_verification(ids):
                self.verify_registration(ids)
                if selected is None:
                    pending = self.pending_sections(ids)

            drop = str(drop_section_id) if drop_section_id else None
            if pending and drop and drop not in self._drop_attempted:
                if drop in ids:
                    message = i18n.tr("log_drop_is_target", drop)
                    self._log(message, "error")
                    return False, message
                # A swap must not drop anything if the replacement is already registered.
                before_swap = self.verify_registration(ids, extra_section_ids=[drop]).records
                negative = (SectionStatus.NOT_REGISTERED, SectionStatus.PLANNED)
                pending = [sid for sid in self.pending_sections(pending)
                           if sid in before_swap and before_swap[sid].status in negative]
                record = before_swap.get(drop)
                if pending and (not record or record.status not in (*negative, SectionStatus.REGISTERED)):
                    message = i18n.tr("log_drop_unknown", drop)
                    self._log(message, "error")
                    return False, message
                if record and record.status in negative:
                    drop = None

            # Only a previous Closed rejection plus an authoritative negative
            # registration read can enable Waitlist, never a timeout/duplicate.
            fallback = [sid for sid in pending if enable_waitlist
                        and self.results[sid].rejection == "closed"]
            for action, targets in (("Add", [sid for sid in pending if sid not in fallback]),
                                    (WAITLIST_ACTION, fallback)):
                targets = self.pending_sections(targets)
                if not targets:
                    continue
                swap_drop = drop if drop not in self._drop_attempted else None
                submitted = self.register_multiple_sections(targets, action=action, drop_section_id=swap_drop)
                if submitted.should_complete:
                    self.complete_registration(targets)
                if submitted.reason == "ConnectTimeout":
                    connect_timeout = True
                elif verify_after:
                    self.verify_registration(ids)
            return self.all_registered(ids), self.result_message(ids)
        except SessionExpiredError:
            self._event("session_expired", section_ids=ids)
            raise
        finally:
            unknown = bool(self._drop_pending) or any(self.results[sid].awaiting_verification for sid in ids)
            if verify_after:
                self._unknown_rounds = self._unknown_rounds + 1 if unknown else 0
                self.retry_delay = (min(30, 2 ** min(self._unknown_rounds, 5)) if unknown
                                    else 2 if connect_timeout else 0)
            self._event("round_finished", sections=self._states(ids),
                        all_registered=self.all_registered(ids), retry_delay=self.retry_delay,
                        drop_pending=sorted(self._drop_pending))
            if verify_after:
                self._log(self.result_message(ids), "success" if self.all_registered(ids) else "normal")
