"""Bonus view backend: AH REST bonus pages -> validated items, /api/bonus."""

import http.server
import json
import threading
from datetime import date

import pytest

from ah_dashboard import create_app
from ah_dashboard.ah import mapping, queries
from ah_dashboard.ah.client import AHClient, AHTransportError
from ah_dashboard.ah.gateway import BONUS_METADATA_PATH, BONUS_SECTION_PATH, AHGateway
from ah_dashboard.ah.session import AHSession
from ah_dashboard.container import services
from ah_dashboard.domain import pricing
from ah_dashboard.domain.dates import amsterdam_today
from ah_dashboard.services import bonus as bonus_service

META = {"periods": [
    {"bonusStartDate": "2026-11-16", "bonusEndDate": "2026-11-22", "tabs": [{"urlMetadataList": [
        {"description": "Zuivel", "bonusType": "NATIONAL"},
        {"description": "Vlees", "bonusType": "NATIONAL"},
        {"description": "Persoonlijk", "bonusType": "PERSONAL"},
        {"description": "Zuivel", "bonusType": "NATIONAL"},          # duplicate
    ]}]},
    {"bonusStartDate": "2026-11-23", "bonusEndDate": "2026-11-29", "tabs": [{"urlMetadataList": [
        {"description": "Volgende week", "bonusType": "NATIONAL"}]}]},
]}

IMAGES = [{"url": "https://static.ah.nl/a-200.jpg", "width": 200},
          {"url": "https://static.ah.nl/a-400.jpg", "width": 400},
          {"url": "https://static.ah.nl/a-800.jpg", "width": 800}]

SECTIONS = {
    "Zuivel": {"bonusGroupOrProducts": [
        {"product": {"webshopId": 111, "title": "Yoghurt", "brand": "AH", "salesUnitSize": "500 g",
                     "images": IMAGES, "currentPrice": 1.49, "priceBeforeBonus": 1.99,
                     "bonusMechanism": "25% KORTING", "unknownField": {"x": 1}}},
        {"product": {"webshopId": 111, "title": "Yoghurt", "currentPrice": 1.49}},   # duplicate
        {"bonusGroup": {"id": "9", "segmentDescription": "Alle Hak*", "discountDescription": "2e HALVE PRIJS",
                        "images": [{"url": "http://insecure.example/x.jpg", "width": 400}],
                        "exampleFromPrice": 2.0, "exampleForPrice": 1.5}},
    ]},
    "Vlees": {"bonusGroupOrProducts": [
        {"bonusGroup": {"segmentDescription": "Kip", "discountDescription": "2 VOOR 7.00", "products": [
            {"webshopId": "222", "title": "Kipfilet", "currentPrice": 3.5, "priceBeforeBonus": "4.99"},
            {"webshopId": True, "title": "<b>Drumsticks</b>", "currentPrice": -1},
        ]}},
    ]},
}


class FakeSession:
    configured = True

    def __init__(self, meta=META, sections=SECTIONS, fail=(), groups=None):
        self.meta, self.sections, self.fail = meta, sections, set(fail)
        self.groups = groups or {}   # segment id -> GraphQL data (or an exception to raise)
        self.calls = []

    def query(self, document, variables=None, operation=None):
        self.calls.append((operation, dict(variables or {})))
        assert operation == queries.GROUP_PRODUCTS_OPERATION
        answer = self.groups.get(variables["id"], {"bonusPromotions": []})
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == BONUS_METADATA_PATH:
            if "meta" in self.fail:
                raise AHTransportError("HTTP 500", 500)
            return self.meta
        category = params["category"]
        if category in self.fail:
            raise AHTransportError("HTTP 503", 503)
        return self.sections.get(category, {})


def load(session, today=None):
    """load_bonus through the gateway, as the API's JSON."""
    return bonus_service.load_bonus(AHGateway(session, "AHWEBSHOP"), today=today).to_json()


