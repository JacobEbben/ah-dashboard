"""The MCP endpoint (/mcp): protocol, tools, limits, errors."""

import json

import pytest
from conftest import FakeAHClient, configure
from test_bonus import FakeSession

from ah_dashboard import create_app
from ah_dashboard.ah.client import AHTransportError
from ah_dashboard.container import services
from ah_dashboard.mcp import tools


def rpc(client, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


def call(client, name, arguments=None):
    resp = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200, resp.data
    return resp.get_json()["result"]


# ---- protocol ---------------------------------------------------------------------------------
def test_initialize_negotiates_the_version(client):
    resp = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "1"}})
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "no-store"
    result = resp.get_json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "ah-dashboard" and result["instructions"]
    unknown = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).get_json()["result"]
    assert unknown["protocolVersion"] == "2025-11-25"


def test_notifications_and_responses_get_202(client):
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "result": {}}).status_code == 202


def test_ping_and_unknown_methods(client):
    assert rpc(client, "ping").get_json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    err = rpc(client, "resources/list", msg_id="x").get_json()
    assert err["id"] == "x" and err["error"]["code"] == -32601


@pytest.mark.parametrize("body,code", [
    (b"{not json", -32700),
    (b'[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]', -32600),     # batches are not supported
    (b'{"id": 1, "method": "ping"}', -32600),                          # no jsonrpc
    (b'{"jsonrpc": "2.0", "id": true, "method": "ping"}', -32600),
])
def test_malformed_messages(client, body, code):
    resp = client.post("/mcp", data=body, content_type="application/json")
    assert resp.status_code == 400 and resp.get_json()["error"]["code"] == code


def test_get_and_delete_are_not_offered(client):
    for method in (client.get, client.delete):
        resp = method("/mcp")
        assert resp.status_code == 405 and resp.headers["Allow"] == "POST"


def test_unsupported_protocol_version_header(client):
    resp = rpc(client, "ping", headers={"MCP-Protocol-Version": "2020-01-01"})
    assert resp.status_code == 400
    assert rpc(client, "ping", headers={"MCP-Protocol-Version": "2025-06-18"}).status_code == 200


def test_other_sites_pages_are_refused(client, app):
    assert rpc(client, "ping", headers={"Origin": "https://evil.example"}).status_code == 403
    assert rpc(client, "ping", headers={"Origin": "http://localhost"}).status_code == 200      # our own
    configure(app, PUBLIC_BASE_URL="https://ah.example.nl")
    assert rpc(client, "ping", headers={"Origin": "https://ah.example.nl"}).status_code == 200


def test_disabled_is_404(client, app):
    configure(app, MCP_ENABLED=False)
    assert rpc(client, "ping").status_code == 404


def test_the_session_cookie_gives_no_rights(client):
    """The endpoint never reads the session: no Set-Cookie, nothing user-specific."""
    resp = rpc(client, "tools/list")
    assert "Set-Cookie" not in resp.headers


def test_tools_list_describes_read_only_tools(client):
    listed = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in listed] == ["find_stores", "get_bargains", "get_bonus"]
    for t in listed:
        assert t["inputSchema"]["type"] == "object" and t["inputSchema"]["additionalProperties"] is False
        assert t["annotations"]["readOnlyHint"] is True and t["annotations"]["destructiveHint"] is False


# ---- tools -------------------------------------------------------------------------------------
def test_find_stores(client, fake_client):
    fake_client.responses = {"stores": {"storesSearch": {"result": [{
        "id": 1001, "name": "Albert Heijn", "address": {"street": "Centrum", "postalCode": "1000AA", "city": "Teststad"},
    }]}}}
    result = call(client, "find_stores", {"postcode": "1000 aa"})
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["postcode"] == "1000AA" and data["stores"][0]["id"] == 1001
    assert json.loads(result["content"][0]["text"]) == data
    assert 1001 in services(client.application).known_stores      # its bargains may now be loaded


