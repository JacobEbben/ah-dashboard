"""Domain models and their JSON form (the API contract).

One :class:`Offer` shape serves both views: a bargain (a product marked down
in one store) and a Bonus offer (this week's national deal) share the product
fields and prices; each adds its own part (:class:`BargainInfo` /
:class:`BonusInfo`). ``Offer.to_json()`` flattens it::

    common   kind ("bargain" | "bonus"), id, title, brand, unitSize, image,
             category, priceNow, priceWas
    bargain  storeId, storeName, stock, discount, expires, markdownType
    bonus    dealType, mechanism, minQty, priceFrom, productCount, isGroup,
             products [{id, title, size}]

``id`` is AH's webshop product id (the same in both views, so liking a
product works across them); a Bonus group deal has no id and lists its
products instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OfferKind = Literal["bargain", "bonus"]


@dataclass(frozen=True, slots=True)
class StoreRef:
    """A store as the page asks for it (its own display name)."""
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Store:
    """A store from AH's store search."""
    id: int | None
    name: str | None
    store_type: str | None = None
    street: str | None = None
    house_number: Any = None
    house_number_extra: str | None = None
    postal_code: str | None = None
    city: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "storeType": self.store_type, "street": self.street,
                "houseNumber": self.house_number, "houseNumberExtra": self.house_number_extra,
                "postalCode": self.postal_code, "city": self.city}


@dataclass(frozen=True, slots=True)
class GroupMember:
    id: int
    title: str
    size: str | None = None      # e.g. "500 g": tells apart products with the same title


@dataclass(slots=True)
class BargainInfo:
    discount: float | None = None        # percentage off
    expires: str | None = None           # "best before" date of the markdown (as AH sends it)
    markdown_type: str | None = None
    stock: float | None = None
    store_id: int | None = None          # set per request (the visitor's own store names)
    store_name: str | None = None


@dataclass(slots=True)
class BonusInfo:
    deal_type: str = "other"             # "percent" | "amount" | "fixed" | "multi" | "other"
    mechanism: str = ""                  # AH's deal text, e.g. "2e HALVE PRIJS"
    min_qty: int | None = None           # buy this many for priceNow (multi-buy deals)
    price_from: bool = False             # priceNow is the cheapest of several products
    product_count: int | None = None
    is_group: bool = False               # a group deal ("Alle Hak") standing for its products
    products: list[GroupMember] = field(default_factory=list)
    # Internal, never sent: what the group lookup needs.
    segment: str | None = None
    labels: list = field(default_factory=list)


@dataclass(slots=True)
class Offer:
    kind: OfferKind
    id: int | None
    title: str | None
    brand: str | None = None
    unit_size: str | None = None
    image: str | None = None
    category: str | None = None
    price_now: float | None = None
    price_was: float | None = None
    bargain: BargainInfo | None = None
    bonus: BonusInfo | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind, "id": self.id, "title": self.title, "brand": self.brand,
            "unitSize": self.unit_size, "image": self.image, "category": self.category,
            "priceNow": self.price_now, "priceWas": self.price_was,
        }
        if (b := self.bargain) is not None:
            out.update(storeId=b.store_id, storeName=b.store_name, stock=b.stock, discount=b.discount,
                       expires=b.expires, markdownType=b.markdown_type)
        if (d := self.bonus) is not None:
            out.update(dealType=d.deal_type, mechanism=d.mechanism, minQty=d.min_qty, priceFrom=d.price_from,
                       productCount=d.product_count, isGroup=d.is_group,
                       products=[{"id": m.id, "title": m.title, "size": m.size} for m in d.products])
        return out


@dataclass(frozen=True, slots=True)
class Period:
    start: str | None    # ISO dates (Dutch calendar)
    end: str | None

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class CategoryError:
    category: str
    code: str            # "category_failed" | "ah_auth_failed"
    ref: str             # error log reference

    def to_json(self) -> dict[str, Any]:
        return {"category": self.category, "code": self.code, "ref": self.ref}


