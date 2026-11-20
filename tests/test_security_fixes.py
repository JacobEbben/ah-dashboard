"""Regression tests for the security review's High and Medium findings:
secrets in .env.example (H1), invented store ids flooding AH (M1), IPv6
rotation around limits (M2) and flushing an account's login lock (M3)."""

import os
import re
import threading
import time

import pytest
from conftest import FakeAHClient, as_admin, configure
from werkzeug.security import generate_password_hash

from ah_dashboard import create_app
from ah_dashboard.auth.lockout import LoginLimiter
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.ratelimit import limit_key
from ah_dashboard.services.stores import KnownStores

PW = "correct horse battery"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- H1: no secrets in .env.example ------------------------------------------------------
def test_env_example_holds_no_secrets():
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        assert not re.fullmatch(r"[0-9a-fA-F-]{36}", value), f"{key} looks like a real token"
        assert not re.fullmatch(r"\d+_[0-9A-Za-z-]{8,}", value), f"{key} looks like a real access token"
        assert not value.startswith(("scrypt:", "pbkdf2:")), f"{key} holds a password hash"
        if key.strip() in ("AH_ACCESS_TOKEN", "AH_REFRESH_TOKEN", "ADMIN_PASSWORD_HASH", "TOKEN_SEAL_SECRET"):
            assert value == "", f"{key} must be empty in the example"


