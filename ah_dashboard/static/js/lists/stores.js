// @ts-check
// "My stores": the saved store list (with a colour per store) and "I'm in this
// store". Shared by the bargains view (cards, filters, Stores panel) and the
// cart (its store filter), through the store bar in ui/storebar.js. This
// module owns the state only; changes are announced with EV.stores
// ({reload: true} when a store was added) and EV.inStore.
import { config } from '../core/config.js';
import { emit, EV } from '../core/events.js';
import { KEYS, storage } from '../core/storage.js';

/** @typedef {{id: number, name: string, color: string}} MyStore */

// Distinct colours that don't clash with the red discount badge.
const PALETTE = ['#2f6fb5', '#d27a1f', '#7a4bb0', '#2f8a5a', '#b5179e',
                 '#00838f', '#8d6e2f', '#3f51b5', '#e0567a', '#546e7a'];
export const MAX_NAME = 30;

/** @type {MyStore[]} */
let stores = [];

/** The saved stores, in the visitor's order. */
export function myStores() { return stores; }

// ---- store list ---------------------------------------------------------
/** @param {MyStore[]} list */
function pickColor(list) {
  const used = new Set(list.map(s => s.color));
  return PALETTE.find(c => !used.has(c)) || PALETTE[list.length % PALETTE.length];
}

/** @returns {MyStore[]} */
function loadStores() {
  const saved = storage.getJSON(KEYS.stores);
  // No built-in stores: a new visitor starts with an empty list (the bargains
  // view then opens the store search).
  const list = Array.isArray(saved) ? saved : [];
  /** @type {MyStore[]} */
  const out = [];
  for (const s of list) {
    // A synced list may come from another device or an older version: skip
    // odd and duplicate ids, and keep at most maxStores (the server refuses
    // a longer list, which would leave the bargains view with only an error).
    if (out.length >= config.maxStores) break;
    if (!s || !/^[0-9]{1,10}$/.test(String(s.id)) || out.some(o => o.id === Number(s.id))) continue;
    out.push({ id: Number(s.id), name: String(s.name || s.id).slice(0, MAX_NAME),
      color: PALETTE.includes(s.color) ? s.color : pickColor(out) });
  }
  return out;
}

/** @param {string} n */
export function storeByName(n) { return stores.find(s => s.name === n); }
/** Position in the visitor's list (unknown names last). @param {string} n */
export function storeIndex(n) {
  const i = stores.findIndex(s => s.name === n);
  return i < 0 ? 99 : i;
}
/** @param {string} n */
function storeColor(n) { const s = storeByName(n); return s ? s.color : null; }
/** CSS class for a palette colour: sc0..sc9 (defined in app.css), scx for
 *  anything else. Classes instead of style attributes keep the CSP strict. */
/** @param {string|null} color */
export function colorClass(color) {
  const i = color ? PALETTE.indexOf(color) : -1;
  return i >= 0 ? `sc${i}` : 'scx';
}
/** Colour class for a store by name (badges, tags, group headings). */
/** @param {string} n */
export function storeClass(n) { return colorClass(storeColor(n)); }
/** Colour class for a store by id (scx for a store that is no longer followed). @param {number} id */
export function storeIdClass(id) {
  const s = stores.find(x => x.id === id);
  return colorClass(s ? s.color : null);
}
/** Stores to request bargains for. */
export function storeRefs() { return stores.map(s => ({ id: s.id, name: s.name })); }

// Store names label the cards, so keep them unique.
/** @param {unknown} base @param {number} id */
function uniqueName(base, id) {
  const name = String(base || id).trim().slice(0, MAX_NAME) || String(id);
  /** @param {string} n */
  const taken = n => stores.some(s => s.name === n && s.id !== id);
  return taken(name) ? `${name} (${id})` : name;
}

/** @param {boolean} reload */
function changed(reload) {
  storage.setJSON(KEYS.stores, stores);
  emit(EV.stores, { reload });
}

/** @param {number} id @param {string} newName */
export function renameStore(id, newName) {
  const s = stores.find(x => x.id === id);
  if (!s) return;
  s.name = uniqueName(newName, id);
  changed(false);       // loaded bargains are relabelled, not fetched again
}

/** @param {number} id */
export function removeStore(id) {
  stores = stores.filter(s => s.id !== id);
  changed(false);
}

/** @param {any} st a store-search result */
export function addStore(st) {
  const id = Number(st.id);
  if (!id || stores.some(s => s.id === id)) return;
  if (stores.length >= config.maxStores) return;
  let label = st.street || st.name || String(id);
  if (stores.some(s => s.name === label) && st.houseNumber) label = `${label} ${st.houseNumber}`;
  stores.push({ id, name: uniqueName(label, id), color: pickColor(stores) });
  changed(true);
}

// ---- "I'm in this store" -----------------------------------------------------------
// Per browser (ah_instore, not synced: where this phone is). It survives a
// reload while shopping, but ends after 2 hours or at midnight, so the next
// visit at home shows every store again (inStoreUntil / activeInStore). The
// bargains view shows only that store's bargains, the cart only what to buy there.

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

/** The store the visitor is in now, or null. */
export function inStoreId() {
  return activeInStore(storage.getJSON(KEYS.inStore), Date.now(), stores.map(s => s.id));
}

let lastHere = /** @type {number|null} */ (null);

/** @param {number|null} id */
function setInStore(id) {
  if (id === null) storage.remove(KEYS.inStore);
  else storage.setJSON(KEYS.inStore, { id, until: inStoreUntil(new Date()) });
  lastHere = inStoreId();
  emit(EV.inStore);
}

/** Enter this store, or leave it when the visitor is in it. @param {number} id */
export const toggleInStore = id => setInStore(inStoreId() === id ? null : id);

export function initStoreList() {
  stores = loadStores();
  lastHere = inStoreId();
  // Back to the tab after a while: "I'm in this store" may have ended.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    const here = inStoreId();
    if (here !== lastHere) { lastHere = here; emit(EV.inStore); }
  });
}