def section_offers(section, category):
    return [o.to_json() for o in mapping.bonus_section(section, category)]


@pytest.fixture
def bonus_app():
    app = create_app("testing")
    services(app).ah = FakeSession()
    return app


# ---- parsing / validation -----------------------------------------------------------
def test_metadata_current_week_national_categories_only():
    period, categories = mapping.bonus_period(META)
    assert period.to_json() == {"start": "2026-11-16", "end": "2026-11-22"}
    assert categories == ["Zuivel", "Vlees"]


def test_items_are_reduced_to_validated_fields():
    session = FakeSession()
    result = load(session, today=date(2026, 11, 17))
    assert result["errors"] == [] and result["categories"] == ["Zuivel", "Vlees"]
    by_title = {i["title"]: i for i in result["items"]}
    assert len(result["items"]) == 4  # duplicate yoghurt removed

    y = by_title["Yoghurt"]
    assert y == {"kind": "bonus", "id": 111, "title": "Yoghurt", "brand": "AH", "unitSize": "500 g",
                 "image": "https://static.ah.nl/a-400.jpg", "priceNow": 1.49, "priceWas": 1.99, "minQty": None,
                 "priceFrom": False, "productCount": None, "products": [],
                 "mechanism": "25% KORTING", "dealType": "other", "category": "Zuivel", "isGroup": False}

    group = by_title["Alle Hak*"]
    assert group["isGroup"] and group["image"] is None          # http:// image dropped
    assert group["mechanism"] == "2e HALVE PRIJS" and group["priceNow"] == 1.5

    kip = by_title["Kipfilet"]
    assert kip["id"] == 222 and kip["mechanism"] == "2 VOOR 7.00"  # from the group
    assert kip["priceWas"] is None                                       # string price ignored

    drum = by_title["<b>Drumsticks</b>"]                             # escaped by the browser
    assert drum["id"] is None and drum["priceNow"] is None                # bool id / negative price ignored

    # One metadata call, one section call per category with the app's parameters.
    sections = [c for c in session.calls if c[0] == BONUS_SECTION_PATH]
    assert {c[1]["category"] for c in sections} == {"Zuivel", "Vlees"}
    assert all(c[1]["date"] == "2026-11-17" and c[1]["promotionType"] == "NATIONAL"
               and c[1]["application"] == "AHWEBSHOP" for c in sections)


def test_failing_category_does_not_fail_the_rest():
    result = load(FakeSession(fail={"Vlees"}))
    assert [e["category"] for e in result["errors"]] == ["Vlees"]
    assert result["errors"][0]["ref"] and "HTTP 503" not in json.dumps(result)
    assert {i["category"] for i in result["items"]} == {"Zuivel"}


# ---- API ---------------------------------------------------------------------------------
def test_bonus_endpoint_is_cached_and_shared(bonus_app):
    c = bonus_app.test_client()
    first = c.get("/api/bonus").get_json()
    second = c.get("/api/bonus").get_json()
    assert first == second and len(first["items"]) == 4
    metadata_calls = [x for x in services(bonus_app).ah.calls if x[0] == BONUS_METADATA_PATH]
    assert len(metadata_calls) == 1


class _Clock:
    def __init__(self, today):
        self.now, self.day = 1_000_000.0, today

    def time(self):
        return self.now

    def today(self):
        return self.day


def _metadata_calls(app):
    return len([x for x in services(app).ah.calls if x[0] == BONUS_METADATA_PATH])


@pytest.fixture
def week_clock(bonus_app):
    clock = _Clock(date(2026, 11, 17))       # inside the test period 16-22 Nov
    cache = services(bonus_app).bonus_cache
    cache._clock, cache._today = clock.time, clock.today
    return clock


