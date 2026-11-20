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
 * What the filters panel and the search field ask for.
 * @typedef {object} Filter
 * @property {Cats} cats
 * @property {number} minDisc minimum discount in percent (0 = any)
 * @property {boolean} bonusOnly only products in this week's Bonus
 * @property {boolean} [likedOnly] only liked products
 * @property {string[]} terms folded search words (all must match)
 * @property {boolean} showHidden show hidden products (dimmed)
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
 * Apply the filters. ``bonus``: this week's Bonus as far as loaded. Returns
 * the offers to show, how many products the hidden filter left out, and why
 * the Bonus filter could not apply yet (null when it did or was off).
 * @param {Offer[]} items
 * @param {Filter} f
 * @param {{isHidden: (o: Offer) => boolean, isLiked?: (o: Offer) => boolean, fold: (s: unknown) => string,
 *          bonus: {status: string, deals: Map<number, string>}}} deps
 * @returns {{items: Offer[], hiddenCount: number, bonusPending: 'loading'|'failed'|null}}
 */
export function filterOffers(items, f, { isHidden, isLiked, fold, bonus }) {
  let out = items.slice();
  if (f.likedOnly && isLiked) out = out.filter(isLiked);
  if (!f.cats.all) out = out.filter(o => f.cats.selected.has(catKey(o)));
  if (f.minDisc > 0) out = out.filter(o => o.discount != null && Number(o.discount) >= f.minDisc);
  // Only bargains that are also in this week's Bonus. Until the Bonus is
  // loaded (or when it failed) nothing is filtered and the summary says why.
  /** @type {'loading'|'failed'|null} */
  let bonusPending = null;
  if (f.bonusOnly) {
    if (bonus.status === 'ready') out = out.filter(o => bonus.deals.has(Number(o.id)));
    else bonusPending = bonus.status === 'failed' ? 'failed' : 'loading';
  }
  if (f.terms.length) {
    out = out.filter(o => {
      const name = fold(o.title);
      return f.terms.every(w => name.includes(w));
    });
  }
  // Count products, not store entries (one product can be at several stores).
  const hiddenCount = new Set(out.filter(isHidden).map(productKey)).size;
  if (!f.showHidden) out = out.filter(o => !isHidden(o));
  return { items: out, hiddenCount, bonusPending };
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
