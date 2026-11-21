"""Staying below AH's rate limit: pacing, pausing after a block, and keeping
the Bonus week across restarts (AH's CDN answers "Access Denied" after a
quick burst of ~160 requests, e.g. a full Bonus load after every redeploy)."""

import json
import sqlite3

import pytest
from test_bonus import FakeSession

from ah_dashboard import create_app
from ah_dashboard.ah.client import AHThrottled, AHTransportError, throttle_error
from ah_dashboard.ah.gateway import BONUS_METADATA_PATH, AHGateway
from ah_dashboard.ah.session import AHSession, Pacer
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.domain.models import bonus_week_from_stored
from ah_dashboard.services import bonus as bonus_service

ACCESS_DENIED = ("<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n"
                 "<P>Reference #0.498a1402.1795297562.9d69b3a3</P>\n</BODY>\n</HTML>")


# ---- recognising a block ------------------------------------------------------------------
def test_access_denied_page_and_429_are_throttling():
    assert isinstance(throttle_error(403, ACCESS_DENIED, "q"), AHThrottled)
    assert isinstance(throttle_error(429, "", "q"), AHThrottled)
    assert throttle_error(403, '{"error": "forbidden"}', "q") is None
    assert throttle_error(500, ACCESS_DENIED, "q") is None


def test_throttling_is_a_transport_error_but_not_an_auth_failure():
    from ah_dashboard.ah.session import is_auth_failure
    exc = throttle_error(403, ACCESS_DENIED, "q")
    assert isinstance(exc, AHTransportError) and not is_auth_failure(exc)


# ---- pacing -----------------------------------------------------------------------------------
class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_pacer_allows_a_burst_then_spaces_requests():
    t = FakeTime()
    pacer = Pacer(rate=2, burst=3, clock=t.clock, sleep=t.sleep)
    for _ in range(5):
        pacer.wait()
    assert t.slept == [0.5, 0.5]          # 3 at once, then one every 0.5 s


def test_pacer_refills_while_idle_and_can_be_switched_off():
    t = FakeTime()
    pacer = Pacer(rate=2, burst=2, clock=t.clock, sleep=t.sleep)
    for _ in range(2):
        pacer.wait()
    t.now += 10                           # idle: the bucket is full again (but no fuller than burst)
    for _ in range(3):
        pacer.wait()
    assert t.slept == [0.5]
    off = Pacer(rate=0, clock=t.clock, sleep=t.sleep)
    for _ in range(50):
        off.wait()
    assert t.slept == [0.5]


# ---- pausing after a block --------------------------------------------------------------------
class Tokens:
    configured = True
    refreshed = 0

    def access_token(self):
        return "token"

    def refresh(self, failed_token):
        self.refreshed += 1
        return "token"


def _session(t, answers, pause=900):
    """A session whose client answers with ``answers`` in turn (an exception is raised)."""
    calls = []

    class Client:
        def __init__(self, token):
            pass

        def get(self, path, params=None):
            calls.append(path)
            answer = answers.pop(0) if answers else {"ok": True}
            if isinstance(answer, Exception):
                raise answer
            return answer

    tokens = Tokens()
    session = AHSession(tokens, Client, rate=0, throttle_pause=pause, clock=t.clock, sleep=t.sleep)
    return session, calls, tokens


def test_a_block_pauses_all_requests_without_contacting_ah():
    t = FakeTime()
    session, calls, tokens = _session(t, [throttle_error(403, ACCESS_DENIED, "/x")])
    with pytest.raises(AHThrottled):
        session.get("/x")
    for _ in range(5):
        with pytest.raises(AHThrottled):
            session.get("/x")
    assert calls == ["/x"] and tokens.refreshed == 0       # only the first request reached AH
    assert session.paused() and session.stats()["paused_seconds"] == 900
    t.now += 901
    assert session.get("/x") == {"ok": True} and not session.paused()
    assert session.stats()["throttled"] == 1


def test_a_bonus_load_stops_at_the_first_block():
    t = FakeTime()
    fake = FakeSession()

    class Client:
        def __init__(self, token):
            pass

        def get(self, path, params=None):
            fake.calls.append((path, params))
            if path != BONUS_METADATA_PATH:
                raise throttle_error(403, ACCESS_DENIED, path)
            return fake.meta

    session = AHSession(Tokens(), Client, rate=0, clock=t.clock, sleep=t.sleep)
    week = bonus_service.load_bonus(AHGateway(session, "AHWEBSHOP"), max_workers=1)
    assert len(fake.calls) == 2                              # metadata + the first (blocked) category
    assert week.errors and not week.complete


def test_a_paused_session_answers_the_page_with_ah_busy():
    app = create_app("testing")

    class Paused(FakeSession):
        def get(self, path, params=None):
            raise AHThrottled("paused")

    services(app).ah = Paused()
    r = app.test_client().get("/api/bonus")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "ah_busy"


