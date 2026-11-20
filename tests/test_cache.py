"""Bargains cache: 5-minute TTL per store, force refresh, single-flight."""

import threading
import time

import pytest
from conftest import as_admin, configure

from ah_dashboard.container import services
from ah_dashboard.services.cache import TTLCache


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


# ---- TTLCache -----------------------------------------------------------------
def test_hit_within_ttl_and_reload_after():
    clock, calls = Clock(), []
    cache = TTLCache(ttl=300, clock=clock)
    load = lambda: calls.append(1) or len(calls)
    assert cache.get_or_load("k", load) == (cache.peek("k"), False)
    clock.now += 299
    entry, hit = cache.get_or_load("k", load)
    assert hit and entry.value == 1
    clock.now += 2                      # now 301 s old
    entry, hit = cache.get_or_load("k", load)
    assert not hit and entry.value == 2


def test_force_always_reloads_and_replaces_entry():
    clock, calls = Clock(), []
    cache = TTLCache(ttl=300, clock=clock)
    load = lambda: calls.append(1) or len(calls)
    cache.get_or_load("k", load)
    entry, hit = cache.get_or_load("k", load, force=True)   # 0 s old: still reloaded
    assert hit is False and entry.value == 2
    assert cache.get_or_load("k", load) == (entry, True)     # normal get uses the new data
    assert len(calls) == 2


def test_failed_force_keeps_previous_entry():
    cache = TTLCache(ttl=300)
    cache.get_or_load("k", lambda: "old")

    def boom():
        raise RuntimeError("AH down")

    with pytest.raises(RuntimeError):
        cache.get_or_load("k", boom, force=True)
    assert cache.peek("k").value == "old"


def test_failures_are_not_cached():
    cache = TTLCache(ttl=300)

    def boom():
        raise RuntimeError("AH down")

    with pytest.raises(RuntimeError):
        cache.get_or_load("k", boom)
    assert cache.peek("k") is None
    assert cache.get_or_load("k", lambda: "ok")[0].value == "ok"


