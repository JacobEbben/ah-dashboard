"""The architecture's rules, checked: layering, configuration, migrations,
the service container, central error mapping."""

import ast
import pathlib
import sqlite3

import pytest

from ah_dashboard import create_app
from ah_dashboard.ah.auth import AHAuthError
from ah_dashboard.ah.client import AHTransportError
from ah_dashboard.auth.users import SYNC_KEYS
from ah_dashboard.config import Settings, TestingConfig, load_config
from ah_dashboard.container import Services, services
from ah_dashboard.db import SCHEMA_VERSION, Database

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "ah_dashboard"


# ---- layering ------------------------------------------------------------------------------
def _imports(path: pathlib.Path) -> set[str]:
    """Absolute module names imported by a file of the package."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                found.add(".".join(base + ([node.module] if node.module else [])))
            elif node.module:
                found.add(node.module)
    return found


def _layer(name: str) -> list[pathlib.Path]:
    return sorted((PACKAGE / name).glob("*.py"))


# Which packages each layer may NOT import (lower layers never reach up).
RULES = {
    "domain": ("flask", "ah_dashboard.ah", "ah_dashboard.services", "ah_dashboard.api", "ah_dashboard.web",
               "ah_dashboard.admin", "ah_dashboard.mcp", "ah_dashboard.auth", "ah_dashboard.container", "ah_dashboard.db"),
    "ah": ("flask", "ah_dashboard.services", "ah_dashboard.api", "ah_dashboard.web", "ah_dashboard.admin",
           "ah_dashboard.mcp", "ah_dashboard.auth", "ah_dashboard.container"),
    "services": ("flask", "ah_dashboard.api", "ah_dashboard.web", "ah_dashboard.admin", "ah_dashboard.mcp",
                 "ah_dashboard.auth",
                 "ah_dashboard.container"),
    "auth": ("ah_dashboard.api", "ah_dashboard.web", "ah_dashboard.admin", "ah_dashboard.mcp", "ah_dashboard.services",
             "ah_dashboard.ah"),
}


@pytest.mark.parametrize("layer", sorted(RULES))
def test_layers_only_import_downwards(layer):
    files = _layer(layer)
    assert files, layer
    for path in files:
        bad = {m for m in _imports(path) for banned in RULES[layer] if m == banned or m.startswith(banned + ".")}
        assert not bad, f"{path.relative_to(PACKAGE)} imports {sorted(bad)}"


def test_only_the_gateway_sends_requests_to_ah():
    """Services use the gateway; no module outside ah/ uses queries or AH paths."""
    for path in PACKAGE.rglob("*.py"):
        if path.parent.name == "ah":
            continue
        text = path.read_text(encoding="utf-8")
        assert "ah.queries" not in text and "from ..ah import queries" not in text, path
        assert "/mobile-services/" not in text, path


# ---- configuration --------------------------------------------------------------------------
def test_testing_profile_ignores_the_environment():
    config = load_config("testing", env={"MAX_STORES": "3", "ADMIN_PASSWORD_HASH": "x"})
    assert config["MAX_STORES"] == 25 and config["ADMIN_PASSWORD_HASH"] is None


def test_environment_is_parsed_like_the_defaults():
    config = load_config("production", env={
        "MAX_STORES": "3", "BONUS_PREWARM": "false", "ADMIN_COOKIE_SECURE": "yes", "AH_USER_AGENT": "",
        "AH_TIMEOUT": "not a number",
        "ERROR_LOG_FILE": "",
    })
    assert config["MAX_STORES"] == 3 and config["BONUS_PREWARM"] is False and config["ADMIN_COOKIE_SECURE"] is True
    assert config["AH_USER_AGENT"] is None and config["AH_TIMEOUT"] == 20     # bad number: default
    assert config["ERROR_LOG_FILE"] is None


@pytest.mark.parametrize("raw, expected", [
    ("'scrypt:32768:8:1$abc$def'", "scrypt:32768:8:1$abc$def"),   # the admin hash, quoted as in .env
    ('"value"', "value"), ("value", "value"), ("'", "'"), ("''", ""),
    ("'mixed\"", "'mixed\""),          # unmatched quotes stay
    ("'a'b'", "a'b"),                 # only one surrounding pair is removed
])
def test_quoted_environment_values_are_unquoted(raw, expected):
    config = load_config("production", env={"ADMIN_PASSWORD_HASH": raw, "ERROR_LOG_FILE": ""})
    assert config["ADMIN_PASSWORD_HASH"] == (expected or None)


def test_quoted_numbers_and_booleans_are_parsed():
    config = load_config("production", env={"MAX_STORES": "'3'", "BONUS_PREWARM": '"false"', "ERROR_LOG_FILE": ""})
    assert config["MAX_STORES"] == 3 and config["BONUS_PREWARM"] is False


def test_environment_is_read_when_the_app_is_created(monkeypatch):
    monkeypatch.setenv("MAX_STORES", "7")
    assert load_config("production")["MAX_STORES"] == 7
    monkeypatch.setenv("MAX_STORES", "9")
    assert load_config("production")["MAX_STORES"] == 9


def test_overrides_win_over_the_environment():
    config = load_config("production", {"MAX_STORES": 5}, env={"MAX_STORES": "3"})
    assert config["MAX_STORES"] == 5


@pytest.mark.parametrize("key", ["AH_GRAPHQL_URL", "AH_AUTH_REFRESH_URL", "AH_REST_URL"])
def test_tokens_are_never_sent_over_plain_http(key):
    with pytest.raises(ValueError, match=key):
        load_config("testing", {key: "http://api.ah.nl"})


def test_settings_are_typed_and_grouped():
    s = Settings.from_config(load_config("testing", {"RATE_LIMIT_BONUS": 4, "RATE_LIMIT_BONUS_SECONDS": 30}))
    assert s.limits.endpoint("bonus").rate == 4 and s.limits.endpoint("bonus").per == 30.0
    assert s.ah.rest_headers == {"x-application": "AHWEBSHOP", "x-client-name": "appie-ios",
                                 "x-client-version": "9.28"}


# ---- container ---------------------------------------------------------------------------------
def test_container_holds_every_service():
    app = create_app(TestingConfig)
    svc = services(app)
    assert isinstance(svc, Services)
    optional = {"login"}                              # None until LOGIN_ISSUER/CLIENT_ID/SECRET are set
    for name in Services.__dataclass_fields__:
        assert name in optional or getattr(svc, name) is not None, name
    assert svc.gateway.configured is False            # testing profile: no AH login
    assert svc.login is None
    with_login = create_app(TestingConfig, {"LOGIN_ISSUER": "https://login.example.nl", "LOGIN_CLIENT_ID": "id",
                                            "LOGIN_CLIENT_SECRET": "secret-not-real"})
    assert services(with_login).login is not None


def test_testing_profile_starts_no_background_threads():
    svc = services(create_app(TestingConfig))
    assert svc.scheduler.jobs() == {"hourly-cleanup": False}


def test_page_gets_the_sync_keys_from_the_server(client):
    import json
    import re
    html = client.get("/").get_data(as_text=True)
    config = json.loads(re.search(r'<script type="application/json" id="app-config">(.*?)</script>', html, re.S)[1])
    assert config["syncKeys"] == sorted(SYNC_KEYS)


# ---- AH errors are mapped in one place -----------------------------------------------------------
class _FailingAH:
    configured = True

    def __init__(self, exc):
        self.exc = exc

    def query(self, *a, **k):
        raise self.exc

    def get(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize("exc,status,code", [
    (AHAuthError("refresh token expired"), 503, "ah_auth_failed"),
    (AHTransportError("HTTP 500 secret details", 500), 502, "upstream_error"),
])
@pytest.mark.parametrize("path", ["/api/stores?postcode=1234AB", "/api/bonus"])
def test_ah_failures_become_generic_errors_everywhere(path, exc, status, code):
    app = create_app(TestingConfig)
    services(app).ah = _FailingAH(exc)
    resp = app.test_client().get(path)
    err = resp.get_json()["error"]
    assert resp.status_code == status and err["code"] == code and err["ref"]
    assert "secret" not in resp.get_data(as_text=True) and "expired" not in resp.get_data(as_text=True)


# ---- database migrations --------------------------------------------------------------------------
def test_old_database_is_upgraded_step_by_step(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at REAL NOT NULL,
                            last_login REAL, disabled INTEGER NOT NULL DEFAULT 0, session_version TEXT NOT NULL);
        INSERT INTO users VALUES ('anna', 'h', 1, NULL, 0, 'v');
        PRAGMA user_version = 1;
    """)
    conn.close()
    db = Database(str(path))
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION
    assert db.one("SELECT is_admin FROM users WHERE username = 'anna'")["is_admin"] == 0
    assert db.scalar("SELECT COUNT(*) FROM sessions") == 0            # new tables created too


