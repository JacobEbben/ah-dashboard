// @ts-check
// Offers: the one item shape both views get from the API (server:
// domain/models.py, Offer.to_json), and the product identity used by likes
// and hidden products.

/**
 * @typedef {object} GroupMember
 * @property {number} id
 * @property {string} title
 *
 * @typedef {object} Offer
 * @property {'bargain'|'bonus'} kind
 * @property {number|null} id AH webshop product id (the same in both views); null for group deals
 * @property {string|null} title
 * @property {string|null} brand
 * @property {string|null} unitSize pack size text, e.g. "500 g"
 * @property {string|null} image https URL
 * @property {string|null} category
 * @property {number|null} priceNow
 * @property {number|null} priceWas
 * -- bargains --
 * @property {number|null} [storeId]
 * @property {string|null} [storeName]
 * @property {number|null} [stock]
 * @property {number|null} [discount] percentage off
 * @property {string|null} [expires] date the markdown ends
 * @property {string|null} [markdownType] e.g. EXPIRATION, OUT_OF_ASSORTMENT
 * -- Bonus --
 * @property {string} [dealType] 'percent' | 'amount' | 'fixed' | 'multi' | 'other'
 * @property {string} [mechanism] AH's deal text
 * @property {number|null} [minQty]
 * @property {boolean} [priceFrom]
 * @property {number|null} [productCount]
 * @property {boolean} [isGroup]
 * @property {GroupMember[]} [products]
 */

const ID_RE = /^[0-9]{1,10}$/;

/**
 * Stable key for an offer's product: AH's product id, or for products
 * without one, their title ("t:<title>").
 * @param {{id?: number|null, title?: string|null}} offer
 */
export function productKey(offer) {
  const id = String(offer?.id ?? '');
  if (ID_RE.test(id)) return id;
  return 't:' + String(offer?.title || '').trim().toLowerCase().slice(0, 120);
}

/**
 * The products an offer stands for, as [key, title] pairs. A Bonus group deal
 * ("Alle AH Verse pizza's") stands for each of its products (webshop ids: the
 * same keys as bargains); anything else for itself.
 * @param {Offer|{id?: number|null, title?: string|null, products?: GroupMember[]}} offer
 * @returns {[string, string][]}
 */
export function memberKeys(offer) {
  const list = offer && Array.isArray(offer.products) ? offer.products : [];
  /** @type {[string, string][]} */
  const members = list
    .filter(m => m && ID_RE.test(String(m.id)))
    .map(m => [String(m.id), String(m.title || m.id).slice(0, 120)]);
  if (members.length) return members;
  const key = productKey(offer);
  return key === 't:' ? [] : [[key, String(offer?.title || key).slice(0, 120)]];
}

/**
 * How many products a group deal stands for (0: not a group with products).
 * @param {Offer} offer
 */
export function groupSize(offer) {
  const n = memberKeys(offer).length;
  return offer && Array.isArray(offer.products) && n > 1 ? n : 0;
}

// Product page on ah.nl: AH's webshop id as "wi<id>" (appie-go prints ids the
// same way); AH redirects to the full URL. Only plain numeric ids are used,
// so the link can never point anywhere else.
const AH_PRODUCT_URL = 'https://www.ah.nl/producten/product/wi';

/** @param {number|null|undefined} id */
export function productUrl(id) {
  const s = String(id ?? '');
  return ID_RE.test(s) ? AH_PRODUCT_URL + s : null;
}

/**
 * Product images: only absolute https URLs (AH's CDN) are used.
 * @param {unknown} value
 */
export function safeImageUrl(value) {
  try {
    const u = new URL(String(value));
    return u.protocol === 'https:' ? u.href : null;
  } catch (e) {
    return null;
  }
}
