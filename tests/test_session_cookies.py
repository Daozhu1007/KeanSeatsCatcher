import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from types import SimpleNamespace

import pytest
import requests

from core_api import KeanApiClient
from registration_state import SessionStatus
from test_registration import events, response


HOST = "kean-ss.colleague.elluciancloud.com"
PARENT = "colleague.elluciancloud.com"
AUTH = "studentselfservice_production"
ROUTING = f"{AUTH}_ls"
ANTIFORGERY = ".ColleagueSelfServiceAntiforgery"
VALID_PAGE = ('<a href="/Student/Account/LogOff">Out</a>'
              '<input name="__RequestVerificationToken" value="page-token">')
SESSION_REQUEST = requests.Session.request


def stub_http(monkeypatch, send):
    # Use Requests' real preparation and cookie parsing with an in-memory adapter.
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", lambda adapter, request, **kwargs: send(request))
    monkeypatch.setattr(requests.Session, "request", SESSION_REQUEST)


def reply(request, *set_cookie, status=200, text=VALID_PAGE, location=None, url=None):
    resp = response(status=status, text=text, location=location)
    resp.request = request
    resp.url = url or request.url
    resp._content_consumed = True
    message = Message()
    for cookie in set_cookie:
        message.add_header("Set-Cookie", cookie)
    resp.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
    return resp


def cookie_header(api, path):
    request = api.session.prepare_request(requests.Request("GET", f"https://{HOST}{path}"))
    return request.headers.get("Cookie", "").split("; ")


def test_valid_check_preserves_authoritative_scope_and_retires_legacy_alias(tmp_path, monkeypatch):
    api = KeanApiClient({AUTH: "old", ROUTING: "remove", ANTIFORGERY: "csrf"}, "header-token", "student",
                        log_dir=tmp_path)

    def send(request):
        assert request.headers["__requestverificationtoken"] == "header-token"
        assert f"{AUTH}=old" in request.headers["Cookie"]
        return reply(request, f"{AUTH}=new; Domain={PARENT}; Path=/Student; Max-Age=3600; Secure; HttpOnly; SameSite=Lax",
                     f"{AUTH}=elsewhere; Domain=unrelated.example; Path=/Student; Secure",
                     f"{ROUTING}=; Path=/; Max-Age=0", f"{ANTIFORGERY}=replacement; Path=/", "unrelated=value; Path=/")

    stub_http(monkeypatch, send)
    assert api.check_session().status == SessionStatus.VALID
    cookie, = [entry for entry in api.session.cookies if entry.name == AUTH]
    assert (cookie.domain, cookie.path, cookie.value) == (f".{PARENT}", "/Student", "new")
    assert cookie.domain_specified and cookie.secure and cookie.expires > time.time()
    assert cookie.has_nonstandard_attr("HttpOnly") and cookie.get_nonstandard_attr("SameSite") == "Lax"
    assert cookie_header(api, "/Student/Student/Courses/SectionDetails").count(f"{AUTH}=new") == 1
    assert not any(pair.startswith(f"{AUTH}=") for pair in cookie_header(api, "/StudentOther"))
    assert api.session.cookies.get(ROUTING) is None
    assert api.session.cookies.get("unrelated") is None
    assert api.session.cookies.get(ANTIFORGERY) == "csrf"
    assert api.session.headers["__requestverificationtoken"] == "header-token"


def test_valid_check_keeps_distinct_browser_paths_and_applies_scoped_expiry(tmp_path, monkeypatch):
    api = KeanApiClient([
        {"name": AUTH, "value": "root", "domain": HOST, "path": "/"},
        {"name": AUTH, "value": "old", "domain": HOST, "path": "/Student/Planning"},
        {"name": ROUTING, "value": "root-routing", "domain": HOST, "path": "/"},
        {"name": ROUTING, "value": "old-routing", "domain": HOST, "path": "/Student/Planning"},
    ], "header-token", "student", log_dir=tmp_path)
    stub_http(monkeypatch, lambda request: reply(
        request, f"{AUTH}=new; Domain={HOST}; Path=/Student/Planning; Secure",
        f"{ROUTING}=; Domain={HOST}; Path=/Student/Planning; Max-Age=0"))

    assert api.check_session().status == SessionStatus.VALID
    assert cookie_header(api, "/Student/Planning/DegreePlans") == [
        f"{AUTH}=new", f"{AUTH}=root", f"{ROUTING}=root-routing"]
    assert cookie_header(api, "/Student/Student/Courses/SectionDetails") == [
        f"{AUTH}=root", f"{ROUTING}=root-routing"]


