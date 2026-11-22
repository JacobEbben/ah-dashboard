"""Admin rights.

Two ways to have them:

- **A signed-in admin user** (sign-in through the login service, ``is_admin`` on
  the account; with REQUIRE_ADMIN_2FA only in a sign-in made WITH the
  authenticator code there).
  Admin rights are not a separate session: they follow from the account on
  every request (``admin_user()``), for as long as the user is signed in
  (USER_SESSION_DAYS). Demoting, disabling, deleting or signing out ends them
  at once; promoting gives them at once.
- **The admin password**, the emergency way in (e.g. when the login service is
  unavailable): checked against ADMIN_PASSWORD_HASH (werkzeug hash, scrypt by
  default; the plain password is never stored). It opens a session of its own
  that ends after ADMIN_SESSION_MINUTES without admin activity (``touch()``)
  and at the latest ADMIN_SESSION_MAX_HOURS after it started. It carries a
  keyed fingerprint of ADMIN_PASSWORD_HASH: changing the password ends all
  those sessions. Failed attempts are limited per client and per account
  (``lockout``); behind a proxy the client is only right with TRUSTED_PROXY.

The panel exists when ADMIN_PASSWORD_HASH is set OR an admin user exists.
Every state-changing form carries the session's CSRF token; admin-only API
calls send it in the X-CSRF-Token header (``require_admin_api``).
"""

from __future__ import annotations

import functools
import hmac
import time

from flask import current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash

from ..container import services
from ..errors import APIError
from . import access, csrf, sessions
from .compare import same_secret
from .lockout import ADMIN_PASSWORD_ACCOUNT, admin_policy, password_check_slot

_SESSION_KEY = "admin_login_at"
_PW_KEY = "admin_pw"       # fingerprint of the admin password hash it was opened with
_SEEN_KEY = "admin_seen_at"   # last admin activity (missing in older cookies: the sign-in time)
_TOUCH_EVERY = 60             # seconds: don't rewrite the cookie on every request


def password_fingerprint(password_hash: str, key: bytes) -> str:
    """Keyed fingerprint of the admin password hash, kept in the (signed but
    readable) cookie: changes with the password, reveals nothing about it."""
    return hmac.new(key, ("admin-password:" + password_hash).encode("utf-8"), "sha256").hexdigest()[:32]


def _password_hash() -> str:
    return services().settings.auth.admin_password_hash or ""


def _current_fingerprint() -> str:
    key = current_app.secret_key
    return password_fingerprint(_password_hash(), key if isinstance(key, bytes) else str(key).encode())


def password_configured() -> bool:
    return bool(_password_hash())


def enabled() -> bool:
    """The admin panel exists: an admin password or at least one admin user."""
    return password_configured() or services().users.count_admins() > 0


def check_password(password: str) -> bool:
    """Check the admin password; counts failures (``lockout.admin_policy``)."""
    policy = admin_policy()
    try:
        with password_check_slot():
            ok = bool(password) and check_password_hash(_password_hash(), password)
    except (ValueError, TypeError):  # malformed hash in config
        ok = False
    if ok:
        policy.succeeded(ADMIN_PASSWORD_ACCOUNT)
    else:
        policy.failed(ADMIN_PASSWORD_ACCOUNT)
    return ok


def start_password_session() -> None:
    """Admin session from the admin password (not tied to a user)."""
    sessions.begin(None)
    session[_SESSION_KEY] = session[_SEEN_KEY] = time.time()
    session[_PW_KEY] = _current_fingerprint()


def start_user_session(record: dict, op_sid: str = "", id_token: str = "", amr: str = "") -> None:
    """Sign a user in (login service). Admin rights are not part of the session:
    they follow from the account on every request (``admin_user``)."""
    access.login_user(record, op_sid, id_token, amr)


def is_last_admin(name: str) -> bool:
    """Without an admin password, is ``name`` the only enabled admin user?
    Then they can't lose admin rights or be removed (nobody could reach the
    panel any more)."""
    users = services().users
    rec = users.get(name)
    return bool(not password_configured() and rec and rec["is_admin"] and not rec["disabled"]
                and users.count_admins() <= 1)


def admin_user() -> str | None:
    """The signed-in user if their account has admin rights, else None.
    Checked against the database on every call: demoting, disabling, deleting
    or signing out takes effect at once, promoting too."""
    name = access.current_user()          # also checks the sign-in is listed and current
    if name is None:
        return None
    s = services()
    rec = s.users.get(name)
    if not rec or not rec["is_admin"] or rec["disabled"]:
        return None
    # A phished password alone must not give admin rights: only a sign-in made with
    # the authenticator code at the login service counts (REQUIRE_ADMIN_2FA).
    if s.settings.auth.require_admin_2fa and "otp" not in sessions.current_amr().split():
        return None
    return name


def admin_needs_2fa() -> bool:
    """An admin account signed in WITHOUT the authenticator code (shown as a notice)."""
    name = access.current_user()
    rec = services().users.get(name) if name else None
    return bool(rec and rec["is_admin"] and not rec["disabled"] and admin_user() is None)


def drop() -> None:
    """Remove the admin-password part of this session (a user stays signed in)."""
    for key in (_SESSION_KEY, _SEEN_KEY, _PW_KEY):
        session.pop(key, None)


def touch() -> None:
    """Admin-password activity: its idle limit (ADMIN_SESSION_MINUTES) starts
    again. Only for an admin-password session (admin users have no limit of
    their own: their sign-in lasts USER_SESSION_DAYS)."""
    if _PW_KEY not in session:
        return
    now = time.time()
    seen = session.get(_SEEN_KEY)
    if not isinstance(seen, (int, float)) or now - seen >= _TOUCH_EVERY:
        session[_SEEN_KEY] = now


def _within_limits(now: float) -> bool:
    """Not idle for ADMIN_SESSION_MINUTES, not older than ADMIN_SESSION_MAX_HOURS."""
    started = session.get(_SESSION_KEY)
    if not isinstance(started, (int, float)):
        return False
    seen = session.get(_SEEN_KEY)
    if not isinstance(seen, (int, float)) or seen < started:
        seen = started
    a = services().settings.auth
    return now - seen < a.admin_session_minutes * 60 and now - started < a.admin_session_max_hours * 3600


def _password_session() -> bool:
    """An admin-password session: within its time limits, the password still
    configured and unchanged since this sign-in, and not ended (logout) on the server."""
    fingerprint = session.get(_PW_KEY)
    return (isinstance(fingerprint, str) and _within_limits(time.time()) and password_configured()
            and same_secret(fingerprint, _current_fingerprint()) and sessions.listed(None))


def password_session() -> bool:
    """Is this request signed in with the admin password (not as an admin user)?"""
    return enabled() and _password_session()


def logged_in() -> bool:
    """Admin rights for this request: a signed-in admin user, or an admin-password session."""
    return admin_user() is not None or _password_session()


def require_admin_api(view):
    """Admin-only JSON API action: needs an active admin session and the
    session's CSRF token in the X-CSRF-Token header."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not (enabled() and logged_in()):
            raise APIError("admin_required", "This action requires an admin login.", 401)
        csrf.check_header()
        touch()
        return view(*args, **kwargs)
    return wrapper


def require_admin(view):
    """Admin pages: admin rights only; POSTs also need the CSRF token. Without
    them a signed-in user keeps their sign-in (only an expired admin-password
    part is removed); anything else is ended."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not logged_in():
            if access.current_user():
                drop()
            else:
                sessions.end()
            return redirect(url_for("admin.login"))
        if request.method == "POST":
            csrf.check_form()
        touch()
        return view(*args, **kwargs)
    return wrapper
