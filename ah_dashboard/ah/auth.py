"""Server-side AH login: the access/refresh token pair and its renewal.

The operator configures ``AH_ACCESS_TOKEN`` and ``AH_REFRESH_TOKEN``. AH
issues a *new* refresh token on every renewal, so the current pair is kept
encrypted in the app database (``DATABASE_FILE``) and survives restarts.
Setting a different ``AH_REFRESH_TOKEN`` in the environment replaces the
stored pair (that is how an operator re-logs in).

Renewal (``POST /mobile-auth/v1/auth/token/refresh``, as appie-go does it):

- only one refresh at a time (lock): parallel requests that saw the same
  expired token all wait for one refresh and then use its result, so a
  refresh token is never used twice;
- a cooldown after each attempt stops refresh loops when AH keeps failing for
  reasons that are not the token;
- tokens are never logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from cryptography.fernet import Fernet, InvalidToken

from .client import _OPENER, AHError, decode_body

log = logging.getLogger(__name__)

# appie-go sends these headers to AH's auth endpoints (the iOS app's values).
AUTH_HEADERS = {
    "User-Agent": "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)",
    "x-client-name": "appie-ios",
    "x-client-version": "9.28",
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class AHAuthError(AHError):
    """The server's AH login is missing or could not be renewed. ``status`` is
    AH's HTTP status when AH answered (401: refresh token expired or already
    used; 400: AH did not accept the request, e.g. the value is no refresh
    token)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# What AH's tokens look like (appie-go doc/albertheijn_api.md):
#   refresh token  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx           (a UUID)
#   access token   12345678_xxxx-xxxx-xxxx-xxxxxxxxxxxx           (member number, "_", ...)
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_REFRESH_RE = re.compile(_UUID)
_ACCESS_RE = re.compile(r"[0-9]+_[0-9A-Za-z-]{8,200}")


def token_kind(value: str | None) -> str:
    """"refresh", "access", "jwt", "code" (a login code or the login-exit
    link), "empty" or "unknown": what a pasted value looks like. Only the
    shape is checked; the value is never logged."""
    v = (value or "").strip()
    if not v:
        return "empty"
    if _REFRESH_RE.fullmatch(v):
        return "refresh"
    if _ACCESS_RE.fullmatch(v):
        return "access"
    if v.count(".") == 2 and v.startswith("eyJ"):
        return "jwt"
    if "code=" in v or v.startswith("appie://"):
        return "code"
    return "unknown"


def clean_pasted(value: str | None) -> str:
    """Common copy-paste leftovers removed: whitespace, surrounding quotes, a
    trailing comma (copied from the JSON answer), a "Bearer " prefix."""
    v = (value or "").strip().rstrip(",").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    return v


def login_code(value: str | None) -> str | None:
    """The login code from what the admin pasted: the code itself, or the
    whole ``appie://login-exit?code=...`` link the AH login ends with."""
    v = clean_pasted(value)
    if not v:
        return None
    if "code=" in v or "?" in v or v.startswith("appie://"):
        query = urllib.parse.urlsplit(v).query or v.split("?", 1)[-1]
        codes = urllib.parse.parse_qs(query).get("code")
        v = codes[0].strip() if codes else ""   # a link without a code: nothing to use
    return v if re.fullmatch(r"[\x21-\x7e]{4,2048}", v) else None


class RefreshCooldown(Exception):
    """A refresh was attempted moments ago; don't try again yet."""


@dataclass
class TokenState:
    access: str | None
    refresh: str
    expires_at: float | None = None  # informational (AH's expires_in)
    seeded_from: str | None = None   # hash of the env refresh token it came from
    source: str | None = None        # "environment" or "admin": where the login was set
    set_at: float | None = None      # when it was set there (epoch seconds)
    renewed_at: float | None = None  # last successful renewal (epoch seconds)


_STATE_KEYS = ("access", "refresh", "expires_at", "seeded_from", "source", "set_at", "renewed_at")


def _fingerprint(token: str | None) -> str | None:
    return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else None


