"""Error types and handlers.

API errors are returned as ``{"error": {"code": ..., "message": ..., "ref": ...}}``.
The ``code`` is stable and machine-readable; the frontend translates known
codes into the UI language. Messages are always generic: details of upstream
or internal failures are written to the error log under the ``ref`` ID.

Failures of AH are mapped here, once, for every endpoint: routes simply let
them propagate.

- ``AHAuthError`` (the server's AH login itself is gone; an admin must log in
  again) -> 503 ``ah_auth_failed``;
- any other ``AHError`` (AH down, unexpected answer) -> 502 ``upstream_error``.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .ah.auth import AHAuthError
from .ah.client import AHError
from .logging_setup import log_error

log = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, ref: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.ref = ref


def error_response(code: str, message: str, status: int, ref: str | None = None):
    body = {"code": code, "message": message}
    if ref:
        body["ref"] = ref
    return jsonify({"error": body}), status


def register(app: Flask) -> None:
    @app.errorhandler(APIError)
    def _api_error(exc: APIError):
        return error_response(exc.code, exc.message, exc.status, exc.ref)

    @app.errorhandler(AHError)
    def _ah_error(exc: AHError):
        # AHAuthError is a subclass: Flask picks this handler for both.
        if isinstance(exc, AHAuthError):
            ref = log_error(log, "%s %s: AH login failed: %s", request.method, request.path, exc)
            return error_response("ah_auth_failed", "The server's AH login does not work anymore.", 503, ref)
        ref = log_error(log, "%s %s: AH request failed: %s", request.method, request.path, exc)
        return error_response("upstream_error", "AH could not be reached. Please try again later.", 502, ref)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            code = (exc.name or "error").lower().replace(" ", "_")
            # Werkzeug's standard short name only ("Not Found", ...), no details.
            return error_response(code, exc.name or "Error", exc.code or 500)
        return exc

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        ref = log_error(log, "unhandled error on %s %s", request.method, request.path, exc_info=True)
        if request.path.startswith("/api/"):
            return error_response("internal_error", "Unexpected server error.", 500, ref)
        return f"Internal Server Error (ref {ref})", 500
