"""Promoting a dashboard user to admin: same rights as the admin password."""

import re

import pytest
from conftest import enable_login, sign_in
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services

ADMIN_PW = "admin-password-12345"


def make_app(tmp_path, fake_client, admin_password=True):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = (generate_password_hash(ADMIN_PW, method="pbkdf2:sha256:1000")
                               if admin_password else None)
    app = create_app(Cfg)
    services(app).ah = fake_client
    enable_login(app)
    for name in ("alice", "bob"):
        services(app).users.create(name, f"{name}@example.com")
    return app


@pytest.fixture
def app(tmp_path, fake_client):
    return make_app(tmp_path, fake_client)


def csrf_of(resp):
    m = re.search(r'name="csrf" value="([^"]+)"', resp.get_data(as_text=True))
    assert m, "no CSRF token in page"
    return m.group(1)


def admin_login(client, password=ADMIN_PW):
    return client.post("/admin/login", data={"csrf": csrf_of(client.get("/admin/login")), "password": password})


def admin_post(client, path, **data):
    data["csrf"] = csrf_of(client.get("/admin/"))
    return client.post(path, data=data, follow_redirects=True)


def is_admin_session(client):
    return client.get("/api/session").get_json()["admin"] is True


# ---- the store ------------------------------------------------------------------------------
def test_store_admin_flag(app):
    users = services(app).users
    assert users.get("alice")["is_admin"] is False and users.count_admins() == 0
    users.set_admin("alice", True)
    assert users.get("alice")["is_admin"] is True and users.count_admins() == 1
    assert [u["is_admin"] for u in users.list()] == [True, False]
    users.set_disabled("alice", True)
    assert users.count_admins() == 0                       # disabled admins don't count


# ---- promoting and demoting in the panel -------------------------------------------------------
def test_promote_and_demote_from_the_panel(app):
    c = app.test_client()
    admin_login(c)
    page = admin_post(c, "/admin/users/promote", username="alice").get_data(as_text=True)
    assert "alice is now an admin" in page and 'class="badge-admin"' in page
    assert services(app).users.get("alice")["is_admin"]
    page = admin_post(c, "/admin/users/demote", username="alice").get_data(as_text=True)
    assert "no longer an admin" in page and not services(app).users.get("alice")["is_admin"]


