"""Protection against password guessing.

- :class:`LoginLimiter` counts failed sign-ins per key: a client key (IPv4
  address or IPv6 /64, ``ratelimit.client_key``) or an account
  (``account:<name>``). Too many recent failures lock that key.
- :class:`LockoutPolicy` applies both at once: a sign-in is blocked when the
  client OR the account is locked (guessing spread over many addresses still
  hits the account limit).
- :func:`login_capacity`: a server-wide cap on sign-in attempts, checked
  before the expensive password hash.
- :func:`password_check_slot`: at most two password checks run at once.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from ..container import services
from ..ratelimit import SHARED, client_key

# The account key of the admin password (it has no user name).
ADMIN_PASSWORD_ACCOUNT = "*admin-password*"

# At most this many password checks at once (scrypt uses ~32 MiB and a lot of
# CPU each): a flood of sign-in attempts can't exhaust the server's memory.
_PASSWORD_CHECKS = threading.BoundedSemaphore(2)


@contextlib.contextmanager
def password_check_slot() -> Iterator[None]:
    with _PASSWORD_CHECKS:
        yield


def login_capacity() -> bool:
    """Server-wide cap on ADMIN-PASSWORD attempts (LOGIN_ATTEMPTS_PER_MINUTE,
    all clients together), checked BEFORE the password hash is computed: every
    check costs ~32 MiB and CPU (scrypt), so a flood of attempts from many
    addresses must not exhaust the server. 0 = off.

    Only for work that is expensive to the server: anything cheap that anyone
    can trigger (e.g. starting a sign-in) must NOT spend this shared
    budget, or it becomes a way to lock everyone out. Those get a per-client
    limit instead (``web/login.py``)."""
    s = services()
    return s.rate_limiter.allow("login", SHARED, s.settings.limits.login_attempts_per_minute, 60)


class LoginLimiter:
    """In-memory failure counters (single process), per key.

    Memory is bounded (``max_keys``), but a key that is currently LOCKED is
    never forgotten: dropping it would lift the lock, and an attacker could
    flush a target account's lock with failures under thousands of junk names.
    When there are too many keys, keys without recent failures go first, then
    the oldest keys that are not locked. Checking a key never creates one.
    """

    def __init__(self, max_keys: int = 10_000, clock=time.monotonic):
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._clock = clock
        # key -> [failure times, max_failures, window] (the limits it was counted with)
        self._failures: dict[str, list] = {}

    def _recent(self, entry, now: float) -> list[float]:
        times, _, window = entry
        return [t for t in times if now - t < window]

    def locked(self, key: str, max_failures: int, window: float) -> bool:
        now = self._clock()
        with self._lock:
            entry = self._failures.get(key)
            if entry is None:
                return False
            entry[0] = [t for t in entry[0] if now - t < window]
            return len(entry[0]) >= max_failures

    def fail(self, key: str, max_failures: int, window: float) -> None:
        now = self._clock()
        with self._lock:
            entry = self._failures.pop(key, None) or [[], max_failures, window]
            entry[0] = [t for t in entry[0] if now - t < window] + [now]
            entry[1], entry[2] = max_failures, window
            self._failures[key] = entry           # re-inserted: now the newest key
            if len(self._failures) > self._max_keys:
                self._prune(now)

    def _prune(self, now: float) -> None:
        """Lock held. Drop expired keys, then the oldest unlocked ones."""
        for key in [k for k, e in self._failures.items() if not self._recent(e, now)]:
            del self._failures[key]
        if len(self._failures) <= self._max_keys:
            return
        for key, entry in list(self._failures.items()):     # oldest first
            if len(self._failures) <= self._max_keys:
                break
            if len(self._recent(entry, now)) < entry[1]:      # not locked: may go
                del self._failures[key]
        # Only locked keys left: keep them all (each one took max_failures
        # failed attempts, which the server-wide login cap slows down).

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        """Forget every key: lifts all locks (the admin panel's "Unlock all")."""
        with self._lock:
            self._failures.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._failures)

    def snapshot(self) -> list[dict]:
        """Keys with recent failures, for the admin panel: locked ones first,
        then the most failures. ``seconds_left``: until a locked key opens
        again by itself (None when not locked). Changes nothing."""
        now = self._clock()
        out = []
        with self._lock:
            for key, (times, max_failures, window) in self._failures.items():
                recent = sorted(t for t in times if now - t < window)
                if not recent:
                    continue
                locked = len(recent) >= max_failures
                # Locked until the max_failures-th newest failure leaves the window.
                left = max(0.0, recent[-max_failures] + window - now) if locked else None
                out.append({"key": key, "failures": len(recent), "max_failures": max_failures,
                            "locked": locked, "seconds_left": left})
        out.sort(key=lambda e: (not e["locked"], -e["failures"], e["key"]))
        return out


def _account_key(account: str | None) -> str | None:
    return "account:" + account.strip().lower() if account and account.strip() else None


@dataclass(frozen=True)
class LockoutPolicy:
    """Failure limits for one sign-in form: ``max_failures`` per client and
    ``account_max_failures`` per account, both within ``window`` seconds."""

    limiter: LoginLimiter
    max_failures: int
    account_max_failures: int
    window: float

    def blocked(self, account: str | None) -> bool:
        """Too many recent failures from this client, or for this account."""
        if self.limiter.locked(client_key(), self.max_failures, self.window):
            return True
        key = _account_key(account)
        return key is not None and self.limiter.locked(key, self.account_max_failures, self.window)

    def failed(self, account: str | None) -> None:
        self.limiter.fail(client_key(), self.max_failures, self.window)
        if key := _account_key(account):
            self.limiter.fail(key, self.account_max_failures, self.window)

    def succeeded(self, account: str | None) -> None:
        self.limiter.reset(client_key())
        if key := _account_key(account):
            self.limiter.reset(key)


def admin_policy() -> LockoutPolicy:
    s = services()
    a = s.settings.auth
    return LockoutPolicy(s.admin_limiter, a.admin_max_failures, a.account_max_failures, a.admin_lockout_seconds)

