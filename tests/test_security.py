"""Security regression tests: no redirect following, https-only upstream,
generic error messages with details written to the error log file."""

import http.server
import threading

import pytest

from ah_dashboard import create_app
from ah_dashboard.ah.client import AHClient, AHGraphQLError, AHTransportError
from ah_dashboard.config import TestingConfig
from ah_dashboard.container import services


# ---- 1. redirects are never followed (token must not leak) -----------------
class _RedirectingHandler(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):
        # Read the body first: on Windows, closing with unread data resets the
        # connection before the client can read the 302 (flaky test).
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).hits.append(("POST", self.path, self.headers.get("Authorization")))
        self.send_response(302)
        self.send_header("Location", "/steal")
        self.end_headers()

    def do_GET(self):
        type(self).hits.append(("GET", self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data": {}}')

    def log_message(self, *args):
        pass


@pytest.fixture
def redirect_server():
    _RedirectingHandler.hits = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_redirect_is_not_followed_and_token_not_forwarded(redirect_server):
    client = AHClient("secret-token", redirect_server + "/graphql", timeout=5)
    with pytest.raises(AHTransportError) as info:
        client.query("query Q { x }")
    assert info.value.status == 302
    # Only the original request was made; the redirect target was never hit.
    assert _RedirectingHandler.hits == [("POST", "/graphql", "Bearer secret-token")]


def test_opener_has_no_file_scheme(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text('{"data": {"leak": true}}')
    with pytest.raises(Exception) as info:
        AHClient("t", secret.as_uri()).query("query Q { x }")
    assert not isinstance(info.value, AHGraphQLError)


def test_config_rejects_non_https_upstream():
    class Bad(TestingConfig):
        AH_GRAPHQL_URL = "file:///etc/passwd"

    with pytest.raises(ValueError, match="https"):
        create_app(Bad)


# ---- 3. generic errors to users, details to the log file --------------------
@pytest.fixture
def logged_app(tmp_path, fake_client):
    class LogConfig(TestingConfig):
        ERROR_LOG_FILE = str(tmp_path / "logs" / "errors.log")

    app = create_app(LogConfig)
    services(app).ah = fake_client
    yield app, tmp_path / "logs" / "errors.log"
    # Close file handles so the temp dir can be removed on Windows.
    import logging
    for h in list(logging.getLogger("ah_dashboard").handlers):
        h.close()


def _log_text(path):
    import logging
    for h in logging.getLogger("ah_dashboard").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_store_failure_detail_only_in_log(logged_app, fake_client):
    app, log_path = logged_app
    fake_client.fail_stores = {"1003"}
    data = app.test_client().post("/api/bargains", json={"stores": [{"id": 1003, "name": "Park"}]}).get_json()
    [err] = data["storeErrors"]
    assert "unavailable" not in str(data)
    text = _log_text(log_path)
    assert f"[ref {err['ref']}]" in text and "store 1003 unavailable" in text
    assert "test-token" not in text


def test_search_upstream_error_is_generic(logged_app, fake_client, monkeypatch):
    app, log_path = logged_app

    def boom(*a, **k):
        raise AHTransportError("HTTP 500: internal stack trace from AH", 500)

    monkeypatch.setattr(fake_client, "query", boom)
    resp = app.test_client().get("/api/stores?postcode=1234AB")
    body = resp.get_json()["error"]
    assert resp.status_code == 502
    assert body["code"] == "upstream_error"
    assert "stack trace" not in body["message"]
    assert "internal stack trace from AH" in _log_text(log_path)
    assert f"[ref {body['ref']}]" in _log_text(log_path)


def test_unexpected_exception_is_generic_and_logged(logged_app, fake_client, monkeypatch):
    app, log_path = logged_app

    def crash(*a, **k):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(fake_client, "query", crash)
    resp = app.test_client().get("/api/stores?postcode=1234AB")
    body = resp.get_json()["error"]
    assert resp.status_code == 500
    assert body == {"code": "internal_error", "message": "Unexpected server error.", "ref": body["ref"]}
    text = _log_text(log_path)
    assert "hunter2" in text and "Traceback" in text


def test_validation_errors_do_not_echo_input(client):
    resp = client.post("/api/bargains", json={"stores": [{"id": "<script>"}]})
    assert "<script>" not in resp.get_data(as_text=True)


# ---- fresh review fixes ------------------------------------------------------
def test_generated_secret_is_persisted_in_instance_folder(tmp_path):
    from types import SimpleNamespace

    from ah_dashboard.sealing import SECRET_FILENAME, _instance_secret
    fake_app = SimpleNamespace(instance_path=str(tmp_path / "instance"))
    first = _instance_secret(fake_app)
    assert len(first) >= 32
    assert (tmp_path / "instance" / SECRET_FILENAME).read_text().strip() == first
    assert _instance_secret(fake_app) == first  # stable across restarts
