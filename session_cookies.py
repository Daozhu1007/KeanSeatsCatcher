from collections.abc import Mapping
from urllib.parse import urlsplit

from requests.cookies import RequestsCookieJar, create_cookie


AUTH_COOKIE_NAMES = frozenset({
    "studentselfservice_production",
    "studentselfservice_production_Sso",
    "studentselfservice_production_0",
    "studentselfservice_production_sfid",
    "studentselfservice_production_ls",
})


class SessionCookies(RequestsCookieJar):
    """Keep browser cookie scope when KeanWISE renews authentication."""

    @classmethod
    def from_browser(cls, cookies, url):
        jar = cls()
        origin = urlsplit(url)
        host = origin.hostname
        legacy = isinstance(cookies, Mapping)
        if legacy:
            cookies = ({"name": name, "value": value} for name, value in cookies.items())
        for entry in cookies:
            domain = entry.get("domain") or host
            scope = domain.lstrip(".").lower()
            if scope != host and not host.endswith("." + scope):
                continue
            expiry = entry.get("expiry")
            rest = {}
            if entry.get("httpOnly"):
                rest["HttpOnly"] = None
            if entry.get("sameSite"):
                rest["SameSite"] = entry["sameSite"]
            cookie = create_cookie(entry["name"], entry["value"], domain=domain,
                                   path=entry.get("path") or "/", expires=expiry,
                                   secure=entry.get("secure", origin.scheme == "https"),
                                   discard=expiry is None, rest=rest)
            cookie.domain_specified = domain.startswith(".") or scope != host
            cookie.legacy_path = origin.path if legacy else None
            jar.set_cookie(cookie)
        return jar

    def copy(self):
        # CookieJar uses this lock when accepting the response's Set-Cookie headers.
        with self._cookies_lock:
            jar = type(self)()
            jar.update(self)
            return jar

    def set_cookie(self, cookie, *args, **kwargs):
        with self._cookies_lock:
            if cookie.name in AUTH_COOKIE_NAMES and not getattr(cookie, "legacy_path", None):
                domain = cookie.domain.lstrip(".")
                path_prefix = cookie.path if cookie.path.endswith("/") else cookie.path + "/"
                for old in self:
                    if old.name != cookie.name:
                        continue
                    same_scope = old.domain.lstrip(".") == domain and old.path == cookie.path
                    # A legacy export had no scope. Replace its fallback when
                    # the new cookie applies where the original was obtained.
                    legacy_path = getattr(old, "legacy_path", None)
                    legacy_replaced = legacy_path and (legacy_path == cookie.path or legacy_path.startswith(path_prefix))
                    if same_scope or legacy_replaced:
                        super().clear(old.domain, old.path, old.name)
            super().set_cookie(cookie, *args, **kwargs)

    def clear(self, domain=None, path=None, name=None):
        with self._cookies_lock:
            try:
                super().clear(domain, path, name)
            except KeyError:
                if name not in AUTH_COOKIE_NAMES or domain is None or path is None:
                    raise
                # CookieJar adds a leading dot to a received Domain attribute.
                alternate = domain.lstrip(".") if domain.startswith(".") else "." + domain
                super().clear(alternate, path, name)