def test_bonus_is_kept_for_the_whole_week(bonus_app, week_clock):
    c = bonus_app.test_client()
    c.get("/api/bonus")
    week_clock.now += 5 * 24 * 3600
    week_clock.day = date(2026, 11, 22)          # Sunday, last day of the period
    c.get("/api/bonus")
    assert _metadata_calls(bonus_app) == 1
    week_clock.day = date(2026, 11, 23)          # new week: reload
    c.get("/api/bonus")
    assert _metadata_calls(bonus_app) == 2


def test_lagging_period_is_retried_after_a_while_not_on_every_request(bonus_app, week_clock):
    week_clock.day = date(2026, 11, 23)          # AH still returns last week's period
    c = bonus_app.test_client()
    c.get("/api/bonus")
    c.get("/api/bonus")
    assert _metadata_calls(bonus_app) == 1
    week_clock.now += 301
    c.get("/api/bonus")
    assert _metadata_calls(bonus_app) == 2


def test_partial_result_is_retried_after_a_while(bonus_app, week_clock):
    session = services(bonus_app).ah = FakeSession(fail={"Vlees"})
    c = bonus_app.test_client()
    assert c.get("/api/bonus").get_json()["errors"]
    session.fail = set()
    assert c.get("/api/bonus").get_json()["errors"]            # kept for a few minutes
    week_clock.now += 301
    assert c.get("/api/bonus").get_json()["errors"] == []      # retried at AH


def test_failed_load_is_not_cached(bonus_app, week_clock):
    session = services(bonus_app).ah = FakeSession(fail={"meta"})
    c = bonus_app.test_client()
    assert c.get("/api/bonus").status_code == 502
    session.fail = set()
    assert c.get("/api/bonus").status_code == 200


@pytest.mark.parametrize("utc, expected", [
    ("2026-11-15T22:30:00+00:00", date(2026, 11, 15)),   # Sun 23:30 CET: still Sunday
    ("2026-11-15T23:30:00+00:00", date(2026, 11, 16)),   # Mon 00:30 CET: new bonus week
    ("2026-07-12T22:30:00+00:00", date(2026, 7, 13)),    # Mon 00:30 CEST (summer time)
    ("2026-03-29T00:59:00+00:00", date(2026, 3, 29)),    # just before summer time starts
    ("2026-10-25T00:30:00+00:00", date(2026, 10, 25)),   # summer time ends 01:00 UTC that day
])
def test_amsterdam_today(utc, expected):
    from datetime import datetime
    assert amsterdam_today(datetime.fromisoformat(utc)) == expected


def test_metadata_failure_gives_generic_error(bonus_app):
    services(bonus_app).ah = FakeSession(fail={"meta"})
    resp = bonus_app.test_client().get("/api/bonus")
    assert resp.status_code == 502
    err = resp.get_json()["error"]
    assert err["code"] == "upstream_error" and err["ref"] and "HTTP 500" not in json.dumps(err)


