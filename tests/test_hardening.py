"""Public-hosting hardening: proxy setup, rate limits, per-account lockout,
stale/negative caching, the Bonus week cache, AH concurrency cap, bargain
normalization, HSTS and the admin health card."""

import re
import threading
import time
from datetime import date

import pytest
from conftest import FakeAHClient, as_admin
from werkzeug.security import generate_password_hash

import serve
from ah_dashboard import create_app
from ah_dashboard.ah.client import AHError, AHTransportError
from ah_dashboard.ah.mapping import bargain
from ah_dashboard.ah.session import AHSession
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.domain.models import BonusWeek, CategoryError, Offer, Period
from ah_dashboard.ratelimit import RateLimiter
from ah_dashboard.scheduler import Scheduler
from ah_dashboard.services.bonus import WeekCache, prewarm_job
from ah_dashboard.services.cache import TTLCache

PW = "correct horse battery"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def from_addr(client, addr):
    client.environ_base["REMOTE_ADDR"] = addr
    return client


# ---- serve.py: reverse proxy --------------------------------------------------------
def test_waitress_trusts_no_proxy_by_default():
    opts = serve.waitress_options({})
    assert "trusted_proxy" not in opts and opts["ident"] == ""


def test_waitress_trusts_configured_proxy():
    opts = serve.waitress_options({"TRUSTED_PROXY": "*", "TRUSTED_PROXY_COUNT": "1"})
    assert opts["trusted_proxy"] == "*" and opts["trusted_proxy_count"] == 1
    assert "x-forwarded-for" in opts["trusted_proxy_headers"]
    assert opts["clear_untrusted_proxy_headers"] is True


def test_waitress_options_accept_quoted_values():
    """Like Compose, quotes around .env values are removed (an environment
    loaded by a shell keeps them)."""
    opts = serve.waitress_options({"HOST": "'0.0.0.0'", "PORT": '"9000"', "TRUSTED_PROXY": "'*'"})
    assert opts["host"] == "0.0.0.0" and opts["port"] == 9000 and opts["trusted_proxy"] == "*"


# ---- rate limits ----------------------------------------------------------------------
def test_rate_limiter_bucket_refills():
    clock = Clock()
    rl = RateLimiter(clock=clock)
    assert all(rl.allow("b", "1.1.1.1", 3, 60) for _ in range(3))
    assert not rl.allow("b", "1.1.1.1", 3, 60)
    assert rl.allow("b", "2.2.2.2", 3, 60)          # other address: own bucket
    assert rl.allow("other", "1.1.1.1", 3, 60)      # other bucket: own budget
    clock.now += 20                                  # one token back (3 per 60 s)
    assert rl.allow("b", "1.1.1.1", 3, 60)
    assert not rl.allow("b", "1.1.1.1", 3, 60)
    assert rl.blocked == 2
    assert rl.allow("b", "1.1.1.1", 0, 60)          # 0 = off


@pytest.fixture
def limited_app():
    app = create_app(TestingConfig, {"RATE_LIMIT_BARGAINS": 2, "RATE_LIMIT_STORES": 1, "RATE_LIMIT_BONUS": 1})
    services(app).ah = FakeAHClient()
    return app


def test_bargains_are_rate_limited_per_address(limited_app):
    c = from_addr(limited_app.test_client(), "10.0.0.1")
    body = {"stores": [{"id": 1002}]}
    assert [c.post("/api/bargains", json=body).status_code for _ in range(3)] == [200, 200, 429]
    assert c.post("/api/bargains", json=body).get_json()["error"]["code"] == "rate_limited"
    other = from_addr(limited_app.test_client(), "10.0.0.2")
    assert other.post("/api/bargains", json=body).status_code == 200


def test_store_search_is_rate_limited_and_cached(limited_app):
    fake = services(limited_app).ah
    fake.responses = {"stores": {"storesSearch": {"result": [{"id": 1, "name": "AH"}]}}}
    a = from_addr(limited_app.test_client(), "10.0.0.1")
    b = from_addr(limited_app.test_client(), "10.0.0.2")
    assert a.get("/api/stores?postcode=1234AB").status_code == 200
    assert a.get("/api/stores?postcode=1234AB").status_code == 429
    assert b.get("/api/stores?postcode=1234 ab").get_json()["stores"][0]["id"] == 1
    assert [c for c in fake.calls if c[0] == "StoresSearch"] == [("StoresSearch", {"filter": {"postalCode": "1234AB"}})]


