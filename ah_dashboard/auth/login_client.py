"""Signing in through the central login service (OpenID Connect, authorization code + PKCE).

The connection comes from the admin panel (auth/login_connection.py) or the
LOGIN_ISSUER / LOGIN_CLIENT_ID / LOGIN_CLIENT_SECRET settings; only the admin password
can change it. The same client design is used by File Share.

Every ID token is checked: RS256 signature against the issuer's published keys
(JWKS), ``iss``, ``aud``/``azp`` (our client id), ``exp``/``iat``, and the
``nonce`` of this sign-in. Discovery and keys are fetched from the issuer over
HTTPS (http only outside production), without redirects or proxies, and cached
for 10 minutes; endpoints must be on the issuer's own origin.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .compare import same_secret

CACHE_SECONDS = 600
REFETCH_SECONDS = 60          # an unknown kid makes us re-download the keys at most this often
LOGOUT_MAX_AGE = 600
CLOCK_SKEW = 120
MAX_RESPONSE = 256 * 1024
BACKCHANNEL_EVENT = "http://schemas.openid.net/event/backchannel-logout"

# (method, url, form data or None, headers) -> (status, body)
Http = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]]


class LoginError(Exception):
    """Sign-in failed; ``reason`` is for the log only (never shown)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_http(method: str, url: str, data: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(_NoRedirects, urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **headers})
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE + 1) if exc.fp else b""
    except (urllib.error.URLError, OSError) as exc:
        raise LoginError(f"login service unreachable: {type(exc).__name__}") from None


def valid_issuer(value: object, production: bool) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 300 or any(c in value for c in " \t\r\n<>\"'\\?#"):
        return False
    p = urllib.parse.urlsplit(value)
    if p.username or p.password or not p.hostname:
        return False
    return p.scheme == "https" or (not production and p.scheme == "http")


def origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


@dataclass(frozen=True)
class Pending:
    """One sign-in in progress (kept in the signed session cookie until the callback)."""
    state: str
    nonce: str
    verifier: str
    issuer: str

    @staticmethod
    def new(issuer: str) -> Pending:
        return Pending(secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48), issuer)

    @property
    def challenge(self) -> str:
        return _b64(hashlib.sha256(self.verifier.encode("ascii")).digest())


class LoginClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, production: bool, http: Http = default_http):
        self._cfg = {"issuer": issuer.rstrip("/"), "client_id": client_id}
        self._client_secret = client_secret
        self._production = production
        self.http = http
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._last_refetch = 0.0
        self._seen_jti: dict[str, float] = {}   # logout tokens already used (replay)

    @property
    def issuer(self) -> str:
        return self._cfg["issuer"]

    def config(self) -> dict[str, Any]:
        return dict(self._cfg)

    def configured(self) -> bool:
        return bool(self._cfg["issuer"] and self._cfg["client_id"] and self._client_secret)

    def _secret(self) -> str:
        return self._client_secret

    # ---- discovery and keys ----
    def _get_json(self, url: str) -> dict[str, Any]:
        status, body = self.http("GET", url, None, {})
        if status != 200 or len(body) > MAX_RESPONSE:
            raise LoginError(f"{urllib.parse.urlsplit(url).path} answered {status}")
        try:
            data = json.loads(body)
        except ValueError:
            raise LoginError("answer is not JSON") from None
        if not isinstance(data, dict):
            raise LoginError("answer is not an object")
        return data

    def _cached(self, key: str, load: Callable[[], Any], force: bool = False) -> Any:
        with self._lock:
            hit = self._cache.get(key)
            if hit and not force and hit[0] > time.monotonic():
                return hit[1]
        value = load()
        with self._lock:
            self._cache[key] = (time.monotonic() + CACHE_SECONDS, value)
        return value

    def discovery(self, issuer: str | None = None, force: bool = False) -> dict[str, Any]:
        iss = issuer or self._cfg["issuer"]
        if not iss:
            raise LoginError("not configured")

        def load() -> dict[str, Any]:
            d = self._get_json(iss.rstrip("/") + "/.well-known/openid-configuration")
            if d.get("issuer") != iss:
                raise LoginError("discovery names another issuer")
            for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(d.get(k), str) or origin(d[k]) != origin(iss):
                    raise LoginError(f"{k} missing or not on the issuer's origin")
            end = d.get("end_session_endpoint")
            if end is not None and (not isinstance(end, str) or origin(end) != origin(iss)):
                raise LoginError("end_session_endpoint not on the issuer's origin")
            return d
        return self._cached("discovery:" + iss, load, force)

    def _keys(self, force: bool = False) -> dict[str, rsa.RSAPublicKey]:
        d = self.discovery()

        def load() -> dict[str, rsa.RSAPublicKey]:
            out = {}
            for k in self._get_json(d["jwks_uri"]).get("keys", []):
                if isinstance(k, dict) and k.get("kty") == "RSA" and isinstance(k.get("kid"), str):
                    try:
                        out[k["kid"]] = rsa.RSAPublicNumbers(int.from_bytes(_b64d(k["e"]), "big"),
                                                             int.from_bytes(_b64d(k["n"]), "big")).public_key()
                    except (KeyError, ValueError, TypeError):
                        continue
            return out
        return self._cached("jwks:" + d["jwks_uri"], load, force)

    def _may_refetch(self) -> bool:
        """At most one forced key download per REFETCH_SECONDS: unsigned junk with an unknown
        kid (e.g. at the public back-channel endpoint) can't make us hammer the login service."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_refetch < REFETCH_SECONDS:
                return False
            self._last_refetch = now
            return True

    def _fresh_jti(self, jti: object, expires: float) -> bool:
        """A logout token works once (its jti is remembered until it can't pass the age check)."""
        if not isinstance(jti, str) or not 0 < len(jti) <= 200:
            return False
        with self._lock:
            now = time.time()
            for k in [k for k, t in self._seen_jti.items() if t < now]:
                del self._seen_jti[k]
            if jti in self._seen_jti or len(self._seen_jti) > 50_000:
                return False
            self._seen_jti[jti] = expires
            return True

    def test(self, issuer: str) -> None:
        """For the admin panel: discovery and keys reachable and consistent (raises LoginError)."""
        d = self.discovery(issuer, force=True)
        keys = self._get_json(d["jwks_uri"]).get("keys")
        if not isinstance(keys, list) or not keys:
            raise LoginError("no signing keys published")

    # ---- tokens ----
    def verify(self, token: str, *, nonce: str | None = None, logout: bool = False) -> dict[str, Any]:
        cfg = self.config()
        if not cfg:
            raise LoginError("not configured")
        try:
            head_b64, body_b64, sig_b64 = token.split(".")
            header, claims = json.loads(_b64d(head_b64)), json.loads(_b64d(body_b64))
        except (ValueError, TypeError, AttributeError):
            raise LoginError("malformed token") from None
        if not isinstance(header, dict) or not isinstance(claims, dict) or header.get("alg") != "RS256":
            raise LoginError("unexpected token type")
        kid = header.get("kid")
        keys = self._keys()
        if kid not in keys and isinstance(kid, str) and self._may_refetch():
            keys = self._keys(force=True)            # the login service may have rotated its key
        key = keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            raise LoginError("unknown signing key")
        try:
            key.verify(_b64d(sig_b64), f"{head_b64}.{body_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            raise LoginError("bad signature") from None
        now = time.time()
        aud = claims.get("aud")
        if claims.get("iss") != cfg["issuer"]:
            raise LoginError("wrong issuer")
        if aud != cfg["client_id"] and not (isinstance(aud, list) and cfg["client_id"] in aud):
            raise LoginError("wrong audience")
        if isinstance(aud, list) and claims.get("azp") != cfg["client_id"]:
            raise LoginError("wrong authorized party")
        iat = claims.get("iat")
        if not isinstance(iat, int) or iat - CLOCK_SKEW > now:
            raise LoginError("issued in the future")
        if logout:
            if header.get("typ") not in ("logout+jwt", "JWT") or "nonce" in claims:
                raise LoginError("not a logout token")
            events = claims.get("events")
            if not isinstance(events, dict) or BACKCHANNEL_EVENT not in events:
                raise LoginError("not a logout event")
            if now - iat > LOGOUT_MAX_AGE:
                raise LoginError("logout token too old")
            exp = claims.get("exp")
            if exp is not None and (not isinstance(exp, int) or exp + CLOCK_SKEW < now):
                raise LoginError("logout token expired")
            if not self._fresh_jti(claims.get("jti"), iat + LOGOUT_MAX_AGE + CLOCK_SKEW):
                raise LoginError("logout token without jti, or replayed")
        else:
            exp = claims.get("exp")
            if not isinstance(exp, int) or exp + CLOCK_SKEW < now:
                raise LoginError("expired")
            if nonce is None or not same_secret(claims.get("nonce"), nonce):
                raise LoginError("nonce mismatch")
        return claims

    # ---- the flow ----
    def start(self, redirect_uri: str) -> tuple[str, Pending]:
        cfg = self.config()
        if not cfg or not self.configured():
            raise LoginError("not configured")
        d = self.discovery()
        pending = Pending.new(cfg["issuer"])
        url = d["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": cfg["client_id"], "redirect_uri": redirect_uri,
            "scope": "openid profile", "state": pending.state, "nonce": pending.nonce,
            "code_challenge": pending.challenge, "code_challenge_method": "S256"})
        return url, pending

    def finish(self, args: dict[str, str], pending: Pending, redirect_uri: str) -> tuple[dict[str, Any], str]:
        """Check the callback, swap the code, verify the ID token: (claims, id_token)."""
        cfg = self.config()
        if not cfg or cfg["issuer"] != pending.issuer:
            raise LoginError("connection changed during sign-in")
        if not same_secret(args.get("state"), pending.state):
            raise LoginError("state mismatch")
        if "iss" in args and args["iss"] != cfg["issuer"]:
            raise LoginError("iss mismatch (mix-up)")
        code = args.get("code") or ""
        if not 0 < len(code) <= 200:
            raise LoginError("no code")
        d = self.discovery()
        auth = base64.b64encode(f"{urllib.parse.quote(cfg['client_id'])}:{urllib.parse.quote(self._secret())}".encode()).decode()
        form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                       "code_verifier": pending.verifier}).encode("ascii")
        status, body = self.http("POST", d["token_endpoint"], form, {
            "Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
        if status != 200 or len(body) > MAX_RESPONSE:
            raise LoginError(f"token endpoint answered {status}")
        try:
            answer = json.loads(body)
            id_token = answer["id_token"]
        except (ValueError, KeyError, TypeError):
            raise LoginError("no id_token in the answer") from None
        claims = self.verify(id_token, nonce=pending.nonce)
        if not isinstance(claims.get("sub"), str) or not 0 < len(claims["sub"]) <= 255:
            raise LoginError("no subject")
        if not isinstance(claims.get("preferred_username"), str):
            raise LoginError("no username (scope profile)")
        return claims, id_token

    def end_session_url(self, id_token: str, post_logout: str) -> str | None:
        try:
            end = self.discovery().get("end_session_endpoint")
        except LoginError:
            return None
        cfg = self.config()
        if not end or not cfg:
            return None
        return end + "?" + urllib.parse.urlencode({"id_token_hint": id_token, "post_logout_redirect_uri": post_logout,
                                                   "client_id": cfg["client_id"]})
