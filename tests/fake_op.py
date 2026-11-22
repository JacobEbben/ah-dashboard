"""A fake login service (OpenID provider) for the tests: discovery, JWKS and a token
endpoint, answering through ``LoginClient.http`` (no network)."""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://login.test"
CLIENT_ID = "ah-dashboard"
CLIENT_SECRET = "test-client-secret-not-real"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeOP:
    def __init__(self, issuer: str = ISSUER):
        self.issuer = issuer
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.codes: dict[str, dict] = {}
        self.token_overrides: dict = {}       # tamper with the next ID token's claims
        self.requests: list[tuple[str, str]] = []

    # ---- signing ----
    def sign(self, claims: dict, typ: str = "JWT", key=None, kid: str | None = None, alg: str = "RS256") -> str:
        header = {"alg": alg, "typ": typ, "kid": kid or self.kid}
        signing_input = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(claims).encode())
        sig = (key or self.key).sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing_input + "." + _b64(sig)

    def id_claims(self, sub: str, username: str, nonce: str, sid: str = "op-sid-1") -> dict:
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": sub, "aud": CLIENT_ID, "azp": CLIENT_ID, "iat": now, "exp": now + 300,
                  "nonce": nonce, "preferred_username": username, "name": "Test", "sid": sid, "amr": ["pwd", "otp"]}
        claims.update(self.token_overrides)
        return {k: v for k, v in claims.items() if v is not None}

    def logout_token(self, sub: str | None = None, sid: str | None = None, **extra) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": secrets.token_hex(8),
                  "events": {"http://schemas.openid.net/event/backchannel-logout": {}}}
        if sub:
            claims["sub"] = sub
        if sid:
            claims["sid"] = sid
        claims.update(extra)
        return self.sign(claims, typ="logout+jwt")

    # ---- the "network" ----
    def http(self, method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, bytes]:
        self.requests.append((method, url))
        if url == self.issuer + "/.well-known/openid-configuration":
            return 200, json.dumps({
                "issuer": self.issuer, "authorization_endpoint": self.issuer + "/authorize",
                "token_endpoint": self.issuer + "/token", "jwks_uri": self.issuer + "/jwks.json",
                "end_session_endpoint": self.issuer + "/logout"}).encode()
        if url == self.issuer + "/jwks.json":
            pub = self.key.public_key().public_numbers()
            n = pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")
            e = pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")
            return 200, json.dumps({"keys": [{"kty": "RSA", "kid": self.kid, "alg": "RS256", "n": _b64(n), "e": _b64(e)}]}).encode()
        if url == self.issuer + "/token" and method == "POST":
            expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if headers.get("Authorization") != expected:
                return 401, b'{"error": "invalid_client"}'
            form = dict(urllib.parse.parse_qsl((data or b"").decode()))
            grant = self.codes.pop(form.get("code", ""), None)
            if grant is None:
                return 400, b'{"error": "invalid_grant"}'
            return 200, json.dumps({"access_token": "at", "token_type": "Bearer",
                                    "id_token": self.sign(self.id_claims(**grant))}).encode()
        return 404, b"{}"

    def approve(self, authorize_url: str, sub: str, username: str) -> dict[str, str]:
        """What the login service does when the user signs in: a code for the callback."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
        assert q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT_ID and q["nonce"]
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"sub": sub, "username": username, "nonce": q["nonce"]}
        return {"code": code, "state": q["state"], "iss": self.issuer}
