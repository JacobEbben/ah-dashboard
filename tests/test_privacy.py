"""The privacy page, the data download and deleting your own account."""

import json
import re

import pytest
from conftest import enable_login, sign_in
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services


def make_app(tmp_path, fake_client, admin_password=True, **settings):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = generate_password_hash("admin-password-12345", method="pbkdf2:sha256:1000") if admin_password else None
    for key, value in settings.items():
        setattr(Cfg, key, value)
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


def delete(client, typed="alice@example.com", confirm="yes"):
    data = {"csrf": csrf_of(client.get("/privacy")), "confirm_text": typed}
    if confirm:
        data["confirm"] = confirm
    return client.post("/privacy/delete", data=data)


def test_footer_links_to_the_privacy_page(app):
    c = app.test_client()
    for path in ("/", "/login?error=loginFailed", "/privacy"):
        assert 'href="/privacy"' in c.get(path).get_data(as_text=True), path


def test_privacy_page_without_account(app):
    c = app.test_client()
    resp = c.get("/privacy")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Privacyverklaring" in page and "Privacy statement" in page      # both languages
    assert "/privacy/export" not in page and "privacy/delete" not in page   # nothing of yours to show
    assert "Set-Cookie" not in resp.headers                                 # no cookie just for reading it
    assert "inlogdienst" in page and "login service" in page and "Google-account" not in page   # the login service is described
    assert c.get("/privacy/export").status_code == 302                      # to the sign-in page


def test_privacy_contact_is_shown_when_set(tmp_path, fake_client):
    app = make_app(tmp_path, fake_client, PRIVACY_CONTACT="privacy@example.org")
    assert "privacy@example.org" in app.test_client().get("/privacy").get_data(as_text=True)


def test_signed_in_user_sees_their_data(app):
    c = app.test_client()
    sign_in(c)
    services(app).users.set_prefs("alice", {"ah_lang": "en", "ah_liked": json.dumps([{"key": "1", "title": "Melk"}])})
    page = c.get("/privacy").get_data(as_text=True)
    assert "alice" in page and "ah_liked" in page and "/privacy/export" in page and "/privacy/delete" in page
    assert 'data-i18n="loginLinked"' in page                                # linked to the login service


def test_export_holds_the_users_data_and_no_secrets(app):
    c = app.test_client()
    sign_in(c)
    services(app).users.set_prefs("alice", {"ah_lang": "en", "ah_liked": json.dumps([{"key": "1", "title": "Melk"}])})
    resp = c.get("/privacy/export")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="ah-dashboard-alice-')
    assert resp.headers["Cache-Control"] == "no-store"
    data = json.loads(resp.get_data(as_text=True))
    assert data["account"]["username"] == "alice" and data["account"]["last_sign_in"]
    assert data["account"]["login_service_user_id"] == "sub-alice" and "google_account_id" not in data["account"]
    assert data["synced_settings"]["values"] == {"ah_lang": "en", "ah_liked": [{"key": "1", "title": "Melk"}]}
    assert len(data["active_sign_ins"]) == 1
    text = resp.get_data(as_text=True)
    rec = services(app).users.get("alice")
    assert rec["session_version"] not in text and "password" not in json.dumps(data["account"])
    with c.session_transaction() as sess:
        assert sess["sid"] not in text                                      # sign-in ids are keys


def test_delete_account_removes_everything(app):
    users = services(app).users
    c = app.test_client()
    sign_in(c)
    other = app.test_client()
    sign_in(other)                                                          # alice on a second device
    users.set_prefs("alice", {"ah_lang": "en"})
    resp = delete(c)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/privacy?deleted=1")
    assert users.get("alice") is None
    db = services(app).db
    assert db.scalar("SELECT COUNT(*) FROM user_prefs WHERE username = 'alice'") == 0
    assert db.scalar("SELECT COUNT(*) FROM sessions WHERE username = 'alice'") == 0
    assert c.get("/api/prefs").status_code == 401                           # signed out here ...
    assert other.get("/api/prefs").status_code == 401                       # ... and on the other device
    assert users.get("bob") is not None                                     # nobody else touched
    assert c.get("/privacy?deleted=1").status_code == 200


@pytest.mark.parametrize("typed, confirm, status", [
    ("bob@example.com", "yes", 400),          # not your address
    ("", "yes", 400),
    ("alice@example.com", None, 400),         # box not ticked
])
def test_delete_needs_typed_address_and_confirmation(app, typed, confirm, status):
    c = app.test_client()
    sign_in(c)
    assert delete(c, typed, confirm).status_code == status
    assert services(app).users.get("alice") is not None
    assert c.get("/api/prefs").status_code == 200                           # still signed in


def test_delete_needs_csrf_and_a_sign_in(app):
    c = app.test_client()
    sign_in(c)
    assert c.post("/privacy/delete", data={"confirm_text": "alice@example.com", "confirm": "yes"}).status_code in (400, 403)
    assert services(app).users.get("alice") is not None
    anon = app.test_client()
    token = csrf_of(anon.get("/admin/login"))
    resp = anon.post("/privacy/delete", data={"csrf": token, "confirm_text": "alice@example.com", "confirm": "yes"})
    assert resp.status_code == 302 and services(app).users.get("alice") is not None


def test_account_without_e_mail_confirms_deleting_with_its_username(app):
    users = services(app).users
    users.create("carol")                                                   # no e-mail address
    c = app.test_client()
    sign_in(c, "carol")
    page = c.get("/privacy").get_data(as_text=True)
    assert 'data-n="carol"' in page                                         # type the username to delete


def test_last_admin_cannot_delete_themselves_without_admin_password(tmp_path, fake_client):
    app = make_app(tmp_path, fake_client, admin_password=False)
    services(app).users.set_admin("alice", True)
    c = app.test_client()
    sign_in(c)
    assert delete(c).status_code == 409
    assert services(app).users.get("alice") is not None
    services(app).users.set_admin("bob", True)                              # a second admin: now it may
    assert delete(c).status_code == 302
    assert services(app).users.get("alice") is None
