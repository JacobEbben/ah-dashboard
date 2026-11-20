"""Server-side AH login: token storage, renewal on auth failure, safety."""

import http.server
import json
import logging
import threading
import time

import pytest

from ah_dashboard import create_app
from ah_dashboard.ah.auth import AHAuthError, TokenManager, TokenStore
from ah_dashboard.ah.client import AHGraphQLError, AHTransportError
from ah_dashboard.ah.session import AHSession, is_auth_failure
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services
from ah_dashboard.db import Database
from ah_dashboard.sealing import derive_fernet

STALE = AHGraphQLError(
    [{"message": "Subgraph errors redacted", "path": ["bargainItems"]}],
    {"valueCompletion": [{"message": "Cannot return null for non-nullable type BargainItem", "path": []}]})


# ---- fake AH refresh endpoint -------------------------------------------------
class _RefreshHandler(http.server.BaseHTTPRequestHandler):
    calls = []          # (headers, body) per refresh request
    fail_status = None  # set to e.g. 400 to make refreshes fail
    counter = 0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(({k.lower(): v for k, v in self.headers.items()}, body))
        time.sleep(0.05)  # widen the race window for the concurrency test
        if type(self).fail_status:
            self.send_response(type(self).fail_status)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid_grant"}')
            return
        type(self).counter += 1
        n = type(self).counter
        payload = {"access_token": f"access-{n}", "refresh_token": f"refresh-{n}", "expires_in": 604798}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def refresh_url():
    _RefreshHandler.calls, _RefreshHandler.fail_status, _RefreshHandler.counter = [], None, 0
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RefreshHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mobile-auth/v1/auth/token/refresh"
    srv.shutdown()
    srv.server_close()


FERNET = derive_fernet("x" * 32, b"test token store")


def make_manager(refresh_url, tmp_path=None, access="access-0", refresh="refresh-0", cooldown=300):
    db = Database(str(tmp_path / "app.db")) if tmp_path else None
    return TokenManager(store=TokenStore(db, FERNET), env_access=access, env_refresh=refresh,
                        refresh_url=refresh_url, cooldown=cooldown, timeout=5)


class FakeAH:
    """GraphQL stand-in: 'access-0' is expired, anything else works."""

    def __init__(self, expired=("access-0",), error=STALE):
        self.expired = set(expired)
        self.error = error
        self.used = []

    def client(self, token):
        outer = self

        class C:
            def query(self, document, variables=None, operation=None):
                outer.used.append(token)
                if token in outer.expired:
                    raise outer.error
                return {"bargainItems": [{"product": {"id": 1}}]}
        return C()


# ---- renewal ------------------------------------------------------------------
def test_expired_token_is_renewed_and_request_retried(refresh_url):
    ah = FakeAH()
    session = AHSession(make_manager(refresh_url), ah.client)
    data = session.query("q", {"storeId": "1"}, "BargainItems")
    assert data["bargainItems"]
    assert ah.used == ["access-0", "access-1"]          # failed once, retried with new token
    headers, body = _RefreshHandler.calls[0]
    assert body == {"clientId": "appie-ios", "refreshToken": "refresh-0"}
    assert headers["x-client-name"] == "appie-ios" and headers["user-agent"].startswith("Appie/")


def test_http_401_also_triggers_renewal(refresh_url):
    ah = FakeAH(error=AHTransportError('HTTP 401: {"error":"invalid_token"}', 401))
    session = AHSession(make_manager(refresh_url), ah.client)
    session.query("q")
    assert ah.used == ["access-0", "access-1"]


def test_other_errors_do_not_trigger_renewal(refresh_url):
    ah = FakeAH(error=AHGraphQLError([{"message": "Field 'x' doesn't exist"}]))
    session = AHSession(make_manager(refresh_url), ah.client)
    with pytest.raises(AHGraphQLError):
        session.query("q")
    assert _RefreshHandler.calls == []


def test_parallel_failures_refresh_only_once(refresh_url):
    """Several stores fail at once with the same expired token: one refresh,
    so the (single-use) refresh token is never sent twice."""
    ah = FakeAH()
    session = AHSession(make_manager(refresh_url), ah.client)
    errors = []

    def worker():
        try:
            session.query("q")
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert len(_RefreshHandler.calls) == 1
    assert ah.used.count("access-1") == 6


