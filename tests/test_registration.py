import json
import re
from unittest.mock import Mock

import pytest
import requests

from core_api import KeanApiClient, SessionExpiredError
from registration_state import (RegistrationReader, RegistrationRecord, RegistrationSnapshot, SectionStatus as S,
                                SessionStatus, SubmissionStatus)


def response(data=None, status=200, text=None, location=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://kean-ss.colleague.elluciancloud.com/Student/Planning/DegreePlans"
    result._content = (text if text is not None else json.dumps(data)).encode()
    if location:
        result.headers["Location"] = location
    return result


def snapshot(**states):
    # Synthetic contract fixture, NOT a captured Ellucian payload.
    return RegistrationSnapshot({sid: RegistrationRecord(state) for sid, state in states.items()},
                                reason="fixture_account_records")


def client(tmp_path, replies, snapshots=None):
    # Initial submissions do not consume a personal-plan read.
    reader = Mock(spec=RegistrationReader)
    remaining = iter(snapshots or [])

    def read(api, section_ids):
        if snapshots is None:
            return RegistrationSnapshot(reason="fixture_reader_unavailable")
        result = next(remaining)
        if isinstance(result, Exception):
            raise result
        return result
    reader.read.side_effect = read
    api = KeanApiClient({"cookie": "COOKIE_SECRET"}, "TOKEN_SECRET", "fixture-student",
                        registration_reader=reader, log_dir=tmp_path)
    api.session.request = Mock(side_effect=replies)
    return api


def events(api):
    return [json.loads(line) for line in api.events.path.read_text(encoding="utf-8").splitlines()]


def error(sid, message):
    return {"Type": "Error", "SectionId": sid, "Message": message}


def test_timeout_but_server_registered_and_never_add_again(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout("TOKEN_SECRET")], [snapshot(a=S.REGISTERED)])
    assert api.execute_full_attack(["a"])[0]
    first = api.results["a"].first_confirmed_at
    assert first and api.results["a"].server_registered_at is None
    assert api.execute_full_attack(["a"])[0]
    assert api.session.request.call_count == 1
    assert api.results["a"].first_confirmed_at == first


def test_six_sections_partial_success_scoped_rejections_unknown_and_retry(tmp_path):
    notices = [error("1", "Already registered"), error("3", "Closed"), error("4", "Credits invalid")]
    api = client(tmp_path, [response(notices), response([]), requests.ReadTimeout()], [
        snapshot(**{"1": S.REGISTERED, "2": S.REGISTERED, "3": S.NOT_REGISTERED,
                    "4": S.NOT_REGISTERED, "5": S.REGISTERED}),
        snapshot(**{"3": S.REGISTERED, "4": S.NOT_REGISTERED}),
    ])
    ids = list("123456")
    success, msg = api.execute_full_attack(ids)
    assert not success and all(sid in msg for sid in ids)
    assert [api.results[x].status for x in ids] == [S.REGISTERED, S.REGISTERED, S.REJECTED,
                                                   S.REJECTED, S.REGISTERED, S.UNKNOWN]
    assert api.session.request.call_args.args[1].endswith("/CompleteRegistration")
    api.execute_full_attack(ids)
    sent = api.session.request.call_args.kwargs["json"]["sectionRegistrations"]
    assert [x["SectionId"] for x in sent] == ["3", "4"]
    assert api.results["6"].status == S.UNKNOWN


def test_duplicate_does_not_erase_other_rejections_or_prove_registration(tmp_path):
    api = client(tmp_path, [response([error("1", "Already registered"), error("2", "Closed")])])
    assert not api.execute_full_attack(["1", "2"])[0]
    assert api.results["1"].status == S.UNKNOWN
    assert api.results["2"].status == S.REJECTED
    assert api.results["2"].awaiting_verification
    assert api.session.request.call_count == 1


def test_unscoped_batch_messages_are_not_assigned_to_every_section(tmp_path):
    api = client(tmp_path, [])
    parsed = api._parse_notifications([
        {"Type": "Error", "Message": "MATH*3415*01 - Closed"},
        {"Type": "Error", "Message": "PHYS*2091*06 - Already registered"}], ["32660", "32641"])
    assert not parsed.rejections and not parsed.duplicates
    assert parsed.status == SubmissionStatus.UNKNOWN


@pytest.mark.parametrize("registered,expected", [(True, S.REGISTERED), (False, S.REJECTED)])
def test_closed_uses_account_record_to_distinguish_enrollment(tmp_path, registered, expected):
    api = client(tmp_path, [response([error("a", "Closed")])],
                 [snapshot(a=S.REGISTERED if registered else S.NOT_REGISTERED)])
    assert api.execute_full_attack(["a"])[0] == registered
    assert api.results["a"].status == expected


