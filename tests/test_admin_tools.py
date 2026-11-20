"""Admin tools: looking up an error by its reference, lifting login lockouts."""

import logging

import pytest
from conftest import as_admin

from ah_dashboard import create_app, errorlog
from ah_dashboard.auth.lockout import ADMIN_PASSWORD_ACCOUNT, LoginLimiter
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.logging_setup import log_error

LINE = "2026-11-19 10:15:0{n},123 {level} ah_dashboard.{src}: {msg}"


def write(path, *lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---- reading the log back --------------------------------------------------------------------
def test_entries_with_stack_traces_newest_first_across_rotated_files(tmp_path):
    log = tmp_path / "errors.log"
    write(tmp_path / "errors.log.1",
          LINE.format(n=1, level="ERROR", src="errors", msg="[ref aaaaaaaaaa] GET /api/bonus: unhandled"),
          "Traceback (most recent call last):",
          '  File "x.py", line 1, in f',
          "ValueError: boom")
    write(log,
          LINE.format(n=2, level="WARNING", src="ah.client", msg="AH HTTP 503 for BargainItems"),
          LINE.format(n=3, level="ERROR", src="services.bargains", msg="[ref bbbbbbbbbb] bargains for store 1 failed"))
    got = errorlog.recent(str(log), 5)
    assert [e.ref for e in got] == ["bbbbbbbbbb", None, "aaaaaaaaaa"]
    newest = got[0]
    assert (newest.time, newest.level, newest.source) == ("2026-11-19 10:15:03", "ERROR", "services.bargains")
    assert newest.message == "bargains for store 1 failed"            # "[ref …]" prefix removed
    old = errorlog.find(str(log), 5, "aaaaaaaaaa")                     # found in the rotated copy
    assert old is not None and old.detail[-1] == "ValueError: boom" and len(old.detail) == 3
    assert errorlog.recent(str(log), 5, limit=1) == [newest]
    assert errorlog.recent(str(log), 0) == got[:2]                     # backups=0: only the current file
    assert errorlog.find(str(log), 5, "cccccccccc") is None


def test_missing_or_odd_log_files_give_nothing(tmp_path):
    assert errorlog.recent(None, 5) == [] and errorlog.recent(str(tmp_path / "none.log"), 5) == []
    log = tmp_path / "errors.log"
    write(log, "orphan line before any entry", "2026-11-19 not a header", "")
    assert errorlog.recent(str(log), 5) == []
    log.write_bytes(b"2026-11-19 10:15:01,123 ERROR ah_dashboard.x: [ref dddddddddd] caf\xe9 \xff\n")
    (entry,) = errorlog.recent(str(log), 5)                              # bad UTF-8 is replaced, not fatal
    assert entry.ref == "dddddddddd" and entry.message.startswith("caf")


def test_long_lines_and_stack_traces_are_capped(tmp_path):
    log = tmp_path / "errors.log"
    write(log, LINE.format(n=1, level="ERROR", src="x", msg="[ref eeeeeeeeee] " + "y" * 10_000),
          *[f"  line {i}" for i in range(1000)])
    (entry,) = errorlog.recent(str(log), 0)
    assert len(entry.message) < errorlog.MAX_LINE and len(entry.detail) == errorlog.MAX_DETAIL_LINES


@pytest.mark.parametrize("text, ref", [
    ("3f9c0a1b2d", "3f9c0a1b2d"),
    ("  (reference 3F9C0A1B2D).", "3f9c0a1b2d"),
    ("ref 3f9c0a1b2d", "3f9c0a1b2d"),
    ("3f9c0a1b2", None),                 # too short
    ("3f9c0a1b2d0", None),               # too long: part of something else
    ("zzzzzzzzzz", None),
    ("", None),
])
def test_reference_in_pasted_text(text, ref):
    assert errorlog.parse_ref(text) == ref


# ---- the admin page --------------------------------------------------------------------------------
@pytest.fixture
def log_app(tmp_path):
    class Cfg(TestingConfig):
        ERROR_LOG_FILE = str(tmp_path / "logs" / "errors.log")
    app = create_app(Cfg)
    yield app
    create_app(TestingConfig)   # detach the file handler again (logging is process-wide)


def test_admin_looks_up_a_real_error_by_reference(log_app):
    try:
        raise RuntimeError("<script>alert(1)</script>")
    except RuntimeError:
        ref = log_error(logging.getLogger("ah_dashboard.test"), "upstream said %s", "<b>no</b>", exc_info=True)
    c = log_app.test_client()
    as_admin(c)
    page = c.get(f"/admin/errors?ref=(reference {ref.upper()})").get_data(as_text=True)
    assert "upstream said &lt;b&gt;no&lt;/b&gt;" in page              # escaped, never raw HTML
    assert "RuntimeError: &lt;script&gt;" in page and "<script>alert" not in page
    assert "Traceback" in page
    latest = c.get("/admin/errors").get_data(as_text=True)
    assert f"ref {ref}" in latest
    assert "No entry with reference" in c.get("/admin/errors?ref=0123456789").get_data(as_text=True)
    assert "is not a reference" in c.get("/admin/errors?ref=hello").get_data(as_text=True)
    resp = c.get("/admin/errors")
    assert resp.headers["Cache-Control"] == "no-store"


def test_error_page_is_admin_only_and_says_when_the_log_is_off(log_app):
    c = log_app.test_client()
    as_admin(c)
    with c.session_transaction() as sess:
        sess.clear()
    resp = c.get("/admin/errors?ref=0123456789")
    assert resp.status_code == 302 and "/admin/login" in resp.headers["Location"]

    app = create_app(TestingConfig)                                     # ERROR_LOG_FILE = None
    c = app.test_client()
    as_admin(c)
    assert "error log is off" in c.get("/admin/errors").get_data(as_text=True)
    assert "error log is off" in c.get("/admin/").get_data(as_text=True)


# ---- lockouts --------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_snapshot_shows_locked_keys_first_and_when_they_open():
    clock = Clock()
    lim = LoginLimiter(clock=clock)
    for _ in range(5):
        lim.fail("1.2.3.4", 5, 900)
        clock.now += 10
    lim.fail("5.6.7.8", 5, 900)
    snap = lim.snapshot()
    assert [e["key"] for e in snap] == ["1.2.3.4", "5.6.7.8"]
    assert snap[0]["locked"] and snap[0]["failures"] == 5
    assert snap[0]["seconds_left"] == pytest.approx(1000 + 900 - clock.now)   # first failure + window
    assert not snap[1]["locked"] and snap[1]["seconds_left"] is None
    assert lim.snapshot() == snap                                              # looking changes nothing
    clock.now += 900
    assert lim.snapshot() == []                                                # all expired
    lim.fail("1.2.3.4", 5, 900)
    lim.clear()
    assert lim.snapshot() == [] and lim.size() == 0


def test_admin_sees_and_lifts_lockouts(log_app):
    c = log_app.test_client()
    as_admin(c, csrf="tok")
    lim = services(log_app).admin_limiter
    account = "account:" + ADMIN_PASSWORD_ACCOUNT
    for _ in range(5):
        lim.fail("203.0.113.9", 5, 900)
    lim.fail(account, 20, 900)
    page = c.get("/admin/").get_data(as_text=True)
    assert "203.0.113.9" in page and "Admin password (all clients together)" in page
    assert "5 of 5 failed attempts" in page and "opens by itself in about 15 min" in page

    assert c.post("/admin/lockouts/unlock", data={"key": "203.0.113.9"}).status_code == 400   # no CSRF token
    assert lim.locked("203.0.113.9", 5, 900)
    resp = c.post("/admin/lockouts/unlock", data={"csrf": "tok", "key": "203.0.113.9"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("#lockouts")
    assert not lim.locked("203.0.113.9", 5, 900)
    assert "already ended" in c.post("/admin/lockouts/unlock", data={"csrf": "tok", "key": "203.0.113.9"},
                                     follow_redirects=True).get_data(as_text=True)
    c.post("/admin/lockouts/clear", data={"csrf": "tok"})
    assert lim.snapshot() == []
    assert "No failed admin-password sign-ins recently" in c.get("/admin/").get_data(as_text=True)


def test_a_locked_out_admin_password_can_be_used_again_after_unlock(log_app):
    """The point of the feature: lift a lock without waiting or restarting."""
    lim = services(log_app).admin_limiter
    for _ in range(5):
        lim.fail("127.0.0.1", 5, 900)                 # the test client's address
    c = log_app.test_client()
    as_admin(c, csrf="tok")
    c.post("/admin/lockouts/unlock", data={"csrf": "tok", "key": "127.0.0.1"})
    assert not lim.locked("127.0.0.1", 5, 900)
