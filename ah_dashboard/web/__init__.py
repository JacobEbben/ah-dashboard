"""HTML pages and health check."""

from flask import Blueprint

bp = Blueprint("web", __name__)

from . import login, privacy, routes, terms  # noqa: E402,F401
