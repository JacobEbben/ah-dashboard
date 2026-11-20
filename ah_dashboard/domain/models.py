"""Domain models and their JSON form (the API contract).

One :class:`Offer` shape serves both views: a bargain (a product marked down
in one store) and a Bonus offer (this week's national deal) share the product
fields and prices; each adds its own part (:class:`BargainInfo` /
:class:`BonusInfo`). ``Offer.to_json()`` flattens it::

    common   kind ("bargain" | "bonus"), id, title, brand, unitSize, image,
             category, priceNow, priceWas
    bargain  storeId, storeName, stock, discount, expires, markdownType
    bonus    dealType, mechanism, minQty, priceFrom, productCount, isGroup,
             products [{id, title}]

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
                       products=[{"id": m.id, "title": m.title} for m in d.products])
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

    def to_json(self) -> dict[str, Any]:
        return {"period": self.period.to_json(), "categories": list(self.categories),
                "items": [o.to_json() for o in self.items], "errors": [e.to_json() for e in self.errors]}