def test_admin_force_is_not_rate_limited(limited_app):
    c = from_addr(limited_app.test_client(), "10.0.0.1")
    headers = as_admin(c)
    body = {"stores": [{"id": 1002}]}
    assert all(c.post("/api/bargains/force", json=body, headers=headers).status_code == 200 for _ in range(4))


# ---- per-account sign-in lockout ------------------------------------------------------
@pytest.fixture
def users_app(tmp_path):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = generate_password_hash("admin-password-12345", method="pbkdf2:sha256:1000")
        ADMIN_MAX_FAILURES = 100
        ACCOUNT_MAX_FAILURES = 3
    app = create_app(Cfg)
    services(app).ah = FakeAHClient()
    return app


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def admin_login(client, password):
    token = _csrf(client.get("/admin/login").get_data(as_text=True))
    return client.post("/admin/login", data={"csrf": token, "password": password})


def test_admin_password_locks_after_failures_from_many_addresses(users_app):
    for i in range(3):
        c = from_addr(users_app.test_client(), f"10.0.2.{i}")
        assert admin_login(c, "wrong password!!").status_code == 401
    fresh = from_addr(users_app.test_client(), "10.0.9.8")
    assert admin_login(fresh, "admin-password-12345").status_code == 429   # account locked, any address


def test_lockout_is_per_address_with_proxy_addresses(tmp_path):
    """With the real client address (TRUSTED_PROXY), one visitor's failures
    don't lock out the others."""
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
        ADMIN_PASSWORD_HASH = generate_password_hash("admin-password-12345", method="pbkdf2:sha256:1000")
        ADMIN_MAX_FAILURES = 2
        ACCOUNT_MAX_FAILURES = 100
    app = create_app(Cfg)
    attacker = from_addr(app.test_client(), "203.0.113.5")
    for _ in range(3):
        admin_login(attacker, "wrong password!!")
    assert admin_login(attacker, "admin-password-12345").status_code == 429
    assert admin_login(from_addr(app.test_client(), "198.51.100.7"), "admin-password-12345").status_code == 302


# ---- TTLCache: stale-while-reloading, remembered errors, no lock leak --------------------
def test_stale_entry_is_served_while_reloading_in_background():
    clock, spawned = Clock(), []
    cache = TTLCache(ttl=300, stale_ttl=600, clock=clock, spawn=spawned.append)
    cache.get_or_load("k", lambda: "old")
    clock.now += 400
    entry, hit = cache.get_or_load("k", lambda: "new")
    assert entry.value == "old" and hit and len(spawned) == 1
    cache.get_or_load("k", lambda: "newer")
    assert len(spawned) == 1                      # one background reload per key
    spawned[0]()                                  # run it
    assert cache.peek("k").value == "new"
    clock.now += 1000                             # past ttl + stale_ttl: wait for a reload
    entry, hit = cache.get_or_load("k", lambda: "latest")
    assert entry.value == "latest" and not hit


def test_failed_background_reload_keeps_the_entry():
    clock, spawned = Clock(), []
    cache = TTLCache(ttl=300, stale_ttl=600, clock=clock, spawn=spawned.append)
    cache.get_or_load("k", lambda: "old")
    clock.now += 400

    def boom():
        raise AHTransportError("down")
    cache.get_or_load("k", boom)
    spawned[0]()
    assert cache.peek("k").value == "old"
    assert cache.stats()["refreshing"] == 0


def test_errors_are_remembered_for_error_ttl_only():
    clock, calls = Clock(), []

    def failing():
        calls.append(1)
        raise AHTransportError("unknown store")
    cache = TTLCache(ttl=300, error_ttl=60, clock=clock, cache_error=lambda e: isinstance(e, AHError))
    for _ in range(3):
        with pytest.raises(AHTransportError):
            cache.get_or_load("k", failing)
    assert len(calls) == 1
    clock.now += 61
    assert cache.get_or_load("k", lambda: "ok")[0].value == "ok"


def test_key_locks_do_not_pile_up_for_failing_keys():
    cache = TTLCache(ttl=300)

    def failing():
        raise AHTransportError("x")
    for i in range(500):
        with pytest.raises(AHTransportError):
            cache.get_or_load(i, failing)
    assert cache.stats() == {"entries": 0, "errors": 0, "loading": 0, "refreshing": 0}


