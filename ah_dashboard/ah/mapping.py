"""AH's answers -> domain models (the anti-corruption layer).

Everything from AH is untrusted: only whitelisted fields with validated
types and sizes become a model; anything else is dropped. Nothing from AH
reaches the browser except through these functions.

Bargains (GraphQL ``bargainItems``): prices arrive as TEXT ("1.09"; appie-go
koopjes.go: PriceNow string). Bonus (REST bonus pages): no current price, it
is calculated from the discount labels (``domain.pricing``). Group deals
("Alle Hak*") come without products; their products and prices come from a
separate lookup (:func:`group_details`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..domain.models import BargainInfo, BonusInfo, GroupMember, Offer, Period, Store
from ..domain.pricing import bonus_price, deal_type
from .client import AHError

MAX_TEXT = 200
MAX_GROUP_PRODUCTS = 200   # products listed per group deal
MAX_PRODUCT_COUNT = 10_000  # a group deal's product count beyond this is not believable


class AHDataError(AHError):
    """AH answered with data the mapping could not handle. An AHError, so it
    is contained like any AH failure: that one store / category / group fails
    (logged with a reference, remembered by the error cache), never the whole
    response, and the Bonus keeps this week's copy."""


def guarded[T](what: str, fn: Callable[..., T], *args: Any) -> T:
    """Run a mapping function; any unexpected error becomes AHDataError.
    The validators are meant never to raise; this is the safety net."""
    try:
        return fn(*args)
    except AHError:
        raise
    except Exception as exc:
        raise AHDataError(f"unusable {what} from AH: {type(exc).__name__}: {exc}"[:300]) from exc


# ---- validated scalars ---------------------------------------------------------------------
def text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Trimmed, length-capped text from a string or number; None if empty."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value).strip()[:limit] or None


def bounded(value: Any, low: float, high: float) -> float | None:
    """A number within [low, high] (no booleans, no strings)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if low <= value <= high else None


def money(value: Any) -> float | None:
    """A price in euros sent as a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:  # NaN
        return None
    return round(float(value), 2) if 0 <= value < 100_000 else None


def money_text(value: Any) -> float | None:
    """A price in euros sent as text ("1.09", "1,09", "€ 1,09") or a number."""
    if isinstance(value, str):
        cleaned = value.replace("€", "").replace(" ", "").strip().replace(",", ".")
        try:
            value = float(cleaned)
        except ValueError:
            return None
    return money(value)


def product_id(value: Any) -> int | None:
    """AH's webshop product id (int or digit string, up to 10 digits)."""
    if isinstance(value, bool):
        return None
    # ASCII only: str.isdigit() also accepts "²" (int() raises) and other
    # scripts' digits ("١٢٣" would silently become product 123).
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 10:
        value = int(value)
    return value if isinstance(value, int) and 0 < value < 10**10 else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def image_from_pack(pack: Any) -> str | None:
    """The first https ``medium`` image URL of a GraphQL imagePack."""
    for img in pack if isinstance(pack, list) else []:
        url = _dict(_dict(img).get("medium")).get("url")
        if isinstance(url, str) and url.startswith("https://") and len(url) <= 500:
            return url
    return None


def image_from_list(images: Any) -> str | None:
    """A medium-sized (>= 300 px wide if possible) https image URL from a REST image list."""
    usable = []
    for img in images if isinstance(images, list) else []:
        url = _dict(img).get("url")
        if isinstance(url, str) and url.startswith("https://") and len(url) <= 500:
            width = img.get("width") if isinstance(img.get("width"), int) else 0
            usable.append((width, url))
    if not usable:
        return None
    usable.sort()
    for width, url in usable:
        if width >= 300:
            return url
    return usable[-1][1]


# ---- stores ----------------------------------------------------------------------------------
def store(raw: Any) -> Store | None:
    st = _dict(raw)
    if not st:
        return None
    address = _dict(st.get("address"))
    number = address.get("houseNumber")
    return Store(
        id=int(st["id"]) if product_id(st.get("id")) else None,
        name=text(st.get("name"), 100),
        store_type=text(st.get("storeType"), 40),
        street=text(address.get("street"), 100),
        house_number=number if isinstance(number, (int, str)) and not isinstance(number, bool) else None,
        house_number_extra=text(address.get("houseNumberExtra"), 20),
        postal_code=text(address.get("postalCode"), 10),
        city=text(address.get("city"), 100),
    )


