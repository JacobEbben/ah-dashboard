"""Dashboard users: the store, sign-in/out through the login service, the login gate and the admin pages."""

import json
import re
import sqlite3
import time

import pytest
from conftest import enable_login, login_sign_in, sign_in
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.auth.users import UserError, UserStore, username_from_email
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.db import Database

ADMIN_PW = "admin-password-12345"


# ---- the store ---------------------------------------------------------------------------
def test_store_keeps_no_passwords_and_persists(tmp_path):
    path = str(tmp_path / "app.db")
    UserStore(Database(path)).create("Alice", "Alice@Example.COM")
    row = Database(path).one("SELECT * FROM users")
    assert row["username"] == "alice" and row["email"] == "alice@example.com" and row["password_hash"] == ""
    again = UserStore(Database(path))
    assert again.get("ALICE")["email"] == "alice@example.com"       # names are case-insensitive
    assert "password_hash" not in again.list()[0]


@pytest.mark.parametrize("name", ["ab", "-alice", "al ice", "a" * 33, "alice<script>", ""])
def test_store_rejects_bad_usernames(name):
    with pytest.raises(UserError) as exc:
        UserStore().create(name)
    assert exc.value.code == "invalid_username"


def test_store_rules():
    store = UserStore()
    store.create("bob", "bob@example.com")
    for name, email, code in [("BOB", None, "exists"), ("carol", "BOB@example.com", "email_taken"),
                              ("dave", "not an address", "invalid_email")]:
        with pytest.raises(UserError) as exc:
            store.create(name, email)
        assert exc.value.code == code
    with pytest.raises(UserError):
        store.delete("nobody")


def test_login_sign_in_creates_finds_and_links():
    store = UserStore()
    user, created = store.sign_in_login("sub-1", "Jan.Jansen")
    assert created and user["username"] == "jan.jansen" and user["login_sub"] == "sub-1"
    again, created = store.sign_in_login("sub-1", "jan.jansen")
    assert not created and again["username"] == user["username"]          # matched by the login-service id
    # An account from before (Google era, or made by the admin) is linked by its username, once.
    store.create("old", "old@example.com")
    linked, created = store.sign_in_login("sub-2", "OLD")
    assert not created and linked["username"] == "old" and store.get("old")["login_sub"] == "sub-2"
    # The same username with ANOTHER login-service user doesn't take over the linked account.
    other, created = store.sign_in_login("sub-3", "old")
    assert created and other["username"] == "old-2" and store.get("old")["login_sub"] == "sub-2"
    with pytest.raises(UserError) as exc:
        store.sign_in_login("sub-4", "x")                                  # not a valid username
    assert exc.value.code == "invalid_username"


def test_login_sign_in_refusals():
    store = UserStore(max_users=1)
    store.sign_in_login("sub-1", "anna")
    with pytest.raises(UserError) as exc:
        store.sign_in_login("sub-2", "bert")
    assert exc.value.code == "too_many"
    store.set_disabled("anna", True)
    with pytest.raises(UserError) as exc:
        store.sign_in_login("sub-1", "anna")
    assert exc.value.code == "disabled"


def test_unlink_login():
    store = UserStore()
    store.sign_in_login("sub-1", "alice")
    version = store.get("alice")["session_version"]
    store.unlink_login("alice")
    rec = store.get("alice")
    assert rec["login_sub"] is None and rec["session_version"] != version   # signed out everywhere
    relinked, created = store.sign_in_login("sub-9", "alice")             # e.g. re-created at the login service
    assert not created and relinked["login_sub"] == "sub-9"


@pytest.mark.parametrize("email, name", [("jo@x.nl", "jouser"), ("@x.nl", "user"), ("Ann!e..B@x.nl", "ann.e..b")])
def test_username_from_email(email, name):
    assert username_from_email(email) == name


def test_database_from_before_google_is_upgraded(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at REAL NOT NULL,
            last_login REAL, disabled INTEGER NOT NULL DEFAULT 0, session_version TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0);
        INSERT INTO users VALUES ('alice', 'scrypt:hash', 1, NULL, 0, 'v1', 1);
        PRAGMA user_version = 2;""")
    conn.close()
    db = Database(path)
    row = db.one("SELECT * FROM users")
    assert row["password_hash"] == "" and row["email"] is None and row["google_sub"] is None and row["is_admin"] == 1
    assert row["login_sub"] is None
    assert db.scalar("PRAGMA user_version") == 4


def test_google_era_database_is_upgraded_and_its_sign_ins_end(tmp_path):
    path = str(tmp_path / "v3.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at REAL NOT NULL,
            last_login REAL, disabled INTEGER NOT NULL DEFAULT 0, session_version TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, email TEXT, google_sub TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, username TEXT REFERENCES users(username) ON DELETE CASCADE,
            created_at REAL NOT NULL);
        INSERT INTO users VALUES ('jacob', '', 1, NULL, 0, 'v1', 1, 'j@example.com', 'g-1');
        INSERT INTO sessions VALUES ('s1', 'jacob', 1e10);
        INSERT INTO sessions VALUES ('s2', NULL, 1e10);
        PRAGMA user_version = 3;""")
    conn.close()
    db = Database(path)
    assert db.scalar("PRAGMA user_version") == 4
    row = db.one("SELECT * FROM users")
    assert row["username"] == "jacob" and row["login_sub"] is None and row["google_sub"] == "g-1"   # kept, unused
    assert [r["id"] for r in db.query("SELECT id FROM sessions")] == ["s2"]   # Google sign-ins ended; admin password kept
    assert {"op_sid", "id_token", "amr"} <= {r[1] for r in db.query("PRAGMA table_info(sessions)")}


