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



# ---- Google sign-in in tests --------------------------------------------------------------
class FakeGoogle:
    """Stands in for Google's token endpoint: the real GoogleSignIn (state,
    PKCE, claim checks), only the HTTP call answers with ``claims`` as an
    unsigned ID token. Set ``claims`` (or ``answer``) before the callback."""

    def __new__(cls, client_id="test-client"):
        from ah_dashboard.auth.google import GoogleSignIn

        class _Fake(GoogleSignIn):
            claims: dict | None = None
            answer: dict | None = None

            def _post_token(self, form):
                self.last_form = form
                if self.answer is not None:
                    return self.answer
                return {"id_token": make_id_token(self.claims or {})}
        return _Fake(client_id, "test-secret")


def make_id_token(claims):
    import base64
    import json
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return part({"alg": "RS256"}) + "." + part(claims) + ".sig"


def enable_google(app):
    """Turn Google sign-in on with a fake Google; returns the fake."""
    configure(app, GOOGLE_CLIENT_ID="test-client", GOOGLE_CLIENT_SECRET="test-secret")
    fake = FakeGoogle()
    services(app).google = fake
    return fake


def google_claims(nonce, sub, email, **extra):
    """ID token claims; by default a FRESH Google login (auth_time = now)."""
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": "test-client", "sub": sub, "email": email,
            "email_verified": True, "nonce": nonce, "iat": now, "exp": now + 600, "auth_time": now, **extra}


def google_sign_in(client, sub="sub-1", email="someone@example.com", purpose="login", **extra):
    """Go through /auth/google and its callback as if Google signed in this
    account; returns the callback's response."""
    from urllib.parse import parse_qs, urlparse
    app = client.application
    fake = services(app).google
    if fake is None:
        fake = enable_google(app)
    start = client.get(f"/auth/google?purpose={purpose}")
    assert start.status_code == 302, start.status_code
    query = parse_qs(urlparse(start.headers["Location"]).query)
    if "state" not in query:       # not sent to Google (e.g. needs a sign-in first)
        return start
    fake.claims = google_claims(query["nonce"][0], sub, email, **extra)
    return client.get(f"/auth/google/callback?code=test-code&state={query['state'][0]}")


def sign_in(client, username="alice", purpose="login"):
    """Sign ``client`` in as the existing user ``username`` with Google (links
    a test Google account to the user first if needed)."""
    users = services(client.application).users
    rec = users.get(username)
    assert rec is not None, f"no user {username}"
    if not rec["google_sub"]:
        users.link_google(username, f"sub-{username}", rec["email"] or f"{username}@example.com")
        rec = users.get(username)
    return google_sign_in(client, rec["google_sub"], rec["email"], purpose=purpose)


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