# ---- WeekCache --------------------------------------------------------------------------
WEEK = {"start": "2026-11-16", "end": "2026-11-22"}


def week(n_items=0, n_errors=0, period=WEEK):
    """A BonusWeek with ``n_items`` offers and ``n_errors`` failed categories."""
    return BonusWeek(period=Period(**period), categories=[],
                     items=[Offer("bonus", i, f"item {i}") for i in range(n_items)],
                     errors=[CategoryError(f"c{i}", "category_failed", "ref") for i in range(n_errors)])


def week_cache(clock, today=date(2026, 11, 18)):
    return WeekCache(retry_seconds=300, clock=clock, today=lambda: today)


def test_week_cache_keeps_partial_copy_when_retry_fails():
    clock = Clock()
    cache = week_cache(clock)
    partial = week(2, 1)
    assert cache.get(lambda: partial) is partial
    clock.now += 301

    def down():
        raise AHTransportError("down")
    assert cache.get(down) is partial            # no error page for a retry
    assert cache.get(down) is partial            # and not retried again at once


def test_week_cache_keeps_the_better_partial_copy():
    clock = Clock()
    cache = week_cache(clock)
    better = week(3, 1)
    cache.get(lambda: better)
    clock.now += 301
    worse = week(1, 2)
    assert cache.get(lambda: worse) is better


def test_week_cache_raises_without_a_usable_copy():
    clock = Clock()
    cache = week_cache(clock)
    cache.get(lambda: week(period={"start": "2026-11-09", "end": "2026-11-15"}))
    clock.now += 301

    def down():
        raise AHTransportError("down")
    with pytest.raises(AHTransportError):
        cache.get(down)                          # last week's copy is no use


def test_week_cache_does_not_wait_for_a_running_reload():
    clock = Clock()
    cache = week_cache(clock)
    partial = week(1, 1)
    cache.get(lambda: partial)
    clock.now += 301
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return week(2)
    t = threading.Thread(target=cache.get, args=(slow,))
    t.start()
    started.wait(5)
    t0 = time.monotonic()
    assert cache.get(lambda: pytest.fail("must not load")) is partial
    assert time.monotonic() - t0 < 1
    release.set()
    t.join(5)
    assert len(cache.get(lambda: pytest.fail("fresh now")).items) == 2


def test_week_cache_background_loader():
    cache = week_cache(time.time)
    loaded = threading.Event()

    def loader():
        loaded.set()
        return week()
    scheduler = Scheduler()
    scheduler.every("bonus", 0.05, prewarm_job(cache, loader, ready=lambda: True))
    scheduler.start()
    try:
        assert loaded.wait(5)
        assert scheduler.running("bonus") and cache.status()["loaded"]
    finally:
        scheduler.stop()
    assert not scheduler.running("bonus")


def test_prewarm_waits_after_a_failure_and_skips_without_login():
    cache = week_cache(time.time)

    def down():
        raise AHTransportError("down")
    assert prewarm_job(cache, down, ready=lambda: True)() == cache.retry_seconds
    assert prewarm_job(cache, lambda: pytest.fail("not ready"), ready=lambda: False)() is None


def test_scheduler_keeps_a_crashing_job_alive():
    runs = []
    done = threading.Event()

    def job():
        runs.append(1)
        if len(runs) >= 3:
            done.set()
        raise RuntimeError("boom")
    scheduler = Scheduler()
    scheduler.every("crashy", 0.01, job, backoff=0.01)
    scheduler.start()
    try:
        assert done.wait(5)
    finally:
        scheduler.stop()


def test_bonus_endpoint_uses_the_shared_copy():
    app = create_app(TestingConfig)
    services(app).ah = FakeAHClient()
    services(app).bonus_cache.get(lambda: week(1))
    data = app.test_client().get("/api/bonus").get_json()
    assert data["period"] == WEEK and data["items"][0]["title"] == "item 0"


# ---- AH session: concurrency cap + counters ---------------------------------------------
class _Tokens:
    configured = True

    def access_token(self):
        return "tok"


