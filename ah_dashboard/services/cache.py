"""Small thread-safe TTL cache with single-flight loading.

Used for AH bargains per store and for store searches per postal code:

- Within ``ttl`` every request is served from memory.
- After it, the first request reloads while concurrent requests for the same
  key wait for that one load instead of all calling AH.
- With ``stale_ttl``, an entry older than ``ttl`` but younger than
  ``ttl + stale_ttl`` is still served at once, and reloaded in the background
  (one reload per key at a time). The visitor never waits for AH then.
- With ``error_ttl``, a failed load is remembered for that long and the same
  error is raised again without calling the loader (``cache_error`` decides
  which errors; e.g. not "the AH login is gone", which an admin can fix).
  A failed load never removes a good entry.
- The number of entries (and remembered errors) is capped; oldest evicted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheEntry:
    value: Any
    fetched_at: float  # wall-clock time of the load (time.time())


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="cache-refresh", daemon=True).start()


class TTLCache:
    def __init__(self, ttl: float, max_entries: int = 1000,
                 clock: Callable[[], float] = time.time, *, stale_ttl: float = 0,
                 error_ttl: float = 0, cache_error: Callable[[BaseException], bool] | None = None,
                 spawn: Callable[[Callable[[], None]], None] = _spawn):
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self.error_ttl = error_ttl
        self.max_entries = max_entries
        self._cache_error = cache_error or (lambda exc: True)
        self._clock = clock
        self._spawn = spawn
        self._entries: OrderedDict[Hashable, CacheEntry] = OrderedDict()
        self._errors: OrderedDict[Hashable, tuple[BaseException, float]] = OrderedDict()
        self._lock = threading.Lock()                     # guards everything below
        # Per-key load locks, only while someone uses them (removed afterwards,
        # so keys that never get an entry don't pile up).
        self._key_locks: dict[Hashable, list] = {}        # key -> [lock, users]
        self._refreshing: set[Hashable] = set()

    # ---- inspection --------------------------------------------------------------------
    def peek(self, key: Hashable) -> CacheEntry | None:
        with self._lock:
            return self._entries.get(key)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._entries), "errors": len(self._errors),
                    "loading": len(self._key_locks), "refreshing": len(self._refreshing)}

    def _age(self, entry: CacheEntry | None) -> float | None:
        return None if entry is None else self._clock() - entry.fetched_at

    def _fresh(self, entry: CacheEntry | None) -> bool:
        age = self._age(entry)
        return age is not None and age < self.ttl

    def _usable_stale(self, entry: CacheEntry | None) -> bool:
        age = self._age(entry)
        return self.stale_ttl > 0 and age is not None and age < self.ttl + self.stale_ttl

    def _recent_error(self, key: Hashable) -> BaseException | None:
        with self._lock:
            found = self._errors.get(key)
            if found is None:
                return None
            if self._clock() - found[1] < self.error_ttl:
                return found[0]
            del self._errors[key]
            return None

    # ---- loading ---------------------------------------------------------------------------
    def _acquire_key_lock(self, key: Hashable) -> threading.Lock:
        with self._lock:
            slot = self._key_locks.setdefault(key, [threading.Lock(), 0])
            slot[1] += 1
            return slot[0]

    def _release_key_lock(self, key: Hashable) -> None:
        with self._lock:
            slot = self._key_locks.get(key)
            if slot is not None:
                slot[1] -= 1
                if slot[1] <= 0:
                    del self._key_locks[key]

    def _store(self, key: Hashable, value: Any) -> CacheEntry:
        entry = CacheEntry(value=value, fetched_at=self._clock())
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self._errors.pop(key, None)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return entry

    def _remember_error(self, key: Hashable, exc: BaseException) -> None:
        if self.error_ttl <= 0 or not self._cache_error(exc):
            return
        with self._lock:
            self._errors[key] = (exc, self._clock())
            self._errors.move_to_end(key)
            while len(self._errors) > self.max_entries:
                self._errors.popitem(last=False)

    def _load(self, key: Hashable, loader: Callable[[], Any]) -> CacheEntry:
        try:
            value = loader()
        except Exception as exc:
            self._remember_error(key, exc)
            raise
        return self._store(key, value)

    def _refresh_in_background(self, key: Hashable, loader: Callable[[], Any]) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def run():
            lock = self._acquire_key_lock(key)
            try:
                with lock:
                    if not self._fresh(self.peek(key)):
                        self._load(key, loader)
            except Exception as exc:  # the stale entry stays; logged, not raised
                log.warning("background refresh of %r failed: %s", key, exc)
            finally:
                self._release_key_lock(key)
                with self._lock:
                    self._refreshing.discard(key)

        try:
            self._spawn(run)
        except Exception:  # pragma: no cover - thread could not start
            with self._lock:
                self._refreshing.discard(key)

    def get_or_load(self, key: Hashable, loader: Callable[[], Any],
                    force: bool = False) -> tuple[CacheEntry, bool]:
        """Return (entry, from_cache). ``force`` always calls the loader and
        replaces the cached entry. Exceptions from ``loader`` propagate and
        nothing is cached (an existing entry is kept)."""
        if not force:
            entry = self.peek(key)
            if entry is not None and self._fresh(entry):
                return entry, True
            if entry is not None and self._usable_stale(entry):
                self._refresh_in_background(key, loader)
                return entry, True
            if (exc := self._recent_error(key)) is not None:
                raise exc

        lock = self._acquire_key_lock(key)
        try:
            with lock:
                if not force:
                    # Another thread may have loaded it while we waited.
                    entry = self.peek(key)
                    if entry is not None and self._fresh(entry):
                        return entry, True
                    if (exc := self._recent_error(key)) is not None:
                        raise exc
                return self._load(key, loader), False
        finally:
            self._release_key_lock(key)

    def discard(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._errors.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._errors.clear()
