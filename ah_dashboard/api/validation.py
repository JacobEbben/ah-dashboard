"""Request parsing and validation for the API."""

from __future__ import annotations

import re

from flask import Request

from ..domain.models import StoreRef
from ..errors import APIError

STORE_ID_RE = re.compile(r"[0-9]{1,10}")


def json_body(request: Request) -> object:
    """Parsed JSON body, or None. Malformed or absurdly nested JSON is a 400,
    not a server error (``get_json(silent=True)`` only swallows ValueError)."""
    try:
        return request.get_json(silent=True)
    except RecursionError:
        raise APIError("invalid_json", "Request body is not valid JSON.", 400) from None


def store_list(payload: object, max_stores: int) -> list[StoreRef]:
    """Validate ``{"stores": [{"id": 1234, "name": "My store"}, ...]}``."""
    if not isinstance(payload, dict) or not isinstance(payload.get("stores"), list):
        raise APIError("stores_required", 'Body must be JSON with a "stores" list.', 400)
    raw = payload["stores"]
    if not raw:
        raise APIError("stores_required", "Add at least one store.", 400)
    if len(raw) > max_stores:
        raise APIError("too_many_stores", f"At most {max_stores} stores per request.", 400)

    stores: list[StoreRef] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise APIError("store_invalid", "Each store must be an object.", 400)
        sid = str(entry.get("id", "")).strip()
        # ASCII digits only: str.isdigit() also accepts e.g. "²", which int() rejects.
        if not STORE_ID_RE.fullmatch(sid):
            raise APIError("store_invalid", "Invalid store id.", 400)
        if int(sid) in seen:
            continue
        seen.add(int(sid))
        name = str(entry.get("name") or sid).strip()[:60] or sid
        stores.append(StoreRef(id=int(sid), name=name))
    return stores
