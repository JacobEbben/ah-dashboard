// @ts-check
// The cart view's controller: one row per bargain or Bonus deal in the cart,
// with its discount and price, in sections per store (the visitor's store
// order) and the Bonus last. What is gone from the latest results is greyed
// out (core/cart.entryStatus). Its own store bar (#cartStoreChips, drawn by
// ui/storebar.js like the bargains view's) filters it: with a store selected,
// only that store's bargains and the Bonus (national) are shown. The selection
// is the cart's own (ah_cart_store, per browser), not "I'm in this store".
//
// It knows the latest results only from events: EV.bargains (the bargains
// controller) and EV.bonusStatus (the Bonus controller); it asks for this
// week's Bonus with EV.needBonus when the cart has Bonus deals.
// Listens to EV.cart, EV.bargains, EV.bonusStatus, EV.stores, EV.lang.
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { activeCartStore, cartSections, entryStatus, indexBargains, indexBonus, todayOf } from '../core/cart.js';
import { euro, fmtDate } from '../core/format.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { config } from '../core/config.js';
import { allowedImageUrl, productUrl } from '../core/offers.js';
import { cartEntries, cartHasBonus, clearCart, removeFromCart } from '../lists/cart.js';
import { myStores, storeIdClass } from '../lists/stores.js';
import { renderStoreBar } from '../ui/storebar.js';

/** @typedef {import('../core/cart.js').CartRow} CartRow */
/** @typedef {import('../core/cart.js').Latest} Latest */

/** @type {Latest} */
const latest = { bargains: null, bonus: null };
let askedBonus = false;
/** @type {string[]} keys of the entries that are gone ("Remove unavailable") */
let goneKeys = [];

// ---- the store filter (the cart's own) ---------------------------------------------------
/** The store whose items are shown, or null for all stores. */
function cartStore() {
  return activeCartStore(storage.get(KEYS.cartStore), myStores().map(s => s.id));
}

/** Show only this store's items, or all stores again when it is selected. @param {number} id */
function toggleCartStore(id) {
  if (cartStore() === id) storage.remove(KEYS.cartStore);
  else storage.set(KEYS.cartStore, String(id));
  renderCart();
}

/** @type {import('../ui/storebar.js').StoreBar} */
const CART_BAR = {
  boxId: 'cartStoreChips', selected: cartStore, toggle: toggleCartStore,
  selectTitle: name => t('cartStoreTitle', name), backTitle: 'cartStoreBackTitle',
};

/** The cart has Bonus deals: load this week's Bonus quietly (once), to check them. */
function needBonus() {
  if (askedBonus || latest.bonus || !cartHasBonus()) return;
  askedBonus = true;
  emit(EV.needBonus);
}

/** The discount as a badge: "-35%", "Special" (the 0% bargain), or the Bonus deal text. @param {CartRow} r */
function discountBadge(r) {
  const o = r.current || r.entry;
  if (r.entry.kind === 'bonus') {
    const mech = o.mechanism || r.entry.mechanism;
    return mech ? html`<span class="badge disc mech">${mech}</span>` : '';
  }
  const d = o.discount ?? r.entry.discount;
  if (d == null) return '';
  return Number(d) === 0 ? html`<span class="badge disc special">${t('specialTag')}</span>`
    : html`<span class="badge disc">-${d}%</span>`;
}

/** Why / until when, under the name. @param {CartRow} r @param {string} today */
function noteText(r, today) {
  const e = r.entry;
  if (r.status === 'gone') return html`<span class="cartgone">${t('cartGone')}</span>`;
  if (r.status === 'upcoming' && e.from) return html`<span>${t('cartFrom', fmtDate(e.from))}</span>`;
  const end = e.kind === 'bargain' ? (r.current && r.current.expires) || e.expires : e.until;
  if (!end) return '';
  return String(end).slice(0, 10) === today
    ? html`<span class="exp today">${t('today')}</span>`
    : html`<span>${t('cartUntil', fmtDate(end))}</span>`;
}

