"""Signing in through the central login service (OpenID Connect).

GET  /auth/login?purpose=...       start; purposes:
    login    sign in, back to the dashboard (the first sign-in creates the account)
    admin    the same, then to the admin panel (admin rights follow from the
             account, auth/admin.admin_user; with REQUIRE_ADMIN_2FA only for a
             sign-in made with the authenticator code there)
GET  /auth/callback                the login service sends the browser back here
POST /auth/backchannel-logout      the login service ends a sign-in (signed logout token)

Starting a sign-in is limited per client (LOGIN_SIGNIN_PER_MINUTE), NOT by the
server-wide admin-password budget (anyone could spend that with a loop of cheap
requests). The sign-in in progress (state, nonce, PKCE verifier, purpose) waits in
the session cookie for at most PENDING_SECONDS. Errors are logged with a
reference; the user sees a generic message.
"""

from __future__ import annotations

import logging
import time

from flask import Response, abort, redirect, request, session, url_for

from ..auth.admin import start_user_session
from ..auth.login_client import LoginError, Pending
from ..auth.users import UserError
from ..container import services
from ..logging_setup import log_error
from ..ratelimit import client_addr, client_key
from . import bp

log = logging.getLogger(__name__)

PENDING_KEY = "login_pending"
PENDING_SECONDS = 600
PURPOSES = ("login", "admin")


def _login():
    login = services().login
    if login is None:
        abort(404)   # sign-in not configured
    return login


def base_url() -> str:
    return services().settings.auth.public_base_url or request.host_url.rstrip("/")


def redirect_uri() -> str:
    """Where the login service sends the browser back: register exactly this there."""
    return base_url() + url_for("web.login_callback")


def _fail(purpose: str, error: str):
    """Back to where the sign-in started, with a generic message."""
    if purpose == "admin":
        return redirect(url_for("admin.login", error=error))
    return redirect(url_for("web.login", error=error))


@bp.get("/auth/login")
def login_start():
    login = _login()
    purpose = request.args.get("purpose", "login")
    if purpose not in PURPOSES:
        purpose = "login"
    s = services()
    if not s.rate_limiter.allow("login-signin", client_key(), s.settings.limits.login_signin_per_minute, 60):
        log.warning("sign-in refused: too many sign-ins started from %s", client_addr())
        return _fail(purpose, "loginLocked")
    try:
        url, pending = login.start(redirect_uri())
    except LoginError as exc:
        log_error(log, "could not start a sign-in: %s", exc.reason)
        return _fail(purpose, "loginFailed")
    session[PENDING_KEY] = {"state": pending.state, "nonce": pending.nonce, "verifier": pending.verifier,
                            "issuer": pending.issuer, "purpose": purpose, "at": time.time()}
    return redirect(url)


@bp.get("/auth/callback")
def login_callback():
    login = _login()
    data = session.pop(PENDING_KEY, None)
    if not isinstance(data, dict) or not isinstance(data.get("at"), (int, float)) \
            or time.time() - data["at"] > PENDING_SECONDS:
        return _fail("login", "loginFailed")             # no (or an old) sign-in in progress here
    purpose = str(data["purpose"]) if data.get("purpose") in PURPOSES else "login"
    if request.args.get("error"):                        # cancelled or refused at the login service
        return redirect(url_for("admin.login") if purpose == "admin" else url_for("web.index"))
    pending = Pending(str(data.get("state", "")), str(data.get("nonce", "")), str(data.get("verifier", "")),
                      str(data.get("issuer", "")))
    try:
        claims, id_token = login.finish(dict(request.args), pending, redirect_uri())
    except LoginError as exc:
        log_error(log, "sign-in failed: %s", exc.reason)
        return _fail(purpose, "loginFailed")
    users = services().users
    try:
        user, created = users.sign_in_login(claims["sub"], claims["preferred_username"])
    except UserError as exc:
        if exc.code == "disabled":
            return _fail(purpose, "accountDisabled")
        if exc.code == "too_many":
            log.warning("sign-up refused: MAX_USERS reached")
            return _fail(purpose, "signupFull")
        log.warning("sign-in refused: %s", exc.code)
        return _fail(purpose, "loginFailed")
    amr = claims.get("amr")
    amr_text = " ".join(a for a in amr if isinstance(a, str) and a.isalnum())[:40] if isinstance(amr, list) else ""
    start_user_session(user, op_sid=str(claims.get("sid") or ""), id_token=id_token, amr=amr_text)
    users.record_login(user["username"])
    log.info("user %s %s through the login service from %s", user["username"],
             "signed up" if created else "signed in", client_addr())
    return redirect(url_for("admin.index") if purpose == "admin" else url_for("web.index"))


@bp.post("/auth/backchannel-logout")
def backchannel_logout():
    """The login service says a sign-in ended there (sign out, blocked, reset): end
    ours too. Server to server, so no CSRF token: the signed logout token is the proof."""
    login = _login()
    s = services()
    if not s.rate_limiter.allow("backchannel", client_key(), 120, 60):
        return Response(status=429)
    try:
        claims = login.verify(request.form.get("logout_token", ""), logout=True)
    except LoginError as exc:
        log.warning("back-channel logout refused: %s", exc.reason)
        return Response(status=400)
    sid, sub = claims.get("sid"), claims.get("sub")
    n = s.sessions.end_login_session(sid if isinstance(sid, str) else None, sub if isinstance(sub, str) else None)
    log.info("back-channel logout ended %d sign-in(s)", n)
    return Response(status=200, headers={"Cache-Control": "no-store"})
