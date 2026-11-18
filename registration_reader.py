"""Read the account/term evidence used by Ellucian's Plan & Schedule page.

Endpoint, query and fields observed on 2026-11-03. Fixtures contain invented
identities and courses; no captured account response belongs in this repository.
"""
from registration_state import RegistrationRecord, RegistrationSnapshot, SectionStatus as S

class DegreePlanRegistrationReader:
    def read(self, client, section_ids):
        response = client._request(
            "registration_read", "GET", f"{client.base_url}/Current", section_ids,
            params={"studentId": str(client.student_id)},
            headers={"Cache-Control": "no-cache"}, timeout=(1.5, 8))
        if response.status_code != 200:
            return RegistrationSnapshot(reason=f"registration_http_{response.status_code}")
        return self.parse(response.json(), client.student_id, section_ids,
                          section_terms=client.section_terms, resolve_term=client.lookup_section_term)

    def parse(self, data, student_id, section_ids, section_terms=None, resolve_term=None):
        """Absence is evidence only in a complete, matching account and term."""
        if not isinstance(data, dict) or not isinstance(data.get("DegreePlan"), dict):
            return RegistrationSnapshot(reason="invalid_degree_plan")
        plan = data["DegreePlan"]
        if str(plan.get("PersonId", "")) != str(student_id):
            return RegistrationSnapshot(reason="registration_account_mismatch")
        terms = plan.get("Terms")
        if not isinstance(terms, list) or any(not isinstance(t, dict) for t in terms):
            return RegistrationSnapshot(reason="invalid_registration_terms")
        by_term = {}
        for term in terms:
            code = term.get("Code")
            if not isinstance(code, str) or not code:
                return RegistrationSnapshot(reason="invalid_registration_terms")
            by_term.setdefault(code, []).append(term)

        matches = {}
        complete_terms = set()
        wanted = set(section_ids)
        for code, entries in by_term.items():
            for term in entries:
                records, complete = self._term_records(term)
                if complete and len(entries) == 1 and plan.get("Notifications") == []:
                    complete_terms.add(code)
                for sid in wanted.intersection(records):
                    status = records[sid] if len(entries) == 1 else S.UNKNOWN
                    matches.setdefault(sid, []).append((code, status))

        result = {}
        for sid in section_ids:
            course_matches = matches.get(sid, [])
            term_id = (section_terms or {}).get(sid)
            if len(course_matches) == 1 and (not term_id or term_id == course_matches[0][0]):
                code, status = course_matches[0]
                if status in (S.NOT_REGISTERED, S.PLANNED) and code not in complete_terms:
                    result[sid] = RegistrationRecord(S.UNKNOWN, term_id=code,
                                                     reason="incomplete_registration_term")
                else:
                    result[sid] = RegistrationRecord(status, term_id=code,
                                                     reason="conflicting_section_records" if status == S.UNKNOWN else "")
            elif course_matches:
                result[sid] = RegistrationRecord(S.UNKNOWN, reason="conflicting_section_records")
            else:
                # With no matching row, only SectionDetails can prove which term to check.
                if not term_id and complete_terms and resolve_term:
                    term_id = resolve_term(sid)
                if term_id in complete_terms:
                    result[sid] = RegistrationRecord(S.NOT_REGISTERED, term_id=term_id)
                else:
                    reason = "section_term_unresolved"
                    if term_id:
                        reason = "incomplete_registration_term" if term_id in by_term else "registration_term_missing"
                    result[sid] = RegistrationRecord(S.UNKNOWN, term_id=term_id, reason=reason)
        return RegistrationSnapshot(result, "account_records")

    def _term_records(self, term):
        code = term["Code"]
        courses, active = term.get("PlannedCourses"), term.get("ActiveSectionIds")
        if not isinstance(courses, list):
            return {}, False
        complete = (term.get("IsAcademicHistoryAvailable") is True
                    and term.get("IsTermCompleted") is False)
        records = {}
        for course in courses:
            if not isinstance(course, dict):
                complete = False
                continue
            section = course.get("Section")
            history = course.get("AcademicHistory")
            history_section = history.get("Section") if isinstance(history, dict) else None
            if section is None:
                section = history_section
            if section is None:
                # A course-only plan is not evidence for any particular Section.
                if (course.get("TermCode") != code or course.get("HasSection") is not False
                        or self._status(course) != S.PLANNED):
                    complete = False
                continue
            if (not isinstance(section, dict) or not isinstance(section.get("Id"), str)
                    or not section["Id"]):
                complete = False
                continue
            sid = section["Id"]
            status = self._status(course)
            if course.get("TermCode") != code or section.get("TermId") != code:
                status = S.UNKNOWN
            if history_section is not None and (
                    not isinstance(history_section, dict)
                    or history_section.get("Id") != sid
                    or history_section.get("TermId") != code):
                if isinstance(history_section, dict) and isinstance(history_section.get("Id"), str):
                    records[history_section["Id"]] = S.UNKNOWN
                status = S.UNKNOWN
            if sid in records and records[sid] != status:
                status = S.UNKNOWN
            records[sid] = status
            complete &= status != S.UNKNOWN
        # The page's active section index must not reference missing records.
        return records, (complete and isinstance(active, list)
                         and all(isinstance(sid, str) and sid in records for sid in active))

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
