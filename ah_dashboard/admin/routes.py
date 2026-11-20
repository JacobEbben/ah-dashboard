"""Admin pages (server-rendered, no JavaScript).

GET  /admin/login    login form          POST /admin/login    log in
GET  /admin/         status + forms      POST /admin/tokens   set AH tokens
POST /admin/login-code  log in to AH with a login code (server exchanges it for tokens)
POST /admin/renew    renew token now     POST /admin/logout   log out
POST /admin/users/email|disable|enable|delete|promote|demote   manage dashboard users
GET  /admin/errors   recent error log entries, or one by its reference (?ref=)
POST /admin/lockouts/unlock|clear   lift admin-password lockouts (one key / all)

Admin users sign in with Google (web/google.py, purpose "admin"); their admin
rights follow from the account for as long as they are signed in. The admin
password is the emergency way in, with a timed session of its own.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from urllib.parse import quote

from flask import abort, flash, redirect, render_template, request, url_for

from .. import errorlog
from ..ah.auth import AHAuthError, clean_pasted, login_code, token_kind
from ..auth import access, csrf, sessions
from ..auth.admin import (
    admin_user,
    check_password,
    enabled,
    is_last_admin,
    logged_in,
    password_configured,
    require_admin,
    start_password_session,
)
from ..auth.lockout import ADMIN_PASSWORD_ACCOUNT, admin_policy, login_capacity
from ..auth.users import UserError
from ..container import services
from ..logging_setup import log_error
from ..ratelimit import client_addr
from . import bp

log = logging.getLogger(__name__)

# A token is one header value: printable ASCII, no spaces.
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")


@bp.before_request
def _admin_enabled():
    if not enabled():
        abort(404)  # no admin password configured: the panel does not exist


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@bp.app_template_filter("admin_size")
def _admin_size(n):
    """Bytes as "812 B", "36 KB" or "1.4 MB"."""
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / 1048576:.1f} MB"


@bp.app_template_filter("admin_time")
def _admin_time(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _tokens():
    return services().tokens


# Messages for errors the Google sign-in sends back here (web/google.py).
_GOOGLE_ERRORS = {
    "googleFailed": "Signing in with Google didn't work. Try again.",
    "accountDisabled": "This account is disabled.",
    "signupFull": "No new accounts can be made right now.",
    "loginLocked": "Too many sign-ins right now. Try again later.",
}


def _login_page(error: str | None = None, status: int = 200):
    s = services()
    if error is None:
        error = _GOOGLE_ERRORS.get(request.args.get("error", ""))
    # Signed in, but this account has no admin rights (an admin would be in the panel).
    return render_template("admin/login.html", csrf=csrf.csrf_token(), error=error,
                           signed_in_user=access.current_user(), google=s.settings.auth.google_enabled,
                           password=password_configured()), status


@bp.get("/login")
def login():
    if logged_in():
        return redirect(url_for("admin.index"))
    return _login_page()


@bp.post("/login")
def login_post():
    """The admin password (the emergency way in; admin users sign in with Google)."""
    csrf.check_form()
    policy = admin_policy()
    if policy.blocked(ADMIN_PASSWORD_ACCOUNT) or not login_capacity():
        log.warning("admin login locked out for %s", client_addr())
        return _login_page("Too many failed attempts. Try again later.", 429)
    if not check_password(request.form.get("password", "")):
        log.warning("failed admin login from %s", client_addr())
        return _login_page("Wrong password.", 401)
    start_password_session()
    log.info("admin logged in from %s", client_addr())
    return redirect(url_for("admin.index"))


@bp.get("/")
@require_admin
def index():
    return render_template("admin/index.html", status=_tokens().status(), csrf=csrf.csrf_token(),
                           users=_users().list(), prefs_sizes=_users().prefs_sizes(), me=admin_user(), health=_health(),
                           ah_login_url=_ah_login_url(), max_users=services().settings.auth.max_users,
                           lockouts=_lockouts(), error_log=bool(services().settings.app.error_log_file))


def _ah_login_url() -> str:
    """AH's login page for this app's client id (appie-go login.go)."""
    return ("https://login.ah.nl/login?client_id=" + quote(services().settings.ah.client_id, safe="")
            + "&response_type=code&redirect_uri=appie://login-exit")


def _health() -> dict:
    """Server health for the admin panel: AH request counters, caches, rate
    limits. Counts and times only (no error details, no tokens)."""
    s = services()
    session_stats = getattr(s.ah, "stats", None)
    return {
        "users": {"count": s.users.count(), "max": s.settings.auth.max_users},
        "db": s.db.file_sizes(),   # {data, log} bytes
        "ah": session_stats() if callable(session_stats) else None,
        "bargains": s.bargains_cache.stats(),
        "stores": s.stores_cache.stats(),
        "bonus": {**s.bonus_cache.status(), "background": s.scheduler.running("bonus")},
        "rate_limited": s.rate_limiter.blocked,
    }


# ---- dashboard users -------------------------------------------------------------------
def _users():
    return services().users


def _keep_an_admin(name: str) -> None:
    """Without an admin password, the last admin user can't lose admin rights
    (that would lock everyone out of the panel)."""
    if is_last_admin(name):
        raise UserError("last_admin")


def _user_action(action, ok_message: str):
    name = request.form.get("username", "")[:64]
    try:
        action(name)
    except UserError as exc:
        flash(str(exc), "error")
    else:
        flash(ok_message.format(name=name.strip().lower()), "ok")
    return redirect(url_for("admin.index") + "#users")


@bp.post("/users/email")
@require_admin
def set_user_email():
    """Set the e-mail address a Google account is linked by (e.g. for an
    account from before Google sign-in). Unlinks the current Google account."""
    email = request.form.get("email", "")[:320]
    def set_email(name):
        _users().set_email(name, email)
        log.info("e-mail address of dashboard user %r changed by admin from %s", name.strip().lower(), request.remote_addr)
    return _user_action(set_email, "E-mail address of {name} set; they are signed out and link on their next Google sign-in.")


@bp.post("/users/disable")
@require_admin
def disable_user():
    def disable(name):
        _keep_an_admin(name)
        _users().set_disabled(name, True)
    return _user_action(disable, "User {name} disabled and signed out.")


@bp.post("/users/promote")
@require_admin
def promote_user():
    def promote(name):
        _users().set_admin(name, True)
        log.info("dashboard user %r promoted to admin from %s", name.strip().lower(), request.remote_addr)
    return _user_action(promote, "User {name} is now an admin (at once, also in their current sign-in).")


@bp.post("/users/demote")
@require_admin
def demote_user():
    def demote(name):
        _keep_an_admin(name)
        _users().set_admin(name, False)
        log.info("dashboard user %r demoted from admin from %s", name.strip().lower(), request.remote_addr)
    return _user_action(demote, "User {name} is no longer an admin.")


@bp.post("/users/enable")
@require_admin
def enable_user():
    return _user_action(lambda name: _users().set_disabled(name, False), "User {name} enabled.")


@bp.post("/users/delete")
@require_admin
def delete_user():
    if request.form.get("confirm") != "yes":
        flash("Tick the box to confirm deleting the user.", "error")
        return redirect(url_for("admin.index") + "#users")
    def delete(name):
        _keep_an_admin(name)
        _users().delete(name)
    return _user_action(delete, "User {name} deleted and signed out.")


# ---- error log ------------------------------------------------------------------------
RECENT_ERRORS = 50


@bp.get("/errors")
@require_admin
def errors():
    """Users get only a reference with an error; this shows what it stands for
    (?ref= accepts anything that contains one, e.g. "(reference 3f9c0a1b2d)")."""
    a = services().settings.app
    query = request.args.get("ref", "")[:200].strip()
    ref = errorlog.parse_ref(query) if query else None
    found = errorlog.find(a.error_log_file, a.error_log_backups, ref) if ref else None
    latest = [] if query else errorlog.recent(a.error_log_file, a.error_log_backups, RECENT_ERRORS)
    return render_template("admin/errors.html", enabled=bool(a.error_log_file), query=query, ref=ref,
                           found=found, latest=latest, limit=RECENT_ERRORS)


# ---- login lockouts ----------------------------------------------------------------------
def _lockout_label(key: str) -> str:
    if key == "account:" + ADMIN_PASSWORD_ACCOUNT:
        return "Admin password (all clients together)"
    if key.startswith("account:"):
        return "Account " + key[len("account:"):]
    return key   # a client key: IPv4 address or IPv6 /64


def _lockouts() -> list[dict]:
    return [{**e, "label": _lockout_label(e["key"])} for e in services().admin_limiter.snapshot()]


@bp.post("/lockouts/unlock")
@require_admin
def unlock():
    key = request.form.get("key", "")[:200]
    if key not in {e["key"] for e in services().admin_limiter.snapshot()}:
        flash("That lockout has already ended.", "ok")
    else:
        services().admin_limiter.reset(key)
        log.info("admin-password lockout for %s lifted by admin from %s", _lockout_label(key), client_addr())
        flash(f"Lifted: {_lockout_label(key)}.", "ok")
    return redirect(url_for("admin.index") + "#lockouts")


@bp.post("/lockouts/clear")
@require_admin
def clear_lockouts():
    services().admin_limiter.clear()
    log.info("all admin-password lockouts lifted by admin from %s", client_addr())
    flash("All lockouts lifted.", "ok")
    return redirect(url_for("admin.index") + "#lockouts")


REFRESH_SHAPE = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Why a pasted refresh token was refused, by what it looks like (auth.token_kind).
_REFRESH_PROBLEMS = {
    "access": "The refresh token field holds the access token (it starts with a number and \"_\"). "
              f"Paste the refresh_token value instead: it looks like {REFRESH_SHAPE}.",
    "code": "That is a login code (or the login link), not a refresh token. "
            "Use \"Log in with an AH login code\" below.",
    "jwt": f"That is not an AH refresh token. An AH refresh token looks like {REFRESH_SHAPE}.",
    "unknown": f"That does not look like an AH refresh token ({REFRESH_SHAPE}). "
               "AH would refuse it (\"Incorrect request\"). Check that you copied the refresh_token value.",
}


def _pasted_tokens(access: str, refresh: str) -> tuple[str, str]:
    """Allow pasting AH's whole JSON answer ({"access_token": ..., "refresh_token": ...})."""
    for raw in (refresh, access):
        if raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):   # not JSON, or absurdly nested
                break
            if isinstance(data, dict):
                return str(data.get("access_token") or ""), str(data.get("refresh_token") or "")
    return access, refresh


