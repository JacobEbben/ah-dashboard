// @ts-check
// The cart: bargains and Bonus deals the visitor picked with a card's cart
// button. Kept in localStorage (ah_cart, synced for signed-in users) as a
// list of entries (core/cart.js), newest last. Changes are announced with
// EV.cart; the cart view and the cards re-render themselves.
import { config } from '../core/config.js';
import { emit, EV } from '../core/events.js';
import { cleanEntries, entriesFor } from '../core/cart.js';
import { KEYS, storage } from '../core/storage.js';

/** @typedef {import('../core/cart.js').CartEntry} CartEntry */

/** @type {Map<string, CartEntry>} key -> entry, oldest first */
let cart = new Map();

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
 * it is in the cart. ``week``: the Bonus week a deal was shown for.
 * @param {any} offer
 * @param {{start: string|null, end: string|null}|null} [week]
 */
export function toggleCart(offer, week = null) {
  const entries = entriesFor(offer, { now: Date.now(), week, imageHosts: config.imageHosts });
  if (!entries.length) return;
  if (entries.some(e => cart.has(e.key))) {
    for (const e of entries) cart.delete(e.key);
  } else {
    for (const e of entries) cart.set(e.key, e);
    // The newest MAX_CART stay (cleanEntries caps the list).
    cart = new Map(cleanEntries([...cart.values()], config.imageHosts).map(e => [e.key, e]));
  }
  changed();
}

/** @param {string[]} keys */
export function removeFromCart(keys) {
  let any = false;
  for (const key of keys) any = cart.delete(key) || any;
  if (any) changed();
}

export function clearCart() {
  if (!cart.size) return;
  cart.clear();
  changed();
}

export function initCartList() {
  cart = new Map(cleanEntries(storage.getJSON(KEYS.cart, []), config.imageHosts).map(e => [e.key, e]));
}
