"""The SQLite database."""

import pytest

from ah_dashboard.db import SCHEMA_VERSION, Database


def test_schema_version_and_settings(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    assert db.one("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert db.one("PRAGMA foreign_keys")[0] == 1
    assert db.one("PRAGMA journal_mode")[0] == "wal"


def test_newer_schema_is_refused(tmp_path):
    path = str(tmp_path / "app.db")
    db = Database(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer version"):
        Database(path)


def test_secrets_round_trip(tmp_path):
    path = str(tmp_path / "app.db")
    Database(path).set_secret("x", b"\x00blob")
    assert Database(path).get_secret("x") == b"\x00blob"
    assert Database(path).get_secret("missing") is None


# ---- the write-ahead log stays small (it used to stay at ~4 MB) ---------------------------------
def _sync_many_times(db, times=150, kb=36):
    """Like a user whose settings sync rewrites a ~36 KB row on every like."""
    db.execute("CREATE TABLE IF NOT EXISTS blobs (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(times):
        db.execute("INSERT OR REPLACE INTO blobs (id, v) VALUES (1, ?)", (f"{i:08d}" + "x" * (kb * 1024),))


def test_wal_limits_are_set(tmp_path):
    from ah_dashboard.db import WAL_CHECKPOINT_PAGES, WAL_SIZE_LIMIT
    db = Database(str(tmp_path / "app.db"))
    assert db.one("PRAGMA wal_autocheckpoint")[0] == WAL_CHECKPOINT_PAGES
    assert db.one("PRAGMA journal_size_limit")[0] == WAL_SIZE_LIMIT


def test_wal_stays_small_under_many_writes(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    _sync_many_times(db)                    # ~5 MB written in all
    assert db.file_sizes()["log"] < 1024 * 1024


def test_checkpoint_empties_the_wal(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    _sync_many_times(db, times=5)
    assert db.file_sizes()["log"] > 0
    db.checkpoint()
    sizes = db.file_sizes()
    assert sizes["log"] == 0 and sizes["data"] > 0
    assert db.scalar("SELECT substr(v, 1, 8) FROM blobs") == "00000004"   # nothing lost


def test_in_memory_database_has_no_files():
    db = Database("")
    db.checkpoint()                         # no-op, no error
    assert db.file_sizes() == {"data": 0, "log": 0}
