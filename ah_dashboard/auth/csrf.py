"""The per-session CSRF token.

Every state-changing request needs it: HTML forms send it as the ``csrf``
field, JSON API calls in the ``X-CSRF-Token`` header. A new token is made at
every sign-in (``sessions.begin``).
"""

from __future__ import annotations

import secrets

from flask import abort, request, session

from ..errors import APIError
from .compare import same_secret

SESSION_KEY = "csrf"


def new_token() -> str:
    token = session[SESSION_KEY] = secrets.token_urlsafe(32)
    return token


def csrf_token() -> str:
    """This session's token (made on first use)."""
    return session.get(SESSION_KEY) or new_token()


def _matches(sent: str) -> bool:
    return same_secret(sent, session.get(SESSION_KEY, ""))


def check_form() -> None:
    """For HTML form posts: 400 unless the form's ``csrf`` field matches."""
    if not _matches(request.form.get("csrf", "")):
        abort(400, description="Invalid or missing form token. Reload the page and try again.")


def check_header() -> None:
    """For JSON API calls: APIError 403 unless ``X-CSRF-Token`` matches."""
    if not _matches(request.headers.get("X-CSRF-Token", "")):
        raise APIError("csrf_invalid", "Missing or invalid CSRF token.", 403)