def test_fresh_database_starts_at_the_current_version(tmp_path):
    db = Database(str(tmp_path / "new.db"))
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION


def test_database_from_a_newer_app_is_refused(tmp_path):
    path = tmp_path / "newer.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        Database(str(path))


def test_expired_sign_ins_are_pruned(monkeypatch):
    import time
    svc = services(create_app(TestingConfig))
    svc.sessions.create(None)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 400 * 86400)
    assert svc.sessions.prune() == 1 and svc.sessions.count() == 0


# ---- front-end layering (static/js) ------------------------------------------------------------
JS = PACKAGE / "static" / "js"
JS_IMPORT = __import__("re").compile(r"""^\s*import\s[^'"]*?from\s+['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]""", __import__("re").M)

# Which folders each folder may import from (besides itself). Features never
# import each other: they talk through core/events.js.
JS_ALLOWED = {
    "core": set(),
    "lists": {"core"},
    "ui": {"core", "lists"},
    "sync": {"core"},
    "bargains": {"core", "lists", "ui"},
    "bonus": {"core", "lists", "ui"},
}


def _js_imports(path):
    text = path.read_text(encoding="utf-8")
    for m in JS_IMPORT.finditer(text):
        target = (path.parent / (m[1] or m[2])).resolve()
        yield target.relative_to(JS.resolve())


def test_js_layers_only_import_downwards():
    checked = 0
    for folder, allowed in JS_ALLOWED.items():
        for path in sorted((JS / folder).glob("*.js")):
            for target in _js_imports(path):
                assert (JS / target).exists(), f"{path.relative_to(JS)} imports missing {target}"
                top = target.parts[0] if len(target.parts) > 1 else "(root)"
                assert top == folder or top in allowed, f"{path.relative_to(JS)} imports {target}"
                checked += 1
    assert checked > 50


def test_every_js_file_is_type_checked():
    for path in JS.rglob("*.js"):
        if path.name == "theme-init.js":      # classic script in <head>, runs before modules
            continue
        assert path.read_text(encoding="utf-8").startswith("// @ts-check"), path.relative_to(JS)


def test_all_storage_access_goes_through_storage_js():
    for path in JS.rglob("*.js"):
        if path.name in ("storage.js", "theme-init.js"):
            continue
        uses = __import__("re").search(r"\b(localStorage|sessionStorage)\s*\.", path.read_text(encoding="utf-8"))
        assert not uses, path.relative_to(JS)