def test_cooldown_stops_refresh_loops(refresh_url):
    """If the renewed token fails the same way, it's not the token: no 2nd refresh."""
    ah = FakeAH(expired=("access-0", "access-1"))
    session = AHSession(make_manager(refresh_url), ah.client)
    with pytest.raises(AHGraphQLError):
        session.query("q")   # refresh -> access-1 -> still failing
    with pytest.raises(AHGraphQLError):
        session.query("q")   # within cooldown: no refresh
    assert len(_RefreshHandler.calls) == 1


def test_failed_renewal_raises_auth_error_and_is_not_hammered(refresh_url):
    _RefreshHandler.fail_status = 400
    session = AHSession(make_manager(refresh_url), FakeAH().client)
    with pytest.raises(AHAuthError):
        session.query("q")
    with pytest.raises(AHAuthError):
        session.query("q")
    assert len(_RefreshHandler.calls) == 1


def test_refresh_only_configuration_gets_first_access_token(refresh_url):
    ah = FakeAH()
    session = AHSession(make_manager(refresh_url, access=None), ah.client)
    session.query("q")
    assert ah.used == ["access-1"]


# ---- storage ----------------------------------------------------------------------
def test_rotated_tokens_are_stored_encrypted_and_survive_restart(refresh_url, tmp_path):
    AHSession(make_manager(refresh_url, tmp_path), FakeAH().client).query("q")
    raw = Database(str(tmp_path / "app.db")).get_secret("ah_tokens")
    assert raw and b"access-1" not in raw and b"refresh-1" not in raw     # encrypted at rest

    ah = FakeAH()
    restarted = make_manager(refresh_url, tmp_path)   # same env tokens as before
    AHSession(restarted, ah.client).query("q")
    assert ah.used == ["access-1"]          # stored, renewed pair wins over the old env pair
    assert len(_RefreshHandler.calls) == 1


def test_new_env_refresh_token_replaces_stored_pair(refresh_url, tmp_path):
    AHSession(make_manager(refresh_url, tmp_path), FakeAH().client).query("q")
    ah = FakeAH(expired=())
    relogin = make_manager(refresh_url, tmp_path, access="access-new", refresh="refresh-new")
    AHSession(relogin, ah.client).query("q")
    assert ah.used == ["access-new"]


def test_unreadable_stored_tokens_are_ignored(refresh_url, tmp_path):
    Database(str(tmp_path / "app.db")).set_secret("ah_tokens", b"garbage")
    ah = FakeAH(expired=())
    AHSession(make_manager(refresh_url, tmp_path), ah.client).query("q")
    assert ah.used == ["access-0"]


# ---- app integration ----------------------------------------------------------------
def test_tokens_never_logged(refresh_url, caplog):
    caplog.set_level(logging.DEBUG)
    _RefreshHandler.fail_status = None
    session = AHSession(make_manager(refresh_url), FakeAH().client)
    session.query("q")
    text = caplog.text
    for secret in ("access-0", "refresh-0", "access-1", "refresh-1"):
        assert secret not in text


def test_failed_login_reported_once_as_ah_auth_failed(refresh_url, fake_client):
    class Cfg(TestingConfig):
        AH_ACCESS_TOKEN = "access-0"
        AH_REFRESH_TOKEN = "refresh-0"
        AH_AUTH_REFRESH_URL = "https://example.invalid/refresh"  # validated as https

    app = create_app(Cfg)
    _RefreshHandler.fail_status = 400
    session = services(app).ah
    session._tokens._refresh_url = refresh_url        # point at the fake endpoint
    session._make_client = FakeAH().client
    data = app.test_client().post("/api/bargains", json={"stores": [{"id": 1, "name": "A"}]}).get_json()
    assert data["storeErrors"][0]["code"] == "ah_auth_failed"
    assert "invalid_grant" not in json.dumps(data)   # details only in the log


def test_refresh_url_must_be_https():
    class Bad(TestingConfig):
        AH_AUTH_REFRESH_URL = "http://api.ah.nl/mobile-auth/v1/auth/token/refresh"

    with pytest.raises(ValueError, match="AH_AUTH_REFRESH_URL"):
        create_app(Bad)


@pytest.mark.parametrize("exc,expected", [
    (STALE, True),
    (AHTransportError("HTTP 401", 401), True),
    (AHGraphQLError([{"message": "x", "extensions": {"code": "SESSION_EXPIRED"}}]), True),
    (AHTransportError("HTTP 503", 503), False),
    (AHGraphQLError([{"message": "Field 'x' doesn't exist"}]), False),
])
def test_is_auth_failure(exc, expected):
    assert is_auth_failure(exc) is expected