def stores(raw: Any) -> list[Store]:
    return [s for s in map(store, raw if isinstance(raw, list) else []) if s is not None]


# ---- bargains --------------------------------------------------------------------------------
def bargain(raw: Any) -> Offer | None:
    """One bargain item; None if it is not an object."""
    if not isinstance(raw, dict):
        return None
    p, md, bp = _dict(raw.get("product")), _dict(raw.get("markdown")), _dict(raw.get("bargainPrice"))
    return Offer(
        kind="bargain",
        id=product_id(p.get("id")),
        title=text(p.get("title")),
        brand=text(p.get("brand"), 80),
        unit_size=text(p.get("salesUnitSize"), 60),
        image=image_from_pack(p.get("imagePack")),
        category=text(raw.get("categoryTitle"), 100),
        price_now=money_text(bp.get("priceNow")),
        price_was=money_text(bp.get("priceWas")),
        bargain=BargainInfo(
            discount=bounded(md.get("markdownPercentage"), 0, 100),
            expires=text(md.get("markdownExpirationDate"), 30),
            markdown_type=text(md.get("markdownType"), 40),
            stock=bounded(raw.get("stock"), 0, 1_000_000),
        ),
    )


def bargains(raw: Any) -> list[Offer]:
    return [o for o in map(bargain, raw if isinstance(raw, list) else []) if o is not None]


# ---- the national Bonus -------------------------------------------------------------------------
def bonus_period(meta: Any) -> tuple[Period, list[str]]:
    """(period of the current week, its national bonus categories). Raises
    AHError when the metadata has no periods."""
    periods = meta.get("periods") if isinstance(meta, dict) else None
    if not isinstance(periods, list) or not periods or not isinstance(periods[0], dict):
        raise AHError("bonus metadata without periods")
    current = periods[0]
    period = Period(start=text(current.get("bonusStartDate"), 10), end=text(current.get("bonusEndDate"), 10))
    categories: list[str] = []
    for tab in _list(current.get("tabs")):
        for item in _list(tab.get("urlMetadataList")) if isinstance(tab, dict) else []:
            if not isinstance(item, dict) or item.get("bonusType") != "NATIONAL":
                continue
            name = text(item.get("description"), 100)
            if name and name not in categories:
                categories.append(name)
    return period, categories


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


_labels = _list


def _bonus_prices(raw: dict, was_key: str, now_key: str, labels: Any) -> tuple[float | None, float | None, int | None]:
    """(now, was, minQty): AH's prices, or the bonus price from the labels."""
    was, now = money(raw.get(was_key)), money(raw.get(now_key))
    min_qty = None
    if now is None:
        now, min_qty = bonus_price(was, labels)
    return now, was, (min_qty if min_qty and min_qty > 1 else None)


def bonus_product(p: dict, category: str, fallback_mechanism: str = "", fallback_labels: Any = None) -> Offer | None:
    title = text(p.get("title"))
    if not title:
        return None
    labels = p.get("discountLabels") if isinstance(p.get("discountLabels"), list) else fallback_labels
    now, was, min_qty = _bonus_prices(p, "priceBeforeBonus", "currentPrice", labels)
    return Offer(
        kind="bonus", id=product_id(p.get("webshopId")), title=title,
        brand=text(p.get("brand"), 80), unit_size=text(p.get("salesUnitSize"), 60),
        image=image_from_list(p.get("images")), category=category, price_now=now, price_was=was,
        bonus=BonusInfo(deal_type=deal_type(labels), min_qty=min_qty,
                        mechanism=text(p.get("bonusMechanism"), 80) or fallback_mechanism),
    )


def bonus_group(g: dict, category: str) -> Offer | None:
    """A group deal without its products (filled in by :func:`group_details`)."""
    title = text(g.get("segmentDescription"))
    if not title:
        return None
    labels = _labels(g.get("discountLabels"))
    now, was, min_qty = _bonus_prices(g, "exampleFromPrice", "exampleForPrice", labels)
    segment = text(g.get("id"), 20)
    return Offer(
        kind="bonus", id=None, title=title, image=image_from_list(g.get("images")), category=category,
        price_now=now, price_was=was,
        bonus=BonusInfo(deal_type=deal_type(labels), min_qty=min_qty, is_group=True,
                        mechanism=text(g.get("discountDescription"), 80) or "",
                        segment=segment if segment and segment.isdigit() else None, labels=labels),
    )


