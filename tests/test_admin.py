"""Admin panel: access control, CSRF, lockout, setting tokens safely."""

import re
import time

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.ah.auth import AHAuthError, TokenManager, TokenStore
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.db import Database
from ah_dashboard.sealing import derive_fernet

PASSWORD = "correct horse battery staple"
FAKE_ACCESS = "12345678_not-a-real-access-token"   # AH access-token shape
FAKE_REFRESH = "abcdef12-3456-4789-8abc-def123456789"            # AH refresh-token shape (UUID)


def make_app(tmp_path, **extra):
    class Cfg(TestingConfig):
        ADMIN_PASSWORD_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
        AH_ACCESS_TOKEN = "env-access"
        AH_REFRESH_TOKEN = "env-refresh"
        DATABASE_FILE = str(tmp_path / "app.db")
    for k, v in extra.items():
        setattr(Cfg, k, v)
    return create_app(Cfg)


@pytest.fixture
def admin_app(tmp_path):
    return make_app(tmp_path)


def csrf_from(resp):
    m = re.search(r'name="csrf" value="([^"]+)"', resp.get_data(as_text=True))
    assert m, "no CSRF token in page"
    return m.group(1)


def login(client, password=PASSWORD):
    token = csrf_from(client.get("/admin/login"))
    return client.post("/admin/login", data={"csrf": token, "password": password})


# ---- availability / access control --------------------------------------------
def test_admin_is_404_when_no_password_configured(tmp_path):
    c = make_app(tmp_path, ADMIN_PASSWORD_HASH=None).test_client()
    for path in ("/admin/", "/admin/login"):
        assert c.get(path).status_code == 404


def test_admin_pages_require_login(admin_app):
    c = admin_app.test_client()
    resp = c.get("/admin/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login")
    resp = c.post("/admin/tokens", data={"refresh_token": "x"})
    assert resp.status_code == 302  # not logged in: no state change
    assert services(admin_app).tokens.status()["source"] == "environment"


def test_login_needs_csrf_token(admin_app):
    c = admin_app.test_client()
    c.get("/admin/login")
    assert c.post("/admin/login", data={"password": PASSWORD}).status_code == 400


def test_wrong_password_and_lockout(admin_app):
    c = admin_app.test_client()
    for _ in range(admin_app.config["ADMIN_MAX_FAILURES"]):
        assert login(c, "wrong").status_code == 401
    resp = login(c, PASSWORD)            # locked out: even the right password is refused
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.get_data(as_text=True)


def test_successful_login_sets_hardened_cookie(admin_app):
    c = admin_app.test_client()
    resp = login(c)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/")
    cookies = resp.headers.getlist("Set-Cookie")
    session_cookie = [h for h in cookies if "Max-Age=0" not in h]
    assert len(session_cookie) == 1
    cookie = session_cookie[0]
    # Lax: links from other sites keep users signed in; POSTs need a CSRF token.
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    page = c.get("/admin/")
    assert page.status_code == 200 and "AH login status" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"


def test_session_expires(admin_app, monkeypatch):
    c = admin_app.test_client()
    login(c)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 61 * 60)
    assert c.get("/admin/").status_code == 302


def test_logout(admin_app):
    c = admin_app.test_client()
    login(c)
    token = csrf_from(c.get("/admin/"))
    c.post("/admin/logout", data={"csrf": token})
    assert c.get("/admin/").status_code == 302


def test_panel_shows_bonus_offer_count(admin_app):
    """The Bonus line shows the number of offers (a Jinja ``.items`` lookup
    once printed the dict's items method instead)."""
    from ah_dashboard.domain.models import BonusWeek, Offer, Period
    week = BonusWeek(Period("2026-11-16", "2026-11-22"), ["Zuivel"],
                     [Offer("bonus", i, f"P{i}") for i in range(42)])
    services(admin_app).bonus_cache.get(lambda: week)
    c = admin_app.test_client()
    login(c)
    page = c.get("/admin/").get_data(as_text=True)
    assert "42 offers · 2026-11-16 – 2026-11-22" in page and "built-in method" not in page