@pytest.mark.parametrize("reply", [requests.ReadTimeout(), requests.ConnectionError(), response(status=503),
                                   response(text="<html>Temporarily unavailable; login later</html>")])
def test_unknown_submission_and_unavailable_reader_prevent_repeated_add(tmp_path, reply):
    api = client(tmp_path, [reply])
    for _ in range(5):
        assert not api.execute_full_attack(["a"])[0]
    assert api.session.request.call_count == 1
    assert api.results["a"].status == S.UNKNOWN
    assert api.retry_delay == 30


def test_connect_timeout_can_retry_without_a_working_reader(tmp_path):
    api = client(tmp_path, [requests.ConnectTimeout(), requests.ReadTimeout()], [snapshot(a=S.REGISTERED)])
    assert not api.execute_full_attack(["a"])[0]
    api.registration_reader.read.assert_not_called()
    assert not api.results["a"].awaiting_verification and api.retry_delay == 2
    assert api.execute_full_attack(["a"])[0]
    assert api.session.request.call_count == 2


def test_positive_ack_and_completion_do_not_prove_registration(tmp_path):
    api = client(tmp_path, [response([]), response([])])
    assert not api.execute_full_attack(["a"])[0]
    assert api.results["a"].status == S.UNKNOWN
    assert api.session.request.call_count == 2
    assert all("Credits" not in reg for reg in
               api.session.request.call_args_list[0].kwargs["json"]["sectionRegistrations"])
    assert next(e for e in events(api) if e["event"] == "submission")["unresolved_credits"] == ["a"]


@pytest.mark.parametrize("action", ["Add", "Waitlist"])
def test_registration_payload_uses_fixed_credits_per_section(tmp_path, action):
    fixed = {"one": 1.0, "three": 3.0, "four": 4.0, "half": 0.5, "zero": 0}
    replies = [response({"Id": sid, "Available": 1, "MinimumCredits": credits,
                         "MaximumCredits": credits if sid == "four" else None})
               for sid, credits in fixed.items()]
    api = client(tmp_path, [*replies, response([])])
    assert all(api.check_section_status(sid) for sid in fixed)
    assert api.register_multiple_sections(fixed, action=action, drop_section_id="old").status == (
        SubmissionStatus.ACKNOWLEDGED)
    call = api.session.request.call_args
    assert call.args == ("POST", api.base_url + "/RegisterSections")
    body = json.loads(requests.Request(*call.args, json=call.kwargs["json"]).prepare().body)
    assert body["studentId"] == "fixture-student"
    entries = body["sectionRegistrations"]
    assert entries[0] == {"SectionId": "old", "Action": "Drop", "Credits": None,
                          "DropReasonCode": None, "IntentToWithdrawId": None}
    assert [(entry["SectionId"], entry["Action"], entry["Credits"]) for entry in entries[1:]] == [
        (sid, action, credits) for sid, credits in fixed.items()]


@pytest.mark.parametrize("details", [
    {"Id": "different", "MinimumCredits": 4, "MaximumCredits": None},
    {"Id": "a", "MinimumCredits": False, "MaximumCredits": None},
    {"Id": "a", "MinimumCredits": float("nan"), "MaximumCredits": None},
    {"Id": "a", "MinimumCredits": 1, "MaximumCredits": 4},
    {"Id": "a", "MinimumCredits": 4},
])
def test_unknown_credits_preserve_initial_payload(tmp_path, details):
    api = client(tmp_path, [response(details), response([])])
    api.check_section_status("a")
    api.register_multiple_sections(["a"])
    entry, = api.session.request.call_args.kwargs["json"]["sectionRegistrations"]
    assert "Credits" not in entry


def test_credit_rejection_does_not_override_account_enrollment(tmp_path):
    notice = error("one", "GE*3000*03 - This is not a variable credit course. Credits cannot be changed.")
    api = client(tmp_path, [response([notice])], [snapshot(one=S.REGISTERED)])
    assert api.execute_full_attack(["one"])[0]
    assert next(e for e in events(api) if e["event"] == "submission")["rejections"] == {
        "one": "credits_rejected"}


@pytest.mark.parametrize("reply", [requests.ReadTimeout(), response(status=503),
                                   response(text="<html>Working...</html>"), response({}),
                                   response([error("a", "Closed")])])
def test_completion_timeout_http_and_body_are_not_success(tmp_path, reply):
    api = client(tmp_path, [reply])
    result = api.complete_registration()
    assert result.status == SubmissionStatus.UNKNOWN
    with pytest.raises(TypeError):
        bool(result)


