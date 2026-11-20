"""Sign in with Google: the flow (state, PKCE, nonce) and the ID token checks.
Google itself is faked (conftest.FakeGoogle): only its token endpoint's answer."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import configure, enable_google, google_claims, google_sign_in, make_id_token

from ah_dashboard import create_app
from ah_dashboard.auth.google import GoogleError, GoogleSignIn, Pending, decode_payload
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.web.google import PENDING_KEY


@pytest.fixture
def app(fake_client):
    app = create_app(TestingConfig)
    services(app).ah = fake_client
    enable_google(app)
    return app


def signed_in(client):
    return client.get("/api/prefs").status_code == 200


def start(client, purpose="login"):
    resp = client.get(f"/auth/google?purpose={purpose}")
    assert resp.status_code == 302
    return resp, {k: v[0] for k, v in parse_qs(urlparse(resp.headers["Location"]).query).items()}


# ---- the redirect to Google -------------------------------------------------------------------
def test_start_sends_the_browser_to_google_with_pkce(app):
    c = app.test_client()
    resp, q = start(c)
    assert resp.headers["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == "test-client" and q["response_type"] == "code" and q["scope"] == "openid email"
    assert q["redirect_uri"] == "http://localhost/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and q["prompt"] == "select_account"
    with c.session_transaction() as sess:
        pending = sess[PENDING_KEY]
    assert pending["state"] == q["state"] and pending["nonce"] == q["nonce"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == expected                       # the verifier stays on the server side
    assert pending["verifier"] not in resp.headers["Location"]


def test_public_base_url_is_the_return_address(app):
    configure(app, PUBLIC_BASE_URL="https://ah.example.nl")
    c = app.test_client()
    _, q = start(c)
    assert q["redirect_uri"] == "https://ah.example.nl/auth/google/callback"
    google_sign_in(c, "g-1", "a@example.com")
    assert services(app).google.last_form["redirect_uri"] == "https://ah.example.nl/auth/google/callback"


def test_the_code_exchange_sends_secret_and_verifier(app):
    c = app.test_client()
    google_sign_in(c, "g-1", "a@example.com")
    form = services(app).google.last_form
    assert form["code"] == "test-code" and form["grant_type"] == "authorization_code"
    assert form["client_secret"] == "test-secret" and len(form["code_verifier"]) >= 43
    assert signed_in(c)


# ---- the way back: only the sign-in this browser started ---------------------------------------
def test_callback_without_a_started_sign_in_is_refused(app):
    c = app.test_client()
    resp = c.get("/auth/google/callback?code=x&state=y")
    assert "error=googleFailed" in resp.headers["Location"] and not signed_in(c)


def test_wrong_state_is_refused(app):
    c = app.test_client()
    _, q = start(c)
    services(app).google.claims = google_claims(q["nonce"], "g-1", "a@example.com")
    resp = c.get("/auth/google/callback?code=x&state=forged")
    assert "error=googleFailed" in resp.headers["Location"] and not signed_in(c)


def test_a_sign_in_can_be_used_once(app):
    c = app.test_client()
    _, q = start(c)
    services(app).google.claims = google_claims(q["nonce"], "g-1", "a@example.com")
    assert c.get(f"/auth/google/callback?code=x&state={q['state']}").headers["Location"].endswith("/")
    c.post("/logout", data={"csrf": c.get("/").get_data(as_text=True).split('name="csrf" value="')[1].split('"')[0]})
    again = c.get(f"/auth/google/callback?code=x&state={q['state']}")      # replayed
    assert "error=googleFailed" in again.headers["Location"] and not signed_in(c)


def test_an_old_sign_in_expires(app, monkeypatch):
    c = app.test_client()
    _, q = start(c)
    services(app).google.claims = google_claims(q["nonce"], "g-1", "a@example.com")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    resp = c.get(f"/auth/google/callback?code=x&state={q['state']}")
    assert "error=googleFailed" in resp.headers["Location"] and not signed_in(c)


def test_cancelled_at_google_just_goes_back(app):
    c = app.test_client()
    _, q = start(c)
    resp = c.get(f"/auth/google/callback?error=access_denied&state={q['state']}")
    assert resp.headers["Location"].endswith("/login") and not signed_in(c)


@pytest.mark.parametrize("bad", [
    {"aud": "someone-else"},
    {"iss": "https://evil.example"},
    {"exp": int(time.time()) - 3600},
    {"nonce": "another-sign-in"},
    {"email_verified": False},
    {"email_verified": "true"},          # must be the JSON boolean
    {"sub": ""},
    {"email": "no-at-sign"},
])
def test_id_token_checks(app, bad):
    c = app.test_client()
    _, q = start(c)
    services(app).google.claims = {**google_claims(q["nonce"], "g-1", "a@example.com"), **bad}
    resp = c.get(f"/auth/google/callback?code=x&state={q['state']}")
    assert "error=googleFailed" in resp.headers["Location"] and not signed_in(c)
    assert services(app).users.count() == 0


def test_token_answer_without_id_token_is_refused(app):
    c = app.test_client()
    _, q = start(c)
    services(app).google.answer = {"access_token": "x"}
    resp = c.get(f"/auth/google/callback?code=x&state={q['state']}")
    assert "error=googleFailed" in resp.headers["Location"] and not signed_in(c)


# ---- purposes ---------------------------------------------------------------------------------
def test_link_needs_a_signed_in_user(app):
    resp = app.test_client().get("/auth/google?purpose=link")
    assert resp.headers["Location"].endswith("/login")


def test_unknown_purpose_is_a_normal_sign_in(app):
    c = app.test_client()
    resp = google_sign_in(c, "g-1", "a@example.com", purpose="evil")
    assert resp.headers["Location"].endswith("/") and signed_in(c)


def test_admin_purpose_returns_to_the_panel(app):
    c = app.test_client()
    resp = google_sign_in(c, "g-1", "a@example.com", purpose="admin")
    assert resp.headers["Location"].endswith("/admin/")


def test_no_new_accounts_beyond_max_users(fake_client):
    app = create_app(TestingConfig, {"MAX_USERS": 1})
    enable_google(app)
    google_sign_in(app.test_client(), "g-1", "a@example.com")
    c = app.test_client()
    resp = google_sign_in(c, "g-2", "b@example.com")
    assert "error=signupFull" in resp.headers["Location"] and not signed_in(c)
    assert signed_in_again(app, "g-1", "a@example.com")          # existing users still sign in


def signed_in_again(app, sub, email):
    c = app.test_client()
    google_sign_in(c, sub, email)
    return signed_in(c)


def test_sign_in_page_errors_are_whitelisted(app):
    page = app.test_client().get("/login?error=googleFailed").get_data(as_text=True)
    assert 'data-i18n="googleFailed"' in page
    page = app.test_client().get("/login?error=__proto__").get_data(as_text=True)
    assert 'data-i18n="__proto__"' not in page


# ---- the ID token itself ------------------------------------------------------------------------
def test_decode_payload_and_audience_lists():
    google = GoogleSignIn("cid", "secret")
    nonce = "n"
    claims = google_claims(nonce, "s", "A@Example.com", aud=["cid", "other"], azp="cid")
    who = google.check_claims(decode_payload(make_id_token(claims)), nonce)
    assert who.sub == "s" and who.email == "a@example.com"
    with pytest.raises(GoogleError):
        google.check_claims({**claims, "azp": "other"}, nonce)      # a list audience needs azp = us
    for junk in ("x", "a.b", "a.!!!.c", "a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c"):
        with pytest.raises(GoogleError):
            decode_payload(junk)


def test_pending_state_compare():
    p = Pending.new()
    assert p.state_matches(p.state) and not p.state_matches("x") and not p.state_matches(None)
    assert Pending.new().state != p.state and len(p.verifier) >= 43


# ---- security review 2026-11-18 ----------------------------------------------------------------
def from_addr(client, addr):
    client.environ_base["REMOTE_ADDR"] = addr
    return client


def admin_password_app():
    from werkzeug.security import generate_password_hash
    app = create_app(TestingConfig, {"LOGIN_ATTEMPTS_PER_MINUTE": 60, "GOOGLE_SIGNIN_PER_MINUTE": 10,
                                     "ADMIN_PASSWORD_HASH": generate_password_hash("admin-password-12345",
                                                                                   method="pbkdf2:sha256:1000")})
    enable_google(app)
    configure(app, GOOGLE_SIGNIN_PER_MINUTE=10)
    return app


def admin_password_login(client):
    import re
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/admin/login").get_data(as_text=True)).group(1)
    return client.post("/admin/login", data={"csrf": token, "password": "admin-password-12345"})


def test_starting_google_sign_ins_cannot_lock_everyone_out():
    """Finding 1: anonymous /auth/google requests used to spend the server-wide
    sign-in budget, locking out every user and the admin password."""
    app = admin_password_app()
    for i in range(100):                                          # many addresses, cheap requests
        from_addr(app.test_client(), f"198.51.100.{i}").get("/auth/google")
    assert admin_password_login(from_addr(app.test_client(), "203.0.113.9")).status_code == 302
    _, q = start(from_addr(app.test_client(), "203.0.113.10"))
    assert "state" in q                                           # another visitor still goes to Google


def test_google_sign_in_starts_are_limited_per_client():
    app = admin_password_app()
    noisy = from_addr(app.test_client(), "2001:db8:7:7::1")
    codes = [noisy.get("/auth/google").headers["Location"] for _ in range(11)]
    assert all(c.startswith("https://accounts.google.com/") for c in codes[:10])
    assert "error=loginLocked" in codes[10]
    same_net = from_addr(app.test_client(), "2001:db8:7:7:abcd::9")        # same IPv6 /64: same limit
    assert "error=loginLocked" in same_net.get("/auth/google").headers["Location"]
    other = from_addr(app.test_client(), "2001:db8:7:8::1")
    assert other.get("/auth/google").headers["Location"].startswith("https://accounts.google.com/")


def test_a_linked_account_cannot_be_relinked_to_another_google_account(app):
    """Finding 2: a stolen session could attach the thief's Google account."""
    from conftest import sign_in
    users = services(app).users
    users.create("alice", "alice@example.com")
    thief = app.test_client()
    sign_in(thief, "alice")                                       # stands in for a stolen cookie
    resp = google_sign_in(thief, "g-thief", "thief@example.com", purpose="link")
    assert "error=googleAlreadyLinked" in resp.headers["Location"]
    assert users.get("alice")["google_sub"] == "sub-alice"
    fresh = app.test_client()
    google_sign_in(fresh, "g-thief", "thief@example.com")
    assert fresh.get("/").get_data(as_text=True).count('"user": "alice"') == 0   # the thief's login is not alice


