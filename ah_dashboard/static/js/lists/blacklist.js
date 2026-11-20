// @ts-check
// Hidden products ("blacklist"): products the visitor never wants to see
// again, at any store and in both views. A group deal is hidden when ALL of
// its products are; hiding it hides each one. "Show hidden products" shows
// them again (dimmed, with a restore button) without forgetting them.
// Changes are announced with EV.lists; the views re-render themselves.
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { memberKeys, productKey } from '../core/offers.js';
import { KEYS, storage } from '../core/storage.js';
import { KeyedList } from './keyed.js';

/** @typedef {import('../core/offers.js').Offer} Offer */

const hidden = new KeyedList(KEYS.blacklist);

export function hiddenCount() { return hidden.size; }

/** Hidden: the offer itself, or (a group deal) every one of its products. @param {Offer} offer */
export function isHidden(offer) {
  if (hidden.has(productKey(offer))) return true;
  const members = memberKeys(offer);
  return members.length > 0 && members.every(([key]) => hidden.has(key));
}

export function showHiddenEnabled() { return $('showHidden').checked; }

function changed() {
  hidden.save();
  emit(EV.lists);
}

/** Hide the offer; a group deal hides each of its products. @param {Offer} offer */
export function hideItem(offer) {
  const members = memberKeys(offer);
  if (!members.length) return;
  for (const [key, title] of members) hidden.add(key, title);
  changed();
}

/** Show the offer again; a group deal un-hides all of its products. @param {Offer} offer */
export function unhideItem(offer) {
  let removed = hidden.delete(productKey(offer));
  for (const [key] of memberKeys(offer)) removed = hidden.delete(key) || removed;
  if (removed) changed();
}

/** @param {string} key */
function unhideKey(key) {
  if (hidden.delete(key)) changed();
}

function clearAll() {
  const n = hidden.size;
  if (!n || !confirm(t('confirmClearHidden', n))) return;
  hidden.clear();
  changed();
}

/** The list of hidden products (liked & hidden panel), each with a restore button. */
function renderHiddenList() {
  const box = $('hiddenList');
  const n = hidden.size;
  $('hiddenCount').textContent = n ? `(${n})` : '';
  $('clearHidden').hidden = !n;
  box.innerHTML = n ? '' : html`<span class="smeta">${t('noHidden')}</span>`;
  for (const [key, title] of hidden.sorted()) {
    const chip = document.createElement('span');
    chip.className = 'hiddenchip';
    chip.innerHTML = html`<span class="hiddenname">${title}</span><button type="button" class="mini restore" title="${t('unhideProduct')}" aria-label="${t('unhideProduct')}: ${title}">↺</button>`;
    chip.querySelector('button')?.addEventListener('click', () => unhideKey(key));
    box.appendChild(chip);
  }
}

export function initBlacklist() {
  hidden.load();
  $('showHidden').checked = storage.get(KEYS.showHidden) === '1';
  $('showHidden').addEventListener('change', (/** @type {any} */ e) => {
    storage.set(KEYS.showHidden, e.target.checked ? '1' : '0');
    emit(EV.lists);
  });
  $('clearHidden').addEventListener('click', clearAll);
  on(EV.lists, renderHiddenList);
  on(EV.lang, renderHiddenList);
  renderHiddenList();
}
