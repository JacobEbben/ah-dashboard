"""Active sign-ins: the server-side list, and starting/ending the cookie session.

The session cookie is signed, not stored on the server, so clearing it at
logout only clears the browser's copy: a copied cookie would keep working.
Every sign-in (dashboard user, admin user, admin password) therefore gets a
random session id that is kept in the ``sessions`` table and in the cookie
(``sid``); a request is signed in only while its id is still listed. Logout
deletes the id, so the cookie is dead everywhere, including any copy (only
that one sign-in ends; the user's other devices stay signed in).

Rows of a deleted user go with the user (foreign key). Expired rows are
removed when new ones are made and by the hourly ``prune`` job; each account
keeps at most ``MAX_PER_ACCOUNT`` sign-ins (the oldest go first).

Sign in only through :func:`begin` (via ``access.login_user`` or
``admin.start_password_session``): never set session keys by hand.
"""

from __future__ import annotations

import secrets
import time

from flask import session

from ..container import services
from . import csrf

MAX_PER_ACCOUNT = 20
SID_KEY = "sid"   # id of this sign-in in the server-side list


class SessionStore:
    def __init__(self, db, max_age: float):
        self._db = db
        self._max_age = max_age

    def create(self, username: str | None, op_sid: str = "", id_token: str = "", amr: str = "") -> str:
        """A new session id for ``username`` (None: the admin password). ``op_sid``,
        ``id_token``, ``amr``: the login service's sign-in it came from."""
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE created_at < ?", (now - self._max_age,))
            conn.execute("INSERT INTO sessions (id, username, created_at, op_sid, id_token, amr) VALUES (?, ?, ?, ?, ?, ?)",
                         (sid, username, now, op_sid, id_token, amr))
            # At most MAX_PER_ACCOUNT per account (IS: NULL = the admin password).
            conn.execute(
                "DELETE FROM sessions WHERE username IS ? AND id NOT IN ("
                " SELECT id FROM sessions WHERE username IS ? ORDER BY created_at DESC LIMIT ?)",
                (username, username, MAX_PER_ACCOUNT))
        return sid

    def valid(self, sid, username: str | None) -> bool:
        """Is ``sid`` a current sign-in of ``username`` (None: admin password)?"""
        if not isinstance(sid, str) or not sid:
            return False
        row = self._db.one("SELECT username, created_at FROM sessions WHERE id = ?", (sid,))
        return bool(row) and row["username"] == username and time.time() - row["created_at"] < self._max_age

    def delete(self, sid) -> str:
        """End a sign-in; returns its login-service ID token ('' if none)."""
        if not isinstance(sid, str) or not sid:
            return ""
        row = self._db.one("SELECT id_token FROM sessions WHERE id = ?", (sid,))
        self._db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return row["id_token"] if row else ""

    def amr(self, sid) -> str:
        """How this sign-in was made at the login service ("pwd otp" = with the code)."""
        row = self._db.one("SELECT amr FROM sessions WHERE id = ?", (sid,)) if isinstance(sid, str) else None
        return row["amr"] if row else ""

    def end_login_session(self, op_sid: str | None, sub: str | None) -> int:
        """Back-channel logout from the login service: one of its sign-ins (``op_sid``)
        or every sign-in of its user (``sub``: blocked, reset, deleted there)."""
        if op_sid:
            return self._db.execute("DELETE FROM sessions WHERE op_sid = ? AND op_sid != ''", (op_sid,))
        if sub:
            return self._db.execute("DELETE FROM sessions WHERE username IN "
                                    "(SELECT username FROM users WHERE login_sub = ?)", (sub,))
        return 0

    def started(self, username: str) -> list[float]:
        """When this user's current sign-ins started (for their data export; never the ids)."""
        rows = self._db.query("SELECT created_at FROM sessions WHERE username = ? AND created_at >= ? "
                              "ORDER BY created_at", (username, time.time() - self._max_age))
        return [row["created_at"] for row in rows]

    def prune(self) -> int:
        """Remove expired sign-ins; returns how many."""
        return self._db.execute("DELETE FROM sessions WHERE created_at < ?", (time.time() - self._max_age,))

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM sessions"))


def begin(username: str | None, op_sid: str = "", id_token: str = "", amr: str = "") -> None:
    """A fresh cookie session for a new sign-in (``username`` None: the admin
    password): the previous sign-in in this browser ends on the server too (no
    session fixation), a new session id is listed, a new CSRF token is made."""
    end()
    session.permanent = True
    session[SID_KEY] = services().sessions.create(username, op_sid, id_token, amr)
    csrf.new_token()


def end() -> str:
    """End this browser's sign-in on the server (a copied cookie dies too)
    and clear the cookie. Returns the login-service ID token of that sign-in ('' if none)."""
    id_token = services().sessions.delete(session.get(SID_KEY))
    session.clear()
    return id_token


def current_amr() -> str:
    return services().sessions.amr(session.get(SID_KEY))


def listed(username: str | None) -> bool:
    """Is this cookie's sign-in still listed for ``username``?"""
    return services().sessions.valid(session.get(SID_KEY), username)
