"""Small, dependency-free client for the AH API (GraphQL plus a few REST GETs)."""

from __future__ import annotations

import gzip
import http.client
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


class AHError(Exception):
    """Base class for errors talking to AH."""


class AHTransportError(AHError):
    """Network failure or a non-GraphQL HTTP error from AH."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AHGraphQLError(AHError):
    """AH answered, but with GraphQL ``errors`` (validation, auth, ...).

    ``str(exc)`` includes the full error objects (``extensions`` such as the
    error code and failing service, ``path``), because AH's gateway often
    replaces the message with "Subgraph errors redacted" and the extensions
    are the only clue. This text goes to the server error log only; users
    get a generic message.
    """

    def __init__(self, errors: list[Any], extensions: Any = None):
        self.errors = errors
        self.extensions = extensions
        text = error_details(errors)
        if extensions:
            try:
                text += " | extensions=" + json.dumps(extensions, ensure_ascii=True, separators=(",", ":"))[:1500]
            except (TypeError, ValueError):
                text += " | extensions=" + repr(extensions)[:1500]
        super().__init__(text)


def error_messages(errors: Any) -> str:
    """Readable text from a GraphQL ``errors`` array (messages only)."""
    try:
        return "; ".join(str(e.get("message", e)) for e in errors)[:500]
    except Exception:  # pragma: no cover - defensive
        return json.dumps(errors)[:500]


def error_details(errors: Any) -> str:
    """Messages plus compact JSON of the full error objects, for the log."""
    try:
        detail = json.dumps(errors, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError):
        detail = repr(errors)
    return f"{error_messages(errors)} | {detail[:1500]}"


def decode_body(raw: bytes, headers: Any = None) -> str:
    """Decode a response body. AH may gzip replies (notably errors) unasked,
    and urllib does not decompress, so handle that here."""
    encoding = ((headers.get("Content-Encoding") if headers else "") or "").lower()
    if "gzip" in encoding or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", "replace")


class AHClient:
    """One client per bearer token. Thread-safe (no shared mutable state)."""

    def __init__(self, token: str, url: str, timeout: int = 20, user_agent: str | None = None,
                 opener: urllib.request.OpenerDirector | None = None,
                 rest_url: str = "https://api.ah.nl", rest_headers: dict[str, str] | None = None):
        self._token = token
        self._url = url
        self._timeout = timeout
        self._user_agent = user_agent
        self._opener = opener  # None = the default direct opener (_OPENER)
        self._rest_url = rest_url.rstrip("/")
        self._rest_headers = dict(rest_headers or {})

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET a JSON document from AH's REST API (e.g. the bonus pages).

        ``path`` is a fixed path starting with "/" (never user input); query
        parameters are URL-encoded here. Raises :class:`AHTransportError` for
        network/HTTP failures and non-JSON answers.
        """
        if not path.startswith("/") or "//" in path or "?" in path:
            raise ValueError("path must be a plain absolute path")
        url = self._rest_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
            **self._rest_headers,
        }
        if self._user_agent:
            headers["User-Agent"] = self._user_agent
        request = urllib.request.Request(url, method="GET", headers=headers)
        label = path
        try:
            with (self._opener or _OPENER).open(request, timeout=self._timeout) as resp:
                body = decode_body(resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            body = decode_body(exc.read(), exc.headers)
            log.warning("AH HTTP %s for %s", exc.code, label)
            raise AHTransportError(f"HTTP {exc.code} for {label}: {body[:300]}", exc.code) from None
        except urllib.error.URLError as exc:
            raise AHTransportError(f"cannot reach AH: {exc.reason}") from None
        except TimeoutError:
            raise AHTransportError("AH did not answer in time") from None
        except (OSError, http.client.HTTPException) as exc:
            raise AHTransportError(f"connection to AH failed: {type(exc).__name__}: {exc}") from None
        parsed = _try_json(body)
        if parsed is None:
            raise AHTransportError(f"AH returned a non-JSON response for {label}")
        return parsed

    def query(self, document: str, variables: dict | None = None,
              operation: str | None = None) -> dict:
        """Run a GraphQL document and return its ``data``.

        Raises :class:`AHGraphQLError` when AH returns ``errors`` and
        :class:`AHTransportError` for network/HTTP failures.
        """
        payload: dict[str, Any] = {"query": document, "variables": variables or {}}
        if operation:
            payload["operationName"] = operation
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
        }
        # No User-Agent by default: urllib then sends "Python-urllib/3.x", the
        # value that is known to work with AH's API. A custom value (e.g. the
        # earlier "ah-bargains/1.0") made AH's backend fail every bargains
        # query with "Subgraph errors redacted".
        if self._user_agent:
            headers["User-Agent"] = self._user_agent
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with (self._opener or _OPENER).open(request, timeout=self._timeout) as resp:
                body = decode_body(resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            body = decode_body(exc.read(), exc.headers)
            # GraphQL validation errors arrive as HTTP 400 with {"errors": [...]}.
            parsed = _try_json(body)
            if isinstance(parsed, dict) and parsed.get("errors"):
                raise AHGraphQLError(parsed["errors"], parsed.get("extensions")) from None
            log.warning("AH HTTP %s for %s", exc.code, operation or "query")
            raise AHTransportError(f"HTTP {exc.code}: {body[:300]}", exc.code) from None
        except urllib.error.URLError as exc:
            raise AHTransportError(f"cannot reach AH: {exc.reason}") from None
        except TimeoutError:
            raise AHTransportError("AH did not answer in time") from None
        except (OSError, http.client.HTTPException) as exc:
            # urllib only wraps errors raised while *sending*. Failures while
            # reading the response (connection reset, RemoteDisconnected,
            # BadStatusLine, IncompleteRead, ssl.SSLError) arrive raw; map them
            # so one broken store connection doesn't fail the whole request.
            raise AHTransportError(f"connection to AH failed: {type(exc).__name__}: {exc}") from None

        parsed = _try_json(body)
        if not isinstance(parsed, dict):
            raise AHTransportError("AH returned a non-JSON response")
        if parsed.get("errors"):
            raise AHGraphQLError(parsed["errors"], parsed.get("extensions"))
        return parsed.get("data") or {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would copy the Authorization header
    (the user's AH token) to whatever host the redirect points at."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError with the 3xx status


def build_opener() -> urllib.request.OpenerDirector:
    """Opener with only http/https support (no file://, ftp://, data:) and no
    redirect following. Proxy settings from the environment (HTTPS_PROXY etc.)
    are deliberately ignored: the AH tokens never pass through a proxy."""
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        _NoRedirect(),
    ]
    opener = urllib.request.OpenerDirector()
    for handler in handlers:
        opener.add_handler(handler)
    return opener


_OPENER = build_opener()


def _try_json(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None
