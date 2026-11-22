"""Signing in through the central login service (replaces Google sign-in)."""

from __future__ import annotations

import pytest
from conftest import configure, enable_login, login_sign_in, sign_in
from fake_op import CLIENT_ID, ISSUER

from ah_dashboard import create_app
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services


@pytest.fixture
def app(tmp_path, fake_client):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
    app = create_app(Cfg)
    services(app).ah = fake_client
    app._fake_op = enable_login(app)
    return app


def _user(client):
    return client.get("/api/prefs").status_code == 200


@pytest.mark.parametrize("override", [
    {"aud": "another-app"}, {"iss": "https://evil.test"}, {"exp": 1}, {"nonce": "not-this-sign-in"},
    {"preferred_username": None}, {"preferred_username": "Not Valid!"}, {"sub": None}, {"iat": 4_000_000_000},
    {"aud": [CLIENT_ID, "other"], "azp": "other"},
])
def test_bad_id_tokens_are_refused(app, override):
    c = app.test_client()
    resp = login_sign_in(c, "sub-1", "anna", overrides=override)
    assert "error=loginFailed" in resp.headers["Location"], override
    assert not _user(c) and services(app).users.get("anna") is None


def test_token_signed_by_another_key_or_unsigned_is_refused(app):
    from cryptography.hazmat.primitives.asymmetric import rsa
    op = app._fake_op
    real = op.sign
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    op.sign = lambda claims, **kw: real(claims, key=other, **kw)
    assert "loginFailed" in login_sign_in(app.test_client(), "sub-1", "anna").headers["Location"]
    op.sign = lambda claims, **kw: real(claims, alg="none", **kw)
    assert "loginFailed" in login_sign_in(app.test_client(), "sub-1", "anna").headers["Location"]


def test_callback_checks_state_and_issuer(app):
    for tamper in (lambda q: q.update(state="wrong"), lambda q: q.update(iss="https://evil.test")):
        c = app.test_client()
        assert "loginFailed" in login_sign_in(c, "sub-1", "anna", tamper=tamper).headers["Location"]
        assert not _user(c)
    assert "loginFailed" in app.test_client().get("/auth/callback?code=x&state=y").headers["Location"]


def test_admin_rights_need_the_authenticator_code(app):
    services(app).users.sign_in_login("sub-adm", "boss")
    services(app).users.set_admin("boss", True)
    c = app.test_client()
    sign_in(c, "boss", amr=("pwd",))                         # password only at the login service
    assert c.get("/admin/").status_code == 302 and _user(c)  # signed in, but no admin rights
    assert "without the authenticator code" in c.get("/admin/login").get_data(as_text=True)
    sign_in(c, "boss", purpose="admin")                      # with the code
    assert c.get("/admin/").status_code == 200
    configure(app, REQUIRE_ADMIN_2FA=False)
    sign_in(c, "boss", amr=("pwd",))
    assert c.get("/admin/").status_code == 200               # REQUIRE_ADMIN_2FA=false


def test_backchannel_logout_by_session_and_by_user(app):
    op = app._fake_op
    a1, a2 = app.test_client(), app.test_client()
    login_sign_in(a1, "sub-a", "anna", sid="op-1")
    login_sign_in(a2, "sub-a", "anna", sid="op-2")
    junk = app.test_client().post("/auth/backchannel-logout", data={"logout_token": "junk"})
    assert junk.status_code == 400 and _user(a1)
    ok = app.test_client().post("/auth/backchannel-logout", data={"logout_token": op.logout_token(sid="op-1")})
    assert ok.status_code == 200 and not _user(a1) and _user(a2)          # only that sign-in
    token = op.logout_token(sub="sub-a")
    assert app.test_client().post("/auth/backchannel-logout", data={"logout_token": token}).status_code == 200
    assert not _user(a2)                                                    # blocked: every sign-in
    assert app.test_client().post("/auth/backchannel-logout", data={"logout_token": token}).status_code == 400  # replay
    id_token = op.sign(op.id_claims("sub-a", "anna", "n"))
    assert app.test_client().post("/auth/backchannel-logout", data={"logout_token": id_token}).status_code == 400


def test_csp_lets_sign_out_continue_to_the_login_service(tmp_path):
    class Cfg(TestingConfig):
        LOGIN_ISSUER = ISSUER
        LOGIN_CLIENT_ID = CLIENT_ID
        LOGIN_CLIENT_SECRET = "test-client-secret-not-real"
    csp = create_app(Cfg).test_client().get("/").headers["Content-Security-Policy"]
    assert f"form-action 'self' {ISSUER}" in csp


def test_issuer_must_be_https():
    from ah_dashboard.config import validate
    base = {"AH_GRAPHQL_URL": "https://a", "AH_AUTH_REFRESH_URL": "https://a", "AH_REST_URL": "https://a",
            "LOGIN_CLIENT_ID": "x", "LOGIN_CLIENT_SECRET": "y"}
    with pytest.raises(ValueError):
        validate({**base, "LOGIN_ISSUER": "http://login.example.nl"})
    with pytest.raises(ValueError):
        validate({**base, "LOGIN_ISSUER": "https://login.example.nl", "LOGIN_CLIENT_SECRET": ""})   # all three or none
    validate({**base, "LOGIN_ISSUER": "https://login.example.nl"})