@dataclass(slots=True)
class BonusWeek:
    """This week's national Bonus: its period, categories (AH's order) and offers."""
    period: Period
    categories: list[str]
    items: list[Offer]
    errors: list[CategoryError] = field(default_factory=list)   # categories that failed to load
    groups_failed: int = 0     # group deals whose products could not be looked up (not sent to the page)

    @property
    def complete(self) -> bool:
        return not self.errors and not self.groups_failed

    def to_json(self) -> dict[str, Any]:
        return {"period": self.period.to_json(), "categories": list(self.categories),
                "items": [o.to_json() for o in self.items], "errors": [e.to_json() for e in self.errors]}

    def to_stored(self) -> dict[str, Any]:
        """The form kept in the database (services/bonus.BonusStore): the page's form plus the gaps."""
        return {**self.to_json(), "groupsFailed": self.groups_failed}


# ---- reading a stored BonusWeek back (our own to_stored() output) ----------------------------
# Strict: anything of an unexpected type means the copy is not used (the week is then
# loaded from AH again), so a damaged or older row can never reach the page.
_DEAL_TYPES = {"percent", "amount", "fixed", "multi", "other"}


def _text(v: Any, *, required: bool = False) -> str | None:
    if v is None and not required:
        return None
    if not isinstance(v, str):
        raise ValueError("text expected")
    return v


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ValueError("number expected")
    return float(v)


def _int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("integer expected")
    return v


def _list(v: Any) -> list:
    if not isinstance(v, list):
        raise ValueError("list expected")
    return v


def _stored_offer(o: Any) -> Offer:
    if not isinstance(o, dict) or o.get("kind") != "bonus":
        raise ValueError("bonus offer expected")
    deal = o.get("dealType")
    if deal not in _DEAL_TYPES:
        raise ValueError("deal type")
    products = []
    for p in _list(o.get("products", [])):
        if not isinstance(p, dict):
            raise ValueError("product expected")
        pid, title = _int(p.get("id")), _text(p.get("title"), required=True)
        if pid is None or title is None:
            raise ValueError("product")
        products.append(GroupMember(pid, title, _text(p.get("size"))))
    price_from, is_group = o.get("priceFrom"), o.get("isGroup")
    if not isinstance(price_from, bool) or not isinstance(is_group, bool):
        raise ValueError("flag expected")
    return Offer(
        kind="bonus", id=_int(o.get("id")), title=_text(o.get("title")), brand=_text(o.get("brand")),
        unit_size=_text(o.get("unitSize")), image=_text(o.get("image")), category=_text(o.get("category")),
        price_now=_num(o.get("priceNow")), price_was=_num(o.get("priceWas")),
        bonus=BonusInfo(deal_type=str(deal), mechanism=_text(o.get("mechanism")) or "",
                        min_qty=_int(o.get("minQty")), price_from=price_from,
                        product_count=_int(o.get("productCount")), is_group=is_group, products=products))


def bonus_week_from_stored(data: Any) -> BonusWeek | None:
    """The BonusWeek written by ``to_stored()``, or None when ``data`` is not exactly that."""
    try:
        if not isinstance(data, dict) or not isinstance(data.get("period"), dict):
            raise ValueError("week expected")
        period = Period(_text(data["period"].get("start")), _text(data["period"].get("end")))
        categories = [c for c in _list(data.get("categories")) if _text(c, required=True)]
        items = [_stored_offer(o) for o in _list(data.get("items"))]
        errors = []
        for e in _list(data.get("errors")):
            if not isinstance(e, dict):
                raise ValueError("error expected")
            errors.append(CategoryError(str(_text(e.get("category"), required=True)),
                                        str(_text(e.get("code"), required=True)),
                                        str(_text(e.get("ref"), required=True))))
        groups_failed = _int(data.get("groupsFailed", 0)) or 0
        if groups_failed < 0:
            raise ValueError("groupsFailed")
        return BonusWeek(period=period, categories=categories, items=items, errors=errors,
                         groups_failed=groups_failed)
    except (ValueError, KeyError, TypeError):
        return None
