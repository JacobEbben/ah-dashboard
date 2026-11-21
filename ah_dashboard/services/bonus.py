"""The national AH Bonus of the current week.

Same approach as appie-go's GetBonusProducts (github.com/gwillem/appie-go,
bonus.go): the bonus metadata lists the week's period and categories; each
category's section lists its products and bonus groups. A load is ~300 AH
requests (one per category, one per group deal), so one shared copy is kept
for the whole week (:class:`WeekCache`) and loaded in the background
(:func:`prewarm_job`). The copy is also kept in the database
(:class:`BonusStore`), so a restart or redeploy reuses it instead of sending
the whole load to AH again (AH's CDN blocks such bursts, see ah/session.py).

Group deals ("Alle Bonduelle") come without products or prices. Their
products are looked up with GraphQL ``bonusPromotions(id)`` (appie-go's
GetBonusGroupProducts); the card then shows the cheapest product's price as
"from" price and the number of products, and lists the products (webshop
ids, the same as a bargain's product id), so liking or hiding the group can
stand for its products.
"""

from __future__ import annotations

import json
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
from ..domain.models import BonusWeek, CategoryError, Offer, Period, bonus_week_from_stored
from ..logging_setup import log_error

log = logging.getLogger(__name__)

Loader = Callable[[], BonusWeek]


class NextWeekUnavailable(Exception):
    """AH hasn't published next week's Bonus yet (its metadata lists only the current week)."""


def next_period(periods: list[tuple[Period, list[str]]], today: date) -> tuple[Period, list[str]] | None:
    """The first period that starts after ``today`` (next week), or None."""
    for period, categories in periods:
        try:
            if date.fromisoformat(period.start or "") > today:
                return period, categories
        except ValueError:
            continue
    return None


def load_bonus(gateway: AHGateway, today: date | None = None, max_workers: int = 6,
               next_week: bool = False) -> BonusWeek:
    """The current week's national Bonus (``next_week``: next week's, asked
    for on its first day; NextWeekUnavailable while AH hasn't published it).
    Raises AHError when the metadata cannot be loaded; a failing category is
    reported in ``errors``."""
    day = today or amsterdam_today()
    if next_week:
        periods = mapping.guarded("bonus metadata", mapping.bonus_periods, gateway.bonus_metadata())
        upcoming = next_period(periods, day)
        if upcoming is None:
            raise NextWeekUnavailable("next week's Bonus is not published yet")
        period, categories = upcoming
        day = date.fromisoformat(period.start or "")
    else:
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
    groups_failed = _fill_groups(gateway, items, period, max_workers)
    return BonusWeek(period=period, categories=categories, items=items, errors=errors,
                     groups_failed=groups_failed)


def _fill_groups(gateway: AHGateway, items: list[Offer], period: Period, max_workers: int) -> int:
    """Look up the products of each group deal and fill in its prices.
    A failed lookup only leaves that card without prices (the week then counts
    as partial and is loaded again later). Returns the number of failed lookups."""
    groups = [it for it in items if it.bonus and it.bonus.is_group and it.bonus.segment]
    if not groups or not period.start or not period.end:
        return 0
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
    return len(failed)


