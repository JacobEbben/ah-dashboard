"""The connection to the central login service, as set in the admin panel.

Where the connection comes from, first match wins:
1. the admin panel (table ``login_config``; the client secret sealed with the
   server's seal secret, like the stored AH login);
2. the LOGIN_ISSUER / LOGIN_CLIENT_ID / LOGIN_CLIENT_SECRET settings (environment).

Only an ADMIN-PASSWORD session may change it (``admin/routes.py``): an admin who
signed in through the login service must not be able to point the dashboard at
another login server. Like File Share's admin pane (file_share/routes/admin.py).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..db import Database
from .login_client import Http, LoginClient, LoginError, default_http


class LoginConnection:
    def __init__(self, db: Database, fernet: Fernet, settings: Callable[[], Any], production: Callable[[], bool],
                 http: Http = default_http):
        self._db = db
        self._fernet = fernet
        self._settings = settings          # read on every use: tests replace the settings
        self._production = production
        self.http = http
        self._lock = threading.Lock()
        self._client: LoginClient | None = None
        self._client_key: tuple | None = None

    # ---- the connection ----
    def _stored(self) -> dict | None:
        row = self._db.one("SELECT issuer, client_id, secret, updated_by, updated_at FROM login_config WHERE id = 1")
        return dict(row) if row else None

    def config(self) -> dict[str, Any] | None:
        """{issuer, client_id, has_secret, source ("panel" | "settings"), updated_by, updated_at} or None."""
        row = self._stored()
        if row:
            return {"issuer": row["issuer"], "client_id": row["client_id"], "has_secret": bool(row["secret"]),
                    "source": "panel", "updated_by": row["updated_by"], "updated_at": row["updated_at"]}
        a = self._settings().auth
        if a.login_issuer or a.login_client_id:
            return {"issuer": a.login_issuer or "", "client_id": a.login_client_id or "",
                    "has_secret": bool(a.login_client_secret), "source": "settings", "updated_by": "", "updated_at": None}
        return None

    def _secret(self) -> str:
        row = self._stored()
        if row:
            try:
                return self._fernet.decrypt(row["secret"].encode()).decode() if row["secret"] else ""
            except (InvalidToken, ValueError):
                return ""          # the seal secret changed: the panel shows "not connected"; save again
        return self._settings().auth.login_client_secret or ""

    def configured(self) -> bool:
        c = self.config()
        return bool(c and c["issuer"] and c["client_id"] and self._secret())

    def client(self) -> LoginClient | None:
        """The sign-in client for the current connection (None: not configured). Kept while
        the connection is unchanged, so its key cache and replay cache stay warm."""
        c = self.config()
        secret = self._secret()
        if not c or not c["issuer"] or not c["client_id"] or not secret:
            return None
        key = (c["issuer"], c["client_id"], secret)
        with self._lock:
            if self._client is None or self._client_key != key:
                self._client = LoginClient(c["issuer"], c["client_id"], secret, production=self._production(),
                                           http=self.http)
                self._client_key = key
            return self._client

    # ---- changes (admin panel, admin-password session only) ----
    def save(self, issuer: str, client_id: str, secret: str | None, by: str) -> None:
        """Save the connection; ``secret`` None keeps the stored one (or the settings' one)."""
        sealed = self._fernet.encrypt(secret.encode()).decode() if secret else None
        if sealed is None:
            current = self._secret()
            sealed = self._fernet.encrypt(current.encode()).decode() if current else ""
        self._db.execute("INSERT OR REPLACE INTO login_config (id, issuer, client_id, secret, updated_by, updated_at) "
                         "VALUES (1, ?, ?, ?, ?, ?)", (issuer.rstrip("/"), client_id, sealed, by, time.time()))

    def clear(self) -> None:
        """Forget the panel's connection (the settings' one, if any, applies again)."""
        self._db.execute("DELETE FROM login_config WHERE id = 1")

    def test(self, issuer: str) -> None:
        """Discovery and keys reachable and consistent (raises LoginError); nothing is saved."""
        probe = LoginClient(issuer, "test", "test", production=self._production(), http=self.http)
        probe.test(issuer)

    @staticmethod
    def error_reason(exc: LoginError) -> str:
        return exc.reason
