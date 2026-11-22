"""The privacy statement, and a signed-in user's own data: download and delete.

GET  /privacy          the statement; signed in: what is stored for you, plus
                       the download and delete options
GET  /privacy/export   signed in: your data as a JSON file
POST /privacy/delete   signed in: delete your account and everything stored
                       for it (type your e-mail address + confirmation box + CSRF token)

Deleting removes the users row; the database cascades to the user's synced
settings and sign-ins. The browser's own copy (localStorage) is not the
server's to delete: the page says how to clear it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from flask import Response, redirect, render_template, request, url_for

from ..auth import access, csrf
from ..auth.admin import is_last_admin
from ..auth.users import UserError
from ..container import services
from . import bp

log = logging.getLogger(__name__)


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if ts else None


def _summary(name: str) -> dict:
    """What the page shows a signed-in user about their stored data."""
    s = services()
    rec = s.users.get(name) or {}
    prefs = s.users.get_prefs(name)
    return {
        "username": name,
        "email": rec.get("email"),
        "linked": bool(rec.get("login_sub")),
        "created_at": rec.get("created_at"),
        "last_login": rec.get("last_login"),
        "is_admin": bool(rec.get("is_admin")),
        "settings": sorted(prefs["prefs"]),
        "settings_bytes": len(json.dumps(prefs["prefs"]).encode("utf-8")),
        "settings_updated": prefs["updatedAt"],
        "sign_ins": len(s.sessions.started(name)),
        "confirm_word": confirm_word(rec) if rec else name,
    }


# Errors shown on the page (data-i18n keys).
PAGE_ERRORS = frozenset({"deleteConfirmMissing", "deleteConfirmMismatch", "deleteLastAdmin"})


def confirm_word(rec: dict) -> str:
    """What the user types to confirm deleting: their e-mail address (else their username)."""
    return rec.get("email") or rec["username"]


def _page(error: str | None = None, status: int = 200, deleted: bool = False):
    user = access.current_user()
    if error is None and request.args.get("error") in PAGE_ERRORS:
        error = request.args.get("error")
    return render_template(
        "privacy.html", user=user, me=_summary(user) if user else None,
        csrf=csrf.csrf_token() if user else None, error=error, deleted=deleted,
        signin=services().login is not None,
        contact=services().settings.app.privacy_contact,
        user_session_days=services().settings.auth.user_session_days,
    ), status


@bp.app_template_filter("utc_time")
def _utc_time(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC") if ts else "—"


@bp.get("/privacy")
def privacy():
    return _page(deleted=request.args.get("deleted") == "1")


@bp.get("/privacy/export")
def privacy_export():
    """The signed-in user's data as a JSON download. Never the password hash
    or session ids (secrets, not personal data you need)."""
    name = access.current_user()
    if name is None:
        return redirect(url_for("web.login"))
    s = services()
    rec = s.users.get(name) or {}
    prefs = s.users.get_prefs(name)

    def readable(value: str):
        # Settings are stored as text, most of it JSON: export it as JSON where it is.
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    data = {
        "exported_at": _iso(datetime.now(UTC).timestamp()),
        "account": {
            "username": name,
            "email": rec.get("email"),
            "login_service_user_id": rec.get("login_sub"),
            "created_at": _iso(rec.get("created_at")),
            "last_sign_in": _iso(rec.get("last_login")),
            "admin": bool(rec.get("is_admin")),
            "disabled": bool(rec.get("disabled")),
        },
        "synced_settings": {
            "updated_at": _iso(prefs["updatedAt"]),
            "values": {key: readable(value) for key, value in sorted(prefs["prefs"].items())},
        },
        "active_sign_ins": [{"started_at": _iso(ts)} for ts in s.sessions.started(name)],
        "not_included": [
            "the random ids of your sign-ins (they work like keys)",
            "no password is stored here (it stays at the login service)",
            "settings kept only in your browser and not synced",
        ],
    }
    body = json.dumps(data, ensure_ascii=False, indent=2)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="ah-dashboard-{name}-{stamp}.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/privacy/delete")
def privacy_delete():
    """Delete the signed-in user's account and all data stored for it."""
    csrf.check_form()
    name = access.current_user()
    if name is None:
        return redirect(url_for("web.login"))
    if request.form.get("confirm") != "yes":
        return _page("deleteConfirmMissing", 400)
    rec = services().users.get(name)
    typed = request.form.get("confirm_text", "").strip().lower()
    if rec is None or typed != confirm_word(rec).lower():
        return _page("deleteConfirmMismatch", 400)
    if is_last_admin(name):
        return _page("deleteLastAdmin", 409)
    try:
        services().users.delete(name)   # cascades: synced settings, sign-ins
    except UserError:
        pass                            # already gone
    access.logout()                     # clears this browser's cookie
    log.info("a user deleted their own account")   # no name: the account is gone
    return redirect(url_for("web.privacy", deleted="1"))
