"""Odd input never crashes a request or turns one bad item into a failed page.

From the input review: AH data the mapping couldn't handle failed the whole
bargains response (all stores) or the whole Bonus, uncached, so every request
loaded from AH again. Validators now reject odd values, and anything they miss
fails only that store / category / group (mapping.AHDataError).
"""

from datetime import date

import pytest
from conftest import as_admin

from ah_dashboard import create_app, ratelimit
from ah_dashboard.ah import mapping
from ah_dashboard.ah.gateway import BONUS_METADATA_PATH
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.domain.models import BonusWeek, CategoryError, Period
from ah_dashboard.domain.pricing import bonus_price
from ah_dashboard.services.bonus import WeekCache

NAN, INF = float("nan"), float("inf")   # json.loads accepts the literals NaN and Infinity


# ---- validators: odd values are rejected, never raised on -------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("1002", 1002), (1002, 1002), ("²", None), ("١٢٣", None), ("12345678901", None),
    (True, None), (-1, None), (NAN, None), ("", None),
])
def test_product_id(value, expected):
    assert mapping.product_id(value) == expected


def test_text_drops_non_finite_numbers():
    assert mapping.text(NAN) is None and mapping.text(INF) is None and mapping.text(1.5) == "1.5"


@pytest.mark.parametrize("label", [
    {"code": "DISCOUNT_X_FOR_Y", "price": 3, "count": NAN},
    {"code": "DISCOUNT_X_FOR_Y", "price": 3, "count": INF},
    {"code": "DISCOUNT_FIXED_PRICE", "price": INF},
    {"code": "DISCOUNT_FIXED_PRICE", "price": NAN},
    {"code": "DISCOUNT_FIXED_PRICE", "price": 1e30},
    {"code": "DISCOUNT_FIXED_PRICE", "price": 99_999_999},
    {"code": "DISCOUNT_PERCENTAGE", "percentage": INF},
    {"code": "DISCOUNT_X_PLUS_Y_FREE", "count": 2, "freeCount": NAN},
])
def test_bonus_price_ignores_odd_labels(label):
    assert bonus_price(2.0, [label]) == (None, None)


def test_bonus_price_still_works():
    assert bonus_price(2.0, [{"code": "DISCOUNT_FIXED_PRICE", "price": 1.5}]) == (1.5, 1)
    assert bonus_price(None, [{"code": "DISCOUNT_X_FOR_Y", "price": 7, "count": 2}]) == (3.5, 2)


@pytest.mark.parametrize("meta", [
    {"periods": [{"tabs": 5}]},
    {"periods": [{"tabs": "abc"}]},
    {"periods": [{"tabs": [{"urlMetadataList": 5}]}]},
    {"periods": [{"tabs": [5, None, {"urlMetadataList": {"x": 1}}]}]},
])
def test_bonus_period_odd_shapes(meta):
    assert mapping.bonus_period(meta)[1] == []


@pytest.mark.parametrize("count, expected", [(-3, 0), (True, 0), (10**9, 0), (NAN, 0), (4, 4)])
def test_group_product_count_bounded(count, expected):
    # Out-of-range counts fall back to the products actually listed (none here).
    found = mapping.group_details({"bonusPromotions": [{"productCount": count, "products": []}]}, [])
    assert (found or {}).get("productCount") == (expected or None)


def test_guarded_turns_bugs_into_ah_data_errors():
    def boom(_):
        raise ValueError("odd")
    with pytest.raises(mapping.AHDataError, match="unusable thing"):
        mapping.guarded("thing", boom, 1)
    assert issubclass(mapping.AHDataError, mapping.AHError)


# ---- end to end: one bad item fails only its store / category ----------------------------------
GOOD = {"product": {"id": 1, "title": "Good"}, "bargainPrice": {"priceNow": "1.00", "priceWas": "2.00"}}


class OddAH:
    """Store 1002 and Bonus category B answer with data the old mapping crashed on."""
    configured = True

    def __init__(self, bad_item=None, bad_label=None):
        self.bad_item = bad_item or {"product": {"id": "1", "title": "Bad"}}
        self.bad_label = bad_label
        self.calls = 0

    def query(self, document, variables=None, operation=None):
        self.calls += 1
        if operation == "BargainItems":
            return {"bargainItems": [GOOD, self.bad_item] if variables["storeId"] == "1002" else [GOOD]}
        return {"bonusPromotions": []}

    def get(self, path, params=None):
        self.calls += 1
        if path == BONUS_METADATA_PATH:
            return {"periods": [{"bonusStartDate": "2026-11-16", "bonusEndDate": "2026-11-22", "tabs": [
                {"urlMetadataList": [{"description": c, "bonusType": "NATIONAL"} for c in ("A", "B")]}]}]}
        labels = [self.bad_label] if self.bad_label and params["category"] == "B" else []
        return {"bonusGroupOrProducts": [{"product": {
            "webshopId": 5 if params["category"] == "A" else 6, "title": "P " + params["category"],
            "priceBeforeBonus": 2, "discountLabels": labels}}]}