# ---- setting tokens ------------------------------------------------------------------
def test_set_tokens_requires_csrf(admin_app):
    c = admin_app.test_client()
    login(c)
    resp = c.post("/admin/tokens", data={"access_token": FAKE_ACCESS, "refresh_token": FAKE_REFRESH})
    assert resp.status_code == 400
    assert services(admin_app).tokens.status()["source"] == "environment"


def test_set_tokens_updates_login_and_never_shows_them(admin_app, tmp_path):
    c = admin_app.test_client()
    login(c)
    token = csrf_from(c.get("/admin/"))
    resp = c.post("/admin/tokens", data={"csrf": token, "access_token": FAKE_ACCESS,
                                         "refresh_token": FAKE_REFRESH}, follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert "AH login saved" in page and "admin panel" in page
    assert FAKE_ACCESS not in page and FAKE_REFRESH not in page
    tokens = services(admin_app).tokens
    assert tokens.access_token() == FAKE_ACCESS
    raw = services(admin_app).db.get_secret("ah_tokens")
    assert raw and FAKE_REFRESH.encode() not in raw  # stored encrypted


def test_invalid_tokens_are_rejected(admin_app):
    c = admin_app.test_client()
    login(c)
    token = csrf_from(c.get("/admin/"))
    resp = c.post("/admin/tokens", data={"csrf": token, "refresh_token": "has space"},
                  follow_redirects=True)
    assert "without spaces" in resp.get_data(as_text=True)
    assert services(admin_app).tokens.status()["source"] == "environment"


def test_renew_now_button(admin_app, monkeypatch):
    c = admin_app.test_client()
    login(c)
    token = csrf_from(c.get("/admin/"))
    calls = []
    monkeypatch.setattr(services(admin_app).tokens, "renew_now", lambda: calls.append(1))
    resp = c.post("/admin/renew", data={"csrf": token}, follow_redirects=True)
    assert calls == [1] and "Access token renewed" in resp.get_data(as_text=True)

    def fail():
        raise AHAuthError("token refresh failed: HTTP 400: invalid_grant")
    monkeypatch.setattr(services(admin_app).tokens, "renew_now", fail)
    page = c.post("/admin/renew", data={"csrf": csrf_from(c.get("/admin/")), }, follow_redirects=True)
    text = page.get_data(as_text=True)
    assert "Renewal failed (reference" in text and "invalid_grant" not in text


# ---- persistence rules ------------------------------------------------------------------
def _manager(tmp_path, env_refresh):
    store = TokenStore(Database(str(tmp_path / "t.db")), derive_fernet("y" * 32, b"t"))
    return TokenManager(store=store, env_access=None, env_refresh=env_refresh,
                        refresh_url="https://example.invalid/refresh")


def test_admin_tokens_survive_restart_with_same_env(tmp_path):
    _manager(tmp_path, "env-refresh").set_tokens("admin-access", "admin-refresh")
    restarted = _manager(tmp_path, "env-refresh")
    assert restarted.status()["source"] == "admin"
    assert restarted.access_token() == "admin-access"


def test_changed_env_token_overrides_admin_tokens(tmp_path):
    _manager(tmp_path, "env-refresh").set_tokens("admin-access", "admin-refresh")
    restarted = _manager(tmp_path, "new-env-refresh")
    assert restarted.status()["source"] == "environment"


def test_status_never_contains_tokens(tmp_path):
    m = _manager(tmp_path, "env-refresh")
    m.set_tokens("admin-access", "admin-refresh")
    assert "admin-access" not in str(m.status()) and "admin-refresh" not in str(m.status())


# ---- password hash helper -----------------------------------------------------------------
def test_password_hash_cli(monkeypatch, capsys):
    from ah_dashboard.admin import __main__ as cli
    answers = iter(["a-long-password-123", "a-long-password-123"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main() == 0
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("ADMIN_PASSWORD_HASH=")][0]
    value = line.split("=", 1)[1]
    assert value.startswith("'") and value.endswith("'")   # "$" in the hash is safe for Docker Compose
    assert check_password_hash(value[1:-1], "a-long-password-123")


def test_password_hash_cli_rejects_short_passwords(monkeypatch):
    from ah_dashboard.admin import __main__ as cli
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "short")
    assert cli.main() == 1