def bonus_section(section: Any, category: str) -> list[Offer]:
    """The offers of one bonus section (deduplicated by id + title)."""
    items: list[Offer] = []
    seen: set[tuple[int | None, str | None]] = set()
    entries = section.get("bonusGroupOrProducts") if isinstance(section, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        found: list[Offer | None] = []
        if isinstance(entry.get("product"), dict):
            found.append(bonus_product(entry["product"], category))
        group = entry.get("bonusGroup")
        if isinstance(group, dict):
            products = group.get("products")
            if isinstance(products, list) and products:
                mechanism = text(group.get("discountDescription"), 80) or ""
                found.extend(bonus_product(p, category, mechanism, group.get("discountLabels"))
                             for p in products if isinstance(p, dict))
            else:
                found.append(bonus_group(group, category))
        for item in found:
            if item is None:
                continue
            key = (item.id, item.title)
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items


def group_details(data: Any, labels: Any) -> dict[str, Any] | None:
    """What a group's product lookup (GraphQL ``bonusPromotions``) adds: the
    cheapest product's bonus price as "from" price, the regular price, the
    product count, an image and the products themselves. Keys: ``now``,
    ``was``, ``minQty``, ``priceFrom``, ``productCount``, ``image``,
    ``products`` (only those known). None when the answer has no product list."""
    promos = data.get("bonusPromotions") if isinstance(data, dict) else None
    promo = promos[0] if isinstance(promos, list) and promos and isinstance(promos[0], dict) else None
    products = promo.get("products") if promo else None
    if promo is None or not isinstance(products, list):
        return None
    best: tuple[float, float | None, float, int | None] | None = None
    image: str | None = None
    members: list[GroupMember] = []
    seen: set[int] = set()
    for p in products:
        if not isinstance(p, dict):
            continue
        pid, title = product_id(p.get("id")), text(p.get("title"))
        if pid and title and pid not in seen and len(members) < MAX_GROUP_PRODUCTS:
            seen.add(pid)
            members.append(GroupMember(pid, title))
        image = image or image_from_pack(p.get("imagePack"))
        price = _dict(p.get("priceV2"))
        now = money(_dict(price.get("now")).get("amount"))
        was = money(_dict(price.get("was")).get("amount"))
        regular = was or now  # without a "was", AH's "now" is the regular price
        if not regular:
            continue
        bonus, qty = bonus_price(regular, labels)
        if bonus is None and was and now and now < was:
            bonus, qty = now, 1  # AH's own bonus price for a deal type we cannot calculate
        candidate = (bonus if bonus is not None else regular, bonus, regular, qty)
        # Cheapest bonus price; on a tie the lower regular price, so the
        # struck-through price never overstates the saving.
        if best is None or (candidate[0], candidate[2]) < (best[0], best[2]):
            best = candidate
    given = bounded(promo.get("productCount"), 0, MAX_PRODUCT_COUNT)
    count: int = int(given) if isinstance(given, int) else len(products)
    if best is None:
        if not (image or count or members):
            return None
        return {"productCount": count or None, "image": image, "products": members}
    _, bonus, regular, qty = best
    return {
        "now": bonus,
        "was": regular if bonus is not None else None,
        "minQty": qty if qty and qty > 1 else None,
        "priceFrom": count > 1,
        "productCount": count or None,
        "image": image,
        "products": members,
    }


def apply_group_details(offer: Offer, found: dict[str, Any]) -> None:
    """Fill a group deal from :func:`group_details`."""
    info = offer.bonus
    assert info is not None
    image = found.get("image")
    if not offer.image and image:
        offer.image = image
    if offer.price_now is not None and "now" in found:
        # Price already known from the deal (fixed price / X voor Y): the
        # same for every product, so no "from"; add regular price + count.
        was = found["was"]
        if was is None or was > offer.price_now:
            offer.price_was = was
    elif "now" in found:
        offer.price_now, offer.price_was = found["now"], found["was"]
        info.min_qty, info.price_from = found["minQty"], found["priceFrom"]
    info.product_count = found.get("productCount")
    info.products = found.get("products") or []
