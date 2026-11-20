"""The terms of use ("Voorwaarden"): a static page in Dutch and English.

GET /voorwaarden   the terms (Dutch first; static/js/statement.js shows the
                   visitor's language, Dutch without JavaScript)
GET /terms         redirects to /voorwaarden

The text is in templates/terms.html. When it changes, update TERMS_UPDATED
(shown on the page) and both language versions.
"""

from __future__ import annotations

from datetime import date

from flask import redirect, render_template, url_for

from ..container import services
from . import bp

# The date of the current version of the terms (shown on the page).
TERMS_UPDATED = date(2026, 11, 19)

# Spelled out: strftime("%B") would follow the server's locale.
_MONTHS_NL = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
              "september", "oktober", "november", "december")
_MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")


@bp.get("/voorwaarden")
def terms():
    d = TERMS_UPDATED
    return render_template(
        "terms.html",
        contact=services().settings.app.privacy_contact,
        updated_nl=f"{d.day} {_MONTHS_NL[d.month - 1]} {d.year}",
        updated_en=f"{d.day} {_MONTHS_EN[d.month - 1]} {d.year}",
        updated_iso=d.isoformat(),
    )


@bp.get("/terms")
def terms_en():
    return redirect(url_for("web.terms"), code=301)
