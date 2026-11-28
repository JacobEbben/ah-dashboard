// @ts-check
// The cart: bargains and Bonus deals the visitor picked with a card's cart
// button. Kept in localStorage (ah_cart, synced for signed-in users) as a
// list of entries (core/cart.js), newest last. Changes are announced with
// EV.cart; the cart view and the cards re-render themselves.
// Removing is per store. What the visitor removes is remembered for the rest of the day
// (ah_cart_removed, synced too): "Liked today" and following a product to other stores
// don't put that store's entry back; other stores still get the product.
import { config } from '../core/config.js';
import { emit, EV } from '../core/events.js';
import { cleanEntries, entriesAtNewStores, entriesFor, MAX_CART, removedOn, todayOf, withoutRemoved, withRemoved }
  from '../core/cart.js';
import { KEYS, storage } from '../core/storage.js';

/** @typedef {import('../core/cart.js').CartEntry} CartEntry */

/** @type {Map<string, CartEntry>} key -> entry, oldest first */
let cart = new Map();

/** @type {import('../core/cart.js').Removed|null} what was removed from the cart today (ah_cart_removed) */
let removed = null;

const today = () => todayOf(new Date());
/** The keys removed today (a record of an earlier day counts for nothing). */
const removedToday = () => removedOn(removed, today());

/** Remember that these keys were removed today. @param {string[]} keys */
function remember(keys) {
  if (!keys.length) return;
  removed = withRemoved(removed, keys, today());
  storage.setJSON(KEYS.cartRemoved, removed);
}

/** The visitor put these in the cart on purpose: they count as removed no more. @param {CartEntry[]} entries */
function unremember(entries) {
  const before = removedToday().size;
  if (!before) return;
  const after = withoutRemoved(removed, entries.map(e => e.key), today());
  if (after.keys.length === before) return;
  removed = after;
  storage.setJSON(KEYS.cartRemoved, removed);
}

/**
 * Whether "Liked today" would put (part of) this offer in the cart: one of its entries is
 * neither in the cart nor removed today.
 * @param {any} offer
 */
export function canAdd(offer) {
  const removed = removedToday();
  return entriesFor(offer, { now: 0 }).some(e => !cart.has(e.key) && !removed.has(e.key));
}

function changed() {
  storage.setJSON(KEYS.cart, [...cart.values()]);
  emit(EV.cart);
}

/** The entries, oldest first. @returns {CartEntry[]} */
export function cartEntries() { return [...cart.values()]; }
export function cartSize() { return cart.size; }
/** Whether the cart has Bonus deals (then this week's Bonus is needed to check them). */
export function cartHasBonus() { return [...cart.values()].some(e => e.kind === 'bonus'); }

/**
 * In the cart: the offer, or for a bargain card shown for several stores, at
 * any of them.
 * @param {any} offer
 */
export function inCart(offer) {
  return entriesFor(offer, { now: 0 }).some(e => cart.has(e.key));
}

/**
 * Add the offer (a bargain card: at each store it shows), or remove it when
 * it is in the cart (at the stores the card shows; other stores keep it).
 * ``week``: the Bonus week a deal was shown for.
 * @param {any} offer
 * @param {{start: string|null, end: string|null}|null} [week]
 */
export function toggleCart(offer, week = null) {
  const entries = entriesFor(offer, { now: Date.now(), week, imageHosts: config.imageHosts });
  if (!entries.length) return;
  if (entries.some(e => cart.has(e.key))) {
    const keys = entries.map(e => e.key).filter(key => cart.delete(key));
    remember(keys);     // stays out for the rest of the day (not followed back there)
  } else {
    unremember(entries);
    for (const e of entries) cart.set(e.key, e);
    // The newest MAX_CART stay (cleanEntries caps the list).
    cart = new Map(cleanEntries([...cart.values()], config.imageHosts).map(e => [e.key, e]));
  }
  changed();
}

/**
 * The latest bargains loaded (one item per store): a product in the cart as a
 * bargain that is now also a bargain at another followed store gets an entry
 * there too. Returns whether anything was added (then EV.cart was emitted).
 * @param {any[]} items
 */
export function followBargains(items) {
  const removed = removedToday();
  const added = entriesAtNewStores(cartEntries(), items,
    { now: Date.now(), imageHosts: config.imageHosts, room: MAX_CART - cart.size }).filter(e => !removed.has(e.key));
  if (!added.length) return false;
  for (const e of added) cart.set(e.key, e);
  changed();
  return true;
}

/**
 * Put these offers in the cart (each bargain at its store); what is already in it stays as it is,
 * and what was removed today stays out. Never more than the room left (MAX_CART). Returns how many
 * entries were added.
 * @param {any[]} offers
 */
export function addToCart(offers) {
  let added = 0;
  const removed = removedToday();
  for (const offer of offers) {
    for (const e of entriesFor(offer, { now: Date.now(), imageHosts: config.imageHosts })) {
      if (cart.has(e.key) || removed.has(e.key) || cart.size >= MAX_CART) continue;
      cart.set(e.key, e);
      added++;
    }
  }
  if (added) changed();
  return added;
}

/** Remove an entry from the cart view: a bargain at its store only (other stores keep it). @param {CartEntry} entry */
export function removeEntry(entry) {
  remember([entry.key]);     // stays out for the rest of the day (not followed back there)
  removeFromCart([entry.key]);
}

/** Remove these entries (not remembered: "Remove gone" uses it). @param {string[]} keys */
export function removeFromCart(keys) {
  let any = false;
  for (const key of keys) any = cart.delete(key) || any;
  if (any) changed();
}

/** Empty the cart; it also forgets what was removed today (so "Liked today" can add everything again). */
export function clearCart() {
  removed = null;
  storage.remove(KEYS.cartRemoved);
  if (!cart.size) return;
  cart.clear();
  changed();
}

export function initCartList() {
  cart = new Map(cleanEntries(storage.getJSON(KEYS.cart, []), config.imageHosts).map(e => [e.key, e]));
  removed = storage.getJSON(KEYS.cartRemoved, null);    // checked on use (core/cart.removedOn)
}
