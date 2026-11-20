"""The MCP tools: what an AI assistant can ask (all read-only, public data).

find_stores    AH stores near a Dutch postal code (their ids are needed for bargains)
get_bargains   the "Laatste Kans Koopjes" of up to MAX_STORES stores, filtered
get_bonus      this week's national Bonus, filtered

Each tool goes through the same services as the dashboard's API (caches, the
per-client rate limits of its endpoint group, the store-load budgets), so an
assistant can't make the server ask AH more than a browser could. Arguments
are checked here; a wrong argument is a tool error the assistant can read and
correct (:class:`ToolError`), not a protocol error.

Pure helpers (``filter_*``, ``matches``) take and return plain offers and
are tested without Flask.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..domain.models import Offer, StoreRef
from ..services import stores as stores_service

MAX_SEARCH = 100              # characters of a search text
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEAL_TYPES = ("percent", "amount", "fixed", "multi", "other")
BARGAIN_SORTS = ("store", "discount", "price")
BONUS_SORTS = ("category", "price")


class ToolError(Exception):
    """A tool call that can't be answered; the message is shown to the assistant
    (always generic: never upstream or internal details)."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    bucket: str                                  # rate-limit group (ratelimit / LimitSettings.endpoint)
    run: Callable[[Any, dict, str], dict]        # (services, arguments, client key) -> result

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.input_schema,
                "annotations": {"title": self.title, "readOnlyHint": True, "destructiveHint": False,
                                "idempotentHint": True, "openWorldHint": True}}