# ---- REST client -------------------------------------------------------------------------------
class _RestHandler(http.server.BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_GET(self):
        type(self).seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


@pytest.fixture
def rest_url():
    _RestHandler.seen, _RestHandler.status = [], 200
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_rest_get_encodes_params_and_sends_client_headers(rest_url):
    client = AHClient("tok", url="unused", rest_url=rest_url,
                      rest_headers={"x-application": "AHWEBSHOP", "x-client-name": "appie-ios"})
    assert client.get("/mobile-services/bonuspage/v2/section", {"category": "Kaas & vleeswaren?x=1"}) == {"ok": True}
    path, headers = _RestHandler.seen[0]
    assert path == "/mobile-services/bonuspage/v2/section?category=Kaas+%26+vleeswaren%3Fx%3D1"
    assert headers["authorization"] == "Bearer tok" and headers["x-application"] == "AHWEBSHOP"


@pytest.mark.parametrize("bad", ["relative", "//evil.example/x", "/a?b=c"])
def test_rest_get_rejects_unsafe_paths(bad):
    with pytest.raises(ValueError):
        AHClient("tok", url="unused").get(bad)


def test_session_get_renews_token_on_401(rest_url):
    class Tokens:
        configured = True
        token = "old"

        def access_token(self):
            return self.token

        def refresh(self, failed_token):
            self.token = "new"
            return "new"

    tokens = Tokens()

    class Client:
        def __init__(self, token):
            self.token = token

        def get(self, path, params=None):
            if self.token == "old":
                raise AHTransportError("HTTP 401", 401)
            return {"token": self.token}

    assert AHSession(tokens, Client).get("/x") == {"token": "new"}


# ---- bonus prices from discountLabels (shapes as AH sends them, Nov 2026) ----------------------
def _label(code, **kw):
    return [{"code": code, **kw}]


@pytest.mark.parametrize("was, labels, expected", [
    # products: AH sends priceBeforeBonus + discountLabels, no currentPrice
    (3.29, _label("DISCOUNT_X_FOR_Y", count=2, price=4.99), (2.50, 2)),      # 4.99 / 2, rounded up
    (2.19, _label("DISCOUNT_X_FOR_Y", count=2, price=4.0), (2.00, 2)),
    (2.00, _label("DISCOUNT_PERCENTAGE", percentage=25, precisePercentage=25), (1.50, 1)),
    (2.49, _label("DISCOUNT_AMOUNT", amount=1.0), (1.49, 1)),
    (3.00, _label("DISCOUNT_X_PLUS_Y_FREE", count=1, freeCount=1), (1.50, 2)),
    (3.00, _label("DISCOUNT_X_PLUS_Y_FREE", count=2, freeCount=1), (2.00, 3)),
    (2.00, _label("DISCOUNT_ONE_HALF_PRICE", count=2), (1.50, 2)),
    (None, _label("DISCOUNT_FIXED_PRICE", price=0.89), (0.89, 1)),
    # groups: no regular price, so only fixed and X-for-Y deals have a price
    (None, _label("DISCOUNT_X_FOR_Y", count=3, price=6.0), (2.00, 3)),
    (None, _label("DISCOUNT_PERCENTAGE", percentage=30), (None, None)),
    (None, _label("DISCOUNT_AMOUNT", amount=1.0), (None, None)),
    (None, _label("DISCOUNT_X_PLUS_Y_FREE", count=1, freeCount=1), (None, None)),
    # nonsense is ignored
    (2.00, _label("DISCOUNT_PERCENTAGE", percentage=150), (None, None)),
    (2.00, _label("DISCOUNT_AMOUNT", amount=5.0), (None, None)),
    (2.00, _label("DISCOUNT_X_FOR_Y", count=0, price=4.0), (None, None)),
    (2.00, _label("DISCOUNT_X_FOR_Y", count=True, price=4.0), (None, None)),
    (2.00, _label("SOMETHING_NEW", price=1.0), (None, None)),
    (2.00, "not a list", (None, None)),
])
def test_bonus_price_from_labels(was, labels, expected):
    assert pricing.bonus_price(was, labels) == expected


def test_real_shaped_section_gets_prices():
    section = {"bonusGroupOrProducts": [
        {"product": {"webshopId": 471641, "title": "AH Snoepgroente tomaat 500 gram", "priceBeforeBonus": 3.29,
                     "bonusMechanism": "2 voor 4.99", "isBonus": True,
                     "discountLabels": [{"code": "DISCOUNT_X_FOR_Y", "count": 2, "defaultDescription": "2 voor 4.99",
                                         "price": 4.99}]}},
        {"bonusGroup": {"id": "819859", "segmentDescription": "Hak witte bonen", "discountDescription": "VOOR 0.89",
                        "products": [], "discountLabels": [{"code": "DISCOUNT_FIXED_PRICE", "price": 0.89}]}},
        {"bonusGroup": {"id": "819899", "segmentDescription": "Alle AH Gerookte zalm", "discountDescription": "25% KORTING",
                        "products": [], "discountLabels": [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]}},
    ]}
    tomaat, bonen, zalm = section_offers(section, "Groente")
    assert (tomaat["priceNow"], tomaat["priceWas"], tomaat["minQty"]) == (2.50, 3.29, 2)
    assert (bonen["priceNow"], bonen["priceWas"], bonen["minQty"], bonen["isGroup"]) == (0.89, None, None, True)
    assert (zalm["priceNow"], zalm["priceWas"], zalm["mechanism"]) == (None, None, "25% KORTING")


def test_products_in_a_group_use_the_group_label():
    section = {"bonusGroupOrProducts": [{"bonusGroup": {
        "segmentDescription": "Kaas", "discountDescription": "25% KORTING",
        "discountLabels": [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}],
        "products": [{"webshopId": 1, "title": "Goudse", "priceBeforeBonus": 4.0}]}}]}
    (item,) = section_offers(section, "Kaas")
    assert (item["priceNow"], item["priceWas"], item["mechanism"]) == (3.0, 4.0, "25% KORTING")


