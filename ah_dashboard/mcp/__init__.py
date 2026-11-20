"""MCP endpoint blueprint (Model Context Protocol), mounted at ``/mcp``."""

from flask import Blueprint

bp = Blueprint("mcp", __name__)

from . import routes  # noqa: E402,F401  (registers the routes on bp)