def test_promote_needs_admin_and_csrf(app):
    c = app.test_client()
    resp = c.post("/admin/users/promote", data={"username": "alice"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login")
    sign_in(c, "bob")                                       # an ordinary user can't promote
    user_csrf = csrf_of(c.get("/"))                         # valid CSRF token, but no admin rights
    assert c.post("/admin/users/promote", data={"username": "bob", "csrf": user_csrf}).status_code == 302
    admin_login(c)
    assert c.post("/admin/users/promote", data={"username": "alice"}).status_code == 400   # no CSRF token
    assert not services(app).users.get("alice")["is_admin"]
    assert not services(app).users.get("bob")["is_admin"]


# ---- an admin user's sign-in = an admin login ---------------------------------------------------
def test_admin_user_signing_in_on_the_dashboard_gets_admin_rights(app):
    services(app).users.set_admin("alice", True)
    c = app.test_client()
    sign_in(c)
    assert is_admin_session(c)
    page = c.get("/admin/")
    assert page.status_code == 200 and "AH login status" in page.get_data(as_text=True)
    assert "(you)" in page.get_data(as_text=True)
    # Full admin rights, e.g. managing users.
    assert "bob is now an admin" in admin_post(c, "/admin/users/promote", username="bob").get_data(as_text=True)
    # Still a normal signed-in user for settings sync.
    assert c.get("/api/prefs").status_code == 200


def test_ordinary_user_gets_no_admin_rights(app):
    c = app.test_client()
    sign_in(c, "bob")
    assert not is_admin_session(c)
    assert c.get("/admin/").status_code == 302


def test_admin_user_can_sign_in_on_the_admin_page(app):
    services(app).users.set_admin("alice", True)
    c = app.test_client()
    assert "/auth/login?purpose=admin" in c.get("/admin/login").get_data(as_text=True)
    resp = sign_in(c, purpose="admin")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/")
    assert c.get("/admin/").status_code == 200
    assert c.get("/api/prefs").status_code == 200           # also signed in as alice


def test_admin_page_refuses_non_admin_users(app):
    c = app.test_client()
    sign_in(c, "bob", purpose="admin")                      # signed in, but no admin rights
    assert c.get("/admin/").status_code == 302
    assert c.get("/api/prefs").status_code == 200           # still signed in as bob


def test_admin_password_failures_lock_out(app):
    c = app.test_client()
    for _ in range(app.config["ADMIN_MAX_FAILURES"]):
        assert admin_login(c, "wrong password!!").status_code == 401
    assert admin_login(c).status_code == 429                # even the right one now


@pytest.mark.parametrize("action", ["demote", "disable", "delete", "unlink"])
def test_admin_rights_end_at_once(app, action):
    users = services(app).users
    users.set_admin("alice", True)
    c = app.test_client()
    sign_in(c)
    assert is_admin_session(c)
    if action == "demote":
        users.set_admin("alice", False)
    elif action == "disable":
        users.set_disabled("alice", True)
    elif action == "delete":
        users.delete("alice")
    else:
        users.unlink_login("alice")                         # unlinked: signs out everywhere
    assert not is_admin_session(c)
    assert c.get("/admin/").status_code == 302


class Clock:
    """time.time() that tests move forward."""
    def __init__(self, monkeypatch):
        import time
        self.real, self.offset = time.time, 0.0
        monkeypatch.setattr(time, "time", lambda: self.real() + self.offset)

    def minutes(self, n):
        self.offset += n * 60


def test_admin_user_keeps_admin_rights_while_signed_in(app, monkeypatch):
    """Admin rights follow from the account: no separate admin timeout or
    confirmation for admin users (only their sign-in's own lifetime)."""
    services(app).users.set_admin("alice", True)
    c = app.test_client()
    sign_in(c)
    clock = Clock(monkeypatch)
    clock.minutes(app.config["ADMIN_SESSION_MAX_HOURS"] * 60 + 120)    # long past the admin-password limits
    assert is_admin_session(c) and c.get("/admin/").status_code == 200
    clock.minutes(app.config["USER_SESSION_DAYS"] * 24 * 60)             # the sign-in itself expired
    assert not is_admin_session(c) and c.get("/api/prefs").status_code == 401


def test_admin_password_session_still_times_out(app, monkeypatch):
    c = app.test_client()
    admin_login(c)
    clock = Clock(monkeypatch)
    clock.minutes(50)
    assert c.get("/admin/").status_code == 200                 # activity keeps it alive
    clock.minutes(50)
    assert is_admin_session(c)
    clock.minutes(app.config["ADMIN_SESSION_MINUTES"] + 1)      # idle too long
    assert not is_admin_session(c)
    c2 = app.test_client()
    admin_login(c2)
    clock2_limit = app.config["ADMIN_SESSION_MAX_HOURS"] * 60
    for _ in range(clock2_limit // 30):
        clock.minutes(30)
        c2.get("/admin/")
    clock.minutes(31)
    assert not is_admin_session(c2)                             # however active: a maximum


def test_signed_in_user_is_not_signed_out_by_the_panel(app):
    c = app.test_client()
    sign_in(c)                                              # alice, no admin rights
    assert c.get("/admin/").status_code == 302
    assert c.get("/api/prefs").status_code == 200
    page = c.get("/admin/login").get_data(as_text=True)
    assert "signed in as <strong>alice</strong>, which has no admin rights" in page


def test_dashboard_admin_button(app, monkeypatch):
    def has_button(client):
        return 'href="/admin/"' in client.get("/").get_data(as_text=True)
    anon = app.test_client()
    assert not has_button(anon)
    user = app.test_client()
    sign_in(user, "bob")
    assert not has_button(user)
    services(app).users.set_admin("alice", True)
    admin = app.test_client()
    sign_in(admin)
    assert has_button(admin)
    Clock(monkeypatch).minutes(app.config["ADMIN_SESSION_MINUTES"] + 1)
    assert has_button(admin) and admin.get("/admin/").status_code == 200   # no admin timeout for users
    pw_admin = app.test_client()
    admin_login(pw_admin)
    assert has_button(pw_admin)                             # admin password, no user


def test_promotion_and_demotion_apply_at_once(app):
    c = app.test_client()
    sign_in(c)
    assert not is_admin_session(c)
    services(app).users.set_admin("alice", True)
    assert is_admin_session(c)                              # rights follow the account, no new sign-in
    services(app).users.set_admin("alice", False)
    assert not is_admin_session(c) and c.get("/api/prefs").status_code == 200


# ---- without an admin password ------------------------------------------------------------------
def test_panel_works_with_only_admin_users(tmp_path, fake_client):
    app = make_app(tmp_path, fake_client, admin_password=False)
    c = app.test_client()
    assert c.get("/admin/login").status_code == 404          # no admin at all: no panel
    services(app).users.set_admin("alice", True)
    assert c.get("/admin/login").status_code == 200
    page = c.get("/admin/login").get_data(as_text=True)
    assert 'name="password"' not in page and "purpose=admin" in page   # only the login service: no admin password
    assert c.post("/admin/login", data={"password": ""}).status_code in (400, 401)
    assert c.get("/admin/").status_code == 302
    sign_in(c, purpose="admin")
    assert c.get("/admin/").status_code == 200


def test_last_admin_cannot_be_removed_without_an_admin_password(tmp_path, fake_client):
    app = make_app(tmp_path, fake_client, admin_password=False)
    users = services(app).users
    users.set_admin("alice", True)
    c = app.test_client()
    sign_in(c)
    for path in ("/admin/users/demote", "/admin/users/disable"):
        page = admin_post(c, path, username="alice").get_data(as_text=True)
        assert "last admin" in page
    page = admin_post(c, "/admin/users/delete", username="alice", confirm="yes").get_data(as_text=True)
    assert "last admin" in page
    assert users.get("alice")["is_admin"] and not users.get("alice")["disabled"]
    # With a second admin it is allowed.
    admin_post(c, "/admin/users/promote", username="bob")
    admin_post(c, "/admin/users/demote", username="alice")
    assert not users.get("alice")["is_admin"]


def test_last_admin_can_be_removed_when_an_admin_password_exists(app):
    services(app).users.set_admin("alice", True)
    c = app.test_client()
    admin_login(c)
    admin_post(c, "/admin/users/demote", username="alice")
    assert not services(app).users.get("alice")["is_admin"]


# ---- storage figures --------------------------------------------------------------------------
def test_prefs_sizes_in_bytes(app):
    users = services(app).users
    users.set_prefs("alice", {"ah_liked": '[{"key":"1","title":"Kaas é"}]'})
    sizes = users.prefs_sizes()
    assert set(sizes) == {"alice"}                      # bob has no synced settings
    assert sizes["alice"] > len('[{"key":"1","title":"Kaas é"}]')   # the stored JSON, in bytes


def test_panel_shows_database_and_settings_sizes(app):
    services(app).users.set_prefs("alice", {"ah_liked": "[]" + " " * 3000})
    client = app.test_client()
    admin_login(client)
    page = client.get("/admin/").get_data(as_text=True)
    assert "write-ahead log" in page and "data " in page
    assert re.search(r"alice.*?synced settings 3 KB", page, re.S)
    assert re.search(r"bob.*?synced settings 0 B", page, re.S)


def test_admin_size_filter():
    from ah_dashboard.admin.routes import _admin_size
    assert [_admin_size(n) for n in (None, 812, 36 * 1024, 1468006)] == ["0 B", "812 B", "36 KB", "1.4 MB"]
