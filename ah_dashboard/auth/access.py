"""Optional user sign-in.

The dashboard is open to everyone. Signing in only adds settings sync across
devices (``/api/prefs``). A user session stores the username, the user's
``session_version`` and the sign-in time next to the session id; it ends when
the version changes (user disabled or deleted, e-mail reset by an admin), after
``USER_SESSION_DAYS``, or at sign-out (which also removes the session id on
the server, so a copy of the cookie stops working too).
"""

from __future__ import annotations

import functools
import time

from flask import request, session

from ..container import services
from ..errors import APIError
from . import csrf, sessions

SESSION_KEY = "user"


def login_user(record: dict) -> None:
    """Start a fresh session for this user (no session fixation)."""
    sessions.begin(record["username"])
    session[SESSION_KEY] = {"name": record["username"], "ver": record["session_version"], "at": time.time()}


def logout() -> None:
    sessions.end()


def current_user() -> str | None:
    """The signed-in user's name, or None."""
    data = session.get(SESSION_KEY)
    if not isinstance(data, dict):
        return None
    name, ver, at = data.get("name"), data.get("ver"), data.get("at")
    if not isinstance(name, str) or not isinstance(ver, str) or not isinstance(at, (int, float)):
        return None
    s = services()
    if time.time() - at > s.settings.auth.user_session_days * 86400:
        return None
    rec = s.users.get(name)
    if rec is None or rec["disabled"] or rec["session_version"] != ver:
        return None
    if not sessions.listed(rec["username"]):
        return None                 # signed out (maybe in another copy of this cookie)
    return rec["username"]


def require_user_api(view):
    """API endpoint for signed-in users (passed as the first argument); state
    changes also need the session's CSRF token in X-CSRF-Token."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            raise APIError("login_required", "Please sign in.", 401)
        if request.method not in ("GET", "HEAD"):
            csrf.check_header()
        return view(user, *args, **kwargs)
    return wrapper
