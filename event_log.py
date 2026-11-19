"""Append-only, credential-free registration audit events."""
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit


_CREDENTIAL = (r"(?:[a-z0-9_-]*token[a-z0-9_-]*|[a-z0-9_-]*cookie[a-z0-9_-]*|"
               r"password|passwd|pwd|passphrase|secret|client[_-]?secret|credentials?|session|auth|"
               r"authorization|proxy[_-]?authorization|auth[_-]?code|session[_-]?id|"
               r"api[_-]?key|saml(?:request|response|assertion)|assertion|relaystate|"
               r"signature|csrf|xsrf|wresult|otp)")
_ASSIGNMENT = re.compile(
    rf"""(?i)(\b{_CREDENTIAL}["']?\s*[:=]\s*)(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s,;<>\]}}]+)""")
_AUTH_HEADER = re.compile(r"(?im)((?:^|\b)(?:set-cookie|cookie|(?:proxy-)?authorization)\s*:)\s*[^\r\n]+")
_URL = re.compile(r"""(?i)((?:https?://|(?<![\w/])/)[^\s<>"'?#]+)[?#][^\s<>"']*""")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^\s/@]+@")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[a-z0-9+/_.~=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_HIDDEN_MARKUP = re.compile(r"(?i)<(?:html|form|input|script|saml[\w:]*|textarea)\b|<!doctype\b")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")

_TITLE_FIELDS = ("FullTitleDisplay", "TitleDisplay", "FullTitle", "CourseTitleDisplay", "CourseTitle",
                 "CourseName", "LongTitle", "ShortTitle", "Title", "SectionName", "Name")
_SECTION_FIELDS = (*_TITLE_FIELDS, "Id", "SectionNumber", "TermId", "TermName", "Credits", "MinimumCredits",
                   "MaximumCredits", "Available", "Enrolled", "Capacity", "Waitlisted",
                   "WaitlistMaximum", "AllowWaitlist")
_COURSE_FIELDS = (*_TITLE_FIELDS, "TermCode", "Credits", "HasSection", "HasRegisteredSection",
                  "IsPreregistered", "IsInProgress", "IsPlanned", "IsWaitlisted", "IsDropped",
                  "IsWithdrawn", "IsCompleted")
_MESSAGE_FIELDS = ("Notifications", "notifications", "Message", "message", "Error", "error",
                   "ErrorMessage", "errorMessage", "Errors", "errors", "Code", "InvalidSession")


