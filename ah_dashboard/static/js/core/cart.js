// @ts-check
// The cart without storage or DOM: what an entry is, checking entries read
// back from storage (or synced from another device), and which entries are
// still available in the latest results. Pure functions; lists/cart.js keeps
// the entries, cart/index.js draws them.
//
// An entry is a copy of the offer as it was added, so the cart can still show
// it after it is gone from AH. A bargain is per store (a card merged across
// stores adds one entry per store); a Bonus deal is national.
import { allowedImageUrl, productKey } from './offers.js';

/**
 * @typedef {object} CartEntry
 * @property {string} key 'b:<storeId>:<productKey>' or 'p:<productKey>'
 * @property {'bargain'|'bonus'} kind
 * @property {number|null} id AH product id (null for group deals)
 * @property {string} title
 * @property {string|null} unitSize
 * @property {string|null} image only on an allowed image host (allowedImageUrl), else null
 * @property {number|null} priceNow
 * @property {number|null} priceWas
 * @property {number} added ms
 * -- bargains --
 * @property {number} [storeId]
 * @property {string} [storeName]
 * @property {number|null} [discount]
 * @property {string|null} [expires] 'YYYY-MM-DD'
 * -- Bonus --
 * @property {string|null} [mechanism]
 * @property {number|null} [minQty]
 * @property {boolean} [isGroup]
 * @property {string|null} [from] first day of its Bonus week
 * @property {string|null} [until] last day of its Bonus week
 *
 * @typedef {'ok'|'gone'|'unknown'|'upcoming'} CartStatus
 *   ok: in the latest results; gone: no longer there (shown greyed out);
 *   unknown: can't tell yet (not loaded, or its store failed); upcoming: next week's Bonus
 *
 * @typedef {object} Latest what the cart compares with
 * @property {{storeIds: Set<number>, byKey: Map<string, any>, byProduct?: Map<string, any[]>}|null} bargains
 * @property {{complete: boolean, byKey: Map<string, any>}|null} bonus
 *
 * @typedef {{storeId: number, stock: number, discount: number|null}} StoreStock
 * @typedef {{entry: CartEntry, current: any|null, status: CartStatus, bonusDeal: string|null, stock: number|null,
 *            stores: StoreStock[]}} CartRow
 *   stores: every followed store where the product is a bargain now, with its stock (the bargain cards' coloured
 *   squares), in the visitor's store order; with a store filter only that store
 *   stock: a bargain still listed: how many units are left at this discount at its store (AH's latest count); else null
 *   bonusDeal: a bargain whose product is also in this week's Bonus: the deal text ('' when AH gave none); else null
 * @typedef {{storeId: number|null, storeName: string, rows: CartRow[]}} CartSection storeId null: the Bonus
 */

/** At most this many entries (the cart is synced, so it must stay small). */
export const MAX_CART = 300;
const MAX_TEXT = 120;
const ID_RE = /^[0-9]{1,10}$/;

/** @param {number|null|undefined} storeId @param {{id?: number|null, title?: string|null}} offer */
export const bargainKey = (storeId, offer) => `b:${storeId}:${productKey(offer)}`;
/** @param {{id?: number|null, title?: string|null}} offer */
export const bonusKey = offer => `p:${productKey(offer)}`;

/** 'YYYY-MM-DD' at the start of a value, else null. @param {unknown} s */
export function dayOf(s) {
  const m = /^([0-9]{4}-[0-9]{2}-[0-9]{2})/.exec(String(s ?? ''));
  return m ? m[1] : null;
}