def test_ah_session_caps_concurrent_requests():
    running, peak, lock = [0], [0], threading.Lock()

    class Client:
        def query(self, *a):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.02)
            with lock:
                running[0] -= 1
            return {}

    session = AHSession(_Tokens(), lambda token: Client(), max_concurrent=2)
    threads = [threading.Thread(target=session.query, args=("q",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= 2
    assert session.stats()["requests"] == 8 and session.stats()["failures"] == 0


def test_ah_session_counts_failures():
    class Client:
        def query(self, *a):
            raise AHTransportError("down", 500)

    session = AHSession(_Tokens(), lambda token: Client())
    with pytest.raises(AHTransportError):
        session.query("q")
    stats = session.stats()
    assert stats["failures"] == 1 and stats["last_failure"] and stats["last_ok"] is None


# ---- bargain items are normalized ---------------------------------------------------------
def test_bargain_mapping_keeps_only_known_fields():
    raw = {
        "product": {"id": "123", "title": " Kip ", "salesUnitSize": "300 g", "secret": "x",
                    "imagePack": [{"medium": {"url": "javascript:alert(1)"}}, {"medium": {"url": "https://static.ah.nl/a.jpg"}}]},
        "categoryTitle": "Vlees", "stock": 3, "extra": {"a": 1},
        "markdown": {"markdownType": "EXPIRATION", "markdownExpirationDate": "2026-11-18", "markdownPercentage": 35},
        "bargainPrice": {"priceWas": "4.99", "priceNow": "1.00"},     # AH sends prices as text
    }
    item = bargain(raw).to_json()
    assert item == {"kind": "bargain", "id": 123, "title": "Kip", "brand": None, "unitSize": "300 g",
                    "image": "https://static.ah.nl/a.jpg", "category": "Vlees", "priceNow": 1.0, "priceWas": 4.99,
                    "storeId": None, "storeName": None, "stock": 3, "discount": 35,
                    "expires": "2026-11-18", "markdownType": "EXPIRATION"}
    assert bargain("nope") is None
    odd = bargain({"stock": True, "markdown": {"markdownPercentage": 500}}).to_json()
    assert odd["discount"] is None and odd["stock"] is None


# ---- headers / admin ---------------------------------------------------------------------
def test_hsts_only_when_served_via_https():
    plain = create_app(TestingConfig).test_client().get("/healthz")
    assert "Strict-Transport-Security" not in plain.headers
    secure = create_app(TestingConfig, {"ADMIN_COOKIE_SECURE": True}).test_client().get("/healthz")
    assert secure.headers["Strict-Transport-Security"].startswith("max-age=")


def test_admin_panel_shows_health(tmp_path):
    class Cfg(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")
    app = create_app(Cfg)
    services(app).ah = FakeAHClient()
    c = app.test_client()
    as_admin(c)
    page = c.get("/admin/").get_data(as_text=True)
    assert "Server health" in page and "Bargains cache" in page and "Rate limited" in page


def test_session_cookie_uses_host_prefix_via_https():
    plain = create_app(TestingConfig)
    assert plain.config["SESSION_COOKIE_NAME"] == "ahb_admin"
    app = create_app(TestingConfig, {"ADMIN_COOKIE_SECURE": True, "ADMIN_PASSWORD_HASH": "configured"})
    assert app.config["SESSION_COOKIE_NAME"] == "__Host-ahb_admin"
    header = app.test_client().get("/admin/login").headers["Set-Cookie"]   # the form creates a session (CSRF)
    assert header.startswith("__Host-ahb_admin=")
    assert "Secure" in header and "Path=/" in header and "Domain=" not in header


@pytest.mark.parametrize("raw,price", [
    ("1.09", 1.09), ("1,09", 1.09), ("€ 2,49", 2.49), (" 3 ", 3.0), (1.5, 1.5), (2, 2.0),
    ("", None), ("gratis", None), (None, None), (True, None), (-1, None), ("nan", None), ("1e9", None),
])
def test_bargain_prices_are_read_from_ahs_text(raw, price):
    assert bargain({"bargainPrice": {"priceNow": raw}}).price_now == price


def test_bargain_api_returns_numeric_prices(client, fake_client):
    fake_client.responses = {"1002": [{"product": {"id": 1, "title": "Kip"},
                                       "bargainPrice": {"priceWas": "4.99", "priceNow": "2.49"}}]}
    item = client.post("/api/bargains", json={"stores": [{"id": 1002}]}).get_json()["items"][0]
    assert (item["priceWas"], item["priceNow"]) == (4.99, 2.49)
