"""Dashboard user accounts, in the SQLite database (table ``users``, ``db.py``).

Users sign in through the central login service (``auth.login_client``, OpenID
Connect); there are no passwords here. Anyone with an account there may sign in:
the first sign-in creates the account (``sign_in_login``), up to ``max_users``.
An account is matched by the login service's permanent user id (``login_sub``),
never by name alone; an account WITHOUT a login-service id yet (from the Google
era, or made by the admin) is linked on the first sign-in with the same username.
The admin can unlink an account (``unlink_login``); the next sign-in with that
username links again.

Every user has a ``session_version``: disabling, deleting or relinking the
user replaces it, which ends that user's sessions everywhere (see
``auth.access.current_user``).

Synced settings ("prefs") are kept per user in the ``user_prefs`` table: a
whitelisted set of the browser's settings keys (``SYNC_KEYS``) with string
values, size-limited. Deleting a user deletes their settings (foreign key).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import time
from typing import ClassVar

from ..db import Database

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}")

# Browser settings (localStorage keys, see static/js/core/storage.js) that
# follow a signed-in user to other devices. Panel open/closed states stay per
# device. The ONLY list: the page gets it in its app-config (syncKeys).
SYNC_KEYS = frozenset({
    "ah_settings", "ah_presets", "ah_preset_order", "ah_stores", "ah_lang", "ah_theme", "ah_view",
    "ah_bonus_filters", "ah_bonus_presets", "ah_bonus_preset_order", "ah_blacklist", "ah_liked", "ah_show_hidden",
    "ah_cart", "ah_cart_removed",
})
MAX_PREF_VALUE = 400 * 1024       # one setting (the hidden/liked lists are the largest)
MAX_PREFS_TOTAL = 900 * 1024      # all settings of one user


class UserError(ValueError):
    """A user action that can't be done; ``code`` says why."""

    MESSAGES: ClassVar[dict[str, str]] = {
        "invalid_username": "Use 3–32 characters: lowercase letters, digits, dot, dash or underscore, starting with a letter or digit.",
        "invalid_email": "That is not an e-mail address.",
        "exists": "A user with that name already exists.",
        "email_taken": "Another user already has that e-mail address.",
        "not_found": "That user doesn't exist.",
        "too_many": "The maximum number of users is reached.",
        "disabled": "This account is disabled.",
        "invalid_prefs": "Settings must be an object of known keys with text values.",
        "prefs_too_large": "These settings are too large to store.",
        "last_admin": "This is the last admin and there is no admin password configured; promote another user first.",
    }

    def __init__(self, code: str):
        super().__init__(self.MESSAGES.get(code, code))
        self.code = code


def normalize(name: str) -> str:
    return str(name or "").strip().lower()


def normalize_email(email: str) -> str:
    """Lower case, trimmed; raises ``invalid_email`` for anything else."""
    value = str(email or "").strip().lower()
    if not EMAIL_RE.fullmatch(value):
        raise UserError("invalid_email")
    return value


def username_from_email(email: str) -> str:
    """A readable internal name from the address's local part (fits USERNAME_RE)."""
    local = re.sub(r"[^a-z0-9._-]+", ".", email.split("@", 1)[0].lower()).strip("._-")[:24]
    if len(local) < 3:
        local = (local + "user")[:24] if local else "user"
    return local


def validate_prefs(prefs) -> dict[str, str]:
    """Known keys with string values only, within the size limits."""
    if not isinstance(prefs, dict):
        raise UserError("invalid_prefs")
    clean, total = {}, 0
    for key, value in prefs.items():
        if key not in SYNC_KEYS or not isinstance(value, str):
            raise UserError("invalid_prefs")
        if len(value) > MAX_PREF_VALUE:
            raise UserError("prefs_too_large")
        total += len(value)
        clean[key] = value
    if total > MAX_PREFS_TOTAL:
        raise UserError("prefs_too_large")
    return clean


