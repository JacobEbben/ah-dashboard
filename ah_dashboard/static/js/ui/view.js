// @ts-check
// Switch between the two views: "bargains" (last-chance bargains per store)
// and "bonus" (this week's national Bonus). Remembered per browser.
import { $ } from '../core/dom.js';
import { storage, KEYS } from '../core/storage.js';

const VIEWS = ['bargains', 'bonus'];
let current = 'bargains';
/** @type {Record<string, () => void>} */
let onShow = {};

export function currentView() { return current; }

/** @param {string} view */
export function setView(view) {
  current = VIEWS.includes(view) ? view : 'bargains';
  $('bargainsView').hidden = current !== 'bargains';
  $('bonusView').hidden = current !== 'bonus';
  document.documentElement.dataset.view = current;
  // One "Liked & hidden products" panel, shown in the current view's panel row.
  const row = $(current === 'bonus' ? 'bonusPanelRow' : 'bargainsPanelRow');
  const lists = $('listsPanel');
  if (row && lists && lists.parentElement !== row) row.appendChild(lists);
  document.querySelectorAll('.viewswitch .viewbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = b.dataset.view === current;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  storage.set(KEYS.view, current);
  if (onShow[current]) onShow[current]();
}

/**
 * @param {Record<string, () => void>} handlers called whenever that view is shown ({bonus: fn})
 */
export function initViewSwitch(handlers = {}) {
  onShow = handlers;
  document.querySelectorAll('.viewswitch .viewbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    b.addEventListener('click', () => { if (b.dataset.view !== current) setView(b.dataset.view || 'bargains'); });
  });
  setView(storage.get(KEYS.view) || 'bargains');
}
