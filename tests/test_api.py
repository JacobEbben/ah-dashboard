def test_index_renders_with_config(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="app-config"' in html
    assert "defaultStores" not in html  # no built-in stores: visitors pick their own
    assert "Content-Security-Policy" in resp.headers


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_bargains_validates_store_ids(client):
    resp = client.post("/api/bargains", json={"stores": [{"id": "12x"}]})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "store_invalid"


def test_bargains_limits_store_count(client, app):
    stores = [{"id": i} for i in range(app.config["MAX_STORES"] + 1)]
    resp = client.post("/api/bargains", json={"stores": stores})
    assert resp.get_json()["error"]["code"] == "too_many_stores"


def test_bargains_tags_items_and_reports_failures(client, fake_client):
    fake_client.responses = {
        "1002": [{"product": {"id": 1, "title": "Kip"}, "stock": 2}],
        "1001": [{"product": {"id": 2, "title": "Brood"}, "stock": 1}],
    }
    fake_client.fail_stores = {"1003"}
    resp = client.post("/api/bargains", json={"stores": [
        {"id": 1002, "name": "Oost"}, {"id": 1003, "name": "Park"}, {"id": 1001, "name": "Centrum"},
    ]})
    assert resp.status_code == 200
    data = resp.get_json()
    # Order follows the requested store order.
    assert [(i["storeName"], i["storeId"]) for i in data["items"]] == [("Oost", 1002), ("Centrum", 1001)]
    # Generic message + reference only; the upstream detail is not exposed.
    [err] = data["storeErrors"]
    assert err["storeId"] == 1003 and err["store"] == "Park" and err["code"] == "store_failed"
    assert "unavailable" not in err["message"]
    assert len(err["ref"]) == 10


def test_bargains_deduplicates_stores(client, fake_client):
    client.post("/api/bargains", json={"stores": [{"id": 5}, {"id": "5"}]})
    assert [c for c in fake_client.calls if c[0] == "BargainItems"] == [("BargainItems", {"storeId": "5"})]


def test_store_search_only_accepts_postcodes(client):
    resp = client.get("/api/stores?postcode=Centrum")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "postcode_only"


def test_store_search_flattens_results(client, fake_client):
    fake_client.responses = {"stores": {"storesSearch": {"result": [{
        "id": 1001, "name": "Albert Heijn", "storeType": "REGULAR",
        "address": {"street": "Centrum", "houseNumber": "1", "postalCode": "1000AA", "city": "Teststad"},
    }]}}}
    resp = client.get("/api/stores?postcode=1000 aa")
    data = resp.get_json()
    assert data["postcode"] == "1000AA"
    assert data["stores"][0]["street"] == "Centrum"
    assert fake_client.calls[-1] == ("StoresSearch", {"filter": {"postalCode": "1000AA"}})


def test_unknown_api_route_is_json(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_other_graphql_errors_stay_store_failed(client, fake_client):
    fake_client.fail_stores = {"1003"}
    data = client.post("/api/bargains", json={"stores": [{"id": 1003}]}).get_json()
    assert data["storeErrors"][0]["code"] == "store_failed"


def test_api_needs_no_authorization_header(client, fake_client):
    fake_client.responses = {"1002": [{"product": {"id": 1}}]}
    resp = client.post("/api/bargains", json={"stores": [{"id": 1002, "name": "Oost"}]})
    assert resp.status_code == 200 and len(resp.get_json()["items"]) == 1


def test_server_without_ah_login_reports_not_configured():
    from ah_dashboard import create_app
    c = create_app("testing").test_client()   # TestingConfig has no AH tokens
    for resp in (c.post("/api/bargains", json={"stores": [{"id": 1}]}),
                 c.get("/api/stores?postcode=1234AB")):
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "ah_not_configured"


def test_token_seal_endpoint_is_gone(client):
    assert client.post("/api/token/seal").status_code == 404
