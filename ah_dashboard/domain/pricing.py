"""Bonus prices and deal types, from AH's structured discount labels.

AH's bonus pages give the regular price and the deal as ``discountLabels``
(``code`` plus numbers), but no bonus price; :func:`bonus_price` calculates
it. For multi-buy deals it is the price per item when buying the required
number. Pure functions, no I/O.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

MAX_PRICE = 100_000   # euros; the same bound as ah.mapping.money(): anything above is not a price


def _number(value) -> float | None:
    """A finite number (no booleans, NaN or infinity: json accepts those literals)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value) -> int | None:
    n = _number(value)
    return int(n) if n is not None and n == int(n) and 1 <= n <= 100 else None


def _cents(value: float) -> float:
    """Round to whole cents, halves up (4.99 / 2 = 2.495 -> 2.50)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bonus_price(was: float | None, labels) -> tuple[float | None, int | None]:
    """(bonus price per item, number of items you must buy for it) from AH's
    ``discountLabels``. ``(None, None)`` when it cannot be calculated, e.g. a
    percentage deal without a regular price (group deals)."""
    for label in labels if isinstance(labels, list) else []:
        if not isinstance(label, dict):
            continue
        code = label.get("code")
        price, count = _number(label.get("price")), _count(label.get("count"))
        if price is not None and not 0 < price < MAX_PRICE:
            price = None
        if code == "DISCOUNT_FIXED_PRICE" and price and price > 0:
            return _cents(price), 1
        if code == "DISCOUNT_X_FOR_Y" and price and price > 0 and count:
            return _cents(price / count), count
        if not was:
            continue  # the remaining deal types are relative to the regular price
        if code == "DISCOUNT_PERCENTAGE":
            pct = _number(label.get("precisePercentage")) or _number(label.get("percentage"))
            if pct and 0 < pct < 100:
                return _cents(was * (100 - pct) / 100), 1
        elif code == "DISCOUNT_AMOUNT":
            amount = _number(label.get("amount"))
            if amount and 0 < amount < was:
                return _cents(was - amount), 1
        elif code == "DISCOUNT_X_PLUS_Y_FREE":
            free = _count(label.get("freeCount"))
            if count and free:
                return _cents(was * count / (count + free)), count + free
        elif code == "DISCOUNT_ONE_HALF_PRICE":
            n = count or 2  # "2e halve prijs": one of every n items at half price
            return _cents(was * (n - 0.5) / n), n
    return None, None


# Deal type per AH discount label code (in the API output as dealType; the MCP get_bonus tool filters on it).
_DEAL_TYPES = {
    "DISCOUNT_PERCENTAGE": "percent",
    "DISCOUNT_AMOUNT": "amount",
    "DISCOUNT_FIXED_PRICE": "fixed",
    "DISCOUNT_X_FOR_Y": "multi",
    "DISCOUNT_X_PLUS_Y_FREE": "multi",
    "DISCOUNT_ONE_HALF_PRICE": "multi",
}


def deal_type(labels) -> str:
    """"percent", "amount", "fixed", "multi" (buy several) or "other"."""
    for label in labels if isinstance(labels, list) else []:
        if isinstance(label, dict) and label.get("code") in _DEAL_TYPES:
            return _DEAL_TYPES[label["code"]]
    return "other"