# ---- argument checks ------------------------------------------------------------------------
def _text(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be text.")
    value = value.strip()
    if len(value) > MAX_SEARCH:
        raise ToolError(f"'{key}' may be at most {MAX_SEARCH} characters.")
    return value or None


def _number(args: dict, key: str, low: float, high: float) -> float | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ToolError(f"'{key}' must be a number from {low:g} to {high:g}.")
    return float(value)


def _limit(args: dict) -> int:
    value = args.get("limit", DEFAULT_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise ToolError(f"'limit' must be a whole number from 1 to {MAX_LIMIT}.")
    return value


def _choice(args: dict, key: str, options: tuple[str, ...], default: str | None) -> str | None:
    value = args.get(key, default)
    if value is None:
        return None
    if value not in options:
        raise ToolError(f"'{key}' must be one of: {', '.join(options)}.")
    return value


def _store_ids(args: dict, max_stores: int) -> list[int]:
    raw = args.get("store_ids")
    if not isinstance(raw, list) or not raw:
        raise ToolError("'store_ids' must be a list of store ids (use find_stores to look them up).")
    if len(raw) > max_stores:
        raise ToolError(f"At most {max_stores} stores per call.")
    ids: list[int] = []
    for value in raw:
        if isinstance(value, str) and value.isascii() and value.isdigit():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**10:
            raise ToolError("Store ids are positive whole numbers.")
        if value not in ids:
            ids.append(value)
    return ids


def _unknown_arguments(args: dict, schema: dict) -> None:
    extra = sorted(set(args) - set(schema["properties"]))
    if extra:
        raise ToolError(f"Unknown argument(s): {', '.join(extra)}.")


# ---- filtering (pure) ------------------------------------------------------------------------
def matches(offer: dict, words: list[str]) -> bool:
    """Every word occurs in the title, brand or category (or, for a Bonus group
    deal, in one of its products' titles). Case-insensitive."""
    hay = " ".join(str(offer.get(k) or "") for k in ("title", "brand", "category"))
    hay += " " + " ".join(str(p.get("title") or "") for p in offer.get("products") or [])
    hay = hay.lower()
    return all(w in hay for w in words)


def _words(search: str | None) -> list[str]:
    return search.lower().split() if search else []


def _sorted(items: list[dict], sort: str | None) -> list[dict]:
    if sort == "discount":
        return sorted(items, key=lambda o: -(o.get("discount") or 0))
    if sort == "price":
        return sorted(items, key=lambda o: (o.get("priceNow") is None, o.get("priceNow") or 0))
    return items                                          # as loaded (store order / AH's category order)


def filter_bargains(items: list[dict], *, search: str | None = None, min_discount: float | None = None,
                    category: str | None = None, sort: str | None = "store") -> list[dict]:
    words = _words(search)
    cat = category.lower() if category else None
    kept = [o for o in items
            if (min_discount is None or (o.get("discount") or 0) >= min_discount)
            and (cat is None or cat in str(o.get("category") or "").lower())
            and matches(o, words)]
    return _sorted(kept, sort)


def filter_bonus(items: list[dict], *, search: str | None = None, category: str | None = None,
                 deal_type: str | None = None, sort: str | None = "category") -> list[dict]:
    words = _words(search)
    cat = category.lower() if category else None
    kept = [o for o in items
            if (deal_type is None or o.get("dealType") == deal_type)
            and (cat is None or cat in str(o.get("category") or "").lower())
            and matches(o, words)]
    return _sorted(kept, sort)


def _page(items: list[dict], limit: int) -> dict[str, Any]:
    return {"total": len(items), "returned": min(len(items), limit), "truncated": len(items) > limit,
            "items": items[:limit]}


def _offers(offers: list[Offer]) -> list[dict]:
    return [o.to_json() for o in offers]


# ---- the tools ---------------------------------------------------------------------------------
def _find_stores(s, args: dict, client: str) -> dict:
    postcode = stores_service.normalize_postcode(_text(args, "postcode") or "")
    if not postcode:
        raise ToolError("AH's store search works by Dutch postal code only, e.g. '1234AB' or '1234'.")
    stores = s.find_stores(postcode)
    return {"postcode": postcode, "stores": [st.to_json() for st in stores]}


def _get_bargains(s, args: dict, client: str) -> dict:
    ids = _store_ids(args, s.settings.app.max_stores)
    search, category = _text(args, "search"), _text(args, "category")
    min_discount = _number(args, "min_discount", 0, 100)
    sort, limit = _choice(args, "sort", BARGAIN_SORTS, "store"), _limit(args)
    result = s.bargains([StoreRef(id=i, name=str(i)) for i in ids], client=client)
    items = filter_bargains(_offers(result.items), search=search, min_discount=min_discount,
                            category=category, sort=sort)
    out = _page(items, limit)
    out["storeErrors"] = [e.to_json() for e in result.store_errors]
    return out


def _get_bonus(s, args: dict, client: str) -> dict:
    search, category = _text(args, "search"), _text(args, "category")
    deal_type = _choice(args, "deal_type", DEAL_TYPES, None)
    sort, limit = _choice(args, "sort", BONUS_SORTS, "category"), _limit(args)
    week = s.bonus_week()
    items = filter_bonus(_offers(week.items), search=search, category=category, deal_type=deal_type, sort=sort)
    out = {"period": week.period.to_json(), "categories": list(week.categories)}
    out.update(_page(items, limit))
    if week.errors:
        out["note"] = "Some categories could not be loaded right now; the list may be incomplete."
    return out


_LIMIT_PROP = {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
               "description": "At most this many items in the answer (the total is always reported)."}
_SEARCH_PROP = {"type": "string", "maxLength": MAX_SEARCH,
                "description": "Words that must all occur in the product name, brand or category "
                               "(Dutch product names, e.g. 'kip', 'yoghurt').", }


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool(
        name="find_stores",
        title="Find Albert Heijn stores",
        description="Albert Heijn stores near a Dutch postal code, with their store ids. "
                    "Use the ids with get_bargains.",
        input_schema=_schema({"postcode": {"type": "string", "maxLength": 10,
                                           "description": "Dutch postal code: '1234AB' or the 4 digits '1234'."}},
                             ("postcode",)),
        bucket="stores", run=_find_stores),
    Tool(
        name="get_bargains",
        title="Last-chance bargains per store",
        description="Albert Heijn's 'Laatste Kans Koopjes': products marked down in a store because their "
                    "best-before date is near (discount in percent, stock, the date). Per store; find store ids "
                    "with find_stores. Prices in euros. Only what the store has today.",
        input_schema=_schema({
            "store_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                          "description": "Store ids from find_stores."},
            "search": _SEARCH_PROP,
            "min_discount": {"type": "number", "minimum": 0, "maximum": 100,
                             "description": "Only bargains with at least this discount (percent)."},
            "category": {"type": "string", "maxLength": MAX_SEARCH,
                         "description": "Only this product category (part of its Dutch name)."},
            "sort": {"type": "string", "enum": list(BARGAIN_SORTS), "default": "store",
                     "description": "store: per store as listed; discount: highest first; price: lowest first."},
            "limit": _LIMIT_PROP,
        }, ("store_ids",)),
        bucket="bargains", run=_get_bargains),
    Tool(
        name="get_bonus",
        title="This week's Albert Heijn Bonus",
        description="This week's national Albert Heijn Bonus offers (the same in every store): the deal text "
                    "(e.g. '2e HALVE PRIJS', '25% KORTING'), the price before and with the Bonus in euros, and "
                    "for group deals the products they cover. Also returns the Bonus week and its categories.",
        input_schema=_schema({
            "search": _SEARCH_PROP,
            "category": {"type": "string", "maxLength": MAX_SEARCH,
                         "description": "Only this Bonus category (part of its Dutch name, see 'categories')."},
            "deal_type": {"type": "string", "enum": list(DEAL_TYPES),
                          "description": "percent: % off; amount: euros off; fixed: a fixed price; "
                                         "multi: buy several (e.g. 2 for ...); other."},
            "sort": {"type": "string", "enum": list(BONUS_SORTS), "default": "category",
                     "description": "category: AH's category order; price: lowest first."},
            "limit": _LIMIT_PROP,
        }),
        bucket="bonus", run=_get_bonus),
)}


def check_arguments(tool: Tool, args: object) -> dict:
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise ToolError("Arguments must be an object.")
    _unknown_arguments(args, tool.input_schema)
    return args