def test_signed_in_admin_reaches_the_panel_whatever_the_google_login_age(app):
    """The reported problem: an admin signed in with an older Google login was
    asked to confirm and refused. Admin rights now follow from the account."""
    users = services(app).users
    users.create("alice", "alice@example.com")
    users.set_admin("alice", True)
    c = app.test_client()
    resp = google_sign_in(c, "sub-alice", "alice@example.com", auth_time=int(time.time()) - 86400)
    assert resp.headers["Location"].endswith("/")
    assert c.get("/api/session").get_json()["admin"] is True
    assert c.get("/admin/").status_code == 200
    _, q = start(c, "admin")
    assert "max_age" not in q and "claims" not in q                # no forced re-login asked of Google


def test_old_resume_links_are_just_a_sign_in(app):
    resp = google_sign_in(app.test_client(), "g-1", "a@example.com", purpose="resume")
    assert resp.headers["Location"].endswith("/")


def test_settings_uploads_are_limited_per_user(app):
    """Finding 4: a signed-in user can't write settings in a tight loop."""
    from conftest import sign_in
    users = services(app).users
    for name in ("alice", "bob"):
        users.create(name, f"{name}@example.com")
    configure(app, PREFS_WRITES_PER_MINUTE=3)
    def put(client):
        csrf = client.get("/").get_data(as_text=True).split('"csrf": "')[1].split('"')[0]
        return client.put("/api/prefs", json={"prefs": {"ah_lang": "en"}}, headers={"X-CSRF-Token": csrf}).status_code
    a, b = app.test_client(), app.test_client()
    sign_in(a, "alice")
    sign_in(b, "bob")
    assert [put(a) for _ in range(4)] == [200, 200, 200, 429]
    assert put(b) == 200                                          # per user, not per server


def test_admin_panel_shows_users_and_database_size(tmp_path):
    from conftest import as_admin
    app = create_app(TestingConfig, {"DATABASE_FILE": str(tmp_path / "app.db"), "MAX_USERS": 2})
    services(app).users.create("alice")
    services(app).users.create("bob")
    c = app.test_client()
    as_admin(c)
    page = c.get("/admin/").get_data(as_text=True)
    assert "2 of at most 2" in page and "nearly full" in page and "write-ahead log" in page
