"""L1: a copied session cookie must stop working after logout, and admin
password sessions must end when the admin password changes."""

import re
import time

import pytest
from conftest import FakeAHClient, enable_google
from conftest import sign_in as google_user_sign_in
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.auth.sessions import MAX_PER_ACCOUNT
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services

ADMIN_PW = "admin-password-12345"
COOKIE = "ahb_admin"


def make_app(tmp_path, admin_pw=ADMIN_PW):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")          # shared by apps made in one test (= restart)
        ADMIN_PASSWORD_HASH = generate_password_hash(admin_pw, method="pbkdf2:sha256:1000")
    app = create_app(Cfg)
    services(app).ah = FakeAHClient()
    return app


@pytest.fixture
def app(tmp_path):
    app = make_app(tmp_path)
    enable_google(app)
    services(app).users.create("alice", "alice@example.com")
    services(app).users.create("boss", "boss@example.com")
    services(app).users.set_admin("boss", True)
    return app


def csrf_of(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def sign_in(client, username, password=None, path="/login"):
    """Users sign in with (fake) Google; the admin password on /admin/login."""
    if path == "/admin/login":
        return client.post(path, data={"csrf": csrf_of(client, path), "password": password})
    return google_user_sign_in(client, username)


def copy_of(client, app):
    """A second browser holding a stolen copy of ``client``'s cookie."""
    thief = app.test_client()
    thief.set_cookie(COOKIE, client.get_cookie(COOKIE).value)
    return thief


def admin_page_ok(client) -> bool:
    return client.get("/admin/").status_code == 200


def signed_in_user(client):
    resp = client.get("/api/prefs")
    return resp.status_code == 200


# ---- admin password ----------------------------------------------------------------------------
def test_copied_admin_cookie_dies_at_logout(app):
    c = app.test_client()
    assert sign_in(c, "", ADMIN_PW, "/admin/login").status_code == 302
    thief = copy_of(c, app)
    assert admin_page_ok(thief)                                         # works while signed in
    c.post("/admin/logout", data={"csrf": csrf_of(c, "/admin/")})
    assert not admin_page_ok(thief)                                     # the copy is dead too
    assert not admin_page_ok(c)


def test_changing_the_admin_password_ends_admin_password_sessions(app, tmp_path):
    c = app.test_client()
    sign_in(c, "", ADMIN_PW, "/admin/login")
    assert admin_page_ok(c)
    restarted = make_app(tmp_path, admin_pw="a completely new password!")   # new ADMIN_PASSWORD_HASH
    stale = restarted.test_client()
    stale.set_cookie(COOKIE, c.get_cookie(COOKIE).value)
    assert not admin_page_ok(stale)


def test_admin_session_survives_restart_with_unchanged_hash(tmp_path):
    hashed = generate_password_hash(ADMIN_PW, method="pbkdf2:sha256:1000")

    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = hashed
    first = create_app(Cfg)
    c = first.test_client()
    sign_in(c, "", ADMIN_PW, "/admin/login")
    again = create_app(Cfg).test_client()                                 # same hash, same database
    again.set_cookie(COOKIE, c.get_cookie(COOKIE).value)
    assert admin_page_ok(again)


def test_old_cookies_without_a_session_id_are_refused(app):
    c = app.test_client()
    with c.session_transaction() as sess:                               # an admin cookie from before this fix
        sess["admin_login_at"] = time.time()
        sess["csrf"] = "x"
    assert not admin_page_ok(c)
    with c.session_transaction() as sess:                               # a user cookie from before this fix
        rec = services(app).users.get("alice")
        sess["user"] = {"name": "alice", "ver": rec["session_version"], "at": time.time()}
    assert not signed_in_user(c)


# ---- users -------------------------------------------------------------------------------------------
def test_copied_user_cookie_dies_at_logout_but_other_devices_stay(app):
    phone, laptop = app.test_client(), app.test_client()
    sign_in(phone, "alice")
    sign_in(laptop, "alice")
    thief = copy_of(phone, app)
    assert signed_in_user(thief)
    phone.post("/logout", data={"csrf": csrf_of(phone, "/")})
    assert not signed_in_user(thief) and not signed_in_user(phone)
    assert signed_in_user(laptop)                                       # only that one sign-in ended


def test_admin_user_logout_ends_user_and_admin_rights(app):
    c = app.test_client()
    sign_in(c, "boss")
    thief = copy_of(c, app)
    assert admin_page_ok(thief)
    c.post("/logout", data={"csrf": csrf_of(c, "/")})
    assert not admin_page_ok(thief) and not signed_in_user(thief)


def test_signing_in_again_ends_the_previous_sign_in_of_that_browser(app):
    c = app.test_client()
    sign_in(c, "alice")
    old_copy = copy_of(c, app)
    sign_in(c, "alice")                                                  # new session id
    assert signed_in_user(c) and not signed_in_user(old_copy)


def test_deleting_a_user_removes_their_sessions(app):
    c = app.test_client()
    sign_in(c, "alice")
    before = services(app).sessions.count()
    services(app).users.delete("alice")
    assert services(app).sessions.count() == before - 1


def test_sessions_per_account_are_capped_and_old_ones_pruned(app):
    store = services(app).sessions
    sids = [store.create("alice") for _ in range(MAX_PER_ACCOUNT + 5)]
    assert not store.valid(sids[0], "alice") and store.valid(sids[-1], "alice")
    assert store.count() <= MAX_PER_ACCOUNT
    assert not store.valid(sids[-1], "boss") and not store.valid(sids[-1], None)   # bound to its account
    services(app).db.execute("UPDATE sessions SET created_at = 0")
    assert not store.valid(sids[-1], "alice")                            # too old
    store.create("boss")
    assert store.count() == 1                                            # expired rows removed