class UserStore:
    def __init__(self, db: Database | None = None, max_users: int = 1000):
        """``db`` None: a fresh in-memory database (tests)."""
        self._db = db if db is not None else Database(None)
        self.max_users = max_users

    @staticmethod
    def _row(row) -> dict:
        return {
            "username": row["username"],
            "created_at": row["created_at"],
            "last_login": row["last_login"],
            "disabled": bool(row["disabled"]),
            "session_version": row["session_version"],
            "is_admin": bool(row["is_admin"]),
            "email": row["email"],
            "login_sub": row["login_sub"],
        }

    # ---- queries ----------------------------------------------------------------------
    def list(self) -> list[dict]:
        """All users, sorted by name."""
        return [self._row(r) for r in self._db.query("SELECT * FROM users ORDER BY username")]

    def get(self, name: str) -> dict | None:
        row = self._db.one("SELECT * FROM users WHERE username = ?", (normalize(name),))
        return self._row(row) if row else None

    def by_login(self, sub: str) -> dict | None:
        row = self._db.one("SELECT * FROM users WHERE login_sub = ?", (str(sub),))
        return self._row(row) if row else None

    def by_email(self, email: str) -> dict | None:
        row = self._db.one("SELECT * FROM users WHERE email = ?", (str(email).strip().lower(),))
        return self._row(row) if row else None

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM users"))

    def prefs_sizes(self) -> dict[str, int]:
        """Bytes of each user's synced settings, by user name (users with none are left out)."""
        rows = self._db.query("SELECT username, length(CAST(prefs AS BLOB)) AS size FROM user_prefs")
        return {r["username"]: int(r["size"] or 0) for r in rows}

    def count_admins(self) -> int:
        """Enabled users with admin rights."""
        return int(self._db.scalar("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0"))

    # ---- sign-in through the login service ------------------------------------------------
    def sign_in_login(self, sub: str, username: str) -> tuple[dict, bool]:
        """The account for this login-service user, linking or creating it.
        Returns (user, created). Raises ``disabled``, ``too_many``, ``invalid_username``.

        1. linked before (same ``login_sub``): that account;
        2. an account with this username and no login-service id yet: link it;
        3. otherwise a new account (named after the username; another name if taken).
        """
        name = normalize(username)
        if not USERNAME_RE.fullmatch(name):
            raise UserError("invalid_username")
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE login_sub = ?", (sub,)).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM users WHERE username = ? AND login_sub IS NULL", (name,)).fetchone()
                if row is not None:
                    conn.execute("UPDATE users SET login_sub = ? WHERE username = ?", (sub, row["username"]))
            created = False
            if row is None:
                if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= self.max_users:
                    raise UserError("too_many")
                free = self._free_name(conn, name)
                conn.execute(
                    "INSERT INTO users (username, password_hash, created_at, last_login, disabled, session_version, "
                    "login_sub) VALUES (?, '', ?, NULL, 0, ?, ?)",
                    (free, time.time(), secrets.token_urlsafe(9), sub))
                created = True
                row = conn.execute("SELECT * FROM users WHERE username = ?", (free,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM users WHERE username = ?", (row["username"],)).fetchone()
            user = self._row(row)
        if user["disabled"]:
            raise UserError("disabled")
        return user, created

    def unlink_login(self, name: str) -> None:
        """Forget the login-service user this account is linked to and sign it out
        everywhere: the next sign-in with this username links again (e.g. after the
        account was re-created at the login service)."""
        self._update(name, "login_sub = NULL, session_version = ?", (secrets.token_urlsafe(9),))

    @staticmethod
    def _free_name(conn, base: str) -> str:
        name, n = base, 1
        while conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            n += 1
            name = f"{base[:24]}-{n}"
        return name

    # ---- changes (admin, tests) ---------------------------------------------------------------
    def create(self, name: str, email: str | None = None) -> dict:
        """An account without a login-service link yet: it is linked on the first
        sign-in with the same username (``email`` is only kept, never used to link)."""
        name = normalize(name)
        if not USERNAME_RE.fullmatch(name):
            raise UserError("invalid_username")
        address = normalize_email(email) if email else None
        with self._db.transaction() as conn:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= self.max_users:
                raise UserError("too_many")
            if address and conn.execute("SELECT 1 FROM users WHERE email = ?", (address,)).fetchone():
                raise UserError("email_taken")
            try:
                conn.execute("INSERT INTO users (username, password_hash, created_at, last_login, disabled, session_version, email) "
                             "VALUES (?, '', ?, NULL, 0, ?, ?)", (name, time.time(), secrets.token_urlsafe(9), address))
            except sqlite3.IntegrityError:
                raise UserError("exists") from None
        created = self.get(name)
        assert created is not None
        return created

    def set_disabled(self, name: str, disabled: bool) -> None:
        if disabled:   # also sign out everywhere
            self._update(name, "disabled = 1, session_version = ?", (secrets.token_urlsafe(9),))
        else:
            self._update(name, "disabled = 0", ())

    def set_admin(self, name: str, is_admin: bool) -> None:
        """Give or take admin rights. Taking them away ends the admin access of
        that user's sessions at once (checked on every admin request)."""
        self._update(name, "is_admin = ?", (1 if is_admin else 0,))

    def delete(self, name: str) -> None:
        if self._db.execute("DELETE FROM users WHERE username = ?", (normalize(name),)) == 0:
            raise UserError("not_found")

    def record_login(self, name: str) -> None:
        try:
            self._update(name, "last_login = ?", (time.time(),))
        except UserError:
            pass

    def _update(self, name: str, assignments: str, params: tuple) -> None:
        if self._db.execute(f"UPDATE users SET {assignments} WHERE username = ?", (*params, normalize(name))) == 0:
            raise UserError("not_found")

    def get_prefs(self, name: str) -> dict:
        """{"prefs": {...}, "updatedAt": float | None} for an existing user."""
        name = normalize(name)
        if self.get(name) is None:
            raise UserError("not_found")
        row = self._db.one("SELECT prefs, updated_at FROM user_prefs WHERE username = ?", (name,))
        if row is None:
            return {"prefs": {}, "updatedAt": None}
        try:
            return {"prefs": validate_prefs(json.loads(row["prefs"])), "updatedAt": row["updated_at"]}
        except (ValueError, UserError) as exc:
            log.warning("unreadable settings for user %s: %s", name, exc)
            return {"prefs": {}, "updatedAt": None}

    def set_prefs(self, name: str, prefs) -> float:
        """Replace a user's synced settings; returns the new updatedAt."""
        clean = validate_prefs(prefs)
        name = normalize(name)
        updated = time.time()
        try:
            self._db.execute(
                "INSERT INTO user_prefs (username, prefs, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET prefs = excluded.prefs, updated_at = excluded.updated_at",
                (name, json.dumps(clean), updated))
        except sqlite3.IntegrityError:          # no such user (foreign key)
            raise UserError("not_found") from None
        return updated
