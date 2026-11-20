// @ts-check
// The Bonus view's logic without the DOM: savings, the filters & sorting
// panel, categories in AH's order, category groups, liked offers and the
// product -> deal index used by bargain cards. Pure functions.
import { productKey } from '../core/offers.js';
import { compareUnit, unitPrice } from '../core/unit.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{types: 'all'|string[], minSave: number, sort: string, liked: boolean}} BonusFilters liked: the "Liked" pill */
/** @typedef {{id: string, name: string, cats: string[]}} CategoryGroup */

export const DEAL_TYPES = ['percent', 'amount', 'multi', 'fixed', 'other'];
export const SORTS = ['ah', 'save', 'price', 'unit', 'name'];
export const MIN_SAVES = [0, 25, 40, 50];

/** @returns {BonusFilters} */
export const defaultFilters = () => ({ types: 'all', minSave: 0, sort: 'ah', liked: false });

/** Validated filters from storage (anything odd falls back to the default). @param {any} raw */
export function parseFilters(raw) {
  const f = defaultFilters();
  if (raw && typeof raw === 'object') {
    if (Array.isArray(raw.types)) f.types = raw.types.filter((/** @type {any} */ x) => DEAL_TYPES.includes(x));
    if (MIN_SAVES.includes(Number(raw.minSave))) f.minSave = Number(raw.minSave);
    if (SORTS.includes(raw.sort)) f.sort = raw.sort;
    f.liked = raw.liked === true;
  }
  return f;
}

/** The panel's settings are the defaults (the Liked pill is outside the panel). @param {BonusFilters} f */
export const isDefaultFilters = f => f.types === 'all' && !f.minSave && f.sort === 'ah';

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

/** Saving in whole percent (0 when unknown). @param {Offer} o */
export function saving(o) {
  const was = o.priceWas, now = o.priceNow;
  return was != null && was > 0 && now != null && now < was ? Math.round(((was - now) / was) * 100) : 0;
}

/** @param {BonusFilters} f @param {string|undefined} type */
export function typeShown(f, type) {
  return f.types === 'all' || f.types.includes(type && DEAL_TYPES.includes(type) ? type : 'other');
}

/** @param {BonusFilters} f @param {Offer} o */
export function passesFilters(f, o) {
  return typeShown(f, o.dealType) && saving(o) >= f.minSave;
}

/** Unit price of an offer: not for group deals (their products differ in size). @param {Offer} o */
export function offerUnitPrice(o) {
  return o.isGroup ? null : unitPrice(o.priceNow, o.unitSize);
}

/**
 * Sorted copy: the chosen order; liked offers break ties (and come first in
 * AH's own order); then AH's order.
 * @param {Offer[]} list
 * @param {string} sort
 * @param {(o: Offer) => boolean} isLiked
 */
export function sortOffers(list, sort, isLiked) {
  /** @param {Offer} o */
  const price = o => (o.priceNow != null ? o.priceNow : Infinity);
  /** @type {Record<string, (a: Offer, b: Offer) => number>} */
  const CRITERIA = {
    ah: () => 0,
    save: (a, b) => saving(b) - saving(a),
    price: (a, b) => price(a) - price(b),
    unit: (a, b) => compareUnit(offerUnitPrice(a), offerUnitPrice(b)),
    name: (a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'nl'),
  };
  const crit = CRITERIA[sort] || CRITERIA.ah;
  /** @param {Offer} o */
  const liked = o => (isLiked(o) ? 1 : 0);
  return list
    .map((o, i) => /** @type {[Offer, number]} */ ([o, i]))
    .sort((a, b) => crit(a[0], b[0]) || (liked(b[0]) - liked(a[0])) || a[1] - b[1])
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