# ---- app: sign-in is optional ---------------------------------------------------------------
@pytest.fixture
def app(tmp_path, fake_client):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = generate_password_hash(ADMIN_PW, method="pbkdf2:sha256:1000")
    app = create_app(Cfg)
    services(app).ah = fake_client
    enable_login(app)
    services(app).users.create("alice", "alice@example.com")
    return app


def csrf_of(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no CSRF token in page"
    return m.group(1)


def config_of(client):
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'<script type="application/json" id="app-config">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_dashboard_is_open_without_signing_in(app):
    c = app.test_client()
    page = c.get("/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'href="/auth/login"' in html and 'data-i18n="signInLogin"' in html   # straight to the login service
    assert c.post("/api/bargains", json={"stores": [{"id": 1002}]}).status_code == 200
    assert c.get("/api/bonus").status_code != 401
    cfg = config_of(c)
    assert cfg["user"] is None and cfg["csrf"] is None
    resp = c.get("/api/prefs")
    assert resp.status_code == 401 and resp.get_json()["error"]["code"] == "login_required"


def test_sign_in_and_out(app):
    c = app.test_client()
    page = c.get("/login")
    assert page.status_code == 302 and page.headers["Location"].endswith("/auth/login")   # no page in between
    failed = c.get("/login?error=loginFailed")                # after a failed sign-in: a page to retry from
    html = failed.get_data(as_text=True)
    assert 'data-i18n="loginFailed"' in html and "/auth/login" in html and "Set-Cookie" not in failed.headers
    resp = sign_in(c)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    page = c.get("/").get_data(as_text=True)
    assert "alice" in page and 'action="/logout"' in page
    assert config_of(c)["user"] == "alice" and config_of(c)["csrf"]
    assert services(app).users.get("alice")["last_login"]
    assert c.get("/login").status_code == 302                # already signed in
    assert c.post("/logout").status_code == 400              # sign-out needs the CSRF token
    resp = c.post("/logout", data={"csrf": csrf_of(page)})
    # Also signed out at the login service: its confirmation page, then back to the dashboard.
    assert resp.status_code == 302 and resp.headers["Location"].startswith("https://login.test/logout?id_token_hint=")
    assert "post_logout_redirect_uri=http%3A%2F%2Flocalhost%2F" in resp.headers["Location"]
    assert config_of(c)["user"] is None


def test_first_sign_in_creates_an_account(app):
    c = app.test_client()
    resp = login_sign_in(c, "sub-new", "new.person")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert config_of(c)["user"] == "new.person"
    rec = services(app).users.get("new.person")
    assert rec["login_sub"] == "sub-new" and rec["email"] is None and rec["last_login"]


def test_no_sign_in_without_the_login_service(tmp_path, fake_client):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
    app = create_app(Cfg)
    c = app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert 'href="/auth/login"' not in html and 'href="/login"' not in html   # no Login button
    assert 'data-i18n="signInUnavailable"' in c.get("/login").get_data(as_text=True)
    assert c.get("/auth/login").status_code == 404
    assert c.post("/login").status_code == 405                               # no password sign-in any more


def test_new_session_on_sign_in(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as sess:
        sess["planted"] = "attacker"
    sign_in(c)
    with c.session_transaction() as sess:
        assert "planted" not in sess and sess["user"]["name"] == "alice"


@pytest.mark.parametrize("action", ["unlink", "disable", "delete"])
def test_admin_changes_end_existing_sessions(app, action):
    c = app.test_client()
    sign_in(c)
    assert c.get("/api/prefs").status_code == 200
    users = services(app).users
    {"unlink": lambda: users.unlink_login("alice"),
     "disable": lambda: users.set_disabled("alice", True),
     "delete": lambda: users.delete("alice")}[action]()
    assert c.get("/api/prefs").status_code == 401
    assert config_of(c)["user"] is None and c.get("/").status_code == 200   # still usable, signed out


def test_session_expires(app, monkeypatch):
    c = app.test_client()
    sign_in(c)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + (app.config["USER_SESSION_DAYS"] + 1) * 86400)
    assert c.get("/api/prefs").status_code == 401


# ---- synced settings ---------------------------------------------------------------------------
def signed_in(app):
    c = app.test_client()
    sign_in(c)
    return c, {"X-CSRF-Token": config_of(c)["csrf"]}


def test_prefs_round_trip_and_persist(app, tmp_path):
    c, h = signed_in(app)
    assert c.get("/api/prefs").get_json() == {"prefs": {}, "updatedAt": None}
    prefs = {"ah_stores": '[{"id":1002,"name":"Home","color":"#2f6fb5"}]', "ah_lang": "nl", "ah_liked": "[]"}
    resp = c.put("/api/prefs", json={"prefs": prefs}, headers=h)
    assert resp.status_code == 200 and resp.get_json()["updatedAt"]
    got = c.get("/api/prefs").get_json()
    assert got["prefs"] == prefs and got["updatedAt"] == resp.get_json()["updatedAt"]
    row = Database(str(tmp_path / "app.db")).one("SELECT prefs FROM user_prefs WHERE username = 'alice'")
    assert json.loads(row["prefs"]) == prefs                      # stored in the database
    # Another device signed in as the same user sees the same settings.
    other, _ = signed_in(app)
    assert other.get("/api/prefs").get_json()["prefs"] == prefs
    # Deleting the user deletes their settings.
    services(app).users.delete("alice")
    assert services(app).db.one("SELECT 1 FROM user_prefs WHERE username = 'alice'") is None


def test_prefs_need_csrf_and_sign_in(app):
    c, h = signed_in(app)
    assert c.put("/api/prefs", json={"prefs": {"ah_lang": "en"}}).status_code == 403
    assert c.put("/api/prefs", json={"prefs": {"ah_lang": "en"}}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    anon = app.test_client()
    assert anon.put("/api/prefs", json={"prefs": {}}, headers=h).status_code == 401


@pytest.mark.parametrize("prefs, status", [
    ({"ah_token": "x"}, 400),                    # not a synced key
    ({"ah_lang": 5}, 400),                       # not a string
    ("not an object", 400),
    ({"ah_blacklist": "x" * 500_000}, 413),      # too large
])
def test_prefs_are_validated(app, prefs, status):
    c, h = signed_in(app)
    resp = c.put("/api/prefs", json={"prefs": prefs}, headers=h)
    assert resp.status_code == status
    assert c.get("/api/prefs").get_json()["prefs"] == {}


def test_prefs_are_per_user(app):
    services(app).users.create("bob", "bob@example.com")
    a, ha = signed_in(app)
    a.put("/api/prefs", json={"prefs": {"ah_lang": "nl"}}, headers=ha)
    b = app.test_client()
    sign_in(b, "bob")
    assert b.get("/api/prefs").get_json()["prefs"] == {}


# ---- admin user management ---------------------------------------------------------------------
def admin_client(app):
    c = app.test_client()
    token = csrf_of(c.get("/admin/login").get_data(as_text=True))
    c.post("/admin/login", data={"csrf": token, "password": ADMIN_PW})
    return c, csrf_of(c.get("/admin/").get_data(as_text=True))


def test_admin_user_pages_need_admin_and_csrf(app):
    c = app.test_client()
    sign_in(c)                                     # a user is not an admin
    resp = c.post("/admin/users/disable", data={"username": "alice", "csrf": csrf_of(c.get("/").get_data(as_text=True))})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login")
    assert not services(app).users.get("alice")["disabled"]
    ac, _ = admin_client(app)
    assert ac.post("/admin/users/disable", data={"username": "alice"}).status_code == 400   # no CSRF
    assert not services(app).users.get("alice")["disabled"]


def test_admin_manages_users(app):
    c, token = admin_client(app)
    users = services(app).users
    users.create("bob")
    # Bob's first sign-in with that username links his account.
    bob = app.test_client()
    login_sign_in(bob, "sub-bob", "bob")
    assert config_of(bob)["user"] == "bob" and users.get("bob")["login_sub"] == "sub-bob"
    page = c.post("/admin/users/unlink", data={"csrf": token, "username": "bob"},
                  follow_redirects=True).get_data(as_text=True)
    assert "bob is unlinked" in page and users.get("bob")["login_sub"] is None and config_of(bob)["user"] is None
    login_sign_in(bob, "sub-bob", "bob")

    c.post("/admin/users/disable", data={"csrf": token, "username": "bob"})
    assert users.get("bob")["disabled"] and config_of(bob)["user"] is None
    assert "error=accountDisabled" in login_sign_in(bob, "sub-bob", "bob").headers["Location"]
    c.post("/admin/users/enable", data={"csrf": token, "username": "bob"})
    assert not users.get("bob")["disabled"]

    page = c.post("/admin/users/delete", data={"csrf": token, "username": "bob"},
                  follow_redirects=True).get_data(as_text=True)
    assert "Tick the box" in page and users.get("bob")
    c.post("/admin/users/delete", data={"csrf": token, "username": "bob", "confirm": "yes"})
    assert users.get("bob") is None
    assert c.post("/admin/users", data={"csrf": token, "username": "x"}).status_code in (404, 405)   # no adding by hand


def test_user_names_are_escaped_in_admin_page(app):
    # Usernames are restricted, but the page must escape whatever it shows.
    c, _ = admin_client(app)
    page = c.get("/admin/").get_data(as_text=True)
    assert "<b>alice</b>" in page
