"""The dashboard page, sign-in / sign-out, and the health check."""

from __future__ import annotations

import logging

from flask import current_app, redirect, render_template, request, send_from_directory, url_for
from flask.typing import ResponseReturnValue

from ..auth import access, admin, csrf
from ..auth.users import SYNC_KEYS
from ..container import services
from ..headers import image_host_list
from . import bp

log = logging.getLogger(__name__)


@bp.get("/")
def index():
    # Server-side settings the frontend needs, embedded as JSON in the page.
    s = services()
    user = access.current_user()
    client_config = {
        "maxStores": s.settings.app.max_stores,
        # The settings that follow a signed-in user (the server's list is the only one).
        "syncKeys": sorted(SYNC_KEYS),
        # Signed in: the page syncs settings with /api/prefs (needs the CSRF token).
        "user": user,
        "csrf": csrf.csrf_token() if user else None,
        # Hosts product images may come from (the CSP's img-src): the cart drops others.
        "imageHosts": image_host_list(s.settings.app.csp_image_hosts),
    }
    # "Admin" button: a signed-in admin user, or an admin-password session.
    admin_link = admin.enabled() and admin.logged_in()
    return render_template("index.html", client_config=client_config,
                           user=user, csrf=client_config["csrf"], admin_link=admin_link,
                           signin=s.login is not None)


@bp.get("/favicon.ico")
def favicon() -> ResponseReturnValue:
    """Browsers and crawlers ask for /favicon.ico on their own (also for pages
    without our <link rel="icon">, like JSON): the 32px PNG, which they all accept."""
    assert current_app.static_folder
    return send_from_directory(current_app.static_folder, "icons/favicon-32.png", mimetype="image/png")


@bp.get("/healthz")
def healthz():
    """Cheap liveness check (no database, never calls AH)."""
    return {"status": "ok"}


# ---- sign in / out --------------------------------------------------------------------
# Sign-in is the login service's (web/login.py): the header's button goes straight there.
# /login only shows a page when there is something to say (a failed sign-in,
# or no sign-in configured on this server); otherwise it sends the browser on to the login service.
LOGIN_ERRORS = frozenset({"loginFailed", "accountDisabled", "signupFull", "loginLocked"})


@bp.get("/login")
def login() -> ResponseReturnValue:
    if access.current_user():
        return redirect(url_for("web.index"))
    error = request.args.get("error")
    enabled = services().login is not None
    if enabled and error not in LOGIN_ERRORS:
        return redirect(url_for("web.login_start"))
    return render_template("login.html", error=error if error in LOGIN_ERRORS else None, signin=enabled)


@bp.post("/logout")
def logout_post():
    csrf.check_form()
    id_token = access.logout()
    # Also sign out at the login service (its own confirmation page, then back here),
    # so the next "sign in" really asks for the password again.
    login = services().login
    if id_token and login is not None:
        from .login import base_url
        url = login.end_session_url(id_token, base_url() + url_for("web.index"))
        if url:
            return redirect(url)
    return redirect(url_for("web.index"))
