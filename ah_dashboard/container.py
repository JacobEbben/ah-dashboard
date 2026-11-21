"""The app's services in one typed container.

``create_app`` (the composition root) builds one :class:`Services` and keeps
it in ``app.extensions["services"]``; code reaches it with :func:`services`.
Tests swap a part by assigning to it, e.g. ``services(app).ah = FakeAH()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask, current_app

if TYPE_CHECKING:
    from .ah.auth import TokenManager
    from .ah.gateway import AHApi, AHGateway
    from .auth.google import GoogleSignIn
    from .auth.lockout import LoginLimiter
    from .auth.sessions import SessionStore
    from .auth.users import UserStore
    from .config import Settings
    from .db import Database
    from .domain.models import BonusWeek, Period, Store, StoreRef
    from .ratelimit import RateLimiter
    from .scheduler import Scheduler
    from .services.bargains import BargainsResult
    from .services.bonus import NextWeekStatus, WeekCache
    from .services.cache import TTLCache
    from .services.stores import KnownStores

EXTENSION_KEY = "services"


@dataclass
class Services:
    settings: Settings
    db: Database
    users: UserStore
    sessions: SessionStore           # active sign-ins
    tokens: TokenManager             # the server's AH login
    ah: AHApi                        # AH requests with that login (AHSession; tests: a fake)
    scheduler: Scheduler             # background jobs and tasks
    rate_limiter: RateLimiter        # per-client API limits and load budgets
    admin_limiter: LoginLimiter      # failed admin-password sign-ins
    google: GoogleSignIn | None      # Google sign-in (None: not configured; tests: a fake)
    known_stores: KnownStores        # store ids that may be loaded freely
    bargains_cache: TTLCache         # bargains per store
    stores_cache: TTLCache           # store searches per postal code
    bonus_cache: WeekCache           # this week's national Bonus
    bonus_next_cache: WeekCache      # next week's national Bonus (admins only)
    bonus_next_status: NextWeekStatus  # is next week's Bonus published yet (admins' "Next week" button)

    @property
    def gateway(self) -> AHGateway:
        """AH's endpoints, on top of whatever ``ah`` currently is."""
        from .ah.gateway import AHGateway
        return AHGateway(self.ah, self.settings.ah.application)

    def load_bonus_week(self) -> BonusWeek:
        """Load this week's Bonus from AH now (~300 requests: use ``bonus_cache``)."""
        from .services.bonus import load_bonus
        return load_bonus(self.gateway, max_workers=self.settings.ah.max_workers)

    def bonus_week(self) -> BonusWeek:
        """This week's Bonus from the shared copy (loaded when needed)."""
        return self.bonus_cache.get(self.load_bonus_week)

    def load_bonus_next_week(self) -> BonusWeek:
        """Load next week's Bonus from AH now (NextWeekUnavailable until AH publishes it)."""
        from .services.bonus import load_bonus
        return load_bonus(self.gateway, max_workers=self.settings.ah.max_workers, next_week=True)

    def bonus_next_week(self) -> BonusWeek:
        """Next week's Bonus from its shared copy (loaded when needed). Admins only."""
        return self.bonus_next_cache.get(self.load_bonus_next_week)

    def refresh_bonus_next_week(self) -> BonusWeek:
        """Reload next week's Bonus from AH now. Admins only."""
        return self.bonus_next_cache.refresh(self.load_bonus_next_week)

    def bonus_next_period(self) -> Period | None:
        """Next week's period once AH has published it, else None (cheap: see NextWeekStatus)."""
        return self.bonus_next_status.period(self.gateway.bonus_metadata)

    def refresh_bonus_week(self) -> BonusWeek:
        """Reload this week's Bonus from AH now (admin only: ~160 paced AH requests)."""
        return self.bonus_cache.refresh(self.load_bonus_week)

    def find_stores(self, postcode: str) -> list[Store]:
        """AH's stores near a (normalized) postal code, cached per postal code.
        Stores AH lists exist, so their bargains may be loaded (LoadGuard)."""
        from .services import stores as stores_service
        gateway = self.gateway
        entry, _ = self.stores_cache.get_or_load(postcode, lambda: stores_service.search_stores(gateway, postcode))
        stores = entry.value
        self.known_stores.add([st.id for st in stores if st.id is not None])
        return stores

    def bargains(self, stores: list[StoreRef], client: str | None) -> BargainsResult:
        """Bargains of ``stores`` from the cache, loading from AH within the
        budgets of ``client`` (a client key). ``client=None``: the admin's force
        refresh, which reloads every store now and is not budgeted."""
        from .services import bargains as bargains_service
        lim = self.settings.limits
        guard = None if client is None else bargains_service.LoadGuard(
            self.rate_limiter, self.known_stores, client,
            unknown_per_client=lim.unknown_stores_per_client, unknown_total=lim.unknown_stores_total,
            unknown_window=lim.unknown_stores_window, loads_per_client=lim.store_loads_per_client,
            loads_total=lim.store_loads_total)
        return bargains_service.fetch_bargains(
            self.gateway, stores, max_workers=self.settings.ah.max_workers,
            cache=self.bargains_cache, force=client is None, guard=guard)


def services(app: Flask | None = None) -> Services:
    """The services of ``app`` (default: the current app)."""
    return (app or current_app).extensions[EXTENSION_KEY]
