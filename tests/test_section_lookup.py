import requests

from core_api import KeanApiClient
from i18n import i18n
from registration_reader import DegreePlanRegistrationReader
from test_registration import events, response
from test_registration_reader import degree_plan
from ui_lookup import SectionLookupInterface


def test_section_lookup_renders_scoped_read_only_results(tmp_path, monkeypatch, qt_app, degree_plan):
    api = KeanApiClient({"id": "cookie"}, "token", "fixture-student",
                        DegreePlanRegistrationReader(), log_dir=tmp_path)
    section = {"CourseName": "ABC*1010", "Number": "02", "Title": "Fixture Seminar",
               "TermId": "2027SP", "TermDisplay": "Spring 2027",
               "MinimumCredits": 1, "MaximumCredits": 3, "Available": 0, "Capacity": 18,
               "Waitlisted": 2, "WaitlistMaximum": 10, "AllowWaitlist": True, "WaitlistAvailable": True}
    catalog = {
        "registered": dict(section, Id="registered", MinimumCredits=3, MaximumCredits=None),
        "waiting": dict(section, Id="waiting", WaitlistAvailable=False),
        "planned": dict(section, Id="planned", AllowWaitlist=False),
        "absent": dict(section, Id="absent"),
        "other": dict(section, Id="other", TermId="2027SPW", TermDisplay="Spring 2027 Wenzhou"),
        "missing": dict(section, Id="wrong"),
    }
    calls = []
    catalog_url = "https://kean-ss.colleague.elluciancloud.com/Student/Courses/SectionDetails"
    account_url = api.base_url + "/Current"

    def request(session, method, url, **kwargs):
        assert session is not api.session
        assert kwargs["allow_redirects"] is False
        calls.append((method, url))
        if url == account_url:
            assert kwargs["params"]["studentId"] == api.student_id
            return response(degree_plan)
        assert url == catalog_url
        assert kwargs["json"]["studentId"] == api.student_id
        return response(catalog[kwargs["json"]["sectionId"]])

    monkeypatch.setattr(requests.Session, "request", request)
    page = SectionLookupInterface()
    page.set_api_engine(api)
    page.section_input.setText("registered, waiting，planned absent, other, missing, registered")
    page.btn_lookup.click()
    assert page.worker.wait(2000)
    qt_app.processEvents()

    def column(index):
        return [page.table.item(row, index).text() for row in range(page.table.rowCount())]

    assert column(0) == list(catalog)
    assert column(1)[0] == "ABC*1010-02\nFixture Seminar"
    assert column(1)[-1] == i18n.tr("lookup_details_unavailable")
    assert column(2)[0] == "Spring 2027\n2027SP"
    assert column(3)[:2] == ["3", "1–3"]
    assert column(4)[0] == "0 / 18"
    assert column(5)[:3] == [f"2 / 10 · {i18n.tr('lookup_waitlist_open')}",
                             f"2 / 10 · {i18n.tr('lookup_waitlist_closed')}",
                             i18n.tr("lookup_waitlist_disabled")]
    assert column(6) == [i18n.tr("lookup_status_" + status) for status in
                         ("registered", "waitlisted", "planned", "not_registered", "unknown", "unknown")]
    assert calls == [("POST", catalog_url)] * len(catalog) + [("GET", account_url)]
    assert api.results == api.section_terms == {}
    assert api._last_registration_read is None
    assert api.history.records() == []
    assert all(event.get("background") for event in events(api) if event["event"] == "request_started")
    page.close()


def test_lookup_preserves_catalog_details_when_account_session_expires(tmp_path, monkeypatch):
    api = KeanApiClient({}, "token", "fixture-student", DegreePlanRegistrationReader(), log_dir=tmp_path)
    catalog = {"Id": "fixture", "TermId": "2027SP", "Title": "Fixture Seminar"}
    replies = iter([response(catalog), response(status=401)])
    monkeypatch.setattr(requests.Session, "request", lambda *args, **kwargs: next(replies))
    details, snapshot = api.lookup_sections(["fixture"])
    assert details == {"fixture": catalog}
    assert snapshot.records == {}
    assert snapshot.reason == "session_expired"