# ---- group deals: products looked up via GraphQL bonusPromotions(id) -------------------------------
def _gql_products(*products, count=None):
    items = [{"id": i, "title": f"P{i}", "priceV2": {"now": {"amount": now} if now is not None else None,
                                                     "was": {"amount": was} if was is not None else None},
              "imagePack": [{"medium": {"url": f"https://static.ah.nl/p{i}.jpg"}}]}
             for i, (now, was) in enumerate(products, 1)]
    return {"bonusPromotions": [{"id": "x", "productCount": count or len(items), "products": items}]}


def _group_section(seg_id, desc, labels):
    return {"bonusGroupOrProducts": [{"bonusGroup": {"id": seg_id, "segmentDescription": "Alle Groep",
                                                     "discountDescription": desc, "products": [],
                                                     "discountLabels": labels}}]}


def _load_group(labels, desc, answer):
    meta = {"periods": [{"bonusStartDate": "2026-11-16", "bonusEndDate": "2026-11-22",
                         "tabs": [{"urlMetadataList": [{"description": "Cat", "bonusType": "NATIONAL"}]}]}]}
    session = FakeSession(meta=meta, sections={"Cat": _group_section("819899", desc, labels)},
                          groups={"819899": answer})
    result = load(session)
    (item,) = result["items"]
    return item, session, result


def test_percentage_group_gets_cheapest_product_price_as_from_price():
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    # AH's priceV2: regular price only ("now") for one product, now/was for the other.
    # 25% off: P1 4.00 -> 3.00, P2 regular 3.00 -> 2.25 (cheapest).
    item, session, result = _load_group(labels, "25% KORTING", _gql_products((4.00, None), (2.25, 3.00)))
    assert (item["priceNow"], item["priceWas"], item["priceFrom"], item["productCount"]) == (2.25, 3.00, True, 2)
    assert item["image"] == "https://static.ah.nl/p1.jpg"
    assert "segment" not in item and "labels" not in item            # internal fields never sent
    op, variables = [c for c in session.calls if c[0] == queries.GROUP_PRODUCTS_OPERATION][0]
    assert variables["id"] == "819899" and variables["periodStart"] == "2026-11-16"
    assert variables["periodEnd"] == "2026-11-22" and result["errors"] == []


def test_x_plus_y_free_group_single_product():
    labels = [{"code": "DISCOUNT_X_PLUS_Y_FREE", "count": 1, "freeCount": 1}]
    item, *_ = _load_group(labels, "1 + 1 GRATIS", _gql_products((2.98, None)))
    assert (item["priceNow"], item["priceWas"], item["minQty"], item["priceFrom"]) == (1.49, 2.98, 2, False)


