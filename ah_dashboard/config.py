"""Configuration.

Every setting is named like its environment variable (see ``.env.example``).
:func:`load_config` builds the configuration in three layers:

1. a profile (:class:`ProductionConfig`, :class:`DevelopmentConfig`,
   :class:`TestingConfig` or a subclass): defaults;
2. the environment, read when the app is created (never at import time), for
   profiles with ``READ_ENV`` (not the testing profile: tests never depend on
   the machine's environment);
3. ``overrides`` (a mapping) passed to ``create_app``.

The flat result goes into Flask's ``app.config``; the code itself reads the
typed, grouped :class:`Settings` built from it (``services().settings``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class BaseConfig:
    """Defaults. Comments say what each setting does; ``.env.example`` too."""

    READ_ENV = True

    # --- AH API -------------------------------------------------------------
    AH_GRAPHQL_URL = "https://api.ah.nl/graphql"
    AH_TIMEOUT = 20                       # seconds per upstream request
    # Leave unset: urllib's default User-Agent is what AH accepts. A custom
    # value made AH fail queries with "Subgraph errors redacted".
    AH_USER_AGENT: str | None = None
    # REST part of AH's API (bonus pages). Sent with the client headers the
    # AH app uses (see appie-go): x-application / x-client-name / x-client-version.
    AH_REST_URL = "https://api.ah.nl"
    AH_APPLICATION = "AHWEBSHOP"
    AH_CLIENT_VERSION = "9.28"

    # --- Behaviour ------------------------------------------------------------
    MAX_STORES = 25                       # stores per bargains request
    MAX_WORKERS = 6                       # parallel upstream requests per load
    # Upper limit on AH requests in flight at once for the whole server (all
    # visitors and loads together), so the shared AH login is never flooded.
    AH_MAX_CONCURRENT = 8

    # --- Rate limits per client (see ratelimit.py) ------------------------------
    # N requests per window; 0 switches a limit off. Behind a reverse proxy set
    # TRUSTED_PROXY (serve.py), or every visitor shares the proxy's address.
    RATE_LIMIT_BARGAINS = 30
    RATE_LIMIT_BARGAINS_SECONDS = 60
    RATE_LIMIT_STORES = 10
    RATE_LIMIT_STORES_SECONDS = 60
    RATE_LIMIT_BONUS = 30
    RATE_LIMIT_BONUS_SECONDS = 60
    # Requests to the MCP endpoint (/mcp, every JSON-RPC message counts). Its
    # tools also count against the limits above, like the dashboard's requests.
    RATE_LIMIT_MCP = 120
    RATE_LIMIT_MCP_SECONDS = 60

    # --- MCP endpoint (/mcp, see ah_dashboard/mcp) ------------------------------------
    # Read-only tools for AI assistants (store search, bargains, Bonus): the same
    # public data as the dashboard, anonymous, no sign-in. False = /mcp is a 404.
    MCP_ENABLED = True

    # --- Caches ------------------------------------------------------------------
    BARGAINS_CACHE_TTL = 300              # seconds (5 min)
    BARGAINS_CACHE_MAX_STORES = 2000      # AH has ~1,100 stores
    # After the TTL, a store's bargains are still served for this many seconds
    # while they are reloaded in the background (the visitor doesn't wait).
    BARGAINS_STALE_SECONDS = 600
    # A store that failed to load is not asked again for this many seconds
    # (stops unknown or broken store ids from reaching AH on every request).
    STORE_ERROR_TTL = 120
    # Store search results per postal code (stores rarely change).
    STORE_SEARCH_CACHE_TTL = 86400
    # Loading a store from AH (a cache miss) is budgeted, so invented store ids
    # can't flood AH with the shared login (services/bargains.LoadGuard). Stores
    # known to exist (store search results, stores with bargains) only
    # count against the load budgets; unknown ids also against the small
    # unknown-store budgets. Counted per client (IPv4 / IPv6 /64) and in total.
    STORE_LOADS_PER_CLIENT = 40           # per minute
    STORE_LOADS_TOTAL = 120               # per minute, whole server
    UNKNOWN_STORES_PER_CLIENT = 10        # per window
    UNKNOWN_STORES_TOTAL = 60             # per window, whole server
    UNKNOWN_STORES_WINDOW = 3600          # seconds
    # The national Bonus is fixed for its week: one shared copy is kept until the
    # bonus week ends. A partial load (some categories failed), or a period AH
    # has not switched yet, is retried after this many seconds.
    BONUS_RETRY_SECONDS = 300
    # Load the Bonus in the background at start-up and when the week changes,
    # so visitors never wait for its ~300 AH requests.
    BONUS_PREWARM = True
    # Run the background jobs (Bonus loading, pruning expired sign-ins). Off in
    # tests, which run jobs explicitly.
    BACKGROUND_JOBS = True

    # --- Server-side AH login (see ah/auth.py) --------------------------------
    # The app calls AH with ONE login configured here; users need no token.
    # AH rotates the refresh token on every renewal, so the current pair is
    # stored encrypted in the database. A new AH_REFRESH_TOKEN value here
    # replaces the stored pair (that is how you log in again).
    AH_ACCESS_TOKEN: str | None = None
    AH_REFRESH_TOKEN: str | None = None
    AH_AUTH_REFRESH_URL = "https://api.ah.nl/mobile-auth/v1/auth/token/refresh"
    AH_CLIENT_ID = "appie-ios"
    # Minimum seconds between renewals (stops loops if AH fails for other reasons)
    AH_REFRESH_COOLDOWN = 300

    # Server-only secret used to encrypt the stored AH tokens and to sign the
    # session cookie. Unset: one is generated once into instance/.
    TOKEN_SEAL_SECRET: str | None = None

    # --- Admin panel (/admin) -----------------------------------------------------
    # Disabled unless set (or an admin user exists). Create with: python -m ah_dashboard.admin
    ADMIN_PASSWORD_HASH: str | None = None
    # Admin-PASSWORD sessions end after this long without admin activity ...
    # (admin users have admin rights for as long as they are signed in)
    ADMIN_SESSION_MINUTES = 60
    ADMIN_SESSION_MAX_HOURS = 8           # ... and this long after the password login, however active
    ADMIN_MAX_FAILURES = 5                # per client ...
    ADMIN_LOCKOUT_SECONDS = 900           # ... within this window
    # Failed sign-ins per account (any address) within the lockout window: stops
    # password guessing spread over many addresses. Only the admin password has
    # one (users sign in with Google and have no password).
    ACCOUNT_MAX_FAILURES = 20
    # Admin-password attempts per minute for the whole server (all clients),
    # checked before the expensive password hash. 0 = off.
    LOGIN_ATTEMPTS_PER_MINUTE = 60
    # Google sign-ins started per minute per client (IPv4 address / IPv6 /64).
    # Per client on purpose: a server-wide budget would let anyone block every
    # sign-in by starting them in a loop. 0 = off.
    GOOGLE_SIGNIN_PER_MINUTE = 10
    # Settings uploads (PUT /api/prefs) per minute per signed-in user. 0 = off.
    PREFS_WRITES_PER_MINUTE = 60
    # Send the session cookie over HTTPS only (and send HSTS). Set true when
    # served via HTTPS; production logs a warning while it is off.
    ADMIN_COOKIE_SECURE = False

    # --- Dashboard users: sign in with Google --------------------------------------
    # Signing in is optional: it only syncs a user's settings across devices.
    # Anyone with a (verified) Google account can sign in; the first sign-in
    # creates the account. Without a client id/secret there is no sign-in
    # (the admin password still opens the admin panel).
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    # The site's public address, e.g. https://ah.example.nl (no path). Google
    # sends users back to <PUBLIC_BASE_URL>/auth/google/callback, which must be
    # listed in the Google OAuth client. Unset: taken from the request (fine
    # locally on http://127.0.0.1:8733; set it behind a proxy).
    PUBLIC_BASE_URL: str | None = None
    MAX_USERS = 1000                      # new accounts are refused beyond this
    DATABASE_FILE: str | None = None      # default: instance/ah_dashboard.db; "" = in memory
    USER_SESSION_DAYS = 30                # stay signed in this long
    # Who to contact about privacy (an e-mail address or a URL), shown on /privacy. Unset: not shown.
    PRIVACY_CONTACT: str | None = None

    # --- HTTP / logging -----------------------------------------------------------
    # Hosts product images may load from (Content-Security-Policy img-src),
    # space-separated https:// origins. AH serves them from static.ah.nl.
    CSP_IMAGE_HOSTS = "https://static.ah.nl"
    MAX_CONTENT_LENGTH = 1024 * 1024      # largest body: a settings upload (≤ 1 MiB)
    LOG_LEVEL = "INFO"
    # Error details are written here (rotating); users only see generic
    # messages with a reference ID. Set ERROR_LOG_FILE="" to disable.
    ERROR_LOG_FILE: str | None = os.path.join(_PROJECT_DIR, "logs", "errors.log")
    ERROR_LOG_MAX_BYTES = 1_000_000
    ERROR_LOG_BACKUPS = 5
    DEBUG = False
    TESTING = False


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    LOG_LEVEL = "DEBUG"


class ProductionConfig(BaseConfig):
    pass


class TestingConfig(BaseConfig):
    READ_ENV = False               # tests never depend on the machine's environment
    TESTING = True
    ERROR_LOG_FILE = None          # tests set their own temporary file when needed
    TOKEN_SEAL_SECRET = "test-seal-secret-not-for-production"
    DATABASE_FILE = ""             # in-memory database
    BONUS_PREWARM = False          # no background threads in tests
    BACKGROUND_JOBS = False
    BARGAINS_STALE_SECONDS = 0     # no background refreshes in app tests (unit-tested in test_cache)
    # Rate limits off by default; tests of the limits switch them on.
    RATE_LIMIT_BARGAINS = 0
    RATE_LIMIT_STORES = 0
    RATE_LIMIT_BONUS = 0
    RATE_LIMIT_MCP = 0
    LOGIN_ATTEMPTS_PER_MINUTE = 0
    GOOGLE_SIGNIN_PER_MINUTE = 0


_PROFILES: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_profile(config: str | type | None, env: Mapping[str, str] = os.environ) -> type:
    """A profile class: given, by name, or from ``APP_ENV`` (default production)."""
    if isinstance(config, type):
        return config
    name = (config or env.get("APP_ENV") or "production").lower()
    try:
        return _PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown APP_ENV {name!r}; use one of {sorted(_PROFILES)}") from None


def _settings_of(profile: type) -> dict[str, Any]:
    return {k: getattr(profile, k) for k in dir(profile) if k.isupper() and k != "READ_ENV"}


# ---- environment ------------------------------------------------------------------------
def _parse_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_bool(raw: str, default: bool) -> bool:
    raw = raw.strip()
    return default if not raw else raw.lower() in ("1", "true", "yes", "on")


def unquote(raw: str) -> str:
    """Strip ONE pair of matching surrounding quotes ('...' or "...").

    `.env` values may be quoted (the admin hash must be: it contains "$",
    which Docker Compose would otherwise expand). Compose removes the quotes
    itself; this does the same when the environment comes from elsewhere
    (e.g. `set -a; . ./.env` before `python serve.py`)."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def from_env(defaults: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> dict[str, Any]:
    """The settings the environment sets, parsed like their defaults: numbers,
    booleans or text (empty text = unset)."""
    found: dict[str, Any] = {}
    for key, default in defaults.items():
        raw = env.get(key)
        if raw is None or key in ("DEBUG", "TESTING"):
            continue
        raw = unquote(raw)
        if isinstance(default, bool):
            found[key] = _parse_bool(raw, default)
        elif isinstance(default, int):
            found[key] = _parse_int(raw, default)
        elif key == "ERROR_LOG_FILE":
            found[key] = raw or None          # "" switches the error log off
        elif key == "DATABASE_FILE":
            found[key] = raw or None          # "" = default location (in-memory only via profile)
        else:
            found[key] = raw or (None if default is None else default)
    return found


