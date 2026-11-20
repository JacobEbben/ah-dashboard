// @ts-check
// The bargains view's logic without the DOM: filtering, merging the same
// product across stores, grouping and sorting. Pure functions: everything
// they depend on (liked / hidden checks, store order, labels) is passed in.
import { compareUnit, unitPrice } from '../core/unit.js';
import { productKey } from '../core/offers.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{name: string, stock: number|null|undefined}} StoreStock */
/** @typedef {Offer & {stores: StoreStock[]}} Card one card: an offer, possibly at several stores */

/**
 * Product-type selection: every type (also ones that appear later) or only
 * the selected ones.
 * @typedef {{all: boolean, selected: Set<string>}} Cats
 *
 * What the filters ask for. (Liking a product doesn't filter: it only puts it first, see sorter.)
 * @typedef {object} Filter
 * @property {Cats} cats
 * @property {number} minDisc minimum discount in percent (0 = any)
 * @property {boolean} showHidden show hidden products (dimmed)
 * @property {number|null} [storeId] "I'm in this store": only this store's bargains
 */

/** Product type of an offer ("Overig" without one). @param {Offer} o */
export function catKey(o) { return (o.category || 'Overig').trim(); }

/** @param {Offer} o */
export const discountOf = o => Number(o.discount) || 0;

/** @param {Offer[]} items @returns {string[]} product types present, A-Z */
export function knownCats(items) {
  return [...new Set(items.map(catKey))].sort((a, b) => a.localeCompare(b, 'nl'));
}

/**
 * Apply the filters. Returns the offers to show and how many products the
 * hidden filter left out.
 * @param {Offer[]} items
 * @param {Filter} f
 * @param {{isHidden: (o: Offer) => boolean}} deps
 * ``otherStores``: with ``storeId``, how many bargains that match the filters
 * are at the other stores (so an empty list never reads as "nothing anywhere").
 * @returns {{items: Offer[], hiddenCount: number, otherStores: number}}
 */
export function filterOffers(items, f, { isHidden }) {
  let out = items.slice();
  if (!f.cats.all) out = out.filter(o => f.cats.selected.has(catKey(o)));
  if (f.minDisc > 0) out = out.filter(o => o.discount != null && Number(o.discount) >= f.minDisc);
  // Count products, not store entries (one product can be at several stores).
  const hiddenCount = new Set(out.filter(isHidden).map(productKey)).size;
  if (!f.showHidden) out = out.filter(o => !isHidden(o));
  let otherStores = 0;
  if (f.storeId != null) {
    const here = out.filter(o => Number(o.storeId) === f.storeId);
    otherStores = out.length - here.length;
    out = here;
  }
  return { items: out, hiddenCount, otherStores };
}

/** "I'm in this store" lasts 2 hours, and never past midnight. */
export const IN_STORE_MS = 2 * 60 * 60 * 1000;

/** When "I'm in this store", started at ``now``, ends (ms). @param {Date} now */
export function inStoreUntil(now) {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime();
  return Math.min(now.getTime() + IN_STORE_MS, midnight);
}

/**
 * The remembered "I'm in this store" if it still applies: a followed store
 * and not expired; else null.
 * @param {unknown} saved what was stored ({id, until})
 * @param {number} now ms
 * @param {number[]} storeIds the followed stores
 * @returns {number|null}
 */
export function activeInStore(saved, now, storeIds) {
  if (!saved || typeof saved !== 'object') return null;
  const { id, until } = /** @type {any} */ (saved);
  if (!Number.isInteger(id) || !storeIds.includes(id)) return null;
  if (typeof until !== 'number' || !(until > now) || until - now > IN_STORE_MS) return null;
  return id;
}

/**
 * Merge the same product at the same discount across stores into one card
 * (not when grouping by store: then every store keeps its own card). Stores
 * of a card are in the visitor's store order; stock is the total.
 * @param {Offer[]} items
 * @param {string} groupBy
 * @param {(storeName: string) => number} storeIndex
 * @returns {Card[]}
 */
export function mergeAcrossStores(items, groupBy, storeIndex) {
  /** @type {Map<string, Card>} */
  const merged = new Map();
  let unkeyed = 0;
  for (const o of items) {
    const pid = o.id ?? o.title ?? `#${unkeyed++}`;
    const key = pid + '|' + o.discount + (groupBy === 'store' ? '|' + (o.storeName || '?') : '');
    const entry = { name: o.storeName || '?', stock: o.stock };
    const found = merged.get(key);
    if (found) found.stores.push(entry);
    else merged.set(key, { ...o, stores: [entry] });
  }
  const out = [...merged.values()];
  for (const c of out) {
    c.stores.sort((a, b) => storeIndex(a.name) - storeIndex(b.name));
    c.storeName = c.stores[0].name;
    c.stock = c.stores.reduce((s, x) => s + (Number(x.stock) || 0), 0);
  }
  return out;
}

