"""Sign in with Google (OpenID Connect, authorization code flow with PKCE).

1. ``start()``: random ``state``, ``nonce`` and PKCE verifier; the browser is
   sent to Google's sign-in page.
2. Google sends the browser back to ``/auth/google/callback`` with a one-time
   code; the route checks ``state`` against the session.
3. ``finish()``: the server exchanges the code at Google's token endpoint
   (server to server, HTTPS, authenticated with the client secret and the
   PKCE verifier) and reads the ID token from the answer.

The ID token comes straight from Google's token endpoint over a verified TLS
connection that our client secret authenticated, so its signature need not be
checked (OpenID Connect Core 3.1.3.7; Google's OIDC guide says the same). Its
claims are still checked: issuer, audience (our client id), expiry, the nonce
of this sign-in, and a verified e-mail address.

No Google tokens are kept: only the account id (``sub``) and e-mail address
are used. Only the scopes ``openid email`` are asked for.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .compare import same_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email"
CLOCK_SKEW = 120          # seconds of clock difference accepted for exp / iat
MAX_RESPONSE = 64 * 1024


class GoogleError(Exception):
    """The sign-in failed; ``reason`` is for the log only (never shown)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Pending:
    """One sign-in in progress (kept in the session cookie until the callback)."""
    state: str
    nonce: str
    verifier: str

    @staticmethod
    def new() -> Pending:
        return Pending(secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48))

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def state_matches(self, state: object) -> bool:
        return same_secret(state, self.state)


@dataclass(frozen=True)
class Identity:
    sub: str        # Google's permanent account id
    email: str      # verified e-mail address


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GoogleSignIn:
    def __init__(self, client_id: str, client_secret: str, timeout: float = 10):
        self.client_id = client_id
        self._secret = client_secret
        self._timeout = timeout
        # No redirects, and no proxies from the environment (like the AH client).
        self._opener = urllib.request.build_opener(_NoRedirects, urllib.request.ProxyHandler({}))

    def authorize_url(self, pending: Pending, redirect_uri: str) -> str:
        """Google's sign-in page for this sign-in."""
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": pending.state,
            "nonce": pending.nonce,
            "code_challenge": pending.challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })

    def finish(self, code: str, pending: Pending, redirect_uri: str) -> Identity:
        """Exchange the code and return the checked identity."""
        if not isinstance(code, str) or not 0 < len(code) <= 2048:
            raise GoogleError("missing or odd code")
        answer = self._post_token({
            "code": code,
            "client_id": self.client_id,
            "client_secret": self._secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": pending.verifier,
        })
        id_token = answer.get("id_token") if isinstance(answer, dict) else None
        if not isinstance(id_token, str):
            raise GoogleError("no id_token in the token answer")
        return self.check_claims(decode_payload(id_token), pending.nonce)

    def check_claims(self, claims: dict, nonce: str, now: float | None = None) -> Identity:
        now = time.time() if now is None else now
        if claims.get("iss") not in ISSUERS:
            raise GoogleError("wrong issuer")
        aud = claims.get("aud")
        if aud != self.client_id and not (isinstance(aud, list) and self.client_id in aud):
            raise GoogleError("wrong audience")
        if isinstance(aud, list) and claims.get("azp") != self.client_id:
            raise GoogleError("wrong authorized party")
        exp, iat = claims.get("exp"), claims.get("iat")
        if not isinstance(exp, (int, float)) or exp + CLOCK_SKEW < now:
            raise GoogleError("expired")
        if isinstance(iat, (int, float)) and iat - CLOCK_SKEW > now:
            raise GoogleError("issued in the future")
        if not same_secret(claims.get("nonce"), nonce):
            raise GoogleError("nonce mismatch")
        sub, email = claims.get("sub"), claims.get("email")
        if not isinstance(sub, str) or not 0 < len(sub) <= 255:
            raise GoogleError("no account id")
        if claims.get("email_verified") is not True or not isinstance(email, str) or "@" not in email:
            raise GoogleError("no verified e-mail address")
        return Identity(sub=sub, email=email.strip().lower())

    def _post_token(self, form: dict) -> dict:
        data = urllib.parse.urlencode(form).encode("ascii")
        req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                body = resp.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            raise GoogleError(f"token endpoint answered {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleError(f"token endpoint unreachable: {type(exc).__name__}") from None
        if len(body) > MAX_RESPONSE:
            raise GoogleError("token answer too large")
        try:
            answer = json.loads(body)
        except ValueError:
            raise GoogleError("token answer is not JSON") from None
        if not isinstance(answer, dict):
            raise GoogleError("token answer is not an object")
        return answer


def decode_payload(jwt: str) -> dict:
    """The claims of a JWT (header.payload.signature), without checking the
    signature (see the module docstring for why that is right here)."""
    parts = jwt.split(".")
    if len(parts) != 3:
        raise GoogleError("id_token is not a JWT")
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, TypeError):
        raise GoogleError("id_token payload unreadable") from None
    if not isinstance(claims, dict):
        raise GoogleError("id_token payload is not an object")
    return claims