def test_find_stores_needs_a_postcode(client):
    result = call(client, "find_stores", {"postcode": "Centrum"})
    assert result["isError"] is True and "postal code" in result["content"][0]["text"]


def test_get_bargains_filters_and_sorts(client, fake_client):
    fake_client.responses = {
        "1002": [{"product": {"id": 1, "title": "Kipfilet", }, "categoryTitle": "Vlees",
                  "bargainPrice": {"priceNow": "2.00", "priceWas": "4.00"}, "markdown": {"markdownPercentage": 50}, "stock": 2},
                 {"product": {"id": 2, "title": "Volkoren brood", }, "categoryTitle": "Brood",
                  "bargainPrice": {"priceNow": "1.50", "priceWas": "2.00"}, "markdown": {"markdownPercentage": 25}, "stock": 1}],
        "1001": [{"product": {"id": 3, "title": "Kippendijen", }, "categoryTitle": "Vlees",
                  "bargainPrice": {"priceNow": "3.00", "priceWas": "5.00"}, "markdown": {"markdownPercentage": 35}, "stock": 4}],
    }
    data = call(client, "get_bargains", {"store_ids": [1002, "1001"], "sort": "discount"})["structuredContent"]
    assert [i["id"] for i in data["items"]] == [1, 3, 2] and data["total"] == 3 and data["storeErrors"] == []
    assert data["items"][0]["storeId"] == 1002
    data = call(client, "get_bargains", {"store_ids": [1002, 1001], "search": "KIP", "min_discount": 40})
    assert [i["id"] for i in data["structuredContent"]["items"]] == [1]
    data = call(client, "get_bargains", {"store_ids": [1002, 1001], "limit": 1})["structuredContent"]
    assert data["total"] == 3 and data["returned"] == 1 and data["truncated"] is True


def test_get_bargains_reports_failed_stores_generically(client, fake_client):
    fake_client.fail_stores = {"1003"}
    data = call(client, "get_bargains", {"store_ids": [1003]})["structuredContent"]
    [err] = data["storeErrors"]
    assert err["code"] == "store_failed" and "unavailable" not in json.dumps(data)


@pytest.mark.parametrize("arguments,text", [
    ({}, "store_ids"),
    ({"store_ids": []}, "store_ids"),
    ({"store_ids": [-1]}, "positive"),
    ({"store_ids": ["1²"]}, "positive"),
    ({"store_ids": [1], "limit": 0}, "limit"),
    ({"store_ids": [1], "min_discount": 101}, "min_discount"),
    ({"store_ids": [1], "sort": "random"}, "sort"),
    ({"store_ids": [1], "search": "x" * 101}, "search"),
    ({"store_ids": [1], "evil": 1}, "Unknown argument"),
])
def test_get_bargains_checks_arguments(client, fake_client, arguments, text):
    result = call(client, "get_bargains", arguments)
    assert result["isError"] is True and text in result["content"][0]["text"]
    assert not fake_client.calls                                    # nothing reached AH


def test_get_bargains_respects_the_store_limit(client, app):
    configure(app, MAX_STORES=2)
    result = call(client, "get_bargains", {"store_ids": [1, 2, 3]})
    assert result["isError"] is True and "At most 2" in result["content"][0]["text"]


def test_get_bargains_uses_the_store_load_budgets(client, app, fake_client):
    """Unknown store ids cost the same unknown-store budget as on the dashboard."""
    configure(app, UNKNOWN_STORES_PER_CLIENT=1)
    call(client, "get_bargains", {"store_ids": [111111]})
    data = call(client, "get_bargains", {"store_ids": [222222]})["structuredContent"]
    assert data["storeErrors"][0]["code"] == "store_unknown"
    assert [c for c in fake_client.calls if c[1]["storeId"] == "222222"] == []


@pytest.fixture
def bonus_client():
    app = create_app("testing")
    services(app).ah = FakeSession()
    return app.test_client()


