"""The MCP server: Model Context Protocol over Streamable HTTP, at ``/mcp``.

AI assistants (Claude, other MCP clients) connect to ``<site>/mcp`` and get
the read-only tools of :mod:`.tools`: store search, bargains and the Bonus.

    POST /mcp     one JSON-RPC 2.0 message; the answer is one JSON response
                  (no event stream: every tool answers at once), a
                  notification or response gets 202 without a body
    GET, DELETE   405: no server-initiated stream and no sessions (stateless)

Methods: ``initialize``, ``ping``, ``tools/list``, ``tools/call``.

Security:

- Anonymous and read-only, like the dashboard's API. It never reads the
  session cookie (no user or admin rights here), so cross-site requests gain
  nothing; browsers sending another site's ``Origin`` are refused anyway
  (403), as the MCP spec asks (DNS rebinding).
- Every message counts against RATE_LIMIT_MCP per client key; every tool
  call also against the limit of its endpoint group and the store-load
  budgets, exactly like the dashboard's requests (``tools.Tool.bucket``).
- Errors: AH failures go to the error log; the assistant gets a generic
  message with the reference. Never upstream or internal details.
- MCP_ENABLED=false: 404.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Response, abort, request

from ..ah.auth import AHAuthError
from ..ah.client import AHError
from ..container import services
from ..logging_setup import log_error
from ..ratelimit import client_addr, client_key
from . import bp
from .tools import TOOLS, ToolError, check_arguments

log = logging.getLogger(__name__)

# Newest first: answered to clients asking for a version we don't know.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "ah-dashboard", "title": "AH Dashboard", "version": "1.0.0"}
INSTRUCTIONS = (
    "Albert Heijn (Dutch supermarket) offers. find_stores gives store ids for a Dutch postal code; "
    "get_bargains lists a store's last-chance bargains (marked down, near best-before date); get_bonus lists "
    "this week's national Bonus deals. Product names and categories are in Dutch; prices are in euros. "
    "Data comes from Albert Heijn and can change during the day; it is not advice."
)

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
RATE_LIMITED = -32000

_TOO_MANY = "Too many requests. Please wait a moment and try again."


def _json(body: dict, status: int = 200) -> Response:
    resp = Response(json.dumps(body, ensure_ascii=False, separators=(",", ":")), status=status,
                    mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _error(msg_id: Any, code: int, message: str, status: int = 200, data: dict | None = None) -> Response:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return _json({"jsonrpc": "2.0", "id": msg_id, "error": err}, status)


def _result(msg_id: Any, result: dict) -> Response:
    return _json({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _enabled() -> None:
    if not services().settings.app.mcp_enabled:
        abort(404)


def _origin_allowed() -> bool:
    """No Origin (not a browser) or our own site's."""
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    own = {request.host_url.rstrip("/")}
    base = services().settings.auth.public_base_url
    if base:
        own.add(base)
    return origin.rstrip("/") in own


@bp.route("", methods=["GET", "DELETE"])
def not_offered():
    _enabled()
    resp = _error(None, INVALID_REQUEST, "Use POST: this server has no event stream and no sessions.", 405)
    resp.headers["Allow"] = "POST"
    return resp


@bp.post("")
def endpoint():
    _enabled()
    s = services()
    if not _origin_allowed():
        log.warning("MCP request from another site's page refused (%s)", client_addr())
        return _error(None, INVALID_REQUEST, "Origin not allowed.", 403)
    limit = s.settings.limits.endpoint("mcp")
    if not s.rate_limiter.allow("mcp", client_key(), limit.rate, limit.per):
        return _error(None, RATE_LIMITED, _TOO_MANY, 429)
    version = request.headers.get("MCP-Protocol-Version")
    if version is not None and version not in PROTOCOL_VERSIONS:
        return _error(None, INVALID_REQUEST, "Unsupported MCP-Protocol-Version.", 400)

    try:
        msg = json.loads(request.get_data(cache=False) or b"null")
    except (ValueError, RecursionError):
        return _error(None, PARSE_ERROR, "Parse error.", 400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Send one JSON-RPC 2.0 message (no batches).", 400)
    if "method" not in msg:
        return Response(status=202)            # a response to a request of ours: we send none
    method, msg_id = msg["method"], msg.get("id")
    if "id" not in msg:
        return Response(status=202)            # a notification (e.g. notifications/initialized)
    if not isinstance(method, str) or not (isinstance(msg_id, (str, int)) and not isinstance(msg_id, bool)):
        return _error(None, INVALID_REQUEST, "Invalid request.", 400)
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "params must be an object.")

    if method == "initialize":
        return _result(msg_id, _initialize(params))
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [t.describe() for t in TOOLS.values()]})
    if method == "tools/call":
        return _call(msg_id, params)
    return _error(msg_id, METHOD_NOT_FOUND, "Method not found.")


def _initialize(params: dict) -> dict:
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def _tool_result(data: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "structuredContent": data, "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _call(msg_id: Any, params: dict) -> Response:
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return _error(msg_id, INVALID_PARAMS, "Unknown tool.")
    s = services()
    try:
        args = check_arguments(tool, params.get("arguments"))
        if not s.gateway.configured:
            raise ToolError("The server has no AH login configured.")
        limit = s.settings.limits.endpoint(tool.bucket)
        if not s.rate_limiter.allow(tool.bucket, client_key(), limit.rate, limit.per):
            raise ToolError(_TOO_MANY)
        return _result(msg_id, _tool_result(tool.run(s, args, client_key())))
    except ToolError as exc:
        return _result(msg_id, _tool_error(str(exc)))
    except AHAuthError as exc:
        ref = log_error(log, "MCP %s: AH login failed: %s", tool.name, exc)
        return _result(msg_id, _tool_error(f"The server's AH login does not work right now (ref {ref})."))
    except AHError as exc:
        ref = log_error(log, "MCP %s: AH request failed: %s", tool.name, exc)
        return _result(msg_id, _tool_error(f"AH could not be reached. Please try again later (ref {ref})."))
    except Exception:
        ref = log_error(log, "MCP %s: unexpected error", tool.name, exc_info=True)
        return _error(msg_id, INTERNAL_ERROR, "Internal error.", data={"ref": ref})
