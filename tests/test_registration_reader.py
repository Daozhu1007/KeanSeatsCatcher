"""Synthetic projection of the observed Current response, never owner data.

Field names and the registered/course-only shapes were observed on 2026-11-03.
Selected-plan and waitlist variants follow the flags used by the page's script;
the owner's account contained no waitlisted sections.
"""
import copy
import threading
from unittest.mock import Mock

import pytest
import requests

from core_api import KeanApiClient
from registration_reader import DegreePlanRegistrationReader
from registration_state import SectionStatus as S
from test_registration import response


def course(sid, state, term="2027SP"):
    section = {"Id": sid, "TermId": term} if sid else None
    registered = state == S.REGISTERED
    return {
        "TermCode": term, "HasSection": sid is not None,
        "HasRegisteredSection": registered, "Section": section,
        "AcademicHistory": {"Section": section if registered else None},
        "IsPreregistered": registered, "IsInProgress": False,
        "IsPlanned": state == S.PLANNED, "IsWaitlisted": state == S.WAITLISTED,
        "IsDropped": state == S.NOT_REGISTERED, "IsWithdrawn": False, "IsCompleted": False,
    }


@pytest.fixture
def degree_plan():
    return {"DegreePlan": {
        "PersonId": "fixture-student", "Notifications": [], "TermToShow": "2026FA",
        "Terms": [{
            "Code": "2027SP", "IsAcademicHistoryAvailable": True, "IsTermCompleted": False,
            "ActiveSectionIds": ["registered"],
            "PlannedCourses": [course("registered", S.REGISTERED), course("planned", S.PLANNED),
                               course("waiting", S.WAITLISTED), course(None, S.PLANNED)],
        }],
    }}


def test_observed_contract_distinguishes_states_and_resolves_missing_sections(degree_plan, tmp_path):
    reader = DegreePlanRegistrationReader()
    api = KeanApiClient({}, "test-token", "fixture-student", reader, log_dir=tmp_path)
    api.session.request = Mock(side_effect=[response(degree_plan), response({"Id": "absent", "TermId": "2027SP"})])
    ids = ["registered", "planned", "waiting", "absent"]
    snapshot = api.verify_registration(ids)
    assert [snapshot.records[sid].status for sid in ids] == [S.REGISTERED, S.PLANNED, S.WAITLISTED, S.NOT_REGISTERED]
    assert api.pending_sections(ids) == ["planned", "absent"]
    call, details = api.session.request.call_args_list
    assert call.args == ("GET", api.base_url + "/Current")
    assert call.kwargs["params"] == {"studentId": "fixture-student"}
    assert call.kwargs["allow_redirects"] is False
    assert details.args[1].endswith("/SectionDetails")
    assert details.kwargs["json"]["sectionId"] == "absent"
    assert snapshot.records["absent"].term_id == "2027SP"
    assert all(record.server_registered_at is None for record in snapshot.records.values())


@pytest.mark.parametrize("problem", ["account", "missing_term", "history_unavailable", "notifications",
                                     "missing_active_record", "missing_flag", "conflicting_flags"])
def test_incomplete_or_mismatched_records_cannot_authorize_absence(degree_plan, problem):
    plan = degree_plan["DegreePlan"]
    term = plan["Terms"][0]
    if problem == "account":
        plan["PersonId"] = "another-student"
    elif problem == "missing_term":
        term["Code"] = "2027SPW"
    elif problem == "history_unavailable":
        term["IsAcademicHistoryAvailable"] = False
    elif problem == "notifications":
        plan["Notifications"] = [{"Type": "Error", "Message": "Fixture partial history"}]
    elif problem == "missing_active_record":
        term["ActiveSectionIds"].append("omitted")
    elif problem == "missing_flag":
        del term["PlannedCourses"][0]["IsPreregistered"]
    else:
        term["PlannedCourses"][0]["IsPlanned"] = True
    result = DegreePlanRegistrationReader().parse(degree_plan, "fixture-student", ["absent", "planned"],
                                                  section_terms={"absent": "2027SP"})
    assert "absent" not in result.records or result.records["absent"].status == S.UNKNOWN
    assert "planned" not in result.records or result.records["planned"].status == S.UNKNOWN


def test_multiple_terms_use_exact_section_records_and_never_borrow_absence(degree_plan):
    other = copy.deepcopy(degree_plan["DegreePlan"]["Terms"][0])
    other.update(Code="2027SPW", PlannedCourses=[course("other-term", S.REGISTERED, "2027SPW")])
    degree_plan["DegreePlan"]["Terms"].append(other)
    result = DegreePlanRegistrationReader().parse(
        degree_plan, "fixture-student", ["registered", "other-term", "unknown-scope", "term-not-loaded"],
        section_terms={"term-not-loaded": "2026FA"})
    assert result.records["registered"].term_id == "2027SP"
    assert result.records["other-term"].status == S.REGISTERED
    assert result.records["other-term"].term_id == "2027SPW"
    assert result.records["unknown-scope"].status == S.UNKNOWN
    assert result.records["term-not-loaded"].reason == "registration_term_missing"