def test_fixed_price_group_keeps_deal_price_and_adds_regular_price():
    labels = [{"code": "DISCOUNT_FIXED_PRICE", "price": 0.89}]
    item, *_ = _load_group(labels, "VOOR 0.89", _gql_products((1.29, None), (1.19, None)))
    assert (item["priceNow"], item["priceWas"], item["productCount"]) == (0.89, 1.19, 2)
    assert item["priceFrom"] is False   # every product costs 0.89: no "from"


def test_unknown_deal_type_uses_ahs_own_bonus_price():
    labels = [{"code": "SOMETHING_NEW"}]
    item, *_ = _load_group(labels, "?", _gql_products((1.50, 2.00)))
    assert (item["priceNow"], item["priceWas"]) == (1.50, 2.00)


def test_failed_group_lookup_keeps_card_and_does_not_mark_partial():
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    item, _, result = _load_group(labels, "25% KORTING", AHTransportError("HTTP 500", 500))
    assert (item["priceNow"], item["priceWas"], item["title"]) == (None, None, "Alle Groep")
    assert result["errors"] == []      # only this card lacks a price; the result is still cached


@pytest.mark.parametrize("answer", [{}, {"bonusPromotions": None}, {"bonusPromotions": [{"products": "x"}]},
                                    _gql_products((None, None)), _gql_products((-1, "4.00"))])
def test_odd_group_answers_are_ignored(answer):
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    item, *_ = _load_group(labels, "25% KORTING", answer)
    assert item["priceNow"] is None and item["priceWas"] is None


def test_deal_type_from_discount_labels():
    from ah_dashboard.domain.pricing import deal_type
    assert deal_type([{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]) == "percent"
    assert deal_type([{"code": "DISCOUNT_AMOUNT"}]) == "amount"
    assert deal_type([{"code": "DISCOUNT_FIXED_PRICE"}]) == "fixed"
    for code in ("DISCOUNT_X_FOR_Y", "DISCOUNT_X_PLUS_Y_FREE", "DISCOUNT_ONE_HALF_PRICE"):
        assert deal_type([{"code": code}]) == "multi"
    assert deal_type([{"code": "SOMETHING_NEW"}]) == deal_type(None) == deal_type("x") == "other"


# ---- group deals list their products (for liking/hiding the whole group) ----------------------------
def test_group_lists_its_products_with_webshop_ids():
    labels = [{"code": "DISCOUNT_X_FOR_Y", "count": 2, "price": 12.00}]
    answer = _gql_products((6.99, None), (7.49, None), (6.49, None))
    answer["bonusPromotions"][0]["products"].append({"id": 2, "title": "P2 again"})     # duplicate id
    answer["bonusPromotions"][0]["products"].append({"id": "x", "title": "bad id"})     # not a webshop id
    answer["bonusPromotions"][0]["products"].append({"id": 9, "title": ""})             # no title
    item, *_ = _load_group(labels, "2 VOOR 12.00", answer)
    assert item["isGroup"] and item["priceNow"] == 6.00          # X voor Y: price known from the deal
    assert item["products"] == [{"id": 1, "title": "P1"}, {"id": 2, "title": "P2"}, {"id": 3, "title": "P3"}]


def test_group_products_listed_even_without_prices():
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    item, *_ = _load_group(labels, "25% KORTING", _gql_products((None, None), (None, None)))
    assert item["priceNow"] is None and [p["id"] for p in item["products"]] == [1, 2]


def test_group_without_lookup_has_empty_product_list():
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    item, *_ = _load_group(labels, "25% KORTING", AHTransportError("HTTP 500", 500))
    assert item["products"] == []


def test_group_product_list_is_capped():
    labels = [{"code": "DISCOUNT_PERCENTAGE", "percentage": 25}]
    many = _gql_products(*[(1.0, None)] * (mapping.MAX_GROUP_PRODUCTS + 20))
    item, *_ = _load_group(labels, "25% KORTING", many)
    assert len(item["products"]) == mapping.MAX_GROUP_PRODUCTS
