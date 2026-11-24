// @ts-check
// The Stores panel: the saved stores (rename, remove, "I'm here"), and
// searching AH stores by postal code to add one. The list itself and "I'm in
// this store" live in lists/stores.js (the cart uses the list too), the store
// bar in ui/storebar.js; this module wires the bar's ＋ part to this panel.
import { api, errorText } from '../core/api.js';
import { $, html, makeFoldable } from '../core/dom.js';
import { on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { addStore, colorClass, inStoreId, initStoreList, MAX_NAME, myStores, removeStore, renameStore,
  toggleInStore } from '../lists/stores.js';
import { initStoreBar } from '../ui/storebar.js';
import { currentView, setView } from '../ui/view.js';

/** @type {(folded: boolean) => void} */
let setStoresFolded = () => {};
/** @type {any[]} latest store-search results */
let lastResults = [];

// ---- rendering ------------------------------------------------------------
function renderMyStores() {
  const here = inStoreId();
  const stores = myStores();
  $('storeCount').textContent = stores.length
    ? '· ' + stores.map(s => s.name).join(', ')
    : t('storeCountNone');

  const box = $('myStores');
  box.innerHTML = stores.length ? '' : html`<span class="smeta">${t('noStoresSaved')}</span>`;
  for (const s of stores) {
    const row = document.createElement('div');
    row.className = 'mystore';
    row.title = t('storeNum', s.id);
    row.innerHTML = html`<span class="dot ${colorClass(s.color)}"></span><input type="text" maxlength="${MAX_NAME}" value="${s.name}" aria-label="${t('nameForStore', s.id)}"><span class="smeta">#${s.id}</span><button type="button" class="mini shere" aria-pressed="${String(s.id === here)}" title="${t('imHereTitle', s.name)}">${t('imHere')}</button><button type="button" class="mini sdel" title="${t('removeStore')}">✕</button>`;
    const input = /** @type {HTMLInputElement} */ (row.querySelector('input'));
    input.addEventListener('change', () => renameStore(s.id, input.value));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
    row.querySelector('.sdel')?.addEventListener('click', () => removeStore(s.id));
    row.querySelector('.shere')?.addEventListener('click', () => toggleInStore(s.id));
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
    const have = myStores().some(s => s.id === Number(st.id));
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

/** Go to the Stores panel's postal-code search (the store bars' ＋; from the cart, switch to this view first). */
export function openStoreSearch() {
  if (currentView() !== 'bargains') setView('bargains');
  openStoresPanel();
  $('storeQuery').focus({ preventScroll: true });
  $('storePanel').scrollIntoView({ block: 'nearest' });
}

export function initStores() {
  initStoreList();
  initStoreBar();
  setStoresFolded = makeFoldable($('storePanel'), $('storeToggle'),
    folded => storage.set(KEYS.storesFolded, folded ? '1' : '0'));
  // ＋ at the end of the store bar: open the Stores panel at the postal-code search.
  $('storeManageBtn').addEventListener('click', openStoreSearch);

  $('storeSearchBtn').addEventListener('click', searchStores);
  $('storeClearBtn').addEventListener('click', clearSearch);
  $('storeQuery').addEventListener('input', updateClearButton);
  $('storeQuery').addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => { if (e.key === 'Enter') searchStores(); });
  on(EV.lang, renderMyStores);
  on(EV.stores, renderMyStores);
  on(EV.inStore, renderMyStores);

  renderMyStores();
  // Open when there are no stores yet (first visit), else as the user left it.
  setStoresFolded(myStores().length > 0 && storage.get(KEYS.storesFolded) !== '0');
}