# ---- the Bonus week survives a restart --------------------------------------------------------
def _app(tmp_path):
    class Config(TestingConfig):
        DATABASE_FILE = str(tmp_path / "app.db")      # shared by apps made in one test (= restart)
    app = create_app(Config)
    services(app).ah = FakeSession()
    return app


def test_a_restart_reuses_the_stored_week_without_asking_ah(tmp_path, monkeypatch):
    first = _app(tmp_path)
    monkeypatch.setattr(services(first).bonus_cache, "_current_week", lambda week: True)
    body = first.test_client().get("/api/bonus").get_json()
    assert services(first).ah.calls

    second = _app(tmp_path)                            # a redeploy: same database, empty memory
    monkeypatch.setattr(services(second).bonus_cache, "_current_week", lambda week: True)
    assert second.test_client().get("/api/bonus").get_json() == body
    assert services(second).ah.calls == []            # nothing sent to AH


def test_clearing_the_cache_also_clears_the_stored_week(tmp_path):
    first = _app(tmp_path)
    first.test_client().get("/api/bonus")
    services(first).bonus_cache.clear()
    assert services(_app(tmp_path)).bonus_cache.status() == {"loaded": False}


def test_a_damaged_stored_week_is_ignored(tmp_path):
    _app(tmp_path).test_client().get("/api/bonus")
    with sqlite3.connect(tmp_path / "app.db") as conn:
        conn.execute("UPDATE bonus_week SET data = '{not json'")
    assert services(_app(tmp_path)).bonus_cache.status() == {"loaded": False}


def test_stored_week_round_trips_and_odd_data_is_refused():
    week = bonus_service.load_bonus(AHGateway(FakeSession(), "AHWEBSHOP"))
    stored = json.loads(json.dumps(week.to_stored()))
    again = bonus_week_from_stored(stored)
    assert again is not None and again.to_json() == week.to_json()

    for damage in (
        lambda d: d.update(items="x"),
        lambda d: d["items"][0].update(priceNow="1.00"),
        lambda d: d["items"][0].update(kind="bargain"),
        lambda d: d["items"][0].update(dealType="<script>"),
        lambda d: d["items"][0].update(isGroup=1),
        lambda d: d.update(groupsFailed=-1),
        lambda d: d.update(period=None),
    ):
        broken = json.loads(json.dumps(stored))
        damage(broken)
        assert bonus_week_from_stored(broken) is None


def test_failed_group_lookups_make_the_week_partial():
    week = bonus_service.load_bonus(AHGateway(FakeSession(groups={"9": AHTransportError("HTTP 500", 500)}),
                                              "AHWEBSHOP"))
    assert week.groups_failed == 1 and not week.complete and week.errors == []


# ---- the admin's force refresh of the Bonus ---------------------------------------------------
def _bonus_app():
    app = create_app("testing")
    services(app).ah = FakeSession()
    return app


def _meta_calls(app):
    return len([c for c in services(app).ah.calls if c[0] == BONUS_METADATA_PATH])


def test_admin_force_refresh_reloads_the_bonus_from_ah():
    from conftest import as_admin
    app = _bonus_app()
    c = app.test_client()
    c.get("/api/bonus")
    c.get("/api/bonus")
    assert _meta_calls(app) == 1                         # cached
    r = c.post("/api/bonus/force", headers=as_admin(c))
    assert r.status_code == 200 and len(r.get_json()["items"]) == 4
    assert _meta_calls(app) == 2                         # reloaded despite the fresh copy


