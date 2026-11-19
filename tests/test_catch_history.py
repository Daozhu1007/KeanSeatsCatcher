import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import requests
from PyQt6.QtTest import QTest

from catch_history import CatchHistory
from core_api import KeanApiClient
from registration_reader import DegreePlanRegistrationReader
from registration_state import SectionStatus as S
from test_registration import client, events, response, snapshot
from test_registration_reader import course
from ui_catches import MyCatchesInterface
from ui_main import MonitorInterface


def test_verified_catch_retains_scoped_evidence_and_scrubs_credentials(tmp_path):
    sid = "24242"
    enrolled = course(sid, S.REGISTERED)
    enrolled.update(Credits=1)
    enrolled["Section"]["FullTitleDisplay"] = "Fixture seminar"
    notice = {
        "Type": "Information",
        "SectionId": sid,
        "Code": "REG_RECEIVED",
        "Message": "Request received. token=NEW_TOKEN Link /login?code=PRIVATE_CODE",
    }
    catalog = response({"Id": sid, "TermId": "2027SP", "Available": 1,
                        "FullTitleDisplay": "Fixture seminar", "MinimumCredits": 1, "MaximumCredits": None})
    added = response({"Notifications": [notice], "Set-Cookie": "ROTATED_COOKIE",
                      "Detail": "REFLECTED_TOKEN and COOKIE_SECRET", "Result": "submitted"})
    finished = response({"Message": "Registration submitted."})
    plan = response({"DegreePlan": {
        "PersonId": "fixture-student", "Notifications": [], "Terms": [
            {"Code": "2027SP", "PlannedCourses": [enrolled, course("unrequested", S.PLANNED)]}
        ],
    }})
    api = KeanApiClient({"cookie": "COOKIE_SECRET"}, "REFLECTED_TOKEN", "fixture-student",
                        DegreePlanRegistrationReader(), log_dir=tmp_path)
    api.session.request = Mock(side_effect=[catalog, added, finished, plan, plan])
    api.log_callback = Mock()
    assert api.check_section_status(sid)
    assert api.execute_full_attack([sid])[0]
    api.verify_registration([sid])
    record, = CatchHistory().records()
    assert record["course_name"] == "Fixture seminar"
    assert record["student_id"] == "fixture-student" and record["term_id"] == "2027SP"
    assert record["credits"] == 1 and record["credits_source"] == "account"
    assert record["sent_credits"] == {"status": "included", "value": 1}
    assert record["confirmed_at"] == api.results[sid].first_confirmed_at
    add, complete, read = record["requests"]
    assert add["request"]["json"] == api.session.request.call_args_list[1].kwargs["json"]
    assert add["response"]["body"]["Notifications"][0]["Code"] == "REG_RECEIVED"
    assert add["response"]["body"]["Result"] == "submitted"
    assert complete["response"]["body"]["Message"] == "Registration submitted."
    assert read["request"]["params"] == {"studentId": "fixture-student"}
    verification = next(event for event in events(api) if event["event"] == "verification")
    assert verification["request_id"] == read["request_id"]
    assert verification["account_records"][sid]["status"] == "REGISTERED"
    assert verification["observed_at"] == record["confirmed_at"]
    assert all(trace["elapsed_ms"] >= 0 and trace["response_received_at"] for trace in record["requests"])
    assert api.session.request.call_count == 5
    output = "\n".join((api.events.path.read_text(encoding="utf-8"), json.dumps(record),
                        str(api.log_callback.call_args_list)))
    assert "Request received." in output
    for secret in ("NEW_TOKEN", "PRIVATE_CODE", "ROTATED_COOKIE", "REFLECTED_TOKEN", "COOKIE_SECRET", "unrequested"):
        assert secret not in output


def test_reverified_duplicate_is_not_a_new_catch(tmp_path):
    api = client(tmp_path, [response([{"Type": "Error", "Message": "Already registered"}])],
                 [snapshot(a=S.REGISTERED)])
    assert api.execute_full_attack(["a"])[0]
    assert CatchHistory().records() == []


def test_error_bodies_keep_diagnostics_and_omit_login_markup(tmp_path):
    api = client(tmp_path, [response(status=503, text="Registration service busy. Cookie: other=NEW_COOKIE"),
                           response(text='<html><input name="token" value="HTML_SECRET"></html>')])
    api.log_callback = Mock()
    api.register_multiple_sections(["a"])
    api.complete_registration(["a"])
    failures = [event for event in events(api) if event["event"] == "request_finished"]
    assert failures[0]["http_status"] == 503
    assert failures[0]["response"]["body"].startswith("Registration service busy.")
    assert failures[1]["response"] == {"format": "html"}
    output = api.events.path.read_text(encoding="utf-8") + str(api.log_callback.call_args_list)
    assert "NEW_COOKIE" not in output and "HTML_SECRET" not in output


