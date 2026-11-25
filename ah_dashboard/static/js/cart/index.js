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
// week's Bonus with EV.needBonus when the cart has anything: to check its Bonus
// deals, and to show the Bonus deal next to a bargain whose product is also on Bonus.
// New bargains also update the cart: a product in it that is now a bargain at another
// followed store gets an entry there (lists/cart.followBargains).
// "🛒 Liked today" adds every liked bargain ending today (core/cart.likedEndingOn).
// Listens to EV.cart, EV.bargains, EV.bonusStatus, EV.stores, EV.lists, EV.lang.
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { activeCartStore, cartSections, entryStatus, indexBargains, indexBonus, likedEndingOn, todayOf } from '../core/cart.js';
import { euro, fmtDate } from '../core/format.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { config } from '../core/config.js';
import { allowedImageUrl, productUrl } from '../core/offers.js';
import { addToCart, cartEntries, clearCart, followBargains, inCart, removeFromCart, removeProduct } from '../lists/cart.js';
import { isHidden } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { myStores, storeIdClass } from '../lists/stores.js';
import { renderStoreBar } from '../ui/storebar.js';

/** @typedef {import('../core/cart.js').CartRow} CartRow */
/** @typedef {import('../core/cart.js').Latest} Latest */

/** @type {Latest} */
const latest = { bargains: null, bonus: null };
let askedBonus = false;
/** @type {Map<number, string>} this week's Bonus deals by product id (EV.bonusStatus) */
let bonusDeals = new Map();
/** @type {any[]} the latest bargains (EV.bargains), for "🛒 Liked today" */
let bargainItems = [];
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

/** The cart has items: load this week's Bonus quietly (once), to check them and tag bargains on Bonus. */
function needBonus() {
  if (askedBonus || latest.bonus || !cartEntries().length) return;
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

/** "Bonus · deal" next to a bargain's discount when this week's Bonus also has the product. @param {CartRow} r */
function bonusNote(r) {
  if (r.bonusDeal === null) return '';
  const label = r.bonusDeal ? t('alsoInBonusDeal', r.bonusDeal) : t('alsoInBonus');
  return html`<span class="bonusnote cartbonus" title="${label}"><span class="bonustag">${t('viewBonus')}</span>${r.bonusDeal ? html`<span class="bonusdeal">${r.bonusDeal}</span>` : ''}</span>`;
}

/**
 * The coloured squares of the bargain cards: per followed store where the product is a bargain now,
 * its stock (store colour; tooltip "Centrum: 3 in stock · -50%").
 * @param {CartRow} r
 */
function storeStocks(r) {
  if (!r.stores.length) return '';
  const names = new Map(myStores().map(s => [s.id, s.name]));
  return html`<span class="stocks cartstocks">${r.stores.map(s => {
    const name = names.get(s.storeId) || String(s.storeId);
    const label = t('inStock', name, s.stock) + (s.discount != null ? ` · -${s.discount}%` : '');
    return html`<span class="badge stock ${storeIdClass(s.storeId)}" title="${label}" aria-label="${label}">${s.stock}</span>`;
  })}</span>`;
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
  const left = r.stock !== null
    ? html`<span class="cartstock${r.stock <= 1 ? ' low' : ''}" title="${t('cartStockTitle')}">${t('cartStock', r.stock)}</span>` : '';
  const meta = [e.unitSize, left, noteText(r, today)].filter(Boolean);
  const li = document.createElement('li');
  li.className = 'cartrow' + (r.status === 'gone' ? ' gone' : '');
  li.innerHTML = html`
    <span class="cartthumb${img ? '' : ' noimg'}">${img ? html`<img src="${img}" alt="" loading="lazy">` : ''}</span>
    <div class="cartmain">${name}${meta.length ? html`<div class="cartmeta">${meta.map((m, i) => (i ? html` · ${m}` : m))}</div>` : ''}</div>
    <span class="cartdisc">${storeStocks(r)}${discountBadge(r)}${bonusNote(r)}</span>
    <span class="cartprices"><span class="now">${now != null ? euro(now) : ''}</span>${was != null && now != null && was > now ? html`<span class="was" title="${t('regularPrice')}">${euro(was)}</span>` : ''}</span>
    <button type="button" class="mini cartdel" title="${t('cartRemove')}" aria-label="${t('cartRemove')}: ${e.title}">✕</button>`;
  li.querySelector('.cartdel')?.addEventListener('click', () => removeProduct(e));
  const image = li.querySelector('img');
  if (image) image.addEventListener('error', () => { image.parentElement?.classList.add('noimg'); image.remove(); });
  return li;
}

/** The liked bargains ending today not in the cart yet (with the store filter: only that store's). */
function likedTodayNotInCart() {
  return likedEndingOn(bargainItems, todayOf(new Date()), { isLiked, isHidden, storeId: cartStore() })
    .filter(o => !inCart(o));
}

/** "🛒 Liked today (n)": how many it would add; disabled at 0. */
function renderLikedToday() {
  const n = likedTodayNotInCart().length;
  const b = /** @type {HTMLButtonElement} */ ($('cartLikedToday'));
  b.textContent = t('cartLikedToday', n);
  b.disabled = n === 0;
}

function renderCart() {
  renderStoreBar(CART_BAR);
  renderLikedToday();
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
    bonusTitle: t('viewBonus'), bonusDeals });
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
  // Every liked bargain ending today into the cart (EV.cart redraws; the count drops to 0).
  $('cartLikedToday').addEventListener('click', () => addToCart(likedTodayNotInCart()));
  $('cartClear').addEventListener('click', () => {
    const n = cartEntries().length;
    if (n && confirm(t('confirmClearCart', n))) clearCart();
  });
  on(EV.cart, () => { needBonus(); renderCart(); });
  on(EV.bargains, (/** @type {{items: any[], storeIds: number[]}} */ d) => {
    bargainItems = d.items || [];
    latest.bargains = indexBargains(bargainItems, d.storeIds || []);
    // A product in the cart that is now a bargain at another store too gets that store (EV.cart redraws).
    if (!followBargains(d.items || [])) renderCart();
  });
  on(EV.bonusStatus, (/** @type {{status: string, deals?: Map<number, string>, items?: any[], complete?: boolean}} */ d) => {
    if (d.status !== 'ready') return;          // loading / failed: keep what was known
    latest.bonus = indexBonus(d.items || [], d.complete === true);
    bonusDeals = d.deals instanceof Map ? d.deals : new Map();
    renderCart();
  });
  on(EV.stores, renderCart);
  on(EV.lists, renderCart);       // liking or hiding changes "🛒 Liked today"
  on(EV.lang, renderCart);
  renderCart();
  needBonus();
}
