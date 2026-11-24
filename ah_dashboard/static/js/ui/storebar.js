// @ts-check
// A store bar: the followed stores as one segmented bar (a colour dot each).
// Tapping one selects it (highlighted, with a ✕) until tapped again. Two bars
// use it, each with its own state: the bargains view's (#storeChips: "I'm in
// this store", lists/stores.js) and the cart's (#cartStoreChips: which
// store's items to show, cart/index.js). Their ＋ parts are wired by
// bargains/stores.js and main.js (they open the Stores panel).
import { $, html } from '../core/dom.js';
import { on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { colorClass, inStoreId, myStores, toggleInStore } from '../lists/stores.js';

/**
 * @typedef {object} StoreBar
 * @property {string} boxId the element that holds the store parts
 * @property {() => number|null} selected the selected store's id, or null
 * @property {(id: number) => void} toggle select this store, or unselect it when selected
 * @property {(name: string) => string} selectTitle tooltip of an unselected store
 * @property {string} backTitle tooltip of the selected store
 */

/** One part per store; the selected one is highlighted, with a ✕. @param {StoreBar} bar */
export function renderStoreBar(bar) {
  const here = bar.selected();
  const stores = myStores();
  const box = $(bar.boxId);
  box.innerHTML = '';
  for (const s of stores) {
    const b = document.createElement('button');
    b.type = 'button';
    const active = s.id === here;
    b.className = 'segbtn storechip' + (active ? ' active' : '');
    b.setAttribute('aria-pressed', String(active));
    b.title = active ? t(bar.backTitle) : bar.selectTitle(s.name);
    b.innerHTML = active
      ? html`<span class="dot ${colorClass(s.color)}"></span><span class="storechipname">${s.name}</span><span class="storechipx" aria-hidden="true">✕</span>`
      : html`<span class="dot ${colorClass(s.color)}"></span><span class="storechipname">${s.name}</span>`;
    b.addEventListener('click', () => {
      bar.toggle(s.id);
      // The bar was rebuilt: keep the keyboard focus on this store.
      const again = box.querySelectorAll('.storechip')[stores.indexOf(s)];
      if (again) /** @type {HTMLElement} */ (again).focus({ preventScroll: true });
    });
    box.appendChild(b);
  }
}

/** The bargains view's bar: "I'm in this store". */
const IN_STORE_BAR = /** @type {StoreBar} */ ({
  boxId: 'storeChips', selected: inStoreId, toggle: toggleInStore,
  selectTitle: name => t('imHereTitle', name), backTitle: 'inStoreBackTitle',
});

export function initStoreBar() {
  const render = () => renderStoreBar(IN_STORE_BAR);
  on(EV.stores, render);
  on(EV.inStore, render);
  on(EV.lang, render);
  render();
}
