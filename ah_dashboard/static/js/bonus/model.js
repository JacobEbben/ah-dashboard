// @ts-check
// The Bonus view's logic without the DOM: the order of offers,
// categories in AH's order, the category selection (Filters, presets),
// liked offers and the product -> deal index used by bargain cards. Pure functions.
import { productKey } from '../core/offers.js';
import { unitPrice } from '../core/unit.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {'all'|string[]} Cats the categories that are on: every one, or these */

/** The "Liked" pill from storage (ah_bonus_filters; older versions also kept filters there: ignored). @param {any} raw */
export const parseLikedPill = raw => !!raw && typeof raw === 'object' && raw.liked === true;

/**
 * A category selection from storage or a preset: an array of names (only
 * those), anything else means every category.
 * @param {unknown} raw
 * @returns {Cats}
 */
export function parseCats(raw) {
  if (!Array.isArray(raw)) return 'all';
  return [...new Set(raw.filter(c => typeof c === 'string' && c && c.length <= 100))].slice(0, 200);
}

/**
 * The offers of the categories that are on, by category in AH's order.
 * @param {Map<string, Offer[]>} byCat
 * @param {Cats} cats
 * @returns {Map<string, Offer[]>}
 */
export function selectCats(byCat, cats) {
  return cats === 'all' ? byCat : new Map([...byCat].filter(([name]) => cats.includes(name)));
}

/**
 * Category groups saved by an older version ([{id, name, cats}]) as presets
 * ({name: {cats}} plus their order). Nameless or duplicate names are skipped.
 * @param {unknown} raw
 * @returns {{presets: Record<string, {cats: string[]}>, order: string[]}}
 */
export function groupsToPresets(raw) {
  /** @type {Record<string, {cats: string[]}>} */
  const presets = Object.create(null);
  /** @type {string[]} */
  const order = [];
  for (const g of Array.isArray(raw) ? raw : []) {
    const name = g && typeof g.name === 'string' ? g.name.replace(/\s+/g, ' ').trim().slice(0, 40) : '';
    if (!name || Object.hasOwn(presets, name)) continue;
    const cats = parseCats(g.cats);
    presets[name] = { cats: cats === 'all' ? [] : [...cats].sort((a, b) => a.localeCompare(b, 'nl')) };
    order.push(name);
  }
  return { presets, order };
}


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
 * The rows of a group deal's product list: one per product (by id), so the
 * list always matches its "N products" label. Titles that occur more than
 * once get their size ("AH Pindakaas · 350 g"); rows that would still read
 * the same (same title, no or the same size) are numbered ("AH Cola (1)",
 * "AH Cola (2)"). Each row links to its own product.
 * @param {unknown} products
 * @returns {{id: number, label: string}[]}
 */
export function groupMemberRows(products) {
  /** @type {Set<unknown>} */
  const ids = new Set();
  /** @type {{id: number, title: string, size: string}[]} */
  const list = (Array.isArray(products) ? products : [])
    .filter(m => m && typeof m.title === 'string' && m.title.trim() && !ids.has(m.id) && ids.add(m.id))
    .map(m => ({ id: m.id, title: m.title.trim(), size: typeof m.size === 'string' ? m.size.trim() : '' }));
  /** @param {string[]} labels @returns {Map<string, number>} */
  const tally = labels => labels.reduce((n, l) => n.set(l, (n.get(l) || 0) + 1), new Map());
  const titles = tally(list.map(m => m.title));
  const labels = list.map(m => ((titles.get(m.title) || 0) > 1 && m.size ? `${m.title} · ${m.size}` : m.title));
  const same = tally(labels);
  /** @type {Map<string, number>} */
  const seen = new Map();
  return list.map((m, i) => {
    const label = labels[i];
    if ((same.get(label) || 0) < 2) return { id: m.id, label };
    const n = (seen.get(label) || 0) + 1;
    seen.set(label, n);
    return { id: m.id, label: `${label} (${n})` };
  });
}
