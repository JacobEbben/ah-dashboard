"""AH's endpoints, as methods: the only place that knows paths, GraphQL
documents, operation names and request parameters.

Services call the gateway and get AH's raw JSON back; ``ah.mapping`` turns it
into domain models. The gateway sits on an :class:`AHApi`: normally the
``AHSession`` (the server's login, token renewal, concurrency cap), in tests
a fake that answers per operation / path.

Adding an AH feature: a document in ``queries.py`` (or a REST path here), a
method here, a mapping function in ``mapping.py``, a service using them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from . import queries

BONUS_METADATA_PATH = "/mobile-services/bonuspage/v3/metadata"
BONUS_SECTION_PATH = "/mobile-services/bonuspage/v2/section"


class AHApi(Protocol):
    """What the gateway needs: AH requests with the server's login."""

    @property
    def configured(self) -> bool: ...

    def query(self, document: str, variables: dict | None = None, operation: str | None = None) -> dict: ...

    def get(self, path: str, params: dict | None = None) -> Any: ...


class AHGateway:
    def __init__(self, api: AHApi, application: str):
        self._api = api
        self._application = application     # x-application / "application" parameter (AHWEBSHOP)

    @property
    def configured(self) -> bool:
        """The server has an AH login (fakes without the attribute count as yes)."""
        return bool(getattr(self._api, "configured", True))

    # ---- bargains ("Laatste Kans Koopjes") -----------------------------------------------
    def bargain_items(self, store_id: int) -> Any:
        """The raw ``bargainItems`` list of one store."""
        data = self._api.query(queries.BARGAIN_ITEMS, {"storeId": str(store_id)}, "BargainItems")
        return data.get("bargainItems") if isinstance(data, dict) else None

    # ---- stores -----------------------------------------------------------------------------
    def search_stores(self, postcode: str) -> Any:
        """The raw store list nearest to a (normalized) postal code."""
        data = self._api.query(queries.STORES_SEARCH, {"filter": {"postalCode": postcode}}, "StoresSearch")
        return ((data.get("storesSearch") or {}).get("result")) if isinstance(data, dict) else None

    # ---- the national Bonus -------------------------------------------------------------------
    def bonus_metadata(self) -> Any:
        """Periods and category tabs of the bonus pages."""
        return self._api.get(BONUS_METADATA_PATH)

    def bonus_section(self, category: str, day: date) -> Any:
        """One national bonus category on ``day`` (products and bonus groups)."""
        return self._api.get(BONUS_SECTION_PATH, {
            "application": self._application, "date": day.isoformat(),
            "promotionType": "NATIONAL", "category": category})

    def bonus_group_products(self, segment: str, period_start: str, period_end: str) -> Any:
        """The products of one bonus group deal (GraphQL ``bonusPromotions``)."""
        return self._api.query(queries.GROUP_PRODUCTS_QUERY, {
            "id": segment, "periodStart": period_start, "periodEnd": period_end,
            "filterUnavailableProducts": True, "forcePromotionVisibility": True,
            "showAllPromotionSegments": True,
        }, queries.GROUP_PRODUCTS_OPERATION)