def _app(fake):
    app = create_app(TestingConfig)
    app.config["PROPAGATE_EXCEPTIONS"] = False   # an escaped exception shows as a 500
    services(app).ah = fake
    return app


def test_a_bad_bargain_fails_only_its_store_and_is_remembered(monkeypatch):
    # Even a validator bug (simulated) only fails that store, and the failure is
    # cached like any AH error, so the next request does not ask AH again.
    real = mapping.bargain
    monkeypatch.setattr(mapping, "bargain", lambda raw: (_ for _ in ()).throw(ValueError("bug"))
                        if raw.get("product", {}).get("title") == "Bad" else real(raw))
    fake = OddAH()
    c = _app(fake).test_client()
    body = {"stores": [{"id": 1002}, {"id": 1003}]}
    first = c.post("/api/bargains", json=body)
    assert first.status_code == 200
    data = first.get_json()
    assert [i["storeId"] for i in data["items"]] == [1003]
    assert [(e["storeId"], e["code"]) for e in data["storeErrors"]] == [(1002, "store_failed")]
    assert "ref" in data["storeErrors"][0]
    before = fake.calls
    assert c.post("/api/bargains", json=body).status_code == 200
    assert fake.calls == before                 # both from the cache (the error is remembered)


def test_odd_ids_from_ah_are_dropped_not_crashing():
    fake = OddAH(bad_item={"product": {"id": "²", "title": "Bad"}})
    data = _app(fake).test_client().post("/api/bargains", json={"stores": [{"id": 1002}]}).get_json()
    assert sorted(i["title"] for i in data["items"]) == ["Bad", "Good"]
    assert [i["id"] for i in data["items"] if i["title"] == "Bad"] == [None]


@pytest.mark.parametrize("label", [
    {"code": "DISCOUNT_FIXED_PRICE", "price": INF},
    {"code": "DISCOUNT_X_FOR_Y", "price": 3, "count": NAN},
])
def test_a_bad_bonus_label_is_ignored(label):
    data = _app(OddAH(bad_label=label)).test_client().get("/api/bonus").get_json()
    assert sorted(i["title"] for i in data["items"]) == ["P A", "P B"] and data["errors"] == []


def test_a_bonus_category_the_mapping_cannot_handle_fails_alone(monkeypatch):
    real = mapping.bonus_section
    def section(raw, category):
        if category == "B":
            raise TypeError("bug")
        return real(raw, category)
    monkeypatch.setattr(mapping, "bonus_section", section)
    data = _app(OddAH()).test_client().get("/api/bonus").get_json()
    assert [i["title"] for i in data["items"]] == ["P A"]
    assert [(e["category"], e["code"]) for e in data["errors"]] == [("B", "category_failed")]


def test_week_cache_keeps_this_weeks_copy_on_any_failure():
    now = [0.0]
    cache = WeekCache(retry_seconds=10, clock=lambda: now[0], today=lambda: date(2026, 11, 18))
    # A partial copy (one category failed): reloaded after retry_seconds.
    week = BonusWeek(period=Period("2026-11-16", "2026-11-22"), categories=["A"], items=[],
                     errors=[CategoryError("A", "category_failed", "r")])
    assert cache.get(lambda: week) is week
    now[0] = 60

    def broken():
        raise RuntimeError("a bug, not an AHError")
    assert cache.get(broken) is week           # kept, not a 500


# ---- server-wide budgets are never evicted -----------------------------------------------------
def test_shared_budget_survives_many_clients(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_CLIENTS", 50)
    limiter = ratelimit.RateLimiter(clock=lambda: 0.0)
    assert limiter.allow("login", ratelimit.SHARED, 2, 60)
    assert limiter.allow("login", ratelimit.SHARED, 2, 60)
    for i in range(500):                        # e.g. 500 IPv6 /64 networks hitting a cheap endpoint
        limiter.allow("stores", f"2001:db8:{i:x}::/64", 10, 60)
    assert not limiter.allow("login", ratelimit.SHARED, 2, 60)   # still used up: not reset
    assert limiter.size() <= 51


# ---- admin: a pasted "token" that is absurdly nested JSON --------------------------------------
def test_nested_json_token_paste_is_refused_not_crashing():
    app = _app(OddAH())
    c = app.test_client()
    headers = as_admin(c)
    resp = c.post("/admin/tokens", data={"csrf": headers["X-CSRF-Token"], "refresh_token": "{" * 100_000})
    assert resp.status_code == 302


def test_group_members_carry_their_size():
    """Products with the same title are told apart by size (the page shows it)."""
    found = mapping.group_details({"bonusPromotions": [{"productCount": 3, "products": [
        {"id": 1, "title": "AH Pindakaas", "salesUnitSize": "350 g"},
        {"id": 2, "title": "AH Pindakaas", "salesUnitSize": "600 g"},
        {"id": 3, "title": "AH Hagelslag", "salesUnitSize": {"bad": 1}},
    ]}]}, [])
    assert found is not None
    assert [(m.title, m.size) for m in found["products"]] == [
        ("AH Pindakaas", "350 g"), ("AH Pindakaas", "600 g"), ("AH Hagelslag", None)]