class BonusStore:
    """The cached week in the database (one row, table ``bonus_week``), so it
    survives restarts and redeploys. Public AH data only, no user data. A row
    that can't be read is ignored (the week is then loaded from AH)."""

    TABLES = ("bonus_week", "bonus_next_week")

    def __init__(self, db, table: str = "bonus_week"):
        if table not in self.TABLES:      # the name goes into the SQL: only these two
            raise ValueError(table)
        self._db = db
        self._table = table

    def load(self) -> tuple[BonusWeek, float] | None:
        try:
            row = self._db.one(f"SELECT data, loaded_at FROM {self._table} WHERE id = 1")
            if row is None:
                return None
            week = bonus_week_from_stored(json.loads(row["data"]))
        except Exception:   # a damaged row must not stop the app: load from AH instead
            log.warning("stored Bonus week could not be read; it will be loaded from AH", exc_info=True)
            return None
        return (week, float(row["loaded_at"])) if week is not None else None

    def save(self, week: BonusWeek, loaded_at: float) -> None:
        try:
            self._db.execute(
                f"INSERT INTO {self._table} (id, data, loaded_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, loaded_at = excluded.loaded_at",
                (json.dumps(week.to_stored(), separators=(",", ":")), loaded_at))
        except Exception:   # only the copy for the next start is lost
            log_error(log, "could not store the Bonus week", exc_info=True)

    def clear(self) -> None:
        self._db.execute(f"DELETE FROM {self._table}")


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
                 today: Callable[[], date] = amsterdam_today, store: BonusStore | None = None,
                 next_week: bool = False):
        self.retry_seconds = retry_seconds
        # next_week: the cache of next week's Bonus. Its copy is
        # usable until that week starts (then this week's cache takes over).
        self.next_week = next_week
        self._clock = clock
        self._today = today
        self._store = store
        # (week, loaded at; wall-clock time, so a stored copy keeps its age across restarts)
        self._entry: tuple[BonusWeek, float] | None = store.load() if store is not None else None
        self._lock = threading.Lock()

    def _set(self, week: BonusWeek, loaded_at: float, *, new: bool) -> None:
        self._entry = (week, loaded_at)
        if new and self._store is not None:
            self._store.save(week, loaded_at)

    def _current_week(self, week: BonusWeek) -> bool:
        """The copy is for the week this cache is about (this week, or still the coming one)."""
        try:
            start, end = date.fromisoformat(week.period.start or ""), date.fromisoformat(week.period.end or "")
        except ValueError:
            return False
        if self.next_week:
            return self._today() < start
        return start <= self._today() <= end

    def current(self) -> BonusWeek | None:
        """The copy if it is still for this cache's week (no AH request)."""
        entry = self._entry
        return entry[0] if entry is not None and self._current_week(entry[0]) else None

    def _fresh(self, entry: tuple[BonusWeek, float]) -> bool:
        week, loaded_at = entry
        recent = self._clock() - loaded_at < self.retry_seconds
        if not week.complete:
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
                    self._set(entry[0], self._clock(), new=False)
                    return entry[0]
                raise                # nothing usable: the request fails
            if not week.complete and entry is not None and self._current_week(entry[0]) \
                    and _worse(week, entry[0]):
                # A worse partial load than what we have: keep the better copy.
                self._set(entry[0], self._clock(), new=False)
                return entry[0]
            self._set(week, self._clock(), new=True)
            return week

    def refresh(self, loader: Loader) -> BonusWeek:
        """Load the week from AH now, however fresh the copy is (the admin's force
        refresh). A failure raises and keeps the current copy; a partial load
        that is worse than this week's copy keeps that copy too (and returns it)."""
        with self._lock:
            entry = self._entry
            week = loader()
            if not week.complete and entry is not None and self._current_week(entry[0]) \
                    and _worse(week, entry[0]):
                log.warning("forced Bonus reload was worse than the current copy; keeping the copy")
                return entry[0]
            self._set(week, self._clock(), new=True)
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
            "failed_groups": week.groups_failed,
            "current_week": self._current_week(week),
        }

    def clear(self) -> None:
        self._entry = None
        if self._store is not None:
            self._store.clear()


def _worse(new: BonusWeek, old: BonusWeek) -> bool:
    """A partial load that is worse than the copy we have: fewer offers, or more gaps."""
    return len(old.items) > len(new.items) or \
        len(old.errors) + old.groups_failed < len(new.errors) + new.groups_failed


class NextWeekStatus:
    """Whether next week's Bonus is published, for the "Next week" button. Asks AH's metadata (one request) at most every ``check_seconds``,
    and not at all while next week's copy is loaded."""

    def __init__(self, cache: WeekCache, check_seconds: float = 600, clock: Callable[[], float] = time.time,
                 today: Callable[[], date] = amsterdam_today):
        self._cache = cache
        self._check_seconds = check_seconds
        self._clock = clock
        self._today = today
        self._checked: tuple[Period | None, float] | None = None
        self._lock = threading.Lock()

    def period(self, metadata: Callable[[], object]) -> Period | None:
        """Next week's period, or None while AH hasn't published it."""
        loaded = self._cache.current()
        if loaded is not None:
            return loaded.period
        with self._lock:
            checked = self._checked
            if checked is not None and self._clock() - checked[1] < self._check_seconds \
                    and (checked[0] is None or self._still_next(checked[0])):
                return checked[0]
            periods = mapping.guarded("bonus metadata", mapping.bonus_periods, metadata())
            found = next_period(periods, self._today())
            self._checked = (found[0] if found else None, self._clock())
            return self._checked[0]

    def _still_next(self, period: Period) -> bool:
        try:
            return date.fromisoformat(period.start or "") > self._today()
        except ValueError:
            return False

    def clear(self) -> None:
        self._checked = None


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
