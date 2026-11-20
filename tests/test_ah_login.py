"""Getting the server's AH login right: token shape checks, logging in with a
login code, and clear messages when AH refuses a renewal."""

import logging
import re

import pytest
from test_server_login import _RefreshHandler, refresh_url  # noqa: F401  (fixture)
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.ah.auth import AHAuthError, clean_pasted, login_code, token_kind
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services

PASSWORD = "correct horse battery staple"
REFRESH = "abcdef12-3456-4789-8abc-def123456789"
ACCESS = "12345678_not-a-real-access-token"


# ---- shapes ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,kind", [
    (REFRESH, "refresh"), (REFRESH.upper(), "refresh"), (ACCESS, "access"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "jwt"),
    ("appie://login-exit?code=abc123", "code"), ("code=abc123", "code"),
    ("refresh-1", "unknown"), ("", "empty"), (None, "empty"),
])
def test_token_kind(value, kind):
    assert token_kind(value) == kind


def test_clean_pasted_removes_copy_paste_leftovers():
    assert clean_pasted(f'  "{REFRESH}",  ') == REFRESH
    assert clean_pasted(f"'{REFRESH}'") == REFRESH
    assert clean_pasted(f"Bearer {ACCESS}") == ACCESS


def test_login_code_from_link_or_code():
    assert login_code("appie://login-exit?code=AbC-123_x&state=1") == "AbC-123_x"
    assert login_code("  AbC-123_x  ") == "AbC-123_x"
    assert login_code("appie://login-exit?foo=1") is None
    assert login_code("") is None and login_code("a b c d") is None


# ---- admin panel --------------------------------------------------------------------------
@pytest.fixture
def app(tmp_path, refresh_url):  # noqa: F811
    class Cfg(TestingConfig):
        ADMIN_PASSWORD_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
        AH_REFRESH_TOKEN = REFRESH
        AH_AUTH_REFRESH_URL = "https://example.invalid/refresh"   # validated as https; replaced below
        DATABASE_FILE = str(tmp_path / "app.db")
    app = create_app(Cfg)
    tokens = services(app).tokens
    tokens._refresh_url = refresh_url                                  # the local fake AH
    tokens._token_url = refresh_url.rsplit("/refresh", 1)[0]
    return app


def csrf_from(resp):
    return re.search(r'name="csrf" value="([^"]+)"', resp.get_data(as_text=True)).group(1)


@pytest.fixture
def admin(app):
    c = app.test_client()
    token = csrf_from(c.get("/admin/login"))
    c.post("/admin/login", data={"csrf": token, "password": PASSWORD})
    return c


def post(c, path, **data):
    data["csrf"] = csrf_from(c.get("/admin/"))
    return c.post(path, data=data, follow_redirects=True).get_data(as_text=True)


@pytest.mark.parametrize("pasted,message", [
    (ACCESS, "holds the access token"),
    ("appie://login-exit?code=abc", "login code"),
    ("refresh-123", "does not look like an AH refresh token"),
])
def test_wrong_kind_in_refresh_field_is_refused_with_a_reason(admin, app, pasted, message):
    page = post(admin, "/admin/tokens", refresh_token=pasted)
    assert message in page
    assert pasted not in page                                          # never echoed
    assert services(app).tokens.status()["source"] == "environment"   # unchanged


def test_swapped_fields_are_refused(admin, app):
    page = post(admin, "/admin/tokens", access_token=REFRESH, refresh_token=REFRESH)
    assert "swapped" in page


def test_quoted_and_json_pastes_are_accepted(admin, app):
    page = post(admin, "/admin/tokens", refresh_token=f'"{REFRESH}",')
    assert "AH login saved" in page
    other = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    page = post(admin, "/admin/tokens",
                refresh_token='{"access_token": "%s", "refresh_token": "%s", "expires_in": 604798}' % (ACCESS, other))
    assert "AH login saved" in page
    assert services(app).tokens.access_token() == ACCESS


def test_status_shows_whether_stored_refresh_token_looks_right(admin, app):
    assert "looks right" in admin.get("/admin/").get_data(as_text=True)
    services(app).tokens.set_tokens(None, ACCESS)               # e.g. set wrongly before this check existed
    page = admin.get("/admin/").get_data(as_text=True)
    assert "does not look like a refresh token" in page and "access token" in page
    assert ACCESS not in page


def test_login_with_code_gets_and_stores_tokens(admin, app):
    page = post(admin, "/admin/login-code", code="appie://login-exit?code=the-code-123")
    assert "Logged in to AH" in page
    headers, body = _RefreshHandler.calls[-1]
    assert body == {"clientId": "appie-ios", "code": "the-code-123"}  # appie-go exchangeCode
    status = services(app).tokens.status()
    assert status["source"] == "admin" and status["expires_at"]
    assert services(app).tokens.access_token() == "access-1"
    assert "the-code-123" not in page


def test_login_with_refused_code_explains(admin, app):
    _RefreshHandler.fail_status = 400
    page = post(admin, "/admin/login-code", code="old-code")
    assert "did not accept the login code" in page and "reference" in page
    assert services(app).tokens.status()["source"] == "environment"


def test_login_with_code_needs_a_code(admin):
    assert "Paste the code" in post(admin, "/admin/login-code", code="appie://login-exit?x=1")


@pytest.mark.parametrize("status,message", [
    (401, "expired or was already used"),
    (400, "probably not a refresh token"),
])
def test_renewal_failures_explain_the_cause(admin, app, status, message):
    _RefreshHandler.fail_status = status
    page = post(admin, "/admin/renew")
    assert message in page and "Log in again below" in page


def test_refresh_error_carries_ahs_status(app):
    _RefreshHandler.fail_status = 400
    with pytest.raises(AHAuthError) as exc:
        services(app).tokens.renew_now()
    assert exc.value.status == 400


def test_env_refresh_token_with_wrong_shape_is_warned_about_without_the_value(tmp_path):
    class Cfg(TestingConfig):
        AH_REFRESH_TOKEN = ACCESS
        DATABASE_FILE = str(tmp_path / "app.db")
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    logger = logging.getLogger("ah_dashboard.ah.auth")
    handler = Keep(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        create_app(Cfg)
    finally:
        logger.removeHandler(handler)
    text = "\n".join(records)
    assert "does not look like a refresh token" in text and "access" in text
    assert ACCESS not in text