# ---- M2: limits count per IPv4 address / IPv6 /64 ---------------------------------------------
@pytest.mark.parametrize("addr,key", [
    ("203.0.113.5", "203.0.113.5"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("::ffff:198.51.100.7", "198.51.100.7"),
    ("unknown", "unknown"),
])
def test_limit_key(addr, key):
    assert limit_key(addr) == key


def from_addr(client, addr):
    client.environ_base["REMOTE_ADDR"] = addr
    return client


def test_ipv6_rotation_shares_one_rate_limit():
    app = create_app(TestingConfig, {"RATE_LIMIT_BARGAINS": 3})
    services(app).ah = FakeAHClient()
    body = {"stores": [{"id": 1002}]}
    codes = [from_addr(app.test_client(), f"2001:db8:1:2::{i:x}").post("/api/bargains", json=body).status_code
             for i in range(1, 6)]
    assert codes == [200, 200, 200, 429, 429]
    other = from_addr(app.test_client(), "2001:db8:1:3::1")            # another /64: own limit
    assert other.post("/api/bargains", json=body).status_code == 200


@pytest.fixture
def users_app(tmp_path):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = generate_password_hash("admin-password-12345", method="pbkdf2:sha256:1000")
        ADMIN_MAX_FAILURES = 3
        ACCOUNT_MAX_FAILURES = 100
    app = create_app(Cfg)
    services(app).ah = FakeAHClient()
    return app


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def admin_login(client, password):
    token = _csrf(client.get("/admin/login").get_data(as_text=True))
    return client.post("/admin/login", data={"csrf": token, "password": password})


ADMIN_PW = "admin-password-12345"


def test_ipv6_rotation_does_not_escape_the_login_lockout(users_app):
    for i in range(3):
        c = from_addr(users_app.test_client(), f"2001:db8:9:9::{i + 1:x}")
        assert admin_login(c, "wrong password!!").status_code == 401
    same_net = from_addr(users_app.test_client(), "2001:db8:9:9:dead:beef::1")
    assert admin_login(same_net, ADMIN_PW).status_code == 429               # the whole /64 is locked
    assert admin_login(from_addr(users_app.test_client(), "2001:db8:9:a::1"), ADMIN_PW).status_code == 302


def test_server_wide_login_cap_stops_hashing(users_app, monkeypatch):
    from conftest import enable_google

    import ah_dashboard.admin.routes as admin_routes
    configure(users_app, LOGIN_ATTEMPTS_PER_MINUTE=3, ACCOUNT_MAX_FAILURES=100, ADMIN_MAX_FAILURES=100)
    checks = []
    original = admin_routes.check_password
    monkeypatch.setattr(admin_routes, "check_password", lambda *a: checks.append(1) or original(*a))
    codes = [admin_login(from_addr(users_app.test_client(), f"198.51.100.{i}"), "wrong password!!").status_code
             for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert len(checks) == 3                               # no password hashed once the cap is reached
    enable_google(users_app)                              # Google sign-ins do NOT spend that budget
    resp = from_addr(users_app.test_client(), "198.51.100.99").get("/auth/google")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("https://accounts.google.com/")


def test_at_most_two_password_checks_at_once():
    from ah_dashboard.auth.lockout import password_check_slot
    running, peak, lock = [0], [0], threading.Lock()

    def check():
        with password_check_slot():
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1
    threads = [threading.Thread(target=check) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= 2


# ---- M3: a locked account can't be flushed out of the limiter -----------------------------------
def test_locked_key_survives_a_flood_of_junk_keys():
    clock = [1000.0]
    limiter = LoginLimiter(max_keys=50, clock=lambda: clock[0])
    for _ in range(20):
        limiter.fail("account:alice", 20, 900)
    assert limiter.locked("account:alice", 20, 900)
    for i in range(5000):                                    # the reviewer's attack, scaled down
        limiter.fail(f"account:junk{i}", 20, 900)
    assert limiter.locked("account:alice", 20, 900)          # still locked
    assert limiter.size() <= 51                              # memory stays bounded
    clock[0] += 901                                          # after the window the lock ends normally
    assert not limiter.locked("account:alice", 20, 900)


def test_checking_a_key_does_not_create_it():
    limiter = LoginLimiter(max_keys=10)
    for i in range(100):
        assert not limiter.locked(f"account:x{i}", 5, 900)
    assert limiter.size() == 0


def test_expired_keys_are_dropped_first():
    clock = [0.0]
    limiter = LoginLimiter(max_keys=3, clock=lambda: clock[0])
    limiter.fail("old", 5, 10)
    clock[0] = 100
    for key in ("a", "b", "c"):
        limiter.fail(key, 5, 10)
    assert limiter.size() == 3 and not limiter.locked("old", 1, 10)


def test_account_lock_survives_junk_logins_over_http(users_app):
    configure(users_app, ACCOUNT_MAX_FAILURES=3, ADMIN_MAX_FAILURES=1000)
    services(users_app).admin_limiter._max_keys = 30
    for i in range(3):
        admin_login(from_addr(users_app.test_client(), f"192.0.2.{i}"), "wrong password!!")
    for i in range(60):                                       # many addresses to flush the limiter
        admin_login(from_addr(users_app.test_client(), f"192.0.2.{100 + i}"), "wrong password!!")
    fresh = from_addr(users_app.test_client(), "203.0.113.200")
    assert admin_login(fresh, ADMIN_PW).status_code == 429     # the admin password is still locked


# ---- M1: invented store ids can't flood AH ---------------------------------------------------------
def make_app(tmp_path=None, **cfg):
    overrides = {"UNKNOWN_STORES_PER_CLIENT": 3, "UNKNOWN_STORES_TOTAL": 5, "STORE_LOADS_PER_CLIENT": 40,
                 "STORE_LOADS_TOTAL": 120, **cfg}
    if tmp_path is not None:
        overrides["DATABASE_FILE"] = str(tmp_path / "app.db")
    app = create_app(TestingConfig, overrides)
    services(app).ah = FakeAHClient()
    return app


def ah_calls(app):
    return [c for c in services(app).ah.calls if c[0] == "BargainItems"]


def test_invented_store_ids_hit_the_unknown_budget():
    app = make_app()
    c = from_addr(app.test_client(), "203.0.113.9")
    for n in range(30):                                       # the reviewer's attack: 30 x 25 random ids
        stores = [{"id": 10_000_000 + n * 25 + i} for i in range(25)]
        assert c.post("/api/bargains", json={"stores": stores}).status_code == 200
    assert len(ah_calls(app)) == 3                            # not 750
    data = c.post("/api/bargains", json={"stores": [{"id": 99_999_999}]}).get_json()
    assert data["storeErrors"][0]["code"] == "store_unknown" and "ref" not in data["storeErrors"][0]
    # Many clients together: the server-wide unknown budget holds.
    for i in range(20):
        from_addr(app.test_client(), f"198.51.100.{i}").post("/api/bargains", json={"stores": [{"id": 20_000_000 + i}]})
    assert len(ah_calls(app)) == 5


def test_known_stores_are_not_limited_by_the_unknown_budget():
    app = make_app()
    services(app).known_stores.add([1002, 1003, 1001])          # e.g. from a store search
    c = app.test_client()
    for sid in (90_000_001, 90_000_002, 90_000_003):          # use up the unknown budget
        c.post("/api/bargains", json={"stores": [{"id": sid}]})
    data = c.post("/api/bargains", json={"stores": [{"id": 1002}, {"id": 1003}, {"id": 1001}]}).get_json()
    assert data["storeErrors"] == []                         # known stores still load


def test_store_search_results_become_known(tmp_path):
    app = make_app(tmp_path, UNKNOWN_STORES_PER_CLIENT=0, UNKNOWN_STORES_TOTAL=0)
    services(app).ah.responses = {"stores": {"storesSearch": {"result": [{"id": 4242, "name": "AH"}]}}}
    c = app.test_client()
    # 0 = budget switched off (unlimited); use a real limit to show the effect:
    configure(app, UNKNOWN_STORES_PER_CLIENT=1, UNKNOWN_STORES_TOTAL=1)
    c.post("/api/bargains", json={"stores": [{"id": 777}]})   # uses the one unknown slot
    assert c.post("/api/bargains", json={"stores": [{"id": 4242}]}).get_json()["storeErrors"][0]["code"] == "store_unknown"
    c.get("/api/stores?postcode=1234AB")
    assert c.post("/api/bargains", json={"stores": [{"id": 4242}]}).get_json()["storeErrors"] == []
    # Known stores survive a restart (database).
    again = make_app(tmp_path)
    assert 4242 in services(again).known_stores


def test_store_with_bargains_becomes_known():
    app = make_app()
    services(app).ah.responses = {"555": [{"product": {"id": 1, "title": "Kip"}}]}
    app.test_client().post("/api/bargains", json={"stores": [{"id": 555}]})
    assert 555 in services(app).known_stores
    app.test_client().post("/api/bargains", json={"stores": [{"id": 556}]})   # empty: stays unknown
    assert 556 not in services(app).known_stores


def test_total_load_budget_reports_busy():
    app = make_app(STORE_LOADS_TOTAL=2)
    stores = [{"id": 1002}, {"id": 1003}, {"id": 1001}]
    data = app.test_client().post("/api/bargains", json={"stores": stores}).get_json()
    assert sorted(e["code"] for e in data["storeErrors"]) == ["store_busy"]
    assert len(ah_calls(app)) == 2


def test_refusals_are_not_cached():
    app = make_app(UNKNOWN_STORES_PER_CLIENT=1)
    c = app.test_client()
    c.post("/api/bargains", json={"stores": [{"id": 777}]})
    assert c.post("/api/bargains", json={"stores": [{"id": 778}]}).get_json()["storeErrors"][0]["code"] == "store_unknown"
    from ah_dashboard.ratelimit import RateLimiter
    services(app).rate_limiter = RateLimiter()            # budget available again (e.g. an hour later)
    assert c.post("/api/bargains", json={"stores": [{"id": 778}]}).get_json()["storeErrors"] == []


def test_cache_hits_cost_no_budget():
    app = make_app(STORE_LOADS_PER_CLIENT=1)
    c = app.test_client()
    for _ in range(5):
        assert c.post("/api/bargains", json={"stores": [{"id": 1002}]}).get_json()["storeErrors"] == []
    assert len(ah_calls(app)) == 1


def test_admin_force_refresh_is_not_budgeted():
    app = make_app(UNKNOWN_STORES_PER_CLIENT=0, STORE_LOADS_TOTAL=1)
    configure(app, UNKNOWN_STORES_PER_CLIENT=1, UNKNOWN_STORES_TOTAL=1)
    c = app.test_client()
    headers = as_admin(c)
    stores = [{"id": 1}, {"id": 2}, {"id": 3}]
    data = c.post("/api/bargains/force", json={"stores": stores}, headers=headers).get_json()
    assert data["storeErrors"] == [] and len(ah_calls(app)) == 3


def test_known_stores_set_is_capped(monkeypatch):
    monkeypatch.setattr(KnownStores, "MAX", 5)
    known = KnownStores(seed=[1, 2])
    known.add(range(3, 100))
    assert len(known) == 5 and 3 in known and 99 not in known
    assert "3" in known                                     # string ids work for lookups
    assert True not in known and -1 not in known
    # Non-ASCII digits: "²".isdigit() is True but int("²") raises; never a crash.
    assert "²" not in known and "١٢٣" not in known
    known.add(["²", "١٢٣"])
    assert len(known) == 5


# ---- non-ASCII tokens are refused, never a 500 (hmac.compare_digest raised on non-ASCII str) ----
def _admin_app():
    from conftest import enable_google

    class Cfg(TestingConfig):
        ADMIN_PASSWORD_HASH = generate_password_hash("pw-123456789", method="pbkdf2:sha256:1000")
    app = create_app(Cfg)
    app.config["PROPAGATE_EXCEPTIONS"] = False     # an exception would show as a 500, as in production
    enable_google(app)
    return app


def test_same_secret():
    from ah_dashboard.auth.compare import same_secret
    assert same_secret("abc", "abc") and same_secret("é", "é")
    assert not same_secret("é", "abc") and not same_secret("abc", "abd")
    assert not same_secret("", "") and not same_secret(None, "abc") and not same_secret("abc", None)


@pytest.mark.parametrize("token", ["é", "токен", "\u0000", "a" * 5000])
def test_odd_csrf_tokens_are_refused_not_crashing(token):
    app = _admin_app()
    c = app.test_client()
    c.get("/admin/login")
    assert c.post("/admin/login", data={"csrf": token, "password": "x"}).status_code == 400
    as_admin(c)
    assert c.post("/api/bargains/force", headers={"X-CSRF-Token": token}, json={}).status_code == 403


def test_odd_google_state_is_refused_not_crashing():
    app = _admin_app()
    c = app.test_client()
    c.get("/auth/google?purpose=login")
    resp = c.get("/auth/google/callback?state=%C3%A9&code=x")
    assert resp.status_code == 302 and "googleFailed" in resp.headers["Location"]


def test_odd_nonce_is_refused():
    from ah_dashboard.auth.google import GoogleError, GoogleSignIn
    claims = {"iss": "https://accounts.google.com", "aud": "cid", "exp": time.time() + 60,
              "sub": "1", "email": "a@example.com", "email_verified": True, "nonce": "é"}
    with pytest.raises(GoogleError, match="nonce"):
        GoogleSignIn("cid", "secret").check_claims(claims, "expected-nonce")


def test_no_direct_compare_digest():
    """Secrets are compared with auth.compare.same_secret only (it handles any input)."""
    root = os.path.join(os.path.dirname(__file__), "..", "ah_dashboard")
    offenders = []
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if name.endswith(".py") and not path.endswith(os.path.join("auth", "compare.py")):
                with open(path, encoding="utf-8") as fh:
                    if "compare_digest(" in fh.read():
                        offenders.append(os.path.relpath(path, root))
    assert offenders == []