/** @param {CartRow} r @param {string} today */
function row(r, today) {
  const e = r.entry;
  const o = r.current || e;
  const img = allowedImageUrl(o.image || e.image, config.imageHosts);   // only the AH image host(s)
  const url = productUrl(e.id);
  const name = url
    ? html`<a class="cartname" href="${url}" target="_blank" rel="noopener noreferrer" title="${t('viewOnAh', e.title)}">${e.title}</a>`
    : html`<span class="cartname">${e.title}</span>`;
  const now = o.priceNow ?? e.priceNow, was = o.priceWas ?? e.priceWas;
  const meta = [e.unitSize, noteText(r, today)].filter(Boolean);
  const li = document.createElement('li');
  li.className = 'cartrow' + (r.status === 'gone' ? ' gone' : '');
  li.innerHTML = html`
    <span class="cartthumb${img ? '' : ' noimg'}">${img ? html`<img src="${img}" alt="" loading="lazy">` : ''}</span>
    <div class="cartmain">${name}${meta.length ? html`<div class="cartmeta">${meta.map((m, i) => (i ? html` · ${m}` : m))}</div>` : ''}</div>
    <span class="cartdisc">${discountBadge(r)}</span>
    <span class="cartprices"><span class="now">${now != null ? euro(now) : ''}</span>${was != null && now != null && was > now ? html`<span class="was" title="${t('regularPrice')}">${euro(was)}</span>` : ''}</span>
    <button type="button" class="mini cartdel" title="${t('cartRemove')}" aria-label="${t('cartRemove')}: ${e.title}">✕</button>`;
  li.querySelector('.cartdel')?.addEventListener('click', () => removeFromCart([e.key]));
  const image = li.querySelector('img');
  if (image) image.addEventListener('error', () => { image.parentElement?.classList.add('noimg'); image.remove(); });
  return li;
}

function renderCart() {
  renderStoreBar(CART_BAR);
  const entries = cartEntries();
  const count = $('cartCount');
  count.textContent = entries.length ? String(entries.length) : '';
  count.hidden = !entries.length;

  const today = todayOf(new Date());
  goneKeys = entries.filter(e => entryStatus(e, latest, today).status === 'gone').map(e => e.key);
  /** @type {HTMLButtonElement} */ ($('cartClearGone')).disabled = !goneKeys.length;
  /** @type {HTMLButtonElement} */ ($('cartClear')).disabled = !entries.length;

  const list = $('cartList');
  const summary = $('cartSummary');
  list.innerHTML = '';
  if (!entries.length) {
    summary.textContent = '';
    list.innerHTML = html`<p class="cartempty">${t('cartEmpty')}</p>`;
    return;
  }
  const stores = myStores();
  const res = cartSections(entries, latest, { today, storeId: cartStore(), storeOrder: stores.map(s => s.id),
    bonusTitle: t('viewBonus') });
  const notes = [
    ...(res.gone ? [t('cartGoneCount', res.gone)] : []),
    ...(res.otherStores ? [t('cartOtherStores', res.otherStores)] : []),
  ];
  summary.textContent = [res.shown ? t('cartSummary', res.shown) : t('cartNoneHere'), ...notes].join(' · ');

  for (const sec of res.sections) {
    const box = document.createElement('section');
    box.className = 'cartsec';
    const followed = sec.storeId !== null ? stores.find(s => s.id === sec.storeId) : null;
    const title = followed ? followed.name : sec.storeName;
    const dot = sec.storeId !== null ? html`<span class="dot ${storeIdClass(sec.storeId)}"></span>` : '';
    box.innerHTML = html`<h2 class="carthead">${dot}<span class="grouptitle">${title}</span><span class="smeta">${t('items', sec.rows.length)}</span></h2>`;
    const ul = document.createElement('ul');
    ul.className = 'cartrows';
    for (const r of sec.rows) ul.appendChild(row(r, today));
    box.appendChild(ul);
    list.appendChild(box);
  }
}

/** Call after lists/cart.initCartList() and the bargains (the store list). */
export function initCart() {
  $('cartClearGone').addEventListener('click', () => removeFromCart(goneKeys));
  $('cartClear').addEventListener('click', () => {
    const n = cartEntries().length;
    if (n && confirm(t('confirmClearCart', n))) clearCart();
  });
  on(EV.cart, () => { needBonus(); renderCart(); });
  on(EV.bargains, (/** @type {{items: any[], storeIds: number[]}} */ d) => {
    latest.bargains = indexBargains(d.items || [], d.storeIds || []);
    renderCart();
  });
  on(EV.bonusStatus, (/** @type {{status: string, items?: any[], complete?: boolean}} */ d) => {
    if (d.status !== 'ready') return;          // loading / failed: keep what was known
    latest.bonus = indexBonus(d.items || [], d.complete === true);
    renderCart();
  });
  on(EV.stores, renderCart);
  on(EV.lang, renderCart);
  renderCart();
  needBonus();
}
