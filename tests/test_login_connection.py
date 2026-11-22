"""Configuring the login-service connection in the admin panel (like File Share's pane)."""

from __future__ import annotations

from conftest import as_admin, configure, enable_login, sign_in
from fake_op import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeOP

from ah_dashboard.container import services

CSRF = "test-csrf"


def _form(**values):
    return {"csrf": CSRF, **values}


def test_password_admin_configures_the_connection(app):
    c = app.test_client()
    as_admin(c, CSRF)
    s = services(app)
    s.login_connection.http = FakeOP().http
    page = c.get("/admin/").data
    assert b'id="login-service"' in page and b"/auth/callback" in page and b"/auth/backchannel-logout" in page
    assert s.login is None and c.get("/auth/login").status_code == 404
    c.post("/admin/login-service", data=_form(issuer="https://unreachable.test", client_id=CLIENT_ID,
                                              client_secret=CLIENT_SECRET))
    assert s.login_connection.config() is None                          # tested before saving
    c.post("/admin/login-service", data=_form(issuer="not a url", client_id=CLIENT_ID, client_secret="x"))
    assert s.login_connection.config() is None
    c.post("/admin/login-service", data=_form(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET))
    assert s.login_connection.configured() and s.login is not None
    assert CLIENT_SECRET not in s.db.one("SELECT secret FROM login_config")["secret"]   # sealed
    assert CLIENT_SECRET not in c.get("/admin/").data.decode()                         # never shown again
    assert c.get("/auth/login").headers["Location"].startswith(ISSUER + "/authorize?")
    assert ISSUER in c.get("/").headers["Content-Security-Policy"]                     # form-action follows
    c.post("/admin/login-service", data=_form(issuer=ISSUER, client_id="renamed"))
    assert s.login_connection.configured() and s.login.config()["client_id"] == "renamed"   # empty secret kept
    assert c.post("/admin/login-service/test", data=_form()).status_code == 302
    c.post("/admin/login-service/disconnect", data=_form())
    assert s.login_connection.config() is None and s.login is None


def test_panel_overrides_settings_and_disconnect_falls_back(app):
    configure(app, LOGIN_ISSUER="https://env.test", LOGIN_CLIENT_ID="env-id", LOGIN_CLIENT_SECRET="env-secret")
    s = services(app)
    s.login_connection.http = FakeOP().http
    assert s.login_connection.config()["source"] == "settings"
    c = app.test_client()
    as_admin(c, CSRF)
    c.post("/admin/login-service", data=_form(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET))
    assert s.login.issuer == ISSUER
    c.post("/admin/login-service/disconnect", data=_form())
    assert s.login_connection.config()["source"] == "settings" and s.login.issuer == "https://env.test"


def test_admins_from_the_login_service_cannot_change_it(app, client):
    enable_login(app)
    configure(app, ADMIN_PASSWORD_HASH="configured")
    s = services(app)
    s.users.create("boss")
    s.users.set_admin("boss", True)
    sign_in(client, "boss", purpose="admin")
    page = client.get("/admin/").data
    assert b'id="login-service"' in page and b'name="client_secret"' not in page
    with client.session_transaction() as sess:
        csrf = sess["csrf"]
    for path in ("/admin/login-service", "/admin/login-service/test", "/admin/login-service/disconnect"):
        r = client.post(path, data={"csrf": csrf, "issuer": "https://evil.test", "client_id": "x", "client_secret": "y"})
        assert r.status_code == 403, path
    assert s.login_connection.config()["issuer"] == ISSUER