@bp.post("/tokens")
@require_admin
def set_tokens():
    access, refresh = _pasted_tokens(request.form.get("access_token", ""), request.form.get("refresh_token", ""))
    access, refresh = clean_pasted(access), clean_pasted(refresh)
    if not refresh or not _TOKEN_RE.fullmatch(refresh) or (access and not _TOKEN_RE.fullmatch(access)):
        flash("Enter a refresh token (and optionally an access token) without spaces.", "error")
        return redirect(url_for("admin.index"))
    kind = token_kind(refresh)
    if kind != "refresh":
        flash(_REFRESH_PROBLEMS.get(kind, _REFRESH_PROBLEMS["unknown"]), "error")
        return redirect(url_for("admin.index"))
    if access and token_kind(access) == "refresh":
        flash("The access token field holds a refresh token: are the two fields swapped?", "error")
        return redirect(url_for("admin.index"))
    _tokens().set_tokens(access or None, refresh)
    log.info("AH tokens replaced by admin from %s", client_addr())
    flash("AH login saved. It is used from now on and survives restarts.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/login-code")
@require_admin
def login_with_code():
    """Log the server in to AH with a login code: the admin logs in on AH's
    login page and pastes the code (or the whole appie://login-exit?code=...
    link); the server exchanges it for tokens (no tokens are handled by hand)."""
    code = login_code(request.form.get("code", ""))
    if not code:
        flash("Paste the code from the end of the AH login (the part after \"code=\"), or the whole "
              "appie://login-exit?code=... link.", "error")
        return redirect(url_for("admin.index"))
    try:
        _tokens().login_with_code(code)
    except AHAuthError as exc:
        ref = log_error(log, "AH login with a code failed: %s", exc)
        if exc.status in (400, 401):
            flash("AH did not accept the login code. Codes work only once and only for a short time: "
                  f"log in again and paste the new code straight away (reference {ref}).", "error")
        else:
            flash(f"Could not reach AH to log in (reference {ref}). Try again later.", "error")
        return redirect(url_for("admin.index"))
    log.info("AH login replaced with a login code by admin from %s", client_addr())
    flash("Logged in to AH. The new login is used from now on and survives restarts.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/renew")
@require_admin
def renew():
    try:
        _tokens().renew_now()
    except AHAuthError as exc:
        ref = log_error(log, "admin-triggered renewal failed: %s", exc)
        if exc.status == 401:
            flash("AH says the refresh token has expired or was already used. AH replaces it on every "
                  "renewal, so this also happens when another copy of the app uses the same AH login. "
                  f"Log in again below (reference {ref}).", "error")
        elif exc.status == 400:
            flash("AH did not accept the stored refresh token (\"Incorrect request\"): it is probably not a "
                  "refresh token, e.g. the access token or a login code was saved in its place. "
                  f"Log in again below (reference {ref}).", "error")
        else:
            flash(f"Renewal failed (reference {ref}). Set new tokens.", "error")
    else:
        flash("Access token renewed.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/logout")
@require_admin
def logout():
    sessions.end()   # ends the sign-in on the server: a copied cookie stops working too
    return redirect(url_for("admin.login"))
