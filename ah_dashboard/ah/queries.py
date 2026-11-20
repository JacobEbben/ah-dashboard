"""GraphQL documents sent to api.ah.nl.

Kept in one place; only ``ah.gateway`` sends them. A new AH endpoint means a
document here (GraphQL) or a path there (REST), one gateway method, and a
mapping function in ``ah.mapping``.
"""

# Last-chance bargains ("Laatste Kans Koopjes") for one store.
BARGAIN_ITEMS = """
query BargainItems($storeId: String!) {
  bargainItems(storeId: $storeId) {
    product {
      id
      title
      brand
      salesUnitSize
      imagePack(angles: [HERO]) { medium { url } }
    }
    categoryTitle
    stock
    markdown { markdownType markdownExpirationDate markdownPercentage }
    bargainPrice { priceWas priceNow }
  }
}
"""

# Store search. AH's StoresFilterInput only accepts a postal code here (no
# free-text/name field), and returns the stores nearest to it.
STORES_SEARCH = """
query StoresSearch($filter: StoresFilterInput) {
  storesSearch(filter: $filter, limit: 25) {
    result {
      id
      name
      storeType
      address { street houseNumber houseNumberExtra postalCode city }
    }
  }
}
"""


# The products of one bonus group (appie-go: FetchBonusPromotionWithProducts),
# reduced to the fields this app uses.
GROUP_PRODUCTS_OPERATION = "FetchBonusPromotionWithProducts"
GROUP_PRODUCTS_QUERY = """query FetchBonusPromotionWithProducts(
  $id: String, $periodStart: String, $periodEnd: String,
  $filterUnavailableProducts: Boolean, $forcePromotionVisibility: Boolean = true,
  $showAllPromotionSegments: Boolean = true
) {
  bonusPromotions(input: {
    id: $id, periodStart: $periodStart, periodEnd: $periodEnd,
    filterUnavailableProducts: $filterUnavailableProducts,
    forcePromotionVisibility: $forcePromotionVisibility,
    showAllPromotionSegments: $showAllPromotionSegments
  }) {
    id
    productCount
    products {
      id
      title
      priceV2(periodStart: $periodStart, periodEnd: $periodEnd,
              filterUnavailableProducts: $filterUnavailableProducts, forcePromotionVisibility: true) {
        now { amount }
        was { amount }
      }
      imagePack { medium { url } }
    }
  }
}"""
