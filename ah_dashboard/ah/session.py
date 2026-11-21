"""AH session: the server's login plus automatic renewal on auth failures.

Services call ``session.query(...)`` exactly like ``AHClient.query``. The
session adds the current access token and, when AH rejects it, renews it once
via the refresh token and retries the request.

It also keeps the whole server polite towards AH, whose CDN blocks the login's
traffic ("Access Denied", HTTP 403) after a burst of ~160 quick requests:

- a :class:`Pacer` lets requests start at ``rate`` per second on average, with
  short bursts of up to ``burst`` (a visitor's few stores stay instant; a
  Bonus load of ~160 requests is spread over more than a minute);
- after AH refuses us (:class:`AHThrottled`), every AH request fails at once
  for ``throttle_pause`` seconds without contacting AH, so a block is not
  prolonged by the requests still queued or by retries.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .auth import RefreshCooldown, TokenManager
from .client import AHClient, AHError, AHGraphQLError, AHThrottled, AHTransportError

log = logging.getLogger(__name__)

# How AH reports an access token it no longer accepts:
#  - a valid-looking but expired token: "Subgraph errors redacted" with
#    valueCompletion "Cannot return null for non-nullable type BargainItem"
#    (seen in practice, see services/bargains.py history);
#  - SESSION_EXPIRED / UNAUTHENTICATED error codes (appie-go's API notes);
#  - HTTP 401 {"error":"invalid_token"}.
_AUTH_SYMPTOMS = (
    "Cannot return null for non-nullable type",
    "SESSION_EXPIRED",
    "UNAUTHENTICATED",
    "invalid_token",
)


def is_auth_failure(exc: AHError) -> bool:
    if isinstance(exc, AHTransportError) and exc.status == 401:
        return True
    if isinstance(exc, AHGraphQLError):
        text = str(exc)
        return any(symptom in text for symptom in _AUTH_SYMPTOMS)
    return False


class Pacer:
    """A token bucket shared by all threads: ``wait()`` returns when the next
    request may start. ``burst`` requests may start at once; after that they
    start ``1 / rate`` seconds apart. ``rate <= 0`` switches pacing off."""

    def __init__(self, rate: float, burst: int = 1, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = rate
        self.burst = max(1, burst)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._tokens = float(self.burst)
        self._at = clock()

    def wait(self) -> None:
        if self.rate <= 0:
            return
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
            self._at = now
            self._tokens -= 1     # reserve a place in the queue (may go below 0)
            delay = 0.0 if self._tokens >= 0 else -self._tokens / self.rate
        if delay > 0:
            self._sleep(delay)


class AHSession:
    """``max_concurrent`` caps the AH requests in flight for the whole server
    (every visitor and every parallel load share the one AH login); ``rate``
    and ``burst`` pace how fast they start (:class:`Pacer`), and
    ``throttle_pause`` is how long all requests stop after AH refuses us."""

    def __init__(self, tokens: TokenManager, make_client: Callable[[str], AHClient],
                 max_concurrent: int = 8, rate: float = 0, burst: int = 10, throttle_pause: float = 900,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self._tokens = tokens
        self._make_client = make_client
        self._slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self._pacer = Pacer(rate, burst, clock=clock, sleep=sleep)
        self._throttle_pause = throttle_pause
        self._clock = clock
        self._paused_until = 0.0
        self._stats_lock = threading.Lock()
        self._stats: dict[str, Any] = {"requests": 0, "failures": 0, "last_ok": None, "last_failure": None,
                                       "throttled": 0, "paused_seconds": 0}

    def stats(self) -> dict:
        """Counters for the admin panel (since start; no error details)."""
        with self._stats_lock:
            out = dict(self._stats)
        out["paused_seconds"] = max(0, round(self._paused_until - self._clock()))
        return out

    def paused(self) -> bool:
        """True while AH requests are paused after AH refused us."""
        return self._clock() < self._paused_until

    def _pause(self) -> None:
        with self._stats_lock:
            first = not self.paused()
            self._paused_until = max(self._paused_until, self._clock() + self._throttle_pause)
            self._stats["throttled"] += 1
        if first:
            log.warning("AH refuses our requests: pausing all AH requests for %d s", self._throttle_pause)

    def _count(self, ok: bool) -> None:
        with self._stats_lock:
            self._stats["requests"] += 1
            if ok:
                self._stats["last_ok"] = time.time()
            else:
                self._stats["failures"] += 1
                self._stats["last_failure"] = time.time()

    def _run(self, fn, client: AHClient):
        self._pacer.wait()
        with self._slots:
            # Checked again after waiting: AH may have refused another request meanwhile.
            if self.paused():
                raise AHThrottled("AH requests are paused: AH refused too many requests")
            try:
                result = fn(client)
            except AHThrottled:
                self._count(False)
                self._pause()
                raise
            except AHError:
                self._count(False)
                raise
            self._count(True)
            return result

    @property
    def configured(self) -> bool:
        return self._tokens.configured

    def query(self, document: str, variables: dict | None = None, operation: str | None = None) -> dict:
        return self._call(lambda c: c.query(document, variables, operation), operation or "query")

    def get(self, path: str, params: dict | None = None):
        """REST GET (see AHClient.get), with the same renewal on auth failures."""
        return self._call(lambda c: c.get(path, params), path)

    def _call(self, fn, label: str):
        if self.paused():
            raise AHThrottled(f"AH requests are paused ({label}): AH refused too many requests")
        token = self._tokens.access_token()
        try:
            return self._run(fn, self._make_client(token))
        except AHError as exc:
            if not is_auth_failure(exc):
                raise
            log.info("AH rejected the access token (%s); renewing it", label)
            try:
                new_token = self._tokens.refresh(failed_token=token)
            except RefreshCooldown:
                raise exc from None  # just renewed: this failure is not about the token
            return self._run(fn, self._make_client(new_token))
