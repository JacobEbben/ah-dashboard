"""Per-client rate limits for the public API.

Anonymous endpoints that make the server call AH (``/api/bargains``,
``/api/stores``) are limited per client address, so one visitor cannot make
the server hammer AH with the shared AH login (AH could then block it for
everyone). Token bucket per (bucket, address): ``rate`` requests per
``per`` seconds, bursts up to ``rate``. In memory, single process.

The client address is ``request.remote_addr``. Behind a reverse proxy that is
the proxy's address unless Waitress is told to trust it (``TRUSTED_PROXY``,
see serve.py), which then puts the real client address there.

Limits count per *client key* (:func:`limit_key`): the IPv4 address, or for
IPv6 the /64 network. One IPv6 connection usually owns a whole /64 (2^64
addresses), so counting single IPv6 addresses would let one visitor rotate
through fresh limits endlessly.
"""

from __future__ import annotations

import functools
import ipaddress
import threading
import time
from collections import OrderedDict

from flask import request

from .container import services
from .errors import APIError

_MAX_CLIENTS = 20_000  # bound memory: the least recently seen clients are forgotten
# The "client" of a server-wide budget (e.g. all admin-password attempts
# together). Kept apart and never forgotten: otherwise requests from 20,000
# addresses (cheap with IPv6) would push it out and reset the budget.
SHARED = "*"


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()  # key -> (tokens, at)
        self._shared: dict[str, tuple[float, float]] = {}   # server-wide budgets (SHARED), by bucket
        self.blocked = 0  # requests refused since start (admin panel)

    def allow(self, bucket: str, addr: str, rate: int, per: float) -> bool:
        if rate <= 0:
            return True  # limit switched off
        now = self._clock()
        key = (bucket, addr)
        with self._lock:
            if addr == SHARED:
                tokens, at = self._shared.get(bucket, (float(rate), now))
            else:
                tokens, at = self._buckets.pop(key, (float(rate), now))
            tokens = min(float(rate), tokens + (now - at) * rate / per)
            ok = tokens >= 1
            if ok:
                tokens -= 1
            else:
                self.blocked += 1
            if addr == SHARED:
                self._shared[bucket] = (tokens, now)
            else:
                self._buckets[key] = (tokens, now)
                while len(self._buckets) > _MAX_CLIENTS:
                    self._buckets.popitem(last=False)
            return ok

    def size(self) -> int:
        with self._lock:
            return len(self._buckets) + len(self._shared)


def client_addr() -> str:
    """The client's address as seen (for logs). Limits use :func:`client_key`."""
    return request.remote_addr or "unknown"


def limit_key(addr: str) -> str:
    """What limits count per: the IPv4 address, or the IPv6 /64 network (an
    IPv4-mapped IPv6 address counts as its IPv4 address)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_key() -> str:
    """The key every per-client limit counts by (never the raw IPv6 address)."""
    return limit_key(client_addr())


def rate_limited(bucket: str):
    """Limit an API view to RATE_LIMIT_<BUCKET> requests per
    RATE_LIMIT_<BUCKET>_SECONDS per client (429 when exceeded). ``bucket``:
    "bargains", "stores" or "bonus" (``LimitSettings.endpoint``)."""

    def decorate(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            s = services()
            limit = s.settings.limits.endpoint(bucket)
            if not s.rate_limiter.allow(bucket, client_key(), limit.rate, limit.per):
                raise APIError("rate_limited", "Too many requests. Please wait a moment and try again.", 429)
            return view(*args, **kwargs)
        return wrapper
    return decorate
