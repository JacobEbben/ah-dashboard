"""The app's SQLite database (``DATABASE_FILE``, default instance/ah_dashboard.db).

Holds the dashboard users, their synced settings and the encrypted AH login.
The app runs as ONE process, so one connection guarded by a lock is enough
(Waitress threads take turns). WAL journal mode, foreign keys on.

The encryption key for the AH login (the generated seal secret in instance/
or the TOKEN_SEAL_SECRET setting) deliberately stays OUTSIDE the database.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# Schema version (SQLite ``user_version``). Deployed databases are at 2 (3 once
# this version has run), so it must never go down (the app refuses a database with a higher number).
#
# How to change the schema:
# - a NEW table or index: add it to _SCHEMA (CREATE ... IF NOT EXISTS); every
#   start creates what is missing, no version bump needed;
# - a change to an EXISTING table (add/rename a column, new constraint, data
#   fix): add a step to MIGRATIONS under the next number and bump
#   SCHEMA_VERSION to it. _SCHEMA must describe the end result (a fresh
#   database is created from it directly and starts at SCHEMA_VERSION).
SCHEMA_VERSION = 3
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,      -- internal name (made from the e-mail address)
    password_hash   TEXT NOT NULL,         -- unused since Google sign-in: always ''
    created_at      REAL NOT NULL,
    last_login      REAL,
    disabled        INTEGER NOT NULL DEFAULT 0,
    session_version TEXT NOT NULL,
    is_admin        INTEGER NOT NULL DEFAULT 0,
    email           TEXT,                  -- lower case; links a Google account on its first sign-in
    google_sub      TEXT                   -- Google's permanent account id, once linked
);
CREATE UNIQUE INDEX IF NOT EXISTS users_by_email ON users (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS users_by_google ON users (google_sub) WHERE google_sub IS NOT NULL;
CREATE TABLE IF NOT EXISTS user_prefs (
    username   TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
    prefs      TEXT NOT NULL,          -- JSON object: settings key -> text value
    updated_at REAL NOT NULL
);
-- Store ids known to exist (store search results, stores with bargains), so
-- only those may be loaded freely (services/stores.KnownStores). A new table:
-- older app versions ignore it, so no SCHEMA_VERSION bump is needed.
CREATE TABLE IF NOT EXISTS known_stores (
    id       INTEGER PRIMARY KEY,
    added_at REAL NOT NULL
);
-- Active sign-ins (sessions.SessionStore): a session id in the cookie must be
-- listed here, so logout ends a session everywhere. username NULL = a sign-in
-- with the admin password. A new table: no SCHEMA_VERSION bump needed.
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    username   TEXT REFERENCES users(username) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (username, created_at);
-- The cached national Bonus week (services/bonus.BonusStore), one row: public AH
-- data, kept so a restart or redeploy doesn't load it from AH again. A new table:
-- no SCHEMA_VERSION bump needed (older versions ignore it).
CREATE TABLE IF NOT EXISTS bonus_week (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    loaded_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS secrets (
    name  TEXT PRIMARY KEY,            -- e.g. "ah_tokens"
    value BLOB NOT NULL                -- already encrypted by the caller
);
"""


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """ALTER TABLE ADD COLUMN, unless the column is already there."""
    if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_to_google(conn: sqlite3.Connection) -> None:
    """Google sign-in replaces passwords: e-mail and Google id columns, and
    the old password hashes are wiped (no longer used, so not kept)."""
    _add_column(conn, "users", "email", "TEXT")
    _add_column(conn, "users", "google_sub", "TEXT")
    conn.execute("UPDATE users SET password_hash = ''")


# Upgrade steps: MIGRATIONS[n] upgrades a database from version n-1 to n. Each
# runs in its own transaction together with the version update.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: lambda conn: None,                                   # the first schema (users, user_prefs, secrets)
    2: lambda conn: _add_column(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
    3: lambda conn: _migrate_to_google(conn),
}


# The write-ahead log: by default SQLite copies it into the database file every
# 1000 pages (~4 MB) and never shrinks the file afterwards. Every settings sync
# rewrites a user's whole settings row, so the log stayed at ~4 MB even with one
# user. Now it is copied every WAL_CHECKPOINT_PAGES pages (~512 KB with 4 KB
# pages), trimmed back to WAL_SIZE_LIMIT when it starts over, and emptied by
# checkpoint() (the hourly scheduler job).
WAL_CHECKPOINT_PAGES = 128
WAL_SIZE_LIMIT = 512 * 1024


class Database:
    def __init__(self, path: str | None):
        """``path`` None or "": an in-memory database (tests)."""
        self.path = path or None
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path or ":memory:", check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            if self.path:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
                self._conn.execute(f"PRAGMA wal_autocheckpoint = {WAL_CHECKPOINT_PAGES}")
                self._conn.execute(f"PRAGMA journal_size_limit = {WAL_SIZE_LIMIT}")
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            self._prepare_schema()

    def _prepare_schema(self) -> None:
        """Create a fresh database, or upgrade an older one step by step."""
        conn = self._conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database {self.path} is from a newer version of the app (schema {version})")
        fresh = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0
        if fresh:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        for target in range(version + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                MIGRATIONS[target](conn)
                conn.execute(f"PRAGMA user_version = {target}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            log.info("database upgraded to schema %d", target)
        conn.executescript(_SCHEMA)   # new tables and indexes (IF NOT EXISTS)

    # ---- access ----------------------------------------------------------------------
    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params=()) -> Any:
        """The first column of the first row (e.g. a COUNT), or None."""
        row = self.one(sql, params)
        return None if row is None else row[0]

    def execute(self, sql: str, params=()) -> int:
        """Run one statement (autocommit); returns the number of changed rows."""
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    @contextlib.contextmanager
    def transaction(self):
        """Several statements as one unit: all or nothing."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ---- upkeep -----------------------------------------------------------------------
    def checkpoint(self) -> None:
        """Copy the write-ahead log into the database file and empty it."""
        if self.path:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def file_sizes(self) -> dict[str, int]:
        """Bytes on disk: ``data`` (the database file) and ``log`` (its write-ahead log)."""
        def size(path: str) -> int:
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        if not self.path:
            return {"data": 0, "log": 0}
        return {"data": size(self.path), "log": size(self.path + "-wal")}

    # ---- small key/value store for encrypted blobs ---------------------------------------
    def get_secret(self, name: str) -> bytes | None:
        row = self.one("SELECT value FROM secrets WHERE name = ?", (name,))
        return bytes(row["value"]) if row else None

    def set_secret(self, name: str, value: bytes) -> None:
        self.execute("INSERT INTO secrets (name, value) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, sqlite3.Binary(value)))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
