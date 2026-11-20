"""Admin panel (/admin): set the server's AH tokens, renew, see login status.

Disabled (404) unless ADMIN_PASSWORD_HASH is configured or an enabled user
has admin rights (auth.admin.enabled). Create the hash with:

    python -m ah_dashboard.admin
"""

from flask import Blueprint

bp = Blueprint("admin", __name__)

from . import routes  # noqa: E402,F401  (registers the routes on bp)