def test_complete_timeout_can_only_succeed_with_independent_records(tmp_path):
    api = client(tmp_path, [response([]), requests.ReadTimeout()], [snapshot(a=S.REGISTERED)])
    assert api.execute_full_attack(["a"])[0]
    assert next(e for e in events(api) if e["event"] == "completion")["status"] == "UNKNOWN"


@pytest.mark.parametrize("reply", [response(status=401),
                                   response(status=302, location="/Student/Account/Login?ReturnUrl=DegreePlans"),
                                   response({"InvalidSession": "true"}),
                                   response(text='<form><input type="password"></form>')])
@pytest.mark.parametrize("operation", ["register", "complete", "availability"])
def test_expired_session_propagates_everywhere(tmp_path, reply, operation):
    api = client(tmp_path, [reply])
    with pytest.raises(SessionExpiredError):
        if operation == "register":
            api.execute_full_attack(["a"])
        elif operation == "complete":
            api.complete_registration()
        else:
            api.check_section_status("a")
    assert api.session.request.call_args.kwargs["allow_redirects"] is False
    assert any(e.get("reason") == "session_expired" for e in events(api))


@pytest.mark.parametrize("reply,expected", [
    (response(text='<a href="/Student/Account/LogOff">Sign out</a>'
                   '<input name="__RequestVerificationToken" value="TOKEN_SECRET">'), SessionStatus.VALID),
    (response(status=302, location="/Student/Account/Login"), SessionStatus.EXPIRED),
    (response(status=401), SessionStatus.EXPIRED),
    (response({"InvalidSession": "true"}), SessionStatus.EXPIRED),
    (response("Could not validate this request's antiforgery token.", status=400), SessionStatus.EXPIRED),
    (response(text='<input type="password">'), SessionStatus.EXPIRED),
    (response(status=403), SessionStatus.UNKNOWN),
    (response(status=429), SessionStatus.UNKNOWN),
    (response(status=503), SessionStatus.UNKNOWN),
    (response(text="<html>Login service temporarily overloaded</html>"), SessionStatus.UNKNOWN),
    (response({}), SessionStatus.UNKNOWN),
    (requests.ReadTimeout(), SessionStatus.UNKNOWN),
])
def test_session_check_three_states(tmp_path, reply, expected):
    api = client(tmp_path, [reply])
    assert api.check_session().status == expected
    assert api.session.request.call_args.args[0] == "GET"


def test_failed_read_preserves_confirmed_and_leaves_others_unknown(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()],
                 [snapshot(a=S.REGISTERED), requests.ReadTimeout(), snapshot(b=S.REGISTERED)])
    assert not api.execute_full_attack(["a", "b"])[0]
    first = api.results["a"].first_confirmed_at
    assert not api.execute_full_attack(["a", "b"])[0]
    assert api.results["a"].status == S.REGISTERED
    assert api.execute_full_attack(["a", "b"])[0]
    assert api.results["a"].first_confirmed_at == first
    assert api.session.request.call_count == 1


def test_retry_requires_two_negative_reads_and_grace_after_completion(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr("core_api.time.monotonic", lambda: clock[0])

    api = client(tmp_path, [], [snapshot(a=S.NOT_REGISTERED)] * 3 + [snapshot(a=S.REGISTERED)])

    def submit(method, url, **kwargs):
        if url.endswith("/CompleteRegistration"):
            clock[0] = 12
        elif clock[0] == 0:
            return response([])
        raise requests.ReadTimeout()

    api.session.request.side_effect = submit
    api.execute_full_attack(["a"])
    clock[0] = 16
    api.execute_full_attack(["a"])
    assert api.session.request.call_count == 2
    assert api.results["a"].awaiting_verification
    assert api._retry_after["a"] == 17
    clock[0] = 17
    assert api.execute_full_attack(["a"])[0]
    assert api.session.request.call_count == 3


@pytest.mark.parametrize("invalid", [None, {}, RegistrationSnapshot({"a": {"status": "REGISTERED"}})])
def test_malformed_reader_output_stays_unknown(tmp_path, invalid):
    api = client(tmp_path, [requests.ReadTimeout()], [invalid])
    assert not api.execute_full_attack(["a"])[0]
    assert api.results["a"].status == S.UNKNOWN
    assert api.results["a"].reason == "invalid_registration_records"


def test_waitlist_requires_scoped_closed_and_verified_absence(tmp_path):
    api = client(tmp_path, [response([error("a", "Closed")]), response([]), response([])],
                 [snapshot(a=S.NOT_REGISTERED), snapshot(a=S.WAITLISTED)])
    api.execute_full_attack(["a"], enable_waitlist=True)
    assert api.session.request.call_count == 1
    assert not api.execute_full_attack(["a"], enable_waitlist=True)[0]
    assert api.session.request.call_args_list[1].kwargs["json"]["sectionRegistrations"][0]["Action"] == "Waitlist"
    assert api.results["a"].status == S.WAITLISTED
    assert not api.pending_sections(["a"])
    assert api.results["a"].first_confirmed_at is None


def test_uncertain_swap_does_not_replay_drop_or_report_success(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()],
                 [snapshot(a=S.NOT_REGISTERED, old=S.REGISTERED), snapshot(a=S.REGISTERED), snapshot(a=S.REGISTERED)])
    assert not api.execute_full_attack(["a"], drop_section_id="old")[0]
    assert not api.execute_full_attack(["a"], drop_section_id="old")[0]
    assert api.session.request.call_count == 1
    assert "old" in api._drop_pending


