"""A local test environment: the app's testing profile with canned AH data.

Never contacts AH or the login service and never reads .env. Run it in a
throwaway container with the repo mounted read-only (static files are served
live from the mount, so JS/CSS edits show after a browser reload):

    docker run -d --name ah-testenv --restart unless-stopped -p 127.0.0.1:8799:8799 \\
      -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \\
      sh -c 'pip install -q -r requirements.txt && python tools/testenv.py'

Then open http://127.0.0.1:8799/, search postal code 1000AA and follow the
stores. Scenario switches (plain GET links, test environment only):

    /testenv/spread   product 501 ("Testkaas") also becomes a bargain at store 1002
    /testenv/reset    back to the start: product 501 only at store 1001
    /testenv          shows the current scenario

"Testkaas" is meant for the cart: add it at Centrum, open /testenv/spread,
reload the page, and the cart should list it under Noord too.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, jsonify
from waitress import serve

from ah_dashboard import create_app
from ah_dashboard.ah import queries
from ah_dashboard.ah.gateway import BONUS_METADATA_PATH
from ah_dashboard.container import services
from ah_dashboard.domain.dates import amsterdam_today

IMG = "https://static.ah.nl/dam/product/AHI_test?revLabel=1&rendition=200x200_JPG_Q85&fileType=binary"
STORES = [
    (1001, "AH Centrum", "Damstraat", "1", "1000AA", "Teststad"),
    (1002, "AH Noord", "Noordweg", "12", "1000AB", "Teststad"),
    (1003, "AH Zuid", "Zuidlaan", "7", "1000AC", "Teststad"),
]


def _bargain(pid: int, title: str, category: str, now: str, was: str, pct: int, stock: int, days: int = 1) -> dict:
    """``days``: how many days from today its last day is (0 = ends today)."""
    expires = (amsterdam_today() + timedelta(days=days)).isoformat()
    return {
        "product": {"id": pid, "title": title, "brand": "AH", "salesUnitSize": "500 g",
                    "imagePack": [{"medium": {"url": IMG}}]},
        "categoryTitle": category,
        "bargainPrice": {"priceNow": now, "priceWas": was},   # text, like AH
        "markdown": {"markdownPercentage": pct, "markdownExpirationDate": expires, "markdownType": "BARGAIN"},
        "stock": stock,
    }


CHEESE = 501   # the product that spreads to a second store (/testenv/spread)


def bargains_for(store_id: str, spread: bool) -> list[dict]:
    base = {
        "1001": [_bargain(CHEESE, "Testkaas jong belegen", "Kaas", "2.50", "4.99", 50, 3),
                 _bargain(502, "Volkorenbrood", "Brood", "1.20", "2.39", 50, 2, days=0),
                 _bargain(503, "Kipfilet", "Vlees", "3.89", "5.99", 35, 4)],
        "1002": [_bargain(504, "Halfvolle melk", "Zuivel", "0.75", "1.49", 50, 6, days=0),
                 _bargain(503, "Kipfilet", "Vlees", "4.49", "5.99", 25, 1)],
        "1003": [_bargain(505, "Appeltaart", "Gebak", "2.99", "5.99", 50, 1, days=0),
                 _bargain(506, "Bananen", "Fruit", "0.99", "1.99", 0, 8)],
    }
    items = base.get(store_id, [])
    if spread and store_id == "1002":
        items = [*items, _bargain(CHEESE, "Testkaas jong belegen", "Kaas", "1.99", "4.99", 60, 2)]
    return items


def bonus_meta() -> dict:
    monday = amsterdam_today() - timedelta(days=amsterdam_today().weekday())

    def period(start: date) -> dict:
        return {"bonusStartDate": start.isoformat(), "bonusEndDate": (start + timedelta(days=6)).isoformat(),
                "tabs": [{"urlMetadataList": [{"description": c, "bonusType": "NATIONAL"}
                                              for c in ("Zuivel", "Kaas", "Brood")]}]}
    return {"periods": [period(monday), period(monday + timedelta(days=7))]}


def bonus_section(category: str, next_week: bool) -> dict:
    def product(pid: int, title: str, before: float, label: dict, mechanism: str) -> dict:
        return {"product": {"webshopId": pid, "title": title, "brand": "AH", "salesUnitSize": "1 st",
                            "images": [{"url": IMG, "width": 400}], "priceBeforeBonus": before,
                            "discountLabels": [label], "bonusMechanism": mechanism}}
    if next_week:
        return {"bonusGroupOrProducts": [product(601, f"Volgende week {category}", 3.00,
                                                 {"code": "DISCOUNT_PERCENTAGE", "percentage": 25}, "25% KORTING")]}
    sections = {
        "Zuivel": [product(504, "Halfvolle melk", 1.49, {"code": "DISCOUNT_ONE_HALF_PRICE"}, "2e HALVE PRIJS")],
        "Kaas": [product(CHEESE, "Testkaas jong belegen", 4.99,
                         {"code": "DISCOUNT_PERCENTAGE", "percentage": 25}, "25% KORTING")],
        "Brood": [product(507, "Croissants", 2.49, {"code": "DISCOUNT_X_FOR_Y", "count": 2, "price": 4.0},
                          "2 VOOR 4.00")],
    }
    return {"bonusGroupOrProducts": sections.get(category, [])}


class FakeAH:
    """Stands in for AHSession: bargains, store search, the Bonus pages."""

    configured = True

    def __init__(self) -> None:
        self.spread = False

    def query(self, document, variables=None, operation=None):
        variables = variables or {}
        if operation == "BargainItems":
            return {"bargainItems": bargains_for(str(variables.get("storeId")), self.spread)}
        if operation == "StoresSearch":
            return {"storesSearch": {"result": [
                {"id": sid, "name": name, "storeType": "REGULAR",
                 "address": {"street": street, "houseNumber": no, "postalCode": pc, "city": city}}
                for sid, name, street, no, pc, city in STORES]}}
        if operation == queries.GROUP_PRODUCTS_OPERATION:
            return {"bonusPromotions": []}
        raise RuntimeError(f"testenv: unexpected operation {operation}")

    def get(self, path, params=None):
        if path == BONUS_METADATA_PATH:
            return bonus_meta()
        params = params or {}
        start = date.fromisoformat(bonus_meta()["periods"][1]["bonusStartDate"])
        next_week = str(params.get("date", "")) == start.isoformat()
        return bonus_section(str(params.get("category", "")), next_week)


def build():
    app = create_app("testing")
    fake = FakeAH()
    svc = services(app)
    svc.ah = fake
    svc.known_stores.add([sid for sid, *_ in STORES])

    bp = Blueprint("testenv", __name__)

    def state():
        return jsonify(scenario="spread" if fake.spread else "start",
                       cheese_stores=[1001, 1002] if fake.spread else [1001])

    @bp.get("/testenv")
    def show():
        return state()

    @bp.get("/testenv/spread")
    def spread():
        fake.spread = True
        svc.bargains_cache.clear()
        return state()

    @bp.get("/testenv/reset")
    def reset():
        fake.spread = False
        svc.bargains_cache.clear()
        return state()

    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8799"))
    print(f"testenv on http://127.0.0.1:{port}/ (canned AH data)", flush=True)
    serve(build(), host="0.0.0.0", port=port, threads=8, ident="")
