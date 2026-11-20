"""JSON API blueprint, mounted at ``/api``."""

from flask import Blueprint

bp = Blueprint("api", __name__)

from . import routes  # noqa: E402,F401  (registers the routes on bp)