def test_concurrent_misses_load_once():
    cache, calls = TTLCache(ttl=300), []

    def slow():
        calls.append(1)
        time.sleep(0.1)
        return "data"

    threads = [threading.Thread(target=cache.get_or_load, args=("k", slow)) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(calls) == 1


def test_entry_cap_evicts_oldest():
    cache = TTLCache(ttl=300, max_entries=2)
    for key in ("a", "b", "c"):
        cache.get_or_load(key, lambda: key)
    assert cache.peek("a") is None and cache.peek("b") and cache.peek("c")


# ---- API ------------------------------------------------------------------------
@pytest.fixture
def clock(app):
    c = Clock(time.time())
    services(app).bargains_cache._clock = c
    return c


def ah_calls(fake_client):
    return [c for c in fake_client.calls if c[0] == "BargainItems"]


def test_refresh_is_served_from_cache_within_5_minutes(client, fake_client, clock):
    fake_client.responses = {"1002": [{"product": {"id": 1}}]}
    body = {"stores": [{"id": 1002, "name": "Oost"}]}
    first = client.post("/api/bargains", json=body).get_json()
    clock.now += 120
    second = client.post("/api/bargains", json=body).get_json()
    assert len(ah_calls(fake_client)) == 1       # second answer came from the cache
    assert second["items"] == first["items"]


def test_response_does_not_reveal_cache_details(client, fake_client, clock):
    body = {"stores": [{"id": 1002}]}
    for _ in range(2):  # fresh load, then a cache hit
        data = client.post("/api/bargains", json=body).get_json()
        assert set(data) == {"items", "storeErrors"}
    data = client.post("/api/bargains/force", json=body, headers=as_admin(client)).get_json()
    assert set(data) == {"items", "storeErrors"}


def test_cache_expires_after_ttl(client, fake_client, clock):
    body = {"stores": [{"id": 1002}]}
    client.post("/api/bargains", json=body)
    clock.now += 301
    client.post("/api/bargains", json=body)
    assert len(ah_calls(fake_client)) == 2


def test_force_endpoint_calls_ah(client, fake_client, clock):
    body = {"stores": [{"id": 1002}]}
    client.post("/api/bargains", json=body)
    clock.now += 90
    client.post("/api/bargains/force", json=body, headers=as_admin(client))
    assert len(ah_calls(fake_client)) == 2


def test_force_endpoint_always_reloads_even_right_after_a_load(client, fake_client, clock):
    body = {"stores": [{"id": 1002}]}
    client.post("/api/bargains", json=body)
    for _ in range(3):                    # no minimum age: every force calls AH
        client.post("/api/bargains/force", json=body, headers=as_admin(client))
    assert len(ah_calls(fake_client)) == 4


def test_force_endpoint_updates_the_shared_cache(client, fake_client, clock):
    body = {"stores": [{"id": 1002}]}
    client.post("/api/bargains", json=body)
    clock.now += 90
    client.post("/api/bargains/force", json=body, headers=as_admin(client))        # reload from AH
    client.post("/api/bargains", json=body)              # normal refresh uses the new data
    assert len(ah_calls(fake_client)) == 2


def test_force_flag_in_body_no_longer_forces(client, fake_client, clock):
    """Forcing is only possible via /api/bargains/force (so it can be
    authorized separately); a "force" field on /api/bargains is ignored."""
    body = {"stores": [{"id": 1002}]}
    client.post("/api/bargains", json=body)
    clock.now += 90
    client.post("/api/bargains", json={**body, "force": True})
    assert len(ah_calls(fake_client)) == 1


def test_force_endpoint_validates_like_the_normal_one(client):
    resp = client.post("/api/bargains/force", json={"stores": [{"id": "12x"}]}, headers=as_admin(client))
    assert resp.status_code == 400 and resp.get_json()["error"]["code"] == "store_invalid"
    assert client.get("/api/bargains/force").status_code == 405


def test_store_names_are_per_request_and_cache_is_not_mutated(client, fake_client, clock):
    fake_client.responses = {"1002": [{"product": {"id": 1}}]}
    a = client.post("/api/bargains", json={"stores": [{"id": 1002, "name": "Oost"}]}).get_json()
    b = client.post("/api/bargains", json={"stores": [{"id": 1002, "name": "Thuis"}]}).get_json()
    assert a["items"][0]["storeName"] == "Oost"
    assert b["items"][0]["storeName"] == "Thuis"
    cached = services(client.application).bargains_cache.peek(1002).value
    assert cached[0].bargain.store_name is None and cached[0].bargain.store_id is None


def test_failed_store_is_remembered_briefly_then_retried(client, fake_client, clock):
    """A failing store is not asked at AH again within STORE_ERROR_TTL (random
    or broken store ids can't make every request reach AH), then retried."""
    fake_client.fail_stores = {"1003"}
    body = {"stores": [{"id": 1003, "name": "Park"}]}
    first = client.post("/api/bargains", json=body).get_json()
    assert first["storeErrors"]
    fake_client.fail_stores = set()
    second = client.post("/api/bargains", json=body).get_json()
    assert second["storeErrors"] and second["storeErrors"][0]["code"] == "store_failed"
    assert len(ah_calls(fake_client)) == 1           # remembered, not asked again
    clock.now += client.application.config["STORE_ERROR_TTL"] + 1
    third = client.post("/api/bargains", json=body).get_json()
    assert third["storeErrors"] == []
    assert len(ah_calls(fake_client)) == 2           # retried at AH after the window


def test_auth_failures_are_not_remembered(client, fake_client, clock):
    from ah_dashboard.ah.auth import AHAuthError

    def broken_login(*a, **k):
        raise AHAuthError("login gone")
    original = fake_client.query
    fake_client.query = broken_login
    body = {"stores": [{"id": 1003}]}
    assert client.post("/api/bargains", json=body).get_json()["storeErrors"][0]["code"] == "ah_auth_failed"
    fake_client.query = original                     # admin fixed the login
    assert client.post("/api/bargains", json=body).get_json()["storeErrors"] == []


def test_force_ignores_a_remembered_failure(client, fake_client, clock):
    fake_client.fail_stores = {"1003"}
    body = {"stores": [{"id": 1003}]}
    client.post("/api/bargains", json=body)
    fake_client.fail_stores = set()
    data = client.post("/api/bargains/force", json=body, headers=as_admin(client)).get_json()
    assert data["storeErrors"] == []


# ---- force refresh is admin-only ----------------------------------------------------
def test_force_requires_admin_session(client, fake_client):
    resp = client.post("/api/bargains/force", json={"stores": [{"id": 1002}]})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "admin_required"
    assert ah_calls(fake_client) == []          # nothing reached AH


def test_force_requires_csrf_header(client, fake_client):
    as_admin(client)
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        resp = client.post("/api/bargains/force", json={"stores": [{"id": 1002}]}, headers=headers)
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "csrf_invalid"
    assert ah_calls(fake_client) == []


def test_force_is_refused_when_admin_is_disabled(client, fake_client):
    headers = as_admin(client)
    configure(client.application, ADMIN_PASSWORD_HASH=None)   # panel switched off
    resp = client.post("/api/bargains/force", json={"stores": [{"id": 1002}]}, headers=headers)
    assert resp.status_code == 401


def test_expired_admin_session_cannot_force(client, fake_client):
    headers = as_admin(client)
    with client.session_transaction() as sess:
        sess["admin_login_at"] = time.time() - 2 * 3600       # older than 60 minutes
    resp = client.post("/api/bargains/force", json={"stores": [{"id": 1002}]}, headers=headers)
    assert resp.status_code == 401


def test_normal_refresh_needs_no_login(client, fake_client):
    resp = client.post("/api/bargains", json={"stores": [{"id": 1002}]})
    assert resp.status_code == 200


def test_session_endpoint(client):
    assert client.get("/api/session").get_json() == {"admin": False}
    as_admin(client, csrf="abc")
    assert client.get("/api/session").get_json() == {"admin": True, "csrf": "abc"}
