// @ts-check
// The Bonus view's logic without the DOM: the order of offers,
// categories in AH's order, category groups, liked offers and the
// product -> deal index used by bargain cards. Pure functions.
import { productKey } from '../core/offers.js';
import { unitPrice } from '../core/unit.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{id: string, name: string, cats: string[]}} CategoryGroup */

/** The "Liked" pill from storage (ah_bonus_filters; older versions also kept filters there: ignored). @param {any} raw */
export const parseLikedPill = raw => !!raw && typeof raw === 'object' && raw.liked === true;


/**
 * Should loadBonus() ask the server again? Yes when nothing is loaded, when
 * forced (Refresh), or when the copy in the page is incomplete (some
 * categories failed): the server retries AH by itself and answers from its
 * cache, so asking again is cheap and picks up the complete Bonus.
 * @param {{errors?: unknown[]}|null} data the Bonus in the page
 * @param {boolean} force
 */
export function needsFetch(data, force) {
  return !data || force || (Array.isArray(data.errors) && data.errors.length > 0);
}

/** Unit price of an offer: not for group deals (their products differ in size). @param {Offer} o */
export function offerUnitPrice(o) {
  return o.isGroup ? null : unitPrice(o.priceNow, o.unitSize);
}

/**
 * Sorted copy: liked offers first, otherwise AH's own order.
 * @param {Offer[]} list
 * @param {(o: Offer) => boolean} isLiked
 */
export function sortOffers(list, isLiked) {
  /** @param {Offer} o */
  const liked = o => (isLiked(o) ? 1 : 0);
  return list
    .map((o, i) => /** @type {[Offer, number]} */ ([o, i]))
    .sort((a, b) => (liked(b[0]) - liked(a[0])) || a[1] - b[1])
    .map(([o]) => o);
}

/**
 * Categories in AH's order, each with its offers (empty ones left out).
 * @param {Offer[]} items
 * @param {string[]} categories AH's order
 * @returns {Map<string, Offer[]>}
 */
export function categoryGroups(items, categories) {
  /** @type {Map<string, Offer[]>} */
  const groups = new Map();
  for (const name of categories) groups.set(name, []);
  for (const o of items) {
    const cat = o.category || '';
    const list = groups.get(cat);
    if (list) list.push(o); else groups.set(cat, [o]);
  }
  for (const [name, list] of groups) if (!list.length) groups.delete(name);
  return groups;
}

/**
 * The offers of a group's categories together, categories in AH's order,
 * each offer once (AH can list the same offer in more than one category).
 * @param {CategoryGroup} group
 * @param {Map<string, Offer[]>} byCat
 */
export function groupOffers(group, byCat) {
  const seen = new Set();
  return [...byCat.keys()].filter(c => group.cats.includes(c))
    .flatMap(c => byCat.get(c) || [])
    .filter(o => {
      const key = productKey(o) + '|' + (o.mechanism || '');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

/**
 * One liked offer per product (a product can be in several categories).
 * @param {Offer[]} items
 * @param {(o: Offer) => boolean} isLiked
 */
export function likedOffers(items, isLiked) {
  const seen = new Set();
  return items.filter(o => {
    if (!isLiked(o)) return false;
    const key = productKey(o);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * Product id -> deal text, so bargain cards can say "also in the Bonus". The
 * products of a group deal ("Alle AH Verse pizza's") are on Bonus too.
 * @param {Offer[]} items
 * @returns {Map<number, string>}
 */
export function indexDeals(items) {
  /** @type {Map<number, string>} */
  const deals = new Map();
  /** @param {unknown} id @param {string|undefined} deal */
  const add = (id, deal) => {
    if (typeof id === 'number' && Number.isInteger(id) && id > 0 && !deals.has(id)) deals.set(id, deal || '');
  };
  for (const o of items) {
    add(o.id, o.mechanism);
    for (const m of Array.isArray(o.products) ? o.products : []) add(m && m.id, o.mechanism);
  }
  return deals;
}

/**
 * Category groups from storage, validated: known id shape, unique ids, each
 * category in at most one group (the first keeps it), capped sizes.
 * @param {unknown} raw
 * @param {(n: number) => string} defaultName
 * @param {{maxGroups: number, maxName: number, maxCats: number}} limits
 * @returns {CategoryGroup[]}
 */
export function parseGroups(raw, defaultName, { maxGroups, maxName, maxCats }) {
  /** @type {CategoryGroup[]} */
  const out = [];
  const ids = new Set();
  const taken = new Set();
  /** @param {unknown} s */
  const cleanName = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, maxName);
  for (const g of Array.isArray(raw) ? raw : []) {
    if (!g || typeof g.id !== 'string' || !/^[a-z0-9]{1,16}$/.test(g.id) || ids.has(g.id)) continue;
    /** @type {string[]} */
    const cats = Array.isArray(g.cats)
      ? [...new Set(/** @type {unknown[]} */ (g.cats).filter(c => typeof c === 'string' && c && c.length <= 100 && !taken.has(c)))]
        .map(String).slice(0, maxCats)
      : [];
    cats.forEach(c => taken.add(c));
    ids.add(g.id);
    out.push({ id: g.id, name: cleanName(g.name) || defaultName(out.length + 1), cats });
    if (out.length >= maxGroups) break;
  }
  return out;
}
