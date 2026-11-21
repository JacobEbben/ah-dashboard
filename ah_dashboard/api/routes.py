"""API endpoints. Open to everyone (the server uses its own AH login), except
/api/prefs (signed-in users) and /api/bargains/force (admin).

GET  /api/stores?postcode=1234AB   stores near a postal code          {stores, postcode}
POST /api/bargains                 bargains for {"stores": [{id, name}]} {items, storeErrors}
POST /api/bargains/force           same body; reloads from AH now (admin only)
GET  /api/bonus                    this week's national Bonus          {period, categories, items, errors}
GET  /api/session                  {"admin": bool, "csrf": "..."} for the UI
GET  /api/prefs                    the signed-in user's synced settings
PUT  /api/prefs                    replace them ({"prefs": {...}}, X-CSRF-Token)

``items`` are offers in one shape for both views (``domain.models.Offer``).
Endpoints that can make the server call AH need an AH login
(:func:`ah_required`), are rate limited per client (``ratelimit``) and are
cached. AH failures become generic errors in ``errors.register``.
"""

from __future__ import annotations

import functools

from flask import jsonify, request

from ..auth.access import require_user_api
from ..auth.admin import enabled as admin_enabled
from ..auth.admin import logged_in, require_admin_api, touch
from ..auth.csrf import csrf_token
from ..auth.users import UserError
from ..container import services
from ..errors import APIError
from ..ratelimit import client_key, rate_limited
from ..services import stores as stores_service
from . import bp
from .validation import json_body, store_list


def ah_required(view):
    """503 ``ah_not_configured`` while the server has no AH login."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not services().gateway.configured:
            raise APIError("ah_not_configured", "The server has no AH login configured.", 503)
        return view(*args, **kwargs)
    return wrapper


@bp.get("/stores")
@rate_limited("stores")
@ah_required
def search_stores():
    s = services()
    postcode = stores_service.normalize_postcode(request.args.get("postcode", ""))
    if not postcode:
        raise APIError(
            "postcode_only",
            "AH's store search only works by postal code. Enter a postal code "
            "(e.g. 1234AB or 1234) to find the stores near it.",
            400,
        )
    stores = s.find_stores(postcode)
    return jsonify({"stores": [st.to_json() for st in stores], "postcode": postcode})


@bp.post("/bargains")
@rate_limited("bargains")
@ah_required
def bargains():
    """Bargains from the cache (reloaded from AH only when older than the TTL)."""
    return _bargains_response(force=False)


@bp.post("/bargains/force")
@require_admin_api
@ah_required
def bargains_force():
    """Reload the requested stores from AH now. Admin only: every call uses
    the server's AH login and bypasses the cache."""
    return _bargains_response(force=True)


def _bargains_response(force: bool):
    s = services()
    stores = store_list(json_body(request), s.settings.app.max_stores)
    # Loads from AH are budgeted per client; the admin's force refresh is not.
    return jsonify(s.bargains(stores, client=None if force else client_key()).to_json())


@bp.get("/bonus")
@rate_limited("bonus")
@ah_required
def bonus():
    """This week's national Bonus. One shared copy for everyone, kept until
    the bonus week ends (services.bonus.WeekCache)."""
    return jsonify(services().bonus_week().to_json())


@bp.post("/bonus/force")
@require_admin_api
@ah_required
def bonus_force():
    """Reload this week's Bonus from AH now. Admin only: ~160 AH requests with
    the server's login (paced, so it takes a minute or two). Not rate limited
    per client, like the bargains' force refresh."""
    return jsonify(services().refresh_bonus_week().to_json())


@bp.get("/prefs")
@require_user_api
def get_prefs(user):
    """The signed-in user's synced settings (localStorage keys and values)."""
    return jsonify(services().users.get_prefs(user))


@bp.put("/prefs")
@require_user_api
def put_prefs(user):
    """Replace the signed-in user's synced settings. Limited per user
    (PREFS_WRITES_PER_MINUTE): the browser uploads at most every ~1.5 s, and a
    refused upload is simply sent again with the next change."""
    s = services()
    if not s.rate_limiter.allow("prefs", "user:" + user, s.settings.limits.prefs_writes_per_minute, 60):
        raise APIError("rate_limited", "Too many requests. Please wait a moment and try again.", 429)
    body = json_body(request)
    prefs = body.get("prefs") if isinstance(body, dict) else None
    try:
        updated = services().users.set_prefs(user, prefs)
    except UserError as exc:
        raise APIError(exc.code, str(exc), 413 if exc.code == "prefs_too_large" else 400) from None
    return jsonify({"updatedAt": updated})


@bp.get("/session")
def session_info():
    """Whether the browser has an active admin session, plus the CSRF token
    for admin-only API calls. Same-origin only (no CORS), so other sites
    cannot read the token. Counts as admin activity (the dashboard asks on
    every page load), so an admin using the dashboard stays admin."""
    if admin_enabled() and logged_in():
        touch()
        return jsonify({"admin": True, "csrf": csrf_token()})
    return jsonify({"admin": False})