def test_a_different_path_cannot_remove_the_legacy_cookie_used_for_the_check(tmp_path, monkeypatch):
    api = KeanApiClient({AUTH: "old"}, "header-token", "student", log_dir=tmp_path)
    stub_http(monkeypatch, lambda request: reply(request, f"{AUTH}=elsewhere; Path=/Other"))

    assert api.check_session().status == SessionStatus.VALID
    assert cookie_header(api, "/Student/Planning/DegreePlans") == [f"{AUTH}=old"]
    assert api.session.cookies.get(AUTH, domain=HOST, path="/Other") == "elsewhere"


@pytest.mark.parametrize("status, options, expected", [
    (302, {"location": "/Student/Account/Login"}, SessionStatus.EXPIRED),
    (503, {}, SessionStatus.UNKNOWN),
    (200, {"text": "<html>Temporarily unavailable</html>"}, SessionStatus.UNKNOWN),
    (200, {"url": "https://another.example/Student/Planning/DegreePlans"}, SessionStatus.UNKNOWN),
])
def test_check_does_not_merge_cookies_without_same_origin_authentication(tmp_path, monkeypatch, status, options,
                                                                 expected):
    api = KeanApiClient({AUTH: "old"}, "header-token", "student", log_dir=tmp_path)
    stub_http(monkeypatch, lambda request: reply(request, f"{AUTH}=unexpected; Path=/", status=status, **options))

    assert api.check_session().status == expected
    assert api.session.cookies.get(AUTH) == "old"


@pytest.mark.parametrize("concurrent_change", ["login", "main_request", "session_check"])
def test_late_valid_check_cannot_roll_back_newer_credentials(tmp_path, monkeypatch, concurrent_change):
    api = KeanApiClient({AUTH: "old"}, "header-token", "student", log_dir=tmp_path)
    started, release = threading.Event(), threading.Event()

    def send(request):
        if not started.is_set():
            started.set()
            assert release.wait(3)
            return reply(request, f"{AUTH}=late; Path=/")
        return reply(request, f"{AUTH}=fresh; Path=/")

    stub_http(monkeypatch, send)
    with ThreadPoolExecutor(max_workers=1) as executor:
        check = executor.submit(api.check_session)
        try:
            assert started.wait(2)
            if concurrent_change == "login":
                api.update_credentials({AUTH: "fresh"}, "fresh-token", student_id="student")
            elif concurrent_change == "main_request":
                api._request("fixture_main_request", "GET", api.base_url)
            else:
                assert api.check_session().status == SessionStatus.VALID
        finally:
            release.set()
        assert check.result(timeout=2).status == SessionStatus.VALID
    assert api.session.cookies.get(AUTH) == "fresh"
    assert [event for event in events(api) if event["event"] == "session_check"][-1]["auth_cookie_updates"] == 0


def test_refresh_isolates_cookies_from_an_in_flight_main_response(tmp_path, monkeypatch):
    api = KeanApiClient({AUTH: "old"}, "header-token", "student", log_dir=tmp_path)
    started, release = threading.Event(), threading.Event()

    def send(request):
        started.set()
        assert release.wait(3)
        return reply(request, f"{AUTH}=late; Path=/")

    stub_http(monkeypatch, send)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(api._request, "fixture_main_request", "GET", api.base_url)
        try:
            assert started.wait(2)
            api.update_credentials({AUTH: "fresh"}, "fresh-token", student_id="student")
        finally:
            release.set()
        assert request.result(timeout=2).status_code == 200
    assert api.session.cookies.get(AUTH) == "fresh"
    assert api.session.headers["__requestverificationtoken"] == "fresh-token"
