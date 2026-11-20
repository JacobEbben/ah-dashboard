import gzip
import io
import json
import urllib.error

import pytest

from ah_dashboard.ah import client as ah_client
from ah_dashboard.ah.client import AHClient, AHGraphQLError, AHTransportError, decode_body
from ah_dashboard.services.stores import normalize_postcode


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_decode_body_handles_gzip_without_header():
    raw = gzip.compress(b'{"ok": true}')
    assert decode_body(raw, {}) == '{"ok": true}'


def test_query_returns_data(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        return FakeResponse(json.dumps({"data": {"x": 1}}).encode())

    monkeypatch.setattr(ah_client._OPENER, "open", fake_urlopen)
    data = AHClient("tok", "https://example.invalid").query("query Q { x }", {"a": 1}, "Q")
    assert data == {"x": 1}
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["operationName"] == "Q"


def test_gzipped_http_400_becomes_graphql_error(monkeypatch):
    body = gzip.compress(json.dumps({"errors": [{"message": "unknown field"}]}).encode())

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                     {"Content-Encoding": "gzip"}, io.BytesIO(body))

    monkeypatch.setattr(ah_client._OPENER, "open", fake_urlopen)
    with pytest.raises(AHGraphQLError, match="unknown field"):
        AHClient("tok", "https://example.invalid").query("query Q { x }")


def test_http_error_without_graphql_body(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, io.BytesIO(b"down"))

    monkeypatch.setattr(ah_client._OPENER, "open", fake_urlopen)
    with pytest.raises(AHTransportError) as info:
        AHClient("tok", "https://example.invalid").query("query Q { x }")
    assert info.value.status == 503


@pytest.mark.parametrize("text,expected", [
    ("1234AB", "1234AB"), ("1234 ab", "1234AB"), (" 1234 ", "1234"),
    ("Centrum", None), ("12345", None), ("", None),
])
def test_normalize_postcode(text, expected):
    assert normalize_postcode(text) == expected


# ---- outgoing request must match the version known to work with AH ----------
import http.server
import threading
import urllib.request as _ur


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    captured = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).captured.append((dict(self.headers), json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data": {"bargainItems": []}}')

    def log_message(self, *args):
        pass


@pytest.fixture
def capture_url():
    _CaptureHandler.captured = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/graphql"
    srv.shutdown()
    srv.server_close()


def test_default_user_agent_is_urllibs(capture_url):
    """A custom User-Agent made AH fail with "Subgraph errors redacted"; the
    default must be urllib's own, as sent by the version that worked."""
    from ah_dashboard import create_app
    from ah_dashboard.ah.gateway import AHGateway
    from ah_dashboard.container import services

    session = services(create_app("testing")).ah
    client = session._make_client("TOKEN")   # the client the app builds
    client._url = capture_url
    AHGateway(client, "AHWEBSHOP").bargain_items(1001)

    headers, body = _CaptureHandler.captured[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["user-agent"] == _ur.OpenerDirector().addheaders[0][1]  # "Python-urllib/3.x"
    assert headers["authorization"] == "Bearer TOKEN"
    assert headers["content-type"] == "application/json"
    assert body["operationName"] == "BargainItems"
    assert body["variables"] == {"storeId": "1001"}


def test_graphql_error_details_are_kept_for_the_log(monkeypatch):
    errors = [{"message": "Subgraph errors redacted",
               "extensions": {"code": "SUBREQUEST_HTTP_ERROR", "service": "bargains"},
               "path": ["bargainItems"]}]

    def fake_open(req, timeout):
        return FakeResponse(json.dumps({"data": None, "errors": errors}).encode())

    monkeypatch.setattr(ah_client._OPENER, "open", fake_open)
    with pytest.raises(AHGraphQLError) as info:
        AHClient("tok", "https://example.invalid").query("query Q { x }")
    text = str(info.value)
    assert "Subgraph errors redacted" in text
    assert "SUBREQUEST_HTTP_ERROR" in text and "bargains" in text


def test_top_level_extensions_are_kept_for_the_log(monkeypatch):
    payload = {"data": None,
               "errors": [{"message": "Subgraph errors redacted", "path": ["bargainItems"]}],
               "extensions": {"valueCompletion": [{"message": "Cannot return null for non-nullable type BargainItem", "path": []}]}}
    monkeypatch.setattr(ah_client._OPENER, "open", lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(AHGraphQLError) as info:
        AHClient("tok", "https://example.invalid").query("query Q { x }")
    assert "valueCompletion" in str(info.value)
    assert "non-nullable type BargainItem" in str(info.value)
