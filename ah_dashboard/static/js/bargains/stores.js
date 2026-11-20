// @ts-check
// "My stores": the saved store list (with a colour per store), the Stores
// panel, and searching AH stores by postal code. This module owns the list;
// changes are announced with EV.stores ({reload: true} when a store was added),
// and the bargains controller reloads or re-renders.
import { api, errorText } from '../core/api.js';
import { config } from '../core/config.js';
import { $, html, makeFoldable } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';

/** @typedef {{id: number, name: string, color: string}} MyStore */

// Distinct colours that don't clash with the red discount badge.
const PALETTE = ['#2f6fb5', '#d27a1f', '#7a4bb0', '#2f8a5a', '#b5179e',
                 '#00838f', '#8d6e2f', '#3f51b5', '#e0567a', '#546e7a'];
const MAX_NAME = 30;

/** @type {(folded: boolean) => void} */
let setStoresFolded = () => {};
/** @type {MyStore[]} */
let stores = [];
/** @type {any[]} latest store-search results */
let lastResults = [];

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

function saveStores() { storage.setJSON(KEYS.stores, stores); }

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
  saveStores();
  renderMyStores();
  emit(EV.stores, { reload });
}

/** @param {number} id @param {string} newName */
function renameStore(id, newName) {
  const s = stores.find(x => x.id === id);
  if (!s) return;
  s.name = uniqueName(newName, id);
  changed(false);       // loaded bargains are relabelled, not fetched again
}

/** @param {number} id */
function removeStore(id) {
  stores = stores.filter(s => s.id !== id);
  changed(false);
}

/** @param {any} st a store-search result */
function addStore(st) {
  const id = Number(st.id);
  if (!id || stores.some(s => s.id === id)) return;
  if (stores.length >= config.maxStores) return;
  let label = st.street || st.name || String(id);
  if (stores.some(s => s.name === label) && st.houseNumber) label = `${label} ${st.houseNumber}`;
  stores.push({ id, name: uniqueName(label, id), color: pickColor(stores) });
  changed(true);
}

// ---- rendering ------------------------------------------------------------
function renderMyStores() {
  $('storeCount').textContent = stores.length
    ? '· ' + stores.map(s => s.name).join(', ')
    : t('storeCountNone');

  const box = $('myStores');
  box.innerHTML = stores.length ? '' : html`<span class="smeta">${t('noStoresSaved')}</span>`;
  for (const s of stores) {
    const row = document.createElement('div');
    row.className = 'mystore';
    row.title = t('storeNum', s.id);
    row.innerHTML = html`<span class="dot ${colorClass(s.color)}"></span><input type="text" maxlength="${MAX_NAME}" value="${s.name}" aria-label="${t('nameForStore', s.id)}"><span class="smeta">#${s.id}</span><button type="button" class="mini sdel" title="${t('removeStore')}">✕</button>`;
    const input = /** @type {HTMLInputElement} */ (row.querySelector('input'));
    input.addEventListener('change', () => renameStore(s.id, input.value));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
    row.querySelector('.sdel')?.addEventListener('click', () => removeStore(s.id));
    box.appendChild(row);
  }
  renderStoreResults();
}

function updateClearButton() {
  $('storeClearBtn').hidden = !(lastResults.length ||
    $('storeQuery').value.trim() || $('storeSearchStatus').textContent);
}

function renderStoreResults() {
  const box = $('storeResults');
  box.innerHTML = '';
  updateClearButton();
  for (const st of lastResults) {
    const addr = [
      [st.street, st.houseNumber, st.houseNumberExtra].filter(Boolean).join(' '),
      [st.postalCode, st.city].filter(Boolean).join(' '),
    ].filter(Boolean).join(', ');
    const have = stores.some(s => s.id === Number(st.id));
    const row = document.createElement('div');
    row.className = 'storeres';
    const meta = st.storeType ? `#${st.id} · ${st.storeType}` : `#${st.id}`;
    row.innerHTML = html`<div class="sinfo"><b>${st.name || 'Albert Heijn'}</b> · ${addr} <span class="smeta">${meta}</span></div><button type="button" class="mini">${have ? t('added') : t('add')}</button>`;
    const button = /** @type {HTMLButtonElement} */ (row.querySelector('button'));
    button.disabled = have;
    button.addEventListener('click', () => addStore(st));
    box.appendChild(row);
  }
}

// ---- search -----------------------------------------------------------------
async function searchStores() {
  const query = $('storeQuery');
  const status = $('storeSearchStatus');
  const q = query.value.trim();
  if (!q) { query.focus(); return; }

  status.textContent = t('searching');
  lastResults = [];
  renderStoreResults();
  try {
    const data = await api.searchStores(q);
    lastResults = data.stores || [];
    status.textContent = lastResults.length
      ? t('storesFound', lastResults.length)
      : t('noStoresNear');
  } catch (err) {
    status.textContent = errorText(err);
  }
  renderStoreResults();
}

function clearSearch() {
  $('storeQuery').value = '';
  $('storeSearchStatus').textContent = '';
  lastResults = [];
  renderStoreResults();
}

/** Open the Stores panel (e.g. when the user has no stores yet). */
export function openStoresPanel() { setStoresFolded(false); }

export function initStores() {
  stores = loadStores();

  setStoresFolded = makeFoldable($('storePanel'), $('storeToggle'),
    folded => storage.set(KEYS.storesFolded, folded ? '1' : '0'));

  $('storeSearchBtn').addEventListener('click', searchStores);
  $('storeClearBtn').addEventListener('click', clearSearch);
  $('storeQuery').addEventListener('input', updateClearButton);
  $('storeQuery').addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => { if (e.key === 'Enter') searchStores(); });
  on(EV.lang, renderMyStores);

  renderMyStores();
  const folded = storage.get(KEYS.storesFolded);
  // Open when there are no stores yet (first visit), else as the user left it.
  setStoresFolded(stores.length > 0 && folded !== '0');
}
