"""Read the account/term evidence used by Ellucian's Plan & Schedule page.

Endpoint, query and fields observed on 2026-11-03. Fixtures contain invented
identities and courses; no captured account response belongs in this repository.
"""
from registration_state import RegistrationRecord, RegistrationSnapshot, SectionStatus as S

DEFAULT_TERM = "2027SP"


class DegreePlanRegistrationReader:
    def __init__(self, term_code=DEFAULT_TERM):
        self.term_code = term_code.strip()
        if not self.term_code:
            raise ValueError("A registration term is required")

    def read(self, client, section_ids):
        response = client._request(
            "registration_read", "GET", f"{client.base_url}/Current", section_ids,
            params={"studentId": str(client.student_id)},
            headers={"Cache-Control": "no-cache"}, timeout=(1.5, 12))
        if response.status_code != 200:
            return RegistrationSnapshot(reason=f"registration_http_{response.status_code}")
        return self.parse(response.json(), client.student_id, section_ids)

    def parse(self, data, student_id, section_ids):
        """Absence is evidence only in a complete, matching account and term."""
        if not isinstance(data, dict) or not isinstance(data.get("DegreePlan"), dict):
            return RegistrationSnapshot(reason="invalid_degree_plan")
        plan = data["DegreePlan"]
        if str(plan.get("PersonId", "")) != str(student_id):
            return RegistrationSnapshot(reason="registration_account_mismatch")
        terms = plan.get("Terms")
        if not isinstance(terms, list) or any(not isinstance(t, dict) for t in terms):
            return RegistrationSnapshot(reason="invalid_registration_terms")
        matches = [t for t in terms if t.get("Code") == self.term_code]
        if len(matches) != 1:
            return RegistrationSnapshot(reason="registration_term_missing")
        term = matches[0]
        courses, active = term.get("PlannedCourses"), term.get("ActiveSectionIds")
        if not isinstance(courses, list) or not isinstance(active, list):
            return RegistrationSnapshot(reason="incomplete_registration_term")
        complete = (plan.get("Notifications") == []
                    and term.get("IsAcademicHistoryAvailable") is True
                    and term.get("IsTermCompleted") is False)
        records = {}
        for course in courses:
            if not isinstance(course, dict) or course.get("TermCode") != self.term_code:
                complete = False
                continue
            section = course.get("Section")
            history = course.get("AcademicHistory")
            history_section = history.get("Section") if isinstance(history, dict) else None
            if section is None:
                section = history_section
            if section is None:
                # A course-only plan is not evidence for any particular Section.
                if course.get("HasSection") is not False or self._status(course) != S.PLANNED:
                    complete = False
                continue
            if (not isinstance(section, dict) or not isinstance(section.get("Id"), str)
                    or not section["Id"]):
                complete = False
                continue
            sid = section["Id"]
            status = self._status(course)
            if section.get("TermId") != self.term_code:
                status = S.UNKNOWN
            if history_section is not None and (
                    not isinstance(history_section, dict)
                    or history_section.get("Id") != sid
                    or history_section.get("TermId") != self.term_code):
                status = S.UNKNOWN
            if sid in records and records[sid].status != status:
                status = S.UNKNOWN
            records[sid] = RegistrationRecord(status)
            complete &= status != S.UNKNOWN
        # The page's active section index must not reference missing records.
        if any(not isinstance(sid, str) or sid not in records for sid in active):
            complete = False
        outside_term = set()
        for other in terms:
            if other is term:
                continue
            for course in other.get("PlannedCourses", []) or []:
                if isinstance(course, dict):
                    history = course.get("AcademicHistory") or {}
                    for section in (course.get("Section"), history.get("Section") if isinstance(history, dict) else None):
                        if isinstance(section, dict) and isinstance(section.get("Id"), str):
                            outside_term.add(section["Id"])
        result = {}
        for sid in section_ids:
            if sid in outside_term:
                result[sid] = RegistrationRecord(S.UNKNOWN)
            elif sid in records:
                record = records[sid]
                # Failed academic-history loading can make a registration look
                # like a plan. Partial data must not authorize an Add.
                result[sid] = (RegistrationRecord(S.UNKNOWN) if not complete and
                               record.status in (S.PLANNED, S.NOT_REGISTERED) else record)
            elif complete:
                result[sid] = RegistrationRecord(S.NOT_REGISTERED)
        return RegistrationSnapshot(result, "account_term_records" if complete else "incomplete_registration_term")

    @staticmethod
    def _status(course):
        flags = ("HasRegisteredSection", "IsPreregistered", "IsInProgress", "IsPlanned",
                 "IsWaitlisted", "IsDropped", "IsWithdrawn", "IsCompleted")
        if any(type(course.get(key)) is not bool for key in flags):
            return S.UNKNOWN
        registered = course["IsPreregistered"] or course["IsInProgress"]
        removed = course["IsDropped"] or course["IsWithdrawn"]
        if sum((registered, removed, course["IsPlanned"], course["IsWaitlisted"], course["IsCompleted"])) != 1:
            return S.UNKNOWN
        if registered:
            history = course.get("AcademicHistory")
            return (S.REGISTERED if course["HasRegisteredSection"] and isinstance(history, dict)
                    and isinstance(history.get("Section"), dict) else S.UNKNOWN)
        if course["HasRegisteredSection"] and not removed:
            return S.UNKNOWN
        if course["IsWaitlisted"]:
            return S.WAITLISTED
        if course["IsPlanned"]:
            return S.PLANNED
        if removed:
            return S.NOT_REGISTERED
        return S.UNKNOWN  # Completed historical credit is outside current registration.