def credential_values(session):
    return {session.headers.get("__requestverificationtoken"), *(cookie.value for cookie in session.cookies)}


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = sorted({value for secret in secrets if secret
                               for value in (secret, quote(secret, safe=""), quote_plus(secret))},
                              key=len, reverse=True)

    def text(self, value):
        value = unescape(value)
        if _HIDDEN_MARKUP.search(value):
            return "[HTML omitted]"
        for secret in self.secrets:
            value = value.replace(secret, "[redacted]")
        value = _AUTH_HEADER.sub(r"\1 [redacted]", value)
        value = _URL_USERINFO.sub(r"\1", value)
        value = _URL.sub(r"\1?[redacted]", value)
        value = _BEARER.sub(r"\1 [redacted]", value)
        value = _JWT.sub("[redacted]", value)
        value = _ASSIGNMENT.sub(r"\1[redacted]", value)
        return _HTML_TAG.sub("", value)

    def payload(self, value):
        if isinstance(value, dict):
            return {self.text(key): self.payload(item) for key, item in value.items()
                    if not re.fullmatch(_CREDENTIAL, key, re.I)
                    and key.lower() not in ("headers", "requestheaders", "responseheaders")}
        if isinstance(value, list):
            return [self.payload(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value


def request_details(url, options, redactor):
    return {"path": redactor.text(urlsplit(url).path),
            **{key: redactor.payload(options[key]) for key in ("params", "json") if key in options}}


def _fields(data, keys):
    return {key: data[key] for key in keys if key in data} if isinstance(data, dict) else {}


def _target_plan(plan, section_ids, student_id):
    summary = _fields(plan, ("Notifications",))
    summary["account_matches"] = str(plan.get("PersonId")) == str(student_id)
    terms = plan.get("Terms")
    if not summary["account_matches"] or not isinstance(terms, list):
        return summary
    summary["PersonId"] = plan["PersonId"]
    summary["Terms"] = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        entry = _fields(term, ("Code", "Description", "IsAcademicHistoryAvailable", "IsTermCompleted"))
        courses = term.get("PlannedCourses")
        if isinstance(courses, list):
            entry["PlannedCourses"] = []
            for course in courses:
                if not isinstance(course, dict):
                    continue
                section = _fields(course.get("Section"), _SECTION_FIELDS)
                history = _fields(course.get("AcademicHistory"), ("Section", "Credits"))
                history_section = _fields(history.get("Section"), _SECTION_FIELDS)
                if not any(str(item.get("Id", "")) in section_ids for item in (section, history_section)):
                    continue
                entry["PlannedCourses"].append({**_fields(course, _COURSE_FIELDS), "Section": section,
                                                "AcademicHistory": {**history, "Section": history_section}})
        active = term.get("ActiveSectionIds")
        if isinstance(active, list):
            entry["ActiveSectionIds"] = [sid for sid in active if str(sid) in section_ids]
        summary["Terms"].append(entry)
    return summary


def response_details(response, operation, section_ids, student_id, redactor):
    if response is None:
        return None
    if response.content.lstrip().startswith(b"<") or "text/html" in response.headers.get("Content-Type", "").lower():
        return {"format": "html"}
    if not response.content:
        return {"format": "empty"}
    try:
        data = response.json()
    except ValueError:
        body = response.text
        return {"format": "text", "body": redactor.text(body)[:8192], "truncated": len(body) > 8192}
    if isinstance(data, dict) and isinstance(data.get("DegreePlan"), dict):
        data = {**_fields(data, _MESSAGE_FIELDS),
                "DegreePlan": _target_plan(data["DegreePlan"], set(section_ids), student_id)}
    elif operation.startswith("section_") and isinstance(data, dict) and "Id" in data:
        data = _fields(data, (*_SECTION_FIELDS, *_MESSAGE_FIELDS))
    return {"format": "json", "body": redactor.payload(data)}


def response_messages(response):
    if not response:
        return
    data = response.get("body")
    if isinstance(data, str) and data.strip():
        yield "Message", data, None
    if isinstance(data, dict):
        for key in ("Message", "message", "Error", "error", "ErrorMessage", "errorMessage"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                yield key, value, None
        plan = data.get("DegreePlan")
        items = data.get("Notifications", data.get("notifications"))
        if items is None and isinstance(plan, dict):
            items = plan.get("Notifications")
    else:
        items = data
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("Message"), str) and item["Message"].strip():
                yield item.get("Type", "Message"), item["Message"], item.get("SectionId")


def course_name(*sources):
    return next((source[key].strip() for key in _TITLE_FIELDS for source in sources
                 if isinstance(source.get(key), str) and source[key].strip()), None)


def data_directory():
    return Path(os.environ.get("KSC_DATA_DIR", Path.home() / ".keanseatscatcher"))


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, directory=None):
        self.run_id = uuid.uuid4().hex
        directory = Path(directory or os.environ.get(
            "KSC_LOG_DIR", data_directory() / "logs"))
        self.path = directory / f"registration-{self.run_id}.jsonl"
        self.error = None
        self._lock = threading.Lock()

    def write(self, event, **fields):
        # HTTP evidence must pass through request_details / response_details first.
        record = dict(timestamp=timestamp(), run_id=self.run_id, event=event, **fields)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
            self.error = None
        except OSError as exc:
            # A logging failure must not obscure the outcome of a sent request.
            self.error = type(exc).__name__
