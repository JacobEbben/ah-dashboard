"""Last-chance bargains for a list of stores: cached per store, loaded in
parallel, loads from AH budgeted (:class:`LoadGuard`)."""

from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..ah import mapping
from ..ah.auth import AHAuthError
from ..ah.client import AHError
from ..ah.gateway import AHGateway
from ..domain.models import BargainInfo, Offer, StoreRef
from ..logging_setup import log_error
from .cache import TTLCache

log = logging.getLogger(__name__)

_MESSAGES = {
    "store_failed": "Could not load bargains for this store.",
    "ah_auth_failed": "The server's AH login does not work anymore.",
    "store_unknown": "This store is not known yet. Find it again with the store search.",
    "store_busy": "Too many stores are being loaded right now. Please try again in a minute.",
}


class StoreRefused(AHError):
    """The server did not ask AH for this store (``code``: "store_unknown" or
    "store_busy"); not an AH failure, so not logged as an error or cached."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class LoadGuard:
    """Budgets for loading stores from AH (cache misses only; cached stores
    cost nothing), so anonymous visitors can't use the shared AH login to
    flood AH:

    - a store id that is not known to exist (stores.KnownStores) only within
      ``unknown_per_client`` / ``unknown_total`` per ``unknown_window``
      seconds; one that turns out to have bargains becomes known;
    - every load counts against ``loads_per_client`` and ``loads_total`` per
      minute.

    ``client`` is the visitor's client key (IPv4 address or IPv6 /64).
    """

    def __init__(self, limiter, known, client: str, *, unknown_per_client: int, unknown_total: int,
                 unknown_window: float, loads_per_client: int, loads_total: int):
        self.limiter, self.known, self.client = limiter, known, client
        self.unknown = (unknown_per_client, unknown_total, unknown_window)
        self.loads = (loads_per_client, loads_total)

    def check(self, store_id: int) -> None:
        # "*" = ratelimit.SHARED (a server-wide budget, never evicted); not
        # imported: ratelimit uses Flask, services don't.
        per_client, total, window = self.unknown
        if store_id not in self.known and not (
                self.limiter.allow("store_unknown", self.client, per_client, window)
                and self.limiter.allow("store_unknown_all", "*", total, window)):
            raise StoreRefused("store_unknown")
        per_client, total = self.loads
        if not (self.limiter.allow("store_loads", self.client, per_client, 60)
                and self.limiter.allow("store_loads_all", "*", total, 60)):
            raise StoreRefused("store_busy")

    def loaded(self, store_id: int, items: list) -> None:
        if items:
            self.known.add([store_id])


@dataclass
class StoreError:
    store: StoreRef
    code: str                    # key of _MESSAGES
    ref: str | None = None       # error log reference (not for refusals)

    def to_json(self) -> dict:
        out = {"storeId": self.store.id, "store": self.store.name, "code": self.code,
               "message": _MESSAGES[self.code]}
        if self.ref:
            out["ref"] = self.ref
        return out


@dataclass
class BargainsResult:
    items: list[Offer] = field(default_factory=list)
    store_errors: list[StoreError] = field(default_factory=list)

    def to_json(self) -> dict:
        # Deliberately no cache details (cached or not, data age, TTL).
        return {"items": [o.to_json() for o in self.items],
                "storeErrors": [e.to_json() for e in self.store_errors]}


def load_store(gateway: AHGateway, store_id: int) -> list[Offer]:
    """One store's bargains from AH, validated and untagged (what is cached).
    Data the mapping can't handle fails only this store (mapping.AHDataError)."""
    return mapping.guarded(f"bargains of store {store_id}", mapping.bargains, gateway.bargain_items(store_id))


def cache_error(exc: BaseException) -> bool:
    """Which failed store loads the bargains cache remembers for a while: not
    a broken AH login (an admin can fix that at any moment), and not a store
    the server declined to load (budgets free up again)."""
    return isinstance(exc, AHError) and not isinstance(exc, (AHAuthError, StoreRefused))


def tag(items: list[Offer], store: StoreRef) -> list[Offer]:
    """Copies of ``items`` labelled with the store; cached data is not changed,
    so every visitor can use their own store names."""
    return [dataclasses.replace(o, bargain=dataclasses.replace(o.bargain or BargainInfo(),
                                                               store_id=store.id, store_name=store.name))
            for o in items]


def fetch_bargains(gateway: AHGateway, stores: list[StoreRef], *, max_workers: int = 6,
                   cache: TTLCache | None = None, force: bool = False,
                   guard: LoadGuard | None = None) -> BargainsResult:
    """Fetch all stores in parallel. A failing store doesn't fail the others;
    it is reported in ``store_errors``. Items keep the order of ``stores``.

    With a ``cache``, stores are served from it while younger than its TTL.
    ``force`` always reloads from AH and updates the cache. With a ``guard``,
    every actual load from AH (not a cache hit) must fit its budgets.
    """

    def load_from_ah(store_id: int) -> list[Offer]:
        if guard is not None:
            guard.check(store_id)
        items = load_store(gateway, store_id)
        if guard is not None:
            guard.loaded(store_id, items)
        return items

    def load(store: StoreRef) -> list[Offer]:
        if cache is None:
            return load_from_ah(store.id)
        entry, _ = cache.get_or_load(store.id, lambda: load_from_ah(store.id), force=force)
        return entry.value

    def run(store: StoreRef) -> tuple[list[Offer], StoreError | None]:
        try:
            return tag(load(store), store), None
        except StoreRefused as exc:
            # Declined by the budgets (not an AH failure): no error log entry.
            log.info("store %s not loaded: %s", store.id, exc.code)
            return [], StoreError(store, exc.code)
        except AHError as exc:
            # Details go to the error log; the visitor gets a generic message + ref.
            ref = log_error(log, "bargains for store %s failed: %s", store.id, exc)
            # Expired tokens are renewed by the AH session; AHAuthError means
            # the server's login itself is gone (an admin must log in again).
            code = "ah_auth_failed" if isinstance(exc, AHAuthError) else "store_failed"
            return [], StoreError(store, code, ref)

    result = BargainsResult()
    if not stores:
        return result
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(stores)))) as pool:
        for items, error in pool.map(run, stores):
            result.items.extend(items)
            if error is not None:
                result.store_errors.append(error)
    return result