def load_config(config: str | type | None = None, overrides: Mapping[str, Any] | None = None,
                env: Mapping[str, str] = os.environ) -> dict[str, Any]:
    """The flat configuration (profile, then environment, then overrides), validated."""
    profile = get_profile(config, env)
    values = _settings_of(profile)
    if getattr(profile, "READ_ENV", True):
        values.update(from_env(values, env))
    if overrides:
        values.update(overrides)
    validate(values)
    return values


def validate(config: Mapping[str, Any]) -> None:
    """Refuse settings that would leak the AH login."""
    # Tokens are sent to these URLs: never plain http or schemes such as file://.
    for key in ("AH_GRAPHQL_URL", "AH_AUTH_REFRESH_URL", "AH_REST_URL"):
        if not str(config.get(key, "")).lower().startswith("https://"):
            raise ValueError(f"{key} must be an https:// URL")
    # Google sends the sign-in code to this address: only http(s), no path.
    base = str(config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base and not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?", base):
        raise ValueError("PUBLIC_BASE_URL must look like https://ah.example.nl (no path)")
    if bool(config.get("GOOGLE_CLIENT_ID")) != bool(config.get("GOOGLE_CLIENT_SECRET")):
        raise ValueError("Set both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or neither")


# ---- typed, grouped settings ---------------------------------------------------------------
@dataclass(frozen=True)
class RateLimit:
    rate: int        # requests per window; 0 = off
    per: float       # window in seconds


@dataclass(frozen=True)
class AHSettings:
    graphql_url: str
    rest_url: str
    auth_refresh_url: str
    timeout: int
    user_agent: str | None
    application: str
    client_id: str
    client_version: str
    access_token: str | None
    refresh_token: str | None
    refresh_cooldown: int
    max_concurrent: int
    max_workers: int

    @property
    def rest_headers(self) -> dict[str, str]:
        return {"x-application": self.application, "x-client-name": self.client_id,
                "x-client-version": self.client_version}


@dataclass(frozen=True)
class CacheSettings:
    bargains_ttl: int
    bargains_max_stores: int
    bargains_stale: int
    store_error_ttl: int
    store_search_ttl: int
    bonus_retry: int
    bonus_prewarm: bool


@dataclass(frozen=True)
class LimitSettings:
    bargains: RateLimit
    stores: RateLimit
    bonus: RateLimit
    mcp: RateLimit
    store_loads_per_client: int
    store_loads_total: int
    unknown_stores_per_client: int
    unknown_stores_total: int
    unknown_stores_window: int
    login_attempts_per_minute: int
    google_signin_per_minute: int
    prefs_writes_per_minute: int

    def endpoint(self, name: str) -> RateLimit:
        """The limit of one rate-limited endpoint group ("bargains", "stores", "bonus", "mcp")."""
        return {"bargains": self.bargains, "stores": self.stores, "bonus": self.bonus, "mcp": self.mcp}[name]


@dataclass(frozen=True)
class AuthSettings:
    admin_password_hash: str | None
    admin_session_minutes: int
    admin_session_max_hours: int
    admin_max_failures: int
    admin_lockout_seconds: int
    account_max_failures: int
    cookie_secure: bool
    user_session_days: int
    google_client_id: str | None
    google_client_secret: str | None
    public_base_url: str | None
    max_users: int

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@dataclass(frozen=True)
class AppSettings:
    max_stores: int
    csp_image_hosts: str
    database_file: str | None
    token_seal_secret: str | None
    background_jobs: bool
    privacy_contact: str | None
    mcp_enabled: bool
    error_log_file: str | None      # None: no error log (the admin panel's error lookup is off)
    error_log_backups: int


@dataclass(frozen=True)
class Settings:
    ah: AHSettings
    cache: CacheSettings
    limits: LimitSettings
    auth: AuthSettings
    app: AppSettings

    @classmethod
    def from_config(cls, c: Mapping[str, Any]) -> Settings:
        def limit(name: str) -> RateLimit:
            return RateLimit(int(c[f"RATE_LIMIT_{name}"]), float(c[f"RATE_LIMIT_{name}_SECONDS"]))

        return cls(
            ah=AHSettings(
                graphql_url=c["AH_GRAPHQL_URL"], rest_url=c["AH_REST_URL"],
                auth_refresh_url=c["AH_AUTH_REFRESH_URL"], timeout=int(c["AH_TIMEOUT"]),
                user_agent=c["AH_USER_AGENT"] or None, application=c["AH_APPLICATION"],
                client_id=c["AH_CLIENT_ID"], client_version=c["AH_CLIENT_VERSION"],
                access_token=c["AH_ACCESS_TOKEN"] or None, refresh_token=c["AH_REFRESH_TOKEN"] or None,
                refresh_cooldown=int(c["AH_REFRESH_COOLDOWN"]), max_concurrent=int(c["AH_MAX_CONCURRENT"]),
                max_workers=int(c["MAX_WORKERS"])),
            cache=CacheSettings(
                bargains_ttl=int(c["BARGAINS_CACHE_TTL"]), bargains_max_stores=int(c["BARGAINS_CACHE_MAX_STORES"]),
                bargains_stale=int(c["BARGAINS_STALE_SECONDS"]), store_error_ttl=int(c["STORE_ERROR_TTL"]),
                store_search_ttl=int(c["STORE_SEARCH_CACHE_TTL"]), bonus_retry=int(c["BONUS_RETRY_SECONDS"]),
                bonus_prewarm=bool(c["BONUS_PREWARM"])),
            limits=LimitSettings(
                bargains=limit("BARGAINS"), stores=limit("STORES"), bonus=limit("BONUS"), mcp=limit("MCP"),
                store_loads_per_client=int(c["STORE_LOADS_PER_CLIENT"]),
                store_loads_total=int(c["STORE_LOADS_TOTAL"]),
                unknown_stores_per_client=int(c["UNKNOWN_STORES_PER_CLIENT"]),
                unknown_stores_total=int(c["UNKNOWN_STORES_TOTAL"]),
                unknown_stores_window=int(c["UNKNOWN_STORES_WINDOW"]),
                login_attempts_per_minute=int(c["LOGIN_ATTEMPTS_PER_MINUTE"]),
                google_signin_per_minute=int(c["GOOGLE_SIGNIN_PER_MINUTE"]),
                prefs_writes_per_minute=int(c["PREFS_WRITES_PER_MINUTE"])),
            auth=AuthSettings(
                admin_password_hash=c["ADMIN_PASSWORD_HASH"] or None,
                admin_session_minutes=int(c["ADMIN_SESSION_MINUTES"]),
                admin_session_max_hours=int(c["ADMIN_SESSION_MAX_HOURS"]),
                admin_max_failures=int(c["ADMIN_MAX_FAILURES"]),
                admin_lockout_seconds=int(c["ADMIN_LOCKOUT_SECONDS"]),
                account_max_failures=int(c["ACCOUNT_MAX_FAILURES"]),
                cookie_secure=bool(c["ADMIN_COOKIE_SECURE"]),
                user_session_days=int(c["USER_SESSION_DAYS"]),
                google_client_id=(c["GOOGLE_CLIENT_ID"] or "").strip() or None,
                google_client_secret=(c["GOOGLE_CLIENT_SECRET"] or "").strip() or None,
                public_base_url=(c["PUBLIC_BASE_URL"] or "").strip().rstrip("/") or None,
                max_users=int(c["MAX_USERS"])),
            app=AppSettings(
                max_stores=int(c["MAX_STORES"]),
                csp_image_hosts=c["CSP_IMAGE_HOSTS"] or "", database_file=c["DATABASE_FILE"],
                token_seal_secret=c["TOKEN_SEAL_SECRET"] or None,
                background_jobs=bool(c["BACKGROUND_JOBS"]),
                privacy_contact=(c["PRIVACY_CONTACT"] or "").strip() or None,
                mcp_enabled=bool(c["MCP_ENABLED"]),
                error_log_file=c["ERROR_LOG_FILE"] or None,
                error_log_backups=int(c["ERROR_LOG_BACKUPS"])),
        )


def setting_names() -> list[str]:
    """Every setting name (for docs and checks)."""
    return sorted(_settings_of(BaseConfig))
