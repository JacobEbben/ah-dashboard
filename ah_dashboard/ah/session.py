"""AH session: the server's login plus automatic renewal on auth failures.

Services call ``session.query(...)`` exactly like ``AHClient.query``. The
session adds the current access token and, when AH rejects it, renews it once
via the refresh token and retries the request.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .auth import RefreshCooldown, TokenManager
from .client import AHClient, AHError, AHGraphQLError, AHTransportError

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


class AHSession:
    """``max_concurrent`` caps the AH requests in flight for the whole server
    (every visitor and every parallel load share the one AH login)."""

    def __init__(self, tokens: TokenManager, make_client: Callable[[str], AHClient],
                 max_concurrent: int = 8):
        self._tokens = tokens
        self._make_client = make_client
        self._slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self._stats_lock = threading.Lock()
        self._stats: dict[str, Any] = {"requests": 0, "failures": 0, "last_ok": None, "last_failure": None}

    def stats(self) -> dict:
        """Counters for the admin panel (since start; no error details)."""
        with self._stats_lock:
            return dict(self._stats)

    def _count(self, ok: bool) -> None:
        with self._stats_lock:
            self._stats["requests"] += 1
            if ok:
                self._stats["last_ok"] = time.time()
            else:
                self._stats["failures"] += 1
                self._stats["last_failure"] = time.time()

    def _run(self, fn, client: AHClient):
        with self._slots:
            try:
                result = fn(client)
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