def test_bonus_force_refresh_needs_an_admin_and_the_csrf_token():
    from conftest import as_admin, sign_in
    app = _bonus_app()
    c = app.test_client()
    assert c.post("/api/bonus/force").status_code == 401                    # anonymous
    services(app).users.create("alice", "alice@example.com")
    sign_in(c)
    assert c.post("/api/bonus/force").status_code == 401                    # a normal user
    as_admin(c)
    assert c.post("/api/bonus/force").status_code == 403                    # admin without CSRF
    assert c.post("/api/bonus/force", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert c.get("/api/bonus/force").status_code == 405
    assert _meta_calls(app) == 0                         # none of these reached AH


def test_failed_force_refresh_keeps_the_current_copy():
    from conftest import as_admin
    app = _bonus_app()
    c = app.test_client()
    before = c.get("/api/bonus").get_json()
    services(app).ah.fail = {"meta"}
    assert c.post("/api/bonus/force", headers=as_admin(c)).status_code == 502
    assert c.get("/api/bonus").get_json() == before


# ---- next week's Bonus (admins only) ----------------------------------------------------------
NEXT_SECTIONS = {"Volgende week": {"bonusGroupOrProducts": [
    {"product": {"webshopId": 333, "title": "Kaas", "priceBeforeBonus": 5.0,
                 "discountLabels": [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]}}]}}


@pytest.fixture
def next_app(monkeypatch):
    from datetime import date
    today = date(2026, 11, 21)                     # Saturday: META lists 16-22 Nov and 23-29 Nov
    monkeypatch.setattr(bonus_service, "amsterdam_today", lambda: today)
    app = create_app("testing")
    s = services(app)
    s.ah = FakeSession(sections={**FakeSession().sections, **NEXT_SECTIONS})
    for part in (s.bonus_cache, s.bonus_next_cache, s.bonus_next_status):
        part._today = lambda: today
    return app


def _admin(app):
    from conftest import as_admin
    c = app.test_client()
    return c, as_admin(c)


def test_next_week_is_listed_and_loaded_for_admins(next_app):
    c, h = _admin(next_app)
    status = c.get("/api/bonus/next/status", headers=h).get_json()
    assert status == {"available": True, "period": {"start": "2026-11-23", "end": "2026-11-29"}}
    week = c.get("/api/bonus/next", headers=h).get_json()
    assert week["period"]["start"] == "2026-11-23" and week["categories"] == ["Volgende week"]
    assert [i["title"] for i in week["items"]] == ["Kaas"]
    sections = [p for p, params in services(next_app).ah.calls if params.get("category") == "Volgende week"]
    assert sections and all(params["date"] == "2026-11-23" for p, params in services(next_app).ah.calls
                            if params.get("category") == "Volgende week")
    # This week is untouched.
    assert c.get("/api/bonus").get_json()["period"]["start"] == "2026-11-16"


def test_next_week_status_is_cheap(next_app):
    c, h = _admin(next_app)
    for _ in range(5):
        c.get("/api/bonus/next/status", headers=h)
    assert _meta_calls(next_app) == 1              # one metadata request per 10 minutes
    c.get("/api/bonus/next", headers=h)
    before = _meta_calls(next_app)
    c.get("/api/bonus/next/status", headers=h)
    c.get("/api/bonus/next", headers=h)
    assert _meta_calls(next_app) == before         # loaded: no AH requests at all


def test_next_week_unpublished_gives_404(next_app):
    import copy

    from test_bonus import META
    meta = copy.deepcopy(META)
    meta["periods"] = meta["periods"][:1]          # AH lists only this week
    services(next_app).ah.meta = meta
    c, h = _admin(next_app)
    assert c.get("/api/bonus/next/status", headers=h).get_json() == {"available": False, "period": None}
    r = c.get("/api/bonus/next", headers=h)
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "bonus_next_unavailable"


def test_next_week_is_for_everyone_but_its_force_reload_for_admins(next_app):
    from conftest import as_admin, configure, sign_in
    c = next_app.test_client()
    # Anonymous visitors (no CSRF token needed to read):
    assert c.get("/api/bonus/next/status").get_json()["available"] is True
    assert c.get("/api/bonus/next").get_json()["period"]["start"] == "2026-11-23"
    calls = len(services(next_app).ah.calls)
    # The force reload: not for visitors or ordinary users, and nothing reaches AH.
    assert c.post("/api/bonus/next/force").status_code == 401
    services(next_app).users.create("alice", "alice@example.com")
    sign_in(c)
    assert c.post("/api/bonus/next/force").status_code == 401
    as_admin(c)
    assert c.post("/api/bonus/next/force").status_code == 403                 # admin without CSRF
    assert len(services(next_app).ah.calls) == calls
    assert c.post("/api/bonus/next/force", headers={"X-CSRF-Token": "test-csrf"}).status_code == 200
    assert len(services(next_app).ah.calls) > calls
    # Reading is rate limited per client, like /api/bonus.
    configure(next_app, RATE_LIMIT_BONUS=2)
    other = next_app.test_client()
    codes = [other.get("/api/bonus/next").status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_next_week_copy_ends_when_that_week_starts(next_app):
    from datetime import date
    c, h = _admin(next_app)
    c.get("/api/bonus/next", headers=h)
    cache = services(next_app).bonus_next_cache
    assert cache.current() is not None
    cache._today = lambda: date(2026, 11, 23)      # Monday: it is this week's Bonus now
    assert cache.current() is None


def test_next_week_is_stored_apart_from_this_week(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr(bonus_service, "amsterdam_today", lambda: date(2026, 11, 21))

    def make():
        app = _app(tmp_path)
        s = services(app)
        s.ah = FakeSession(sections={**FakeSession().sections, **NEXT_SECTIONS})
        for part in (s.bonus_cache, s.bonus_next_cache, s.bonus_next_status):
            part._today = lambda: date(2026, 11, 21)
        return app

    first = make()
    c, h = _admin(first)
    c.get("/api/bonus/next", headers=h)
    second = make()
    assert services(second).bonus_next_cache.current().period.start == "2026-11-23"
    assert services(second).bonus_cache.current() is None     # this week was never loaded


def test_bonus_store_only_accepts_its_own_tables():
    with pytest.raises(ValueError):
        bonus_service.BonusStore(None, "users; DROP TABLE users")