def test_get_bonus(bonus_client):
    data = call(bonus_client, "get_bonus")["structuredContent"]
    assert data["period"] == {"start": "2026-11-16", "end": "2026-11-22"}
    assert data["categories"] and data["total"] == len(data["items"]) > 0
    one = call(bonus_client, "get_bonus", {"search": "yoghurt"})["structuredContent"]
    assert [i["title"] for i in one["items"]] == ["Yoghurt"]
    kind = data["items"][0]["dealType"]
    same = call(bonus_client, "get_bonus", {"deal_type": kind})["structuredContent"]
    assert same["items"] and all(i["dealType"] == kind for i in same["items"])
    other = next(t for t in tools.DEAL_TYPES if t != kind)
    assert call(bonus_client, "get_bonus", {"deal_type": other})["structuredContent"]["total"] == 0


def test_get_bonus_ah_failure_is_generic(bonus_client):
    services(bonus_client.application).ah = FakeSession(fail={"meta"})
    result = call(bonus_client, "get_bonus")
    assert result["isError"] is True
    assert "ref " in result["content"][0]["text"] and "500" not in result["content"][0]["text"]


def test_unknown_tool_is_a_protocol_error(client):
    resp = rpc(client, "tools/call", {"name": "delete_everything", "arguments": {}})
    assert resp.get_json()["error"]["code"] == -32602


def test_without_ah_login(client, app):
    services(app).ah = FakeAHClient()
    services(app).ah.configured = False
    result = call(client, "get_bonus")
    assert result["isError"] is True and "no AH login" in result["content"][0]["text"]


# ---- limits ------------------------------------------------------------------------------------
def test_messages_are_rate_limited(client, app):
    configure(app, RATE_LIMIT_MCP=2)
    assert rpc(client, "ping").status_code == 200
    assert rpc(client, "ping").status_code == 200
    resp = rpc(client, "ping")
    assert resp.status_code == 429 and resp.get_json()["error"]["code"] == -32000


def test_tool_calls_share_the_dashboards_limits(client, app, fake_client):
    configure(app, RATE_LIMIT_STORES=1)
    fake_client.responses = {"stores": {"storesSearch": {"result": []}}}
    assert client.get("/api/stores?postcode=1000AA").status_code == 200        # the dashboard used it up
    result = call(client, "find_stores", {"postcode": "1000AA"})
    assert result["isError"] is True and "Too many" in result["content"][0]["text"]


def test_unexpected_errors_hide_details(client, app, monkeypatch):
    def boom(s, args, key):
        raise RuntimeError("secret detail")
    monkeypatch.setitem(tools.TOOLS, "get_bonus", tools.TOOLS["get_bonus"].__class__(
        **{**tools.TOOLS["get_bonus"].__dict__, "run": boom}))
    resp = rpc(client, "tools/call", {"name": "get_bonus"})
    err = resp.get_json()["error"]
    assert err["code"] == -32603 and "secret" not in resp.get_data(as_text=True) and err["data"]["ref"]


def test_transport_errors_are_tool_errors(client, monkeypatch):
    def down(s, args, key):
        raise AHTransportError("HTTP 503 from upstream", 503)
    monkeypatch.setitem(tools.TOOLS, "get_bonus", tools.TOOLS["get_bonus"].__class__(
        **{**tools.TOOLS["get_bonus"].__dict__, "run": down}))
    result = call(client, "get_bonus")
    assert result["isError"] is True and "upstream" not in result["content"][0]["text"]


# ---- pure helpers ------------------------------------------------------------------------------
def test_search_matches_group_products():
    group = {"title": "Alle Hak", "products": [{"id": 1, "title": "Hak rode kool"}]}
    assert tools.matches(group, ["kool"]) and not tools.matches(group, ["kip"])
    assert tools.matches({"title": "Kip", "brand": "AH", "category": "Vlees"}, ["ah", "vlees"])
