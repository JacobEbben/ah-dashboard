"""The national AH Bonus of the current week.

Same approach as appie-go's GetBonusProducts (github.com/gwillem/appie-go,
bonus.go): the bonus metadata lists the week's period and categories; each
category's section lists its products and bonus groups. A load is ~300 AH
requests (one per category, one per group deal), so one shared copy is kept
for the whole week (:class:`WeekCache`) and loaded in the background
(:func:`prewarm_job`).

Group deals ("Alle Bonduelle") come without products or prices. Their
products are looked up with GraphQL ``bonusPromotions(id)`` (appie-go's
GetBonusGroupProducts); the card then shows the cheapest product's price as
"from" price and the number of products, and lists the products (webshop
ids, the same as a bargain's product id), so liking or hiding the group can
stand for its products.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from ..ah import mapping
from ..ah.auth import AHAuthError
from ..ah.client import AHError
from ..ah.gateway import AHGateway
from ..domain.dates import amsterdam_today
from ..domain.models import BonusWeek, CategoryError, Offer, Period
from ..logging_setup import log_error

log = logging.getLogger(__name__)

Loader = Callable[[], BonusWeek]


def load_bonus(gateway: AHGateway, today: date | None = None, max_workers: int = 6) -> BonusWeek:
    """The current week's national Bonus. Raises AHError when the metadata
    cannot be loaded; a failing category is reported in ``errors``."""
    day = today or amsterdam_today()
    period, categories = mapping.guarded("bonus metadata", mapping.bonus_period, gateway.bonus_metadata())

    def run(category: str) -> tuple[list[Offer], CategoryError | None]:
        try:
            # Data the mapping can't handle fails only this category (mapping.AHDataError).
            return mapping.guarded(f"bonus category {category!r}", mapping.bonus_section,
                                   gateway.bonus_section(category, day), category), None
        except AHError as exc:
            ref = log_error(log, "bonus category %r failed: %s", category, exc)
            code = "ah_auth_failed" if isinstance(exc, AHAuthError) else "category_failed"
            return [], CategoryError(category, code, ref)

    items: list[Offer] = []
    errors: list[CategoryError] = []
    if categories:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(categories)))) as pool:
            for found, error in pool.map(run, categories):
                items.extend(found)
                if error:
                    errors.append(error)
    _fill_groups(gateway, items, period, max_workers)
    return BonusWeek(period=period, categories=categories, items=items, errors=errors)


def _fill_groups(gateway: AHGateway, items: list[Offer], period: Period, max_workers: int) -> None:
    """Look up the products of each group deal and fill in its prices.
    A failed lookup only leaves that card without prices."""
    groups = [it for it in items if it.bonus and it.bonus.is_group and it.bonus.segment]
    if not groups or not period.start or not period.end:
        return
    start, end = period.start, period.end

    def run(offer: Offer):
        assert offer.bonus is not None and offer.bonus.segment is not None
        try:
            data = gateway.bonus_group_products(offer.bonus.segment, start, end)
            return offer, mapping.guarded("bonus group products", mapping.group_details, data, offer.bonus.labels), None
        except AHError as exc:
            return offer, None, exc

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(groups)))) as pool:
        for offer, found, exc in pool.map(run, groups):
            if exc is not None:
                failed.append((offer.bonus.segment if offer.bonus else None, exc))
            elif found:
                mapping.apply_group_details(offer, found)
    if failed:
        log_error(log, "bonus group lookup failed for %d of %d groups, e.g. %s: %s",
                  len(failed), len(groups), failed[0][0], failed[0][1])


class WeekCache:
    """One shared copy of the Bonus, kept until its bonus week ends.

    The Bonus is fixed for its week (AH's ``bonusStartDate`` .. ``bonusEndDate``),
    so a complete load is reused until that period no longer includes today
    (Dutch date). Two cases are retried after ``retry_seconds`` instead:
    a partial load (some categories failed), and a period that does not include
    today (AH can lag just after the week changes). Concurrent requests wait
    for one load instead of all calling AH.

    While a copy for the current week exists (e.g. a partial one being
    retried), visitors get it at once instead of waiting for the reload, and a
    failed reload keeps it (retried again after ``retry_seconds``). Only
    without a usable copy does a request wait for, or fail with, the load.
    """

    def __init__(self, retry_seconds: float = 300, clock: Callable[[], float] = time.time,
                 today: Callable[[], date] = amsterdam_today):
        self.retry_seconds = retry_seconds
        self._clock = clock
        self._today = today
        self._entry: tuple[BonusWeek, float] | None = None   # (week, loaded at)
        self._lock = threading.Lock()

    def _current_week(self, week: BonusWeek) -> bool:
        try:
            start, end = date.fromisoformat(week.period.start or ""), date.fromisoformat(week.period.end or "")
        except ValueError:
            return False
        return start <= self._today() <= end

    def _fresh(self, entry: tuple[BonusWeek, float]) -> bool:
        week, loaded_at = entry
        recent = self._clock() - loaded_at < self.retry_seconds
        if week.errors:
            return recent
        return self._current_week(week) or recent

    def get(self, loader: Loader) -> BonusWeek:
        entry = self._entry
        if entry is not None and self._fresh(entry):
            return entry[0]
        # A reload is running and we have this week's copy: don't wait for it.
        if entry is not None and self._current_week(entry[0]) and self._lock.locked():
            return entry[0]
        with self._lock:
            entry = self._entry
            if entry is not None and self._fresh(entry):
                return entry[0]  # loaded by another request while we waited
            try:
                week = loader()
            except Exception:
                # Any failure (AHError, or a bug): keep this week's copy if we have one.
                if entry is not None and self._current_week(entry[0]):
                    # Keep this week's copy; try again after retry_seconds.
                    self._entry = (entry[0], self._clock())
                    return entry[0]
                raise                # nothing usable: the request fails
            if week.errors and entry is not None and self._current_week(entry[0]) \
                    and len(entry[0].items) > len(week.items):
                # A worse partial load than what we have: keep the better copy.
                self._entry = (entry[0], self._clock())
                return entry[0]
            self._entry = (week, self._clock())
            return week

    def status(self) -> dict:
        """For the admin panel (counts and dates only)."""
        entry = self._entry
        if entry is None:
            return {"loaded": False}
        week, loaded_at = entry
        return {
            "loaded": True,
            "loaded_at": loaded_at,
            "period": week.period.to_json(),
            "offers": len(week.items),
            "failed_categories": len(week.errors),
            "current_week": self._current_week(week),
        }

    def clear(self) -> None:
        self._entry = None


def prewarm_job(cache: WeekCache, loader: Loader, ready: Callable[[], bool]) -> Callable[[], float | None]:
    """A scheduler job keeping the Bonus loaded: at start-up and whenever the
    copy is missing, partial or from a past week (``get`` does nothing while it
    is fresh). Skips a round while ``ready()`` is false (no AH login yet);
    after a failure it waits ``retry_seconds`` (don't hammer a failing AH)."""
    def job() -> float | None:
        if not ready():
            return None
        try:
            cache.get(loader)
        except AHError as exc:
            log_error(log, "background Bonus load failed: %s", exc)
            return cache.retry_seconds
        return None
    return job
