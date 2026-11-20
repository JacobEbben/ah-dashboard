"""Store lookup by Dutch postal code."""

from __future__ import annotations

import re
import threading
import time

from ..ah import mapping
from ..ah.gateway import AHGateway
from ..domain.models import Store

# "1234AB", "1234 ab" or just "1234".
POSTCODE_RE = re.compile(r"^\s*([0-9]{4})\s*([A-Za-z]{2})?\s*$")


class KnownStores:
    """Store ids known to exist: every store in a store search result, and
    stores AH returned bargains for. Visitors may load
    these freely; any other id (anyone can invent 10^10 of them) only within a
    small budget (services/bargains.LoadGuard), so invented ids can't make the
    server flood AH with the shared login. Kept in the database (survives
    restarts); capped (AH has about 1,100 stores)."""

    MAX = 20_000

    def __init__(self, db=None, seed=()):
        self._db = db
        self._lock = threading.Lock()
        self._ids: set[int] = {int(i) for i in seed if _valid_id(i)}
        if db is not None:
            for row in db.query("SELECT id FROM known_stores LIMIT ?", (self.MAX,)):
                self._ids.add(int(row["id"]))

    def __contains__(self, store_id) -> bool:
        return _valid_id(store_id) and int(store_id) in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, ids) -> None:
        with self._lock:
            new = [int(i) for i in ids if _valid_id(i) and int(i) not in self._ids]
            new = list(dict.fromkeys(new))[:max(0, self.MAX - len(self._ids))]
            if not new:
                return
            self._ids.update(new)
        if self._db is not None:
            now = time.time()
            with self._db.transaction() as conn:
                conn.executemany("INSERT OR IGNORE INTO known_stores (id, added_at) VALUES (?, ?)",
                                 [(i, now) for i in new])


def _valid_id(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 < value < 10**10
    # isascii(): "²".isdigit() is True, but int("²") raises.
    return isinstance(value, str) and value.isascii() and value.isdigit() and 0 < len(value) <= 10 and int(value) > 0


def normalize_postcode(text: str) -> str | None:
    """Return "1234AB"/"1234", or None if ``text`` is not a postal code."""
    m = POSTCODE_RE.match(text or "")
    if not m:
        return None
    return (m.group(1) + (m.group(2) or "")).upper()


def search_stores(gateway: AHGateway, postcode: str) -> list[Store]:
    """Stores nearest to ``postcode`` (already normalized)."""
    return mapping.stores(gateway.search_stores(postcode))