class TokenStore:
    """The token pair, encrypted (Fernet), as one row in the app database.

    The encryption key comes from the seal secret, which is kept OUTSIDE the
    database (see sealing.py), so a copy of the database alone reveals nothing.
    ``db`` None: nothing is stored (tests)."""

    KEY = "ah_tokens"

    def __init__(self, db, fernet: Fernet):
        self._db = db
        self._fernet = fernet

    def load(self) -> TokenState | None:
        if self._db is None:
            return None
        blob = self._db.get_secret(self.KEY)
        if blob is None:
            return None
        try:
            data = json.loads(self._fernet.decrypt(blob))
            return TokenState(**{k: data.get(k) for k in _STATE_KEYS})
        except (InvalidToken, ValueError, TypeError):
            log.error("stored AH login is unreadable (other secret or corrupt); ignoring it")
            return None

    def save(self, state: TokenState) -> None:
        if self._db is None:
            return
        self._db.set_secret(self.KEY, self._fernet.encrypt(json.dumps(asdict(state)).encode("utf-8")))


class TokenManager:
    def __init__(self, *, store: TokenStore, env_access: str | None, env_refresh: str | None,
                 refresh_url: str, client_id: str = "appie-ios", timeout: int = 20,
                 cooldown: float = 300.0, opener: urllib.request.OpenerDirector | None = None,
                 token_url: str | None = None):
        self._store = store
        self._refresh_url = refresh_url
        # Login-code exchange: same API, without "/refresh" (appie-go exchangeCode).
        self._token_url = token_url or refresh_url.rsplit("/refresh", 1)[0]
        self._client_id = client_id
        self._timeout = timeout
        self._cooldown = cooldown
        self._opener = opener
        self._lock = threading.Lock()
        self._last_attempt = 0.0
        self._last_failed = False
        self._env_fp = _fingerprint(env_refresh)
        self._state = self._bootstrap(env_access, env_refresh)

    # -- setup -----------------------------------------------------------------
    def _bootstrap(self, env_access: str | None, env_refresh: str | None) -> TokenState | None:
        disk = self._store.load()
        env_fp = _fingerprint(env_refresh)
        if env_refresh and token_kind(env_refresh) != "refresh":
            # Only the shape is reported, never the value.
            log.warning("AH_REFRESH_TOKEN does not look like a refresh token (it looks like: %s); "
                        "AH will probably refuse to renew it", token_kind(env_refresh))
        if env_refresh and (disk is None or disk.seeded_from != env_fp):
            # New tokens from the operator: they replace whatever was stored.
            state = TokenState(access=env_access or None, refresh=env_refresh, seeded_from=env_fp,
                               source="environment", set_at=time.time())
            self._store.save(state)
            log.info("AH login: using the tokens from the environment")
            return state
        if disk is not None:
            log.info("AH login: using the stored (renewed) tokens")
            return disk
        log.warning("AH login: no tokens configured (set AH_ACCESS_TOKEN and AH_REFRESH_TOKEN)")
        return None

    @property
    def configured(self) -> bool:
        return self._state is not None

    # -- use -------------------------------------------------------------------
    def access_token(self) -> str:
        state = self._state
        if state is None:
            raise AHAuthError("no AH login configured")
        if state.access:
            return state.access
        # Only a refresh token was configured: get a first access token.
        return self.refresh(failed_token=None)

    def refresh(self, failed_token: str | None) -> str:
        """Renew the access token; returns the token to use now.

        ``failed_token`` is the access token AH rejected. If another request
        already renewed it, the new token is returned without refreshing again.
        Raises RefreshCooldown when a refresh happened moments ago (so the
        failure is probably not about the token), AHAuthError when AH refuses.
        """
        with self._lock:
            state = self._state
            if state is None:
                raise AHAuthError("no AH login configured")
            if failed_token is not None and state.access and state.access != failed_token:
                return state.access  # someone else already renewed it

            # Another server process may have renewed and saved it meanwhile.
            disk = self._store.load()
            if disk and disk.refresh != state.refresh and disk.access and disk.access != failed_token:
                self._state = disk
                return disk.access

            now = time.monotonic()
            if self._last_attempt and now - self._last_attempt < self._cooldown:
                if self._last_failed:
                    raise AHAuthError("AH login renewal failed recently; not retrying yet")
                raise RefreshCooldown()
            return self._renew_locked(state)

    def _renew_locked(self, state: TokenState) -> str:
        """Call AH's refresh endpoint and store the new pair (lock held)."""
        self._last_attempt = time.monotonic()
        try:
            new_state = self._call_refresh(state)
        except AHAuthError:
            self._last_failed = True
            raise
        self._last_failed = False
        self._state = new_state
        self._store.save(new_state)
        log.info("AH access token renewed (valid for %s s)",
                 int(new_state.expires_at - time.time()) if new_state.expires_at else "?")
        assert new_state.access   # _call_refresh guarantees a non-empty access token
        return new_state.access

    # -- administration (admin panel) ---------------------------------------------
    def set_tokens(self, access: str | None, refresh: str) -> None:
        """Replace the login with new tokens (e.g. from the admin panel).

        They are stored encrypted and used immediately. They stay in use after
        a restart; only a *changed* AH_REFRESH_TOKEN in the environment
        replaces them (it is compared with the env value at this moment).
        """
        with self._lock:
            state = TokenState(access=access or None, refresh=refresh, seeded_from=self._env_fp,
                               source="admin", set_at=time.time())
            self._state = state
            self._store.save(state)
            # A fresh login: allow an immediate renewal if AH rejects it.
            self._last_attempt = 0.0
            self._last_failed = False
        log.info("AH login replaced via the admin panel")

    def login_with_code(self, code: str) -> None:
        """Exchange a login code (from AH's login page) for a new token pair
        and use it (``POST /mobile-auth/v1/auth/token``, as appie-go's
        exchangeCode). Raises AHAuthError when AH refuses the code."""
        body = {"clientId": self._client_id, "code": code}
        data = self._post_json(self._token_url, body, "login code exchange")
        access, refresh = data.get("access_token"), data.get("refresh_token")
        if not access or not refresh:
            raise AHAuthError("login code exchange failed: no tokens in response")
        self.set_tokens(access, refresh)
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            with self._lock:
                if self._state is not None:
                    self._state.expires_at = time.time() + expires_in
                    self._store.save(self._state)

    def renew_now(self) -> None:
        """Renew immediately, ignoring the cooldown (admin action)."""
        with self._lock:
            if self._state is None:
                raise AHAuthError("no AH login configured")
            self._renew_locked(self._state)

    def status(self) -> dict:
        """Login status for the admin panel. Never includes the tokens."""
        state = self._state
        if state is None:
            return {"configured": False}
        return {
            "configured": True,
            # Shape only (never the value): is the stored refresh token a UUID
            # like AH's refresh tokens? If not, AH answers 400 on renewal.
            "refresh_kind": token_kind(state.refresh),
            "has_access_token": bool(state.access),
            "source": state.source or "unknown",
            "set_at": state.set_at,
            "renewed_at": state.renewed_at,
            "expires_at": state.expires_at,
            "last_renewal_failed": self._last_failed,
        }

    def _post_json(self, url: str, payload: dict, what: str) -> dict:
        """POST to AH's auth API; the JSON answer as a dict. AHAuthError
        (with AH's HTTP status) on failure. Tokens never end up in messages."""
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST", headers=AUTH_HEADERS)
        opener = self._opener or _OPENER
        try:
            with opener.open(request, timeout=self._timeout) as resp:
                text = decode_body(resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            detail = decode_body(exc.read(), exc.headers)[:300]
            raise AHAuthError(f"{what} failed: HTTP {exc.code}: {detail}", exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise AHAuthError(f"{what} failed: {type(exc).__name__}") from None
        try:
            data = json.loads(text)
        except ValueError:
            raise AHAuthError(f"{what} failed: non-JSON response") from None
        if not isinstance(data, dict):
            raise AHAuthError(f"{what} failed: unexpected response")
        return data

    def _call_refresh(self, state: TokenState) -> TokenState:
        data = self._post_json(self._refresh_url, {"clientId": self._client_id, "refreshToken": state.refresh},
                               "token refresh")
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise AHAuthError("token refresh failed: no access_token in response")
        expires_in = data.get("expires_in")
        return TokenState(
            access=access,
            # AH rotates refresh tokens; keep the old one only if none was sent.
            refresh=data.get("refresh_token") or state.refresh,
            expires_at=time.time() + expires_in if isinstance(expires_in, (int, float)) else None,
            seeded_from=state.seeded_from,
            source=state.source,
            set_at=state.set_at,
            renewed_at=time.time(),
        )
