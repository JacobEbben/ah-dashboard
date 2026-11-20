"""Sign in with Google: the redirect to Google and the way back.

GET /auth/google?purpose=...     start; purposes:
    login    sign in (anyone with a verified Google account; the first
             sign-in creates the account), back to the dashboard
    admin    the same, then to the admin panel (admin users: admin rights
             follow from the account, auth/admin.admin_user)
    link     a signed-in user from before Google sign-in links their Google account
GET /auth/google/callback        Google sends the browser back here

Starting a sign-in is limited per client (GOOGLE_SIGNIN_PER_MINUTE), NOT by
the server-wide admin-password budget: anyone could otherwise spend that
budget with a loop of cheap requests and block every sign-in.

The sign-in in progress (state, nonce, PKCE verifier, purpose) waits in the
session cookie for at most PENDING_SECONDS. Errors are logged with a
reference; the user sees a generic message.
"""

from __future__ import annotations

import logging
import time

from flask import abort, redirect, request, session, url_for

from ..auth import access
from ..auth.admin import start_user_session
from ..auth.google import GoogleError, Pending
from ..auth.users import UserError
from ..container import services
from ..logging_setup import log_error
from ..ratelimit import client_addr, client_key
from . import bp

log = logging.getLogger(__name__)

PENDING_KEY = "google_signin"
PENDING_SECONDS = 600
PURPOSES = ("login", "admin", "link")


def _google():
    google = services().google
    if google is None:
        abort(404)   # Google sign-in not configured
    return google


def redirect_uri() -> str:
    """Where Google sends the browser back: must be listed in the OAuth client."""
    base = services().settings.auth.public_base_url
    return (base + url_for("web.google_callback")) if base else url_for("web.google_callback", _external=True)


def _fail(purpose: str, error: str):
    """Back to where the sign-in started, with a generic message."""
    if purpose == "admin":
        return redirect(url_for("admin.login", error=error))
    if purpose == "link":
        return redirect(url_for("web.privacy", error=error))
    return redirect(url_for("web.login", error=error))


@bp.get("/auth/google")
def google_start():
    google = _google()
    purpose = request.args.get("purpose", "login")
    if purpose not in PURPOSES:
        purpose = "login"
    if purpose == "link" and access.current_user() is None:
        return redirect(url_for("web.login"))
    s = services()
    if not s.rate_limiter.allow("google-signin", client_key(), s.settings.limits.google_signin_per_minute, 60):
        log.warning("sign-in refused: too many sign-ins started from %s", client_addr())
        return _fail(purpose, "loginLocked")
    pending = Pending.new()
    session[PENDING_KEY] = {"state": pending.state, "nonce": pending.nonce, "verifier": pending.verifier,
                            "purpose": purpose, "at": time.time()}
    return redirect(google.authorize_url(pending, redirect_uri()))


@bp.get("/auth/google/callback")
def google_callback():
    google = _google()
    data = session.pop(PENDING_KEY, None)
    if not isinstance(data, dict) or not isinstance(data.get("at"), (int, float)) \
            or time.time() - data["at"] > PENDING_SECONDS:
        return _fail("login", "googleFailed")            # no (or an old) sign-in in progress here
    purpose = str(data["purpose"]) if data.get("purpose") in PURPOSES else "login"
    if request.args.get("error"):                        # cancelled at Google, or refused
        return redirect(url_for("admin.login") if purpose == "admin"
                        else url_for("web.privacy") if purpose == "link" else url_for("web.login"))
    pending = Pending(str(data.get("state", "")), str(data.get("nonce", "")), str(data.get("verifier", "")))
    if not pending.state_matches(request.args.get("state")):
        log.warning("Google sign-in with a wrong state from %s", client_addr())
        return _fail(purpose, "googleFailed")
    try:
        who = google.finish(request.args.get("code", ""), pending, redirect_uri())
    except GoogleError as exc:
        log_error(log, "Google sign-in failed: %s", exc.reason)
        return _fail(purpose, "googleFailed")

    users = services().users
    if purpose == "link":
        name = access.current_user()
        if name is None:
            return redirect(url_for("web.login"))
        try:
            users.link_google(name, who.sub, who.email)
        except UserError as exc:
            if exc.code == "already_linked":
                log.warning("refused to re-link the Google account of %s from %s", name, client_addr())
            return _fail(purpose, {"google_taken": "googleTaken", "already_linked": "googleAlreadyLinked"}
                         .get(exc.code, "googleFailed"))
        log.info("user %s linked a Google account", name)
        return redirect(url_for("web.privacy"))

    try:
        user, created = users.sign_in_google(who.sub, who.email)
    except UserError as exc:
        if exc.code == "disabled":
            return _fail(purpose, "accountDisabled")
        if exc.code == "too_many":
            log.warning("sign-up refused: MAX_USERS reached")
            return _fail(purpose, "signupFull")
        raise
    start_user_session(user)       # admin rights follow from the account (auth/admin.admin_user)
    users.record_login(user["username"])
    log.info("user %s %s with Google from %s", user["username"],
             "signed up" if created else "signed in", client_addr())
    return redirect(url_for("admin.index") if purpose == "admin" else url_for("web.index"))