/** Today in the browser's time zone, 'YYYY-MM-DD'. @param {Date} now */
export function todayOf(now) {
  const p = (/** @type {number} */ n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** @param {unknown} v @returns {number|null} */
const num = v => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v < 1e6 ? v : null);
/** @param {unknown} v @returns {string|null} */
const text = v => (typeof v === 'string' && v.trim() ? v.slice(0, MAX_TEXT) : null);
/** An image URL on an allowed host, at most 500 characters; else null. @param {unknown} v @param {string[]} hosts */
const image = (v, hosts) => (typeof v === 'string' && v.length <= 500 ? allowedImageUrl(v, hosts) : null);
/** @param {unknown} v @returns {number|null} */
const pid = v => (ID_RE.test(String(v ?? '')) ? Number(v) : null);

/**
 * The cart entries an offer stands for: a bargain card one per store it shows
 * (``stores`` from bargains/model.mergeAcrossStores, else its own store); a
 * Bonus deal one. ``week``: the Bonus week the deal was shown for.
 * ``imageHosts``: where images may come from (app-config imageHosts); other images are dropped.
 * @param {any} offer
 * @param {{now: number, week?: {start: string|null, end: string|null}|null, imageHosts?: string[]}} ctx
 * @returns {CartEntry[]}
 */
export function entriesFor(offer, { now, week = null, imageHosts = [] }) {
  if (!offer || typeof offer !== 'object') return [];
  const base = {
    id: pid(offer.id), title: text(offer.title) || '?', unitSize: text(offer.unitSize),
    image: image(offer.image, imageHosts),
    priceNow: num(offer.priceNow), priceWas: num(offer.priceWas), added: now,
  };
  if (offer.kind === 'bonus') {
    return [{ ...base, key: bonusKey(offer), kind: 'bonus', mechanism: text(offer.mechanism),
      minQty: num(offer.minQty), isGroup: offer.isGroup === true,
      from: dayOf(week && week.start), until: dayOf(week && week.end) }];
  }
  const stores = Array.isArray(offer.stores) && offer.stores.length
    ? offer.stores : [{ id: offer.storeId, name: offer.storeName }];
  /** @type {CartEntry[]} */
  const out = [];
  for (const s of stores) {
    const storeId = pid(s && s.id);
    if (storeId === null) continue;
    out.push({ ...base, key: bargainKey(storeId, offer), kind: 'bargain', storeId,
      storeName: text(s.name) || String(storeId), discount: num(offer.discount), expires: dayOf(offer.expires) });
  }
  return out;
}

/**
 * A bargain in the cart follows its product: the entries to add for every
 * store in the latest bargains (``items``, one per store) that has a product
 * the cart holds as a bargain, but no entry for that store yet (it became a
 * bargain there after it was added). Bonus entries don't count (national).
 * At most ``room`` entries, so adding never pushes out what the visitor picked.
 * @param {CartEntry[]} entries
 * @param {any[]} items
 * @param {{now: number, imageHosts?: string[], room?: number}} ctx
 * @returns {CartEntry[]}
 */
export function entriesAtNewStores(entries, items, { now, imageHosts = [], room = MAX_CART - entries.length }) {
  const have = new Set(entries.map(e => e.key));
  const products = new Set(entries.filter(e => e.kind === 'bargain').map(e => productKey(e)));
  /** @type {CartEntry[]} */
  const out = [];
  for (const o of Array.isArray(items) ? items : []) {
    if (out.length >= room) break;
    if (!o || typeof o !== 'object' || o.kind === 'bonus' || !products.has(productKey(o))) continue;
    for (const e of entriesFor({ ...o, stores: undefined }, { now, imageHosts })) {
      if (have.has(e.key) || out.length >= room) continue;
      have.add(e.key);
      out.push(e);
    }
  }
  return out;
}

/**
 * The liked bargains whose last day is ``day`` ('YYYY-MM-DD'): what the cart's "🛒 Liked today" adds.
 * Hidden products are left out; with ``storeId`` (the cart's store filter) only that store's.
 * @param {any[]} items the latest bargains (one per store)
 * @param {string} day
 * @param {{isLiked: (o: any) => boolean, isHidden: (o: any) => boolean, storeId?: number|null}} deps
 * @returns {any[]}
 */
export function likedEndingOn(items, day, { isLiked, isHidden, storeId = null }) {
  return items.filter(o => o && o.kind !== 'bonus' && dayOf(o.expires) === day && isLiked(o) && !isHidden(o)
    && (storeId == null || Number(o.storeId) === storeId));
}

/** "🛒 N%+" in the cart: bargains with at least N% off; the visitor picks N (ah_cart_min_disc), else 70. */
export const BIG_DISCOUNT = 70;
export const MIN_DISC_CHOICES = [40, 50, 60, 70, 80, 90];

/**
 * The remembered minimum discount for "🛒 N%+" if it is one of the choices, else BIG_DISCOUNT.
 * @param {unknown} saved what was stored (maybe from another device or version)
 * @returns {number}
 */
export function cartMinDiscount(saved) {
  const n = Number(String(saved ?? '').trim() || NaN);
  return MIN_DISC_CHOICES.includes(n) ? n : BIG_DISCOUNT;
}

/**
 * The bargains with at least ``min`` percent off (any end day): what the cart's "🛒 N%+" adds.
 * Hidden products are left out; with ``storeId`` (the cart's store filter) only that store's.
 * @param {any[]} items the latest bargains (one per store)
 * @param {number} min
 * @param {{isHidden: (o: any) => boolean, storeId?: number|null}} deps
 * @returns {any[]}
 */
export function bargainsFrom(items, min, { isHidden, storeId = null }) {
  return items.filter(o => o && o.kind !== 'bonus' && typeof o.discount === 'number' && o.discount >= min
    && !isHidden(o) && (storeId == null || Number(o.storeId) === storeId));
}

/**
 * What the visitor removed from the cart today: ``{day: 'YYYY-MM-DD', keys: [entry key, …]}`` (ah_cart_removed).
 * Removing is per store (that store may have thrown the product away while AH still lists it): "🛒 Liked today"
 * and a bargain following its product leave out exactly these keys, so that store's entry stays out for the rest
 * of the day while other stores still get the product; the next day (or emptying the cart) forgets them.
 * @typedef {{day: string, keys: string[]}} Removed
 */

/** At most this many removed keys are remembered (the record is synced, so it must stay small). */
export const MAX_REMOVED = 1000;
const KEY_RE = /^(?:b:[0-9]{1,10}|p):.{1,200}$/;

/**
 * The removed keys that still count on ``day``: a record of another day (or unusable) counts for nothing.
 * @param {any} value the stored record (maybe from another device or version)
 * @param {string} day
 * @returns {Set<string>}
 */
export function removedOn(value, day) {
  if (!value || typeof value !== 'object' || value.day !== day || !Array.isArray(value.keys)) return new Set();
  return new Set(value.keys.filter(k => typeof k === 'string' && KEY_RE.test(k)).slice(-MAX_REMOVED));
}

/**
 * The record after removing ``keys`` on ``day`` (a record of an earlier day starts over).
 * @param {any} value @param {string[]} keys @param {string} day
 * @returns {Removed}
 */
export function withRemoved(value, keys, day) {
  const set = removedOn(value, day);
  for (const k of keys) { set.delete(k); set.add(k); }       // newest last, so the cap drops the oldest
  return { day, keys: [...set].slice(-MAX_REMOVED) };
}

/**
 * The record after the visitor put ``keys`` in the cart again on purpose (the card's cart button).
 * @param {any} value @param {string[]} keys @param {string} day
 * @returns {Removed}
 */
export function withoutRemoved(value, keys, day) {
  const back = new Set(keys);
  return { day, keys: [...removedOn(value, day)].filter(k => !back.has(k)) };
}

/**
 * Two devices' records combined (settings sync): the same day: both; else the later day's.
 * @param {any} a @param {any} b
 * @returns {Removed|null}
 */
export function mergeRemoved(a, b) {
  const day = [a, b].map(v => (v && typeof v === 'object' ? dayOf(v.day) : null)).filter(Boolean).sort().pop();
  if (!day) return null;
  return withRemoved(b, [...removedOn(a, day)], day);
}

/**
 * One entry read back from storage (maybe from another device or version),
 * checked field by field; null when it isn't usable. The key is made again
 * from the checked fields, so it always matches what it says. An image not on
 * one of ``imageHosts`` is dropped (a synced entry could point anywhere).
 * @param {any} e
 * @param {string[]} [imageHosts]
 * @returns {CartEntry|null}
 */
export function cleanEntry(e, imageHosts = []) {
  if (!e || typeof e !== 'object' || typeof e.key !== 'string') return null;
  const kind = e.kind === 'bonus' ? 'bonus' : e.kind === 'bargain' ? 'bargain' : null;
  if (!kind) return null;
  const offer = { id: pid(e.id), title: text(e.title) };
  if (!offer.title && offer.id === null) return null;
  const base = {
    id: offer.id, title: offer.title || String(offer.id), unitSize: text(e.unitSize),
    image: image(e.image, imageHosts),
    priceNow: num(e.priceNow), priceWas: num(e.priceWas), added: num(e.added) ?? 0,
  };
  if (kind === 'bonus') {
    return { ...base, key: bonusKey(offer), kind, mechanism: text(e.mechanism), minQty: num(e.minQty), isGroup: e.isGroup === true,
      from: dayOf(e.from), until: dayOf(e.until) };
  }
  const storeId = pid(e.storeId);
  if (storeId === null) return null;
  return { ...base, key: bargainKey(storeId, offer), kind, storeId, storeName: text(e.storeName) || String(storeId),
    discount: num(e.discount), expires: dayOf(e.expires) };
}

/**
 * The stored list, checked: usable entries, no duplicate keys, the newest MAX_CART.
 * @param {unknown} raw
 * @param {string[]} [imageHosts]
 * @returns {CartEntry[]}
 */
export function cleanEntries(raw, imageHosts = []) {
  /** @type {Map<string, CartEntry>} */
  const out = new Map();
  for (const e of Array.isArray(raw) ? raw : []) {
    const entry = cleanEntry(e, imageHosts);
    if (entry) { out.delete(entry.key); out.set(entry.key, entry); }
  }
  return [...out.values()].slice(-MAX_CART);
}

/**
 * The latest bargains, by cart key. ``storeIds``: stores whose bargains are
 * known (loaded without an error); for other stores nothing can be said.
 * @param {any[]} items
 * @param {number[]} storeIds
 */
export function indexBargains(items, storeIds) {
  /** @type {Map<string, any>} */
  const byKey = new Map();
  /** @type {Map<string, any[]>} productKey -> the product's bargains at every store */
  const byProduct = new Map();
  for (const o of items) {
    if (!o || pid(o.storeId) === null) continue;
    byKey.set(bargainKey(Number(o.storeId), o), o);
    const list = byProduct.get(productKey(o));
    if (list) list.push(o); else byProduct.set(productKey(o), [o]);
  }
  return { storeIds: new Set(storeIds), byKey, byProduct };
}

/**
 * This week's Bonus, by cart key. ``complete``: no category failed (else a
 * missing deal may just be in a failed category).
 * @param {any[]} items
 * @param {boolean} complete
 */
export function indexBonus(items, complete) {
  /** @type {Map<string, any>} */
  const byKey = new Map();
  for (const o of items) if (o) byKey.set(bonusKey(o), o);
  return { complete, byKey };
}

/**
 * Is the entry still on offer? Past its end date it is gone even before
 * anything loaded; otherwise the latest results decide.
 * @param {CartEntry} entry
 * @param {Latest} latest
 * @param {string} today 'YYYY-MM-DD'
 * @returns {{status: CartStatus, current: any|null}}
 */
export function entryStatus(entry, latest, today) {
  if (entry.kind === 'bargain') {
    if (entry.expires && entry.expires < today) return { status: 'gone', current: null };
    const b = latest.bargains;
    if (!b || entry.storeId === undefined || !b.storeIds.has(entry.storeId)) return { status: 'unknown', current: null };
    const current = b.byKey.get(entry.key) || null;
    return { status: current ? 'ok' : 'gone', current };
  }
  if (entry.until && entry.until < today) return { status: 'gone', current: null };
  if (entry.from && entry.from > today) return { status: 'upcoming', current: null };
  const p = latest.bonus;
  const current = p ? p.byKey.get(entry.key) || null : null;
  if (current) return { status: 'ok', current };
  return { status: p && p.complete ? 'gone' : 'unknown', current: null };
}

/** How the cart's rows are ordered: by name (the default), by discount or by stock (most first). */
export const CART_SORTS = /** @type {const} */ (['name', 'discount', 'stock']);
/** @typedef {'name'|'discount'|'stock'} CartSort */

/**
 * The remembered order if it is one of CART_SORTS, else 'name'.
 * @param {unknown} saved
 * @returns {CartSort}
 */
export function cartSort(saved) {
  return saved === 'discount' || saved === 'stock' ? saved : 'name';
}

/**
 * A row's discount in percent, for sorting: a bargain's (latest, else when added); a Bonus deal's
 * from its prices (the deal text, e.g. "1+1 gratis", has no percentage). null when unknown.
 * @param {CartRow} r
 * @returns {number|null}
 */
export function rowDiscount(r) {
  const o = r.current || r.entry;
  if (r.entry.kind === 'bargain') return num(o.discount) ?? num(r.entry.discount);
  const now = num(o.priceNow) ?? r.entry.priceNow, was = num(o.priceWas) ?? r.entry.priceWas;
  return now != null && was != null && was > 0 && was > now ? Math.round((was - now) / was * 100) : null;
}

/**
 * The cart as sections: one per store (in the visitor's store order, stores no
 * longer followed last), then the Bonus. With ``storeId`` ("I'm in this
 * store") only that store's bargains and the Bonus (national); ``otherStores``
 * counts the bargains left out. Within a section what is gone comes last.
 * ``bonusDeals``: this week's Bonus by product id (bonus/model.indexDeals, also
 * the products of group deals): a bargain also on Bonus gets its deal. ``sort``: the order within a
 * section after "gone last": by name, or by discount / stock (most first, unknown last, then by name).
 * @param {CartEntry[]} entries
 * @param {Latest} latest
 * @param {{today: string, storeId: number|null, storeOrder: number[], bonusTitle: string,
 *          bonusDeals?: Map<number, string>, sort?: CartSort}} o
 * @returns {{sections: CartSection[], shown: number, gone: number, otherStores: number}}
 */
export function cartSections(entries, latest, { today, storeId, storeOrder, bonusTitle, bonusDeals = new Map(),
  sort = 'name' }) {
  /** @type {Map<number|null, CartSection>} */
  const sections = new Map();
  let otherStores = 0, gone = 0, shown = 0;
  for (const entry of entries) {
    const sid = entry.kind === 'bargain' ? entry.storeId ?? null : null;
    if (storeId != null && sid !== null && sid !== storeId) { otherStores++; continue; }
    const { status, current } = entryStatus(entry, latest, today);
    let sec = sections.get(sid);
    if (!sec) {
      sec = { storeId: sid, storeName: sid === null ? bonusTitle : entry.storeName || String(sid), rows: [] };
      sections.set(sid, sec);
    }
    const onBonus = entry.kind === 'bargain' && entry.id !== null && bonusDeals.has(entry.id);
    // Only the latest count: a copy from when it was added would be out of date.
    const stock = status === 'ok' && current && Number.isInteger(current.stock) && current.stock >= 0 ? current.stock : null;
    sec.rows.push({ entry, current, status, bonusDeal: onBonus ? bonusDeals.get(entry.id) || '' : null, stock,
      stores: [] });
    shown++;
    if (status === 'gone') gone++;
  }
  /** @param {number|null} id */
  const rank = id => {
    if (id === null) return 1e6;                   // the Bonus last
    const i = storeOrder.indexOf(id);
    return i < 0 ? 1e5 + id : i;
  };
  const list = [...sections.values()].sort((a, b) => rank(a.storeId) - rank(b.storeId));
  // Per row: the stores where the product is a bargain now, with their stock.
  const byProduct = latest.bargains && latest.bargains.byProduct;
  for (const sec of list) {
    for (const row of sec.rows) {
      /** @type {Map<number, StoreStock>} */
      const seen = new Map();
      for (const o of (byProduct && byProduct.get(productKey(row.entry))) || []) {
        const sid = pid(o.storeId);
        if (sid === null || seen.has(sid) || (storeId != null && sid !== storeId)) continue;
        if (!Number.isInteger(o.stock) || o.stock < 0) continue;
        seen.set(sid, { storeId: sid, stock: o.stock, discount: num(o.discount) });
      }
      row.stores = [...seen.values()].sort((a, b) => rank(a.storeId) - rank(b.storeId) || a.storeId - b.storeId);
    }
  }
  /** @param {CartRow} r */
  const goneLast = r => (r.status === 'gone' ? 1 : 0);
  /** @type {(r: CartRow) => number|null} */
  const amount = sort === 'discount' ? rowDiscount : sort === 'stock' ? r => r.stock : () => null;
  /** Most first; unknown last. @param {CartRow} a @param {CartRow} b */
  const byAmount = (a, b) => {
    const x = amount(a), y = amount(b);
    return x === y ? 0 : x === null ? 1 : y === null ? -1 : y - x;
  };
  for (const sec of list) {
    sec.rows.sort((a, b) => goneLast(a) - goneLast(b) || byAmount(a, b)
      || a.entry.title.localeCompare(b.entry.title, 'nl'));
  }
  return { sections: list, shown, gone, otherStores };
}

/**
 * The cart's remembered store filter (its own, not "I'm in this store") if it
 * still applies: a followed store; else null (all stores).
 * @param {unknown} saved what was stored (the store id as text)
 * @param {number[]} storeIds the followed stores
 * @returns {number|null}
 */
export function activeCartStore(saved, storeIds) {
  const id = pid(saved);
  return id !== null && storeIds.includes(id) ? id : null;
}