/**
 * How cards are grouped: by product type, discount, store, or not at all.
 * @param {string} groupBy
 * @param {{noDiscount: string, special: string, all: string}} labels
 * @returns {(c: Offer) => string}
 */
export function groupKeyFn(groupBy, labels) {
  if (groupBy === 'disc') {
    return c => (c.discount == null ? labels.noDiscount : isSpecial(c) ? labels.special : `-${c.discount}%`);
  }
  if (groupBy === 'store') return c => c.storeName || '?';
  if (groupBy === 'none') return () => labels.all;
  return catKey;
}

/**
 * Discount groups biggest first; stores in the visitor's order; types A-Z.
 * @param {string[]} keys
 * @param {string} groupBy
 * @param {(storeName: string) => number} storeIndex
 */
export function sortGroupKeys(keys, groupBy, storeIndex) {
  /** @param {string} k */
  const keyNum = k => { const n = parseFloat(String(k).replace('-', '')); return isNaN(n) ? -1 : n; };
  if (groupBy === 'disc') return keys.sort((a, b) => keyNum(b) - keyNum(a));
  if (groupBy === 'store') return keys.sort((a, b) => storeIndex(a) - storeIndex(b) || a.localeCompare(b, 'nl'));
  return keys.sort((a, b) => a.localeCompare(b, 'nl'));
}

/**
 * Card order within a group: each order's own criteria first; when those are
 * all equal, liked first.
 * @param {string} sortBy 'store' | 'disc' | 'qty' | 'unit'
 * @param {(o: Offer) => boolean} isLiked
 * @returns {(a: Offer, b: Offer) => number}
 */
export function sorter(sortBy, isLiked) {
  /** @param {Offer} a @param {Offer} b */
  const byStore = (a, b) => (a.storeName || '').localeCompare(b.storeName || '', 'nl');
  /** @param {Offer} o */
  const qty = o => Number(o.stock) || 0;
  /** @param {Offer} o */
  const liked = o => (isLiked(o) ? 1 : 0);
  /** @param {Offer} o */
  const unit = o => unitPrice(o.priceNow, o.unitSize);
  const d = discountOf;
  /** @type {Record<string, (a: Offer, b: Offer) => number>} */
  const SORTERS = {
    store: (a, b) => byStore(a, b) || d(b) - d(a) || liked(b) - liked(a),
    disc: (a, b) => d(b) - d(a) || liked(b) - liked(a) || byStore(a, b),
    qty: (a, b) => qty(b) - qty(a) || d(b) - d(a) || liked(b) - liked(a),
    // Price per kg / l (cheapest first), then per piece, then products without one.
    unit: (a, b) => compareUnit(unit(a), unit(b)) || d(b) - d(a) || liked(b) - liked(a),
  };
  return Object.hasOwn(SORTERS, sortBy) ? SORTERS[sortBy] : byStore;
}

/**
 * The special product "AH Groente & Fruit zakje": AH lists it among the
 * bargains with a 0% discount (a real markdown always has a percentage).
 * @param {Offer} o
 */
export function isSpecial(o) {
  return o.discount != null && Number(o.discount) === 0;
}

/**
 * The visible special offers and where they are: unique titles and store
 * names (in the visitor's store order). Hidden products don't count.
 * @param {Offer[]} items
 * @param {(o: Offer) => boolean} isHidden
 * @param {(storeName: string) => number} storeIndex
 */
export function specialSummary(items, isHidden, storeIndex) {
  const found = items.filter(o => isSpecial(o) && !isHidden(o));
  const titles = [...new Set(found.map(o => o.title).filter(Boolean))];
  const stores = [...new Set(found.map(o => o.storeName).filter(Boolean))]
    .sort((a, b) => storeIndex(String(a)) - storeIndex(String(b)));
  return { count: found.length, titles: /** @type {string[]} */ (titles), stores: /** @type {string[]} */ (stores) };
}

/** Markdown reason "ASSORTMENT" (also OUT_OF_ASSORTMENT): leaving the range. @param {Offer} o */
export function isLeavingAssortment(o) {
  return /ASSORTMENT/i.test(String(o.markdownType || ''));
}