def test_timeout_can_be_saved_but_read_only_confirmation_cannot(tmp_path):
    api = client(tmp_path, [requests.ReadTimeout("Do not log the exception text")],
                 [snapshot(a=S.REGISTERED, b=S.REGISTERED)])
    assert api.execute_full_attack(["a", "b"], submission_ids=["a"])[0]
    record, = CatchHistory().records()
    assert record["section_id"] == "a"
    assert record["requests"][0]["response"] is None
    assert record["requests"][0]["reason"] == "ReadTimeout"


def test_concurrent_history_keeps_first_confirmation_per_account_and_term(tmp_path):
    history = CatchHistory()
    original = dict(student_id="student-a", term_id="2027SP", section_id="54321",
                    confirmed_at="2026-11-19T07:00:00.123+08:00")
    assert history.save([original])
    repeated = dict(original, confirmed_at="2026-11-19T07:00:01.123+08:00")
    account = dict(repeated, student_id="student-b")
    term = dict(repeated, term_id="2027FA")
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(lambda row: CatchHistory().save([row]), [repeated, account, term])) == [
            False, True, True,
        ]
    assert CatchHistory(history.path).records()[-1] == original
    assert len(history.records()) == 3


def test_legacy_recovery_retains_timings_and_knows_when_credits_were_omitted(tmp_path):
    path = tmp_path / "registration-legacy.jsonl"
    time = "2026-11-19T07:00:00.123+08:00"
    common = dict(run_id="legacy", timestamp=time)
    calls = []
    for request_id, operation, method, round_id in (
            ("a", "register_add", "POST", 1), ("b", "complete_registration", "POST", 1),
            ("c", "registration_read", "GET", 2)):
        request = dict(common, request_id=request_id, operation=operation, section_ids=["a", "b"], round_id=round_id)
        calls.extend((dict(request, event="request_started", method=method),
                      dict(request, event="request_finished", elapsed_ms=145.6, http_status=200,
                           response_received_at=time)))
    calls.insert(2, dict(common, event="submission", section_ids=["a", "b"], unresolved_credits=["b"]))
    confirmed = dict(status="REGISTERED", attempted=True, term_id="2027SP", first_confirmed_at=time)
    calls.append(dict(common, event="verification", sections={"a": confirmed, "b": confirmed}))
    path.write_text("\n".join(map(json.dumps, calls)) + '\n{"event":', encoding="utf-8")
    history = CatchHistory()
    details = {"a": {"course_name": "Fixture A", "credits": 1}, "b": {"course_name": "Fixture B", "credits": 3}}
    for _ in range(2):
        history.import_legacy(path, details)
    a, b = history.records()
    assert a["sent_credits"] == {"status": "included_value_unrecorded"}
    assert b["sent_credits"] == {"status": "omitted"}
    assert b["credits"] == 3 and b["credits_source"] == "provided"
    assert all(record["student_id"] is None and record["confirmed_at"] == time for record in (a, b))
    assert [trace["round_id"] for trace in a["requests"]] == [1, 1, 2]
    assert all(trace["elapsed_ms"] == 145.6 and "response" not in trace for trace in a["requests"])
    log = path.read_bytes()
    assert history.delete(a)
    assert CatchHistory(history.path).records() == [b]
    assert path.read_bytes() == log


def test_my_catches_deletes_only_the_selected_local_record(tmp_path, monkeypatch, qt_app):
    history = CatchHistory()
    record = dict(student_id="student-a", term_id="2027SP", section_id="fixture", course_name="Fixture",
                  confirmed_at="2026-11-19T07:00:00.123+08:00", source="live", source_log="run.jsonl",
                  sent_credits={"status": "unrecorded"}, requests=[])
    other_account = dict(record, student_id="student-b")
    other_term = dict(record, term_id="2027FA")
    history.save([record, other_account, other_term])
    log = tmp_path / record["source_log"]
    log.write_bytes(b'{"event":"verification"}\n')

    page = MyCatchesInterface()
    dialog = Mock()
    dialog.exec.side_effect = [False, True]
    monkeypatch.setattr("ui_catches.MessageBox", Mock(return_value=dialog))
    page.refresh()
    page.accounts.setCurrentIndex(page.accounts.findData("student-a"))
    page.table.selectRow(page.visible_records.index(record))
    assert page.btn_delete.isEnabled()
    page.btn_delete.click()
    assert len(history.records()) == 3
    page.btn_delete.click()
    assert CatchHistory(history.path).records() == [other_term, other_account]
    assert page.visible_records == [other_term]
    assert log.read_bytes() == b'{"event":"verification"}\n'
    page.close()


def test_dashboard_asks_for_credits_after_editing_finishes(qt_app):
    monitor = MonitorInterface()
    api = Mock()
    monitor.set_api_engine(api)
    monitor.show()
    monitor.section_input.setFocus()
    try:
        for keys in ("3", "1", "98"):
            QTest.keyClicks(monitor.section_input, keys)
            QTest.qWait(330)
            api.prefetch_registration_credits.assert_not_called()
        QTest.keyClicks(monitor.section_input, "1")
        monitor.time_input.setFocus()
        qt_app.processEvents()
        api.prefetch_registration_credits.assert_called_once_with(["31981"], include_section_details=True)
    finally:
        monitor.close()
