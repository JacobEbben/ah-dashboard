"""Dashboard user accounts, in the SQLite database (table ``users``, ``db.py``).

Users sign in with Google (``auth.google``); there are no passwords. Anyone
with a verified Google account may sign in: the first sign-in creates the
account (``sign_in_google``), up to ``max_users``. An account is matched by
Google's permanent account id (``google_sub``), never by e-mail alone; an
account without a Google id yet (made before Google sign-in, or by the admin
setting its e-mail address) is linked on the first sign-in with that verified
address.

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
    "ah_bonus_cat", "ah_bonus_filters", "ah_bonus_groups", "ah_blacklist", "ah_liked", "ah_show_hidden",
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
        "google_taken": "That Google account is already linked to another user.",
        "already_linked": "This account is already linked to a Google account.",
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
            "google_sub": row["google_sub"],
        }

    # ---- queries ----------------------------------------------------------------------
    def list(self) -> list[dict]:
        """All users, sorted by name."""
        return [self._row(r) for r in self._db.query("SELECT * FROM users ORDER BY username")]

    def get(self, name: str) -> dict | None:
        row = self._db.one("SELECT * FROM users WHERE username = ?", (normalize(name),))
        return self._row(row) if row else None

    def by_google(self, sub: str) -> dict | None:
        row = self._db.one("SELECT * FROM users WHERE google_sub = ?", (str(sub),))
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

    # ---- Google sign-in -----------------------------------------------------------------
    def sign_in_google(self, sub: str, email: str) -> tuple[dict, bool]:
        """The account for this Google account, linking or creating it.
        Returns (user, created). Raises ``disabled``, ``too_many``.

        1. linked before (same ``google_sub``): that account (its e-mail
           address is updated when it changed at Google);
        2. an account with this e-mail address and no Google account yet: link it;
        3. otherwise a new account.
        """
        email = normalize_email(email)
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM users WHERE email = ? AND google_sub IS NULL", (email,)).fetchone()
                if row is not None:
                    conn.execute("UPDATE users SET google_sub = ? WHERE username = ?", (sub, row["username"]))
            created = False
            if row is None:
                if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= self.max_users:
                    raise UserError("too_many")
                name = self._free_name(conn, username_from_email(email))
                # The address may be on another account linked to a different
                # Google account (rare: an address moved): then don't store it twice.
                taken = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
                conn.execute(
                    "INSERT INTO users (username, password_hash, created_at, last_login, disabled, session_version, "
                    "email, google_sub) VALUES (?, '', ?, NULL, 0, ?, ?, ?)",
                    (name, time.time(), secrets.token_urlsafe(9), None if taken else email, sub))
                row = conn.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
                created = True
            elif row["email"] != email and not conn.execute(
                    "SELECT 1 FROM users WHERE email = ? AND username != ?", (email, row["username"])).fetchone():
                conn.execute("UPDATE users SET email = ? WHERE username = ?", (email, row["username"]))
                row = conn.execute("SELECT * FROM users WHERE username = ?", (row["username"],)).fetchone()
            user = self._row(row)
        if user["disabled"]:
            raise UserError("disabled")
        return user, created

    @staticmethod
    def _free_name(conn, base: str) -> str:
        name, n = base, 1
        while conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            n += 1
            name = f"{base[:24]}-{n}"
        return name

    def link_google(self, name: str, sub: str, email: str) -> None:
        """Link a Google account to an existing user (who is signed in) that
        has NONE yet. An account's Google account is never replaced this way: a
        stolen session could otherwise attach the thief's Google account and
        keep the account for good. Relinking goes through the admin (set_email,
        which unlinks and signs the user out everywhere)."""
        email, name = normalize_email(email), normalize(name)
        rec = self.get(name)
        if rec is None:
            raise UserError("not_found")
        if rec["google_sub"] and rec["google_sub"] != sub:
            raise UserError("already_linked")
        other = self.by_google(sub)
        if other is not None and other["username"] != name:
            raise UserError("google_taken")
        owner = self.by_email(email)
        if owner is not None and owner["username"] != name:
            self._update(name, "google_sub = ?", (sub,))              # the address belongs to someone else
        else:
            self._update(name, "google_sub = ?, email = ?", (sub, email))

    # ---- changes (admin, tests) ---------------------------------------------------------------
    def create(self, name: str, email: str | None = None) -> dict:
        """An account without a Google account yet; with ``email`` it is
        linked on the first Google sign-in with that address."""
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

    def set_email(self, name: str, email: str) -> None:
        """Set the address a Google account is linked by. This unlinks the
        current Google account (the next sign-in with the new address links)
        and signs the user out everywhere."""
        address = normalize_email(email)
        owner = self.by_email(address)
        if owner and owner["username"] != normalize(name):
            raise UserError("email_taken")
        self._update(name, "email = ?, google_sub = NULL, session_version = ?", (address, secrets.token_urlsafe(9)))

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