def test_failure_log_contains_paired_requests_and_results_without_credentials(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout("TOKEN_SECRET COOKIE_SECRET")])
    api.execute_full_attack(["a", "b"])
    raw = api.events.path.read_text(encoding="utf-8")
    assert "TOKEN_SECRET" not in raw and "COOKIE_SECRET" not in raw
    records = events(api)
    assert records[0]["event"] == "round_started"
    assert records[-1]["event"] == "round_finished"
    start = next(e for e in records if e["event"] == "request_started")
    end = next(e for e in records if e["event"] == "request_finished")
    assert start["request_id"] == end["request_id"]
    assert end["response_received_at"] is None
    assert end["reason"] == "ReadTimeout" and end["elapsed_ms"] >= 0
    assert set(records[-1]["sections"]) == {"a", "b"}
    assert all(re.search(r"\.\d{3}\+00:00$", e["timestamp"]) for e in records)
    assert all(x["server_registered_at"] is None for x in records[-1]["sections"].values())


def test_disk_error_is_visible_and_does_not_mask_registration(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout()], [snapshot(a=S.REGISTERED)])
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    api.events.path = blocked / "registration.jsonl"
    api.log_callback = Mock()
    assert api.execute_full_attack(["a"])[0]
    assert api.events.error
    assert any(call.args[1] == "error" for call in api.log_callback.call_args_list)


def test_server_timestamp_is_only_copied_from_explicit_record(tmp_path):
    recorded = "2026-11-02T11:00:00.123Z"
    snap = RegistrationSnapshot({"a": RegistrationRecord(S.REGISTERED, recorded)}, "fixture")
    api = client(tmp_path, [requests.ReadTimeout()], [snap])
    api.execute_full_attack(["a"])
    assert api.results["a"].server_registered_at == recorded
    assert api.results["a"].first_confirmed_at != recorded


def test_response_timestamp_and_malformed_notifications_are_not_success(tmp_path):
    api = client(tmp_path, [response([{}])])
    assert not api.execute_full_attack(["a"])[0]
    end = next(e for e in events(api) if e["event"] == "request_finished")
    assert end["response_received_at"] is not None
    assert api.session.request.call_count == 1  # No CompleteRegistration for malformed response.


def test_explicit_expiration_rolls_back_attempt_then_rechecks_after_relogin(tmp_path):
    before = snapshot(a=S.NOT_REGISTERED, old=S.REGISTERED)
    api = client(tmp_path, [response({"InvalidSession": "true"}), requests.ReadTimeout()],
                 [before, before, snapshot(a=S.REGISTERED, old=S.NOT_REGISTERED)])
    with pytest.raises(SessionExpiredError):
        api.execute_full_attack(["a"], drop_section_id="old")
    assert not api.results["a"].awaiting_verification
    assert not api.results["a"].attempted
    assert not api._retry_after and not api._drop_pending and not api._drop_attempted
    api.update_credentials({}, "new-token", "fixture-student")
    assert api.execute_full_attack(["a"], drop_section_id="old")[0]
    assert api.session.request.call_count == 2


def test_expired_completion_does_not_erase_an_accepted_add(tmp_path):
    api = client(tmp_path, [response([]), response(status=401)],
                 [snapshot(a=S.NOT_REGISTERED, old=S.REGISTERED)])
    with pytest.raises(SessionExpiredError):
        api.execute_full_attack(["a"], drop_section_id="old")
    assert api.results["a"].attempted and api.results["a"].awaiting_verification
    assert api._drop_pending == {"old"}
    assert not api.pending_sections(["a"])