def test_already_confirmed_registration_is_skipped_when_starting_ordinary_attack(degree_plan, tmp_path):
    api = KeanApiClient({}, "test-token", "fixture-student", DegreePlanRegistrationReader(), log_dir=tmp_path)
    api.session.request = Mock(return_value=response(degree_plan))
    assert api.execute_full_attack(["registered"], submission_ids=[])[0]
    assert api.execute_full_attack(["registered"])[0]
    assert api.session.request.call_count == 1
    assert api.session.request.call_args.args[0] == "GET"
    assert not api.results["registered"].attempted


def test_missing_reader_pauses_initial_add(tmp_path):
    api = KeanApiClient({}, "test-token", "fixture-student", log_dir=tmp_path)
    api.session.request = Mock(side_effect=AssertionError("No HTTP allowed"))
    assert not api.execute_full_attack(["unverified"])[0]
    assert api.results["unverified"].status == S.UNKNOWN
    api.session.request.assert_not_called()


def test_dashboard_sends_before_reading_account_plan(degree_plan, tmp_path):
    api = KeanApiClient({}, "test-token", "fixture-student", DegreePlanRegistrationReader(), log_dir=tmp_path)
    api.session.request = Mock(side_effect=[response([{"Type": "Error", "Message": "Already registered"}]),
                                           response(degree_plan)])
    assert api.execute_full_attack(["registered"])[0]
    assert [call.args[1].rsplit("/", 1)[-1] for call in api.session.request.call_args_list] == [
        "RegisterSections", "Current"]


def test_prefetched_planned_and_catalog_credits_reach_the_first_add(degree_plan, tmp_path, monkeypatch):
    term = degree_plan["DegreePlan"]["Terms"][0]
    term["PlannedCourses"][1]["Credits"] = 2.5
    api = KeanApiClient({}, "test-token", "fixture-student", DegreePlanRegistrationReader(), log_dir=tmp_path)
    calls = []
    ready = threading.Event()
    load = api._prefetch_registration_credits

    def load_and_signal(*args):
        try:
            load(*args)
        finally:
            ready.set()

    def catalog(session, method, url, **kwargs):
        assert session is not api.session
        calls.append((method, url.rsplit("/", 1)[-1]))
        if method == "GET":
            return response(degree_plan)
        sid = kwargs["json"]["sectionId"]
        assert sid == "unplanned"
        return response({"Id": sid, "MinimumCredits": 4.0, "MaximumCredits": None})

    monkeypatch.setattr(api, "_prefetch_registration_credits", load_and_signal)
    monkeypatch.setattr(requests.Session, "request", catalog)
    api.prefetch_registration_credits(["planned", "unplanned"], include_section_details=True)
    assert ready.wait(3)
    assert calls == [("GET", "Current"), ("POST", "SectionDetails")]
    assert not api.results  # Reading credits does not reconcile or change registration state.
    api.session.request = Mock(return_value=response([]))
    api.register_multiple_sections(["planned", "unplanned"])
    entries = api.session.request.call_args.kwargs["json"]["sectionRegistrations"]
    assert [(entry["SectionId"], entry["Credits"]) for entry in entries] == [
        ("planned", 2.5), ("unplanned", 4.0)]


@pytest.mark.parametrize("condition", ["valid", "wrong_account", "wrong_term", "duplicate", "out_of_range"])
def test_variable_credit_payload_requires_a_matching_legal_plan_choice(degree_plan, tmp_path, condition):
    plan = degree_plan["DegreePlan"]
    term = plan["Terms"][0]
    planned = term["PlannedCourses"][1]
    planned["Credits"] = 2.5
    if condition == "wrong_account":
        plan["PersonId"] = "different-student"
    elif condition == "wrong_term":
        planned["TermCode"] = "2027SPW"
    elif condition == "duplicate":
        term["PlannedCourses"].append(dict(planned, Credits=3.0))
    elif condition == "out_of_range":
        planned["Credits"] = 5.0
    api = KeanApiClient({}, "test-token", "fixture-student", DegreePlanRegistrationReader(), log_dir=tmp_path)
    api.session.request = Mock(side_effect=[
        response({"Id": "planned", "TermId": "2027SP", "MinimumCredits": 1, "MaximumCredits": 4}),
        response(degree_plan), response([])])
    api.check_section_status("planned")
    api.verify_registration(["planned"])
    api.register_multiple_sections(["planned"], action="Waitlist")
    entry, = api.session.request.call_args.kwargs["json"]["sectionRegistrations"]
    if condition == "valid":
        assert entry["Credits"] == 2.5
    else:
        assert "Credits" not in entry
