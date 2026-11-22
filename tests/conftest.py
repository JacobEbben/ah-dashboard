import time

import pytest

from ah_dashboard import create_app
from ah_dashboard.ah.client import AHGraphQLError
from ah_dashboard.config import Settings
from ah_dashboard.container import services


class FakeAHClient:
    """Stands in for the AH session: answers from canned data, records calls."""

    configured = True  # behaves like a server with an AH login

    def __init__(self, responses=None, fail_stores=(), stale_token_stores=()):
        self.responses = responses or {}
        self.fail_stores = set(fail_stores)
        # AH's symptom of an expired/unsuitable token (see services/bargains.py)
        self.stale_token_stores = set(stale_token_stores)
        self.calls = []

    def query(self, document, variables=None, operation=None):
        self.calls.append((operation, variables))
        if operation == "BargainItems":
            store_id = variables["storeId"]
            if store_id in self.fail_stores:
                raise AHGraphQLError([{"message": f"store {store_id} unavailable"}])
            if store_id in self.stale_token_stores:
                raise AHGraphQLError(
                    [{"message": "Subgraph errors redacted", "path": ["bargainItems"]}],
                    {"valueCompletion": [{"message": "Cannot return null for non-nullable type BargainItem", "path": []}]})
            return {"bargainItems": [dict(item) for item in self.responses.get(store_id, [])]}
        if operation == "StoresSearch":
            return self.responses.get("stores", {"storesSearch": {"result": []}})
        raise AssertionError(f"unexpected operation {operation}")


def configure(app, **values):
    """Change settings of a running test app (flat names, like the environment).
    Services built at start-up keep their values; anything read per request
    (limits, budgets, admin password) follows."""
    app.config.update(values)
    services(app).settings = Settings.from_config(app.config)


@pytest.fixture
def fake_client():
    return FakeAHClient()


@pytest.fixture
def app(fake_client):
    app = create_app("testing")
    services(app).ah = fake_client
    return app


@pytest.fixture
def client(app):
    return app.test_client()



# ---- sign-in through the login service in tests ------------------------------------------
def enable_login(app):
    """Turn sign-in on with a fake login service (tests/fake_op.py: real RS256 tokens,
    discovery, JWKS, token endpoint); returns the fake."""
    from fake_op import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeOP

    from ah_dashboard.auth.login_client import LoginClient
    configure(app, LOGIN_ISSUER=ISSUER, LOGIN_CLIENT_ID=CLIENT_ID, LOGIN_CLIENT_SECRET=CLIENT_SECRET)
    op = FakeOP()
    services(app).login = LoginClient(ISSUER, CLIENT_ID, CLIENT_SECRET, production=False, http=op.http)
    return op


def login_sign_in(client, sub="sub-1", username="someone", purpose="login", tamper=None, overrides=None, **claims):
    """Go through /auth/login, the (fake) login service and /auth/callback; returns
    the callback's response. ``claims`` / ``overrides`` replace the ID token's claims
    (``overrides`` for names like "sub" that clash with the parameters)."""
    from urllib.parse import parse_qs, urlparse
    app = client.application
    op = getattr(app, "_fake_op", None)
    if op is None or services(app).login is None:
        op = app._fake_op = enable_login(app)
    op.token_overrides = {**claims, **(overrides or {})}
    start = client.get(f"/auth/login?purpose={purpose}")
    assert start.status_code == 302, start.status_code
    if "state=" not in start.headers["Location"]:      # not sent to the login service
        return start
    query = op.approve(start.headers["Location"], sub, username)
    assert parse_qs(urlparse(start.headers["Location"]).query)["state"][0] == query["state"]
    if tamper:
        tamper(query)
    return client.get("/auth/callback", query_string=query)


def sign_in(client, username="alice", purpose="login", amr=("pwd", "otp")):
    """Sign ``client`` in as the existing user ``username`` through the (fake) login
    service (links a test login-service user to the account first if needed)."""
    users = services(client.application).users
    rec = users.get(username)
    assert rec is not None, f"no user {username}"
    if not rec["login_sub"]:
        services(client.application).db.execute("UPDATE users SET login_sub = ? WHERE username = ?",
                                                (f"sub-{username}", username))
        rec = users.get(username)
    return login_sign_in(client, rec["login_sub"], username, purpose=purpose, amr=list(amr))


def as_admin(client, csrf="test-csrf"):
    """Give ``client`` an active admin session; returns the headers an
    admin-only API call needs (the session's CSRF token)."""
    from ah_dashboard.auth.admin import password_fingerprint
    app = client.application
    configure(app, ADMIN_PASSWORD_HASH="configured")   # admin enabled
    sid = services(app).sessions.create(None)       # listed on the server, like a real sign-in
    with client.session_transaction() as sess:
        sess["admin_login_at"] = time.time()
        sess["sid"] = sid
        sess["admin_pw"] = password_fingerprint("configured", app.secret_key)
        sess["csrf"] = csrf
    return {"X-CSRF-Token": csrf}
