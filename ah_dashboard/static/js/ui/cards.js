// @ts-check
// Parts shared by the product cards of both views: the cart / like / hide buttons,
// the unit-price line, the link to ah.nl and the broken-image fallback.
import { html } from '../core/dom.js';
import { euro } from '../core/format.js';
import { t } from '../core/i18n.js';
import { packSections } from '../core/layout.js';
import { groupSize, productUrl } from '../core/offers.js';
import { unitPrice } from '../core/unit.js';
import { hideItem, isHidden, unhideItem } from '../lists/blacklist.js';
import { inCart, toggleCart } from '../lists/cart.js';
import { isLiked, toggleLike } from '../lists/likes.js';

/** @typedef {import('../core/offers.js').Offer} Offer */

// Shopping-cart icon (static markup).
const CART_ICON = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.6 12.2a2 2 0 0 0 2 1.6h8.2a2 2 0 0 0 2-1.5L21.5 8H6"/></svg>`;

// Thumbs-up icon (static markup).
export const THUMB_ICON = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v11"/><path d="M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 17.5 21H7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h2.8a2 2 0 0 0 1.8-1.1L15 2a3.1 3.1 0 0 1 0 3.9Z"/></svg>`;

/** "€ 2.18 / kg" for a unit price from unit.js. @param {{value: number, basis: string}} up */
export function unitPriceText(up) {
  return `${euro(up.value)} ${t('per_' + up.basis)}`;
}

/**
 * Meta line: ``text`` (e.g. pack size, brand) plus, when known, the unit price.
 * @param {string} text
 * @param {{value: number, basis: string}|null} up
 */
export function metaLine(text, up) {
  if (!text && !up) return '';
  return html`<div class="unit">${text || ''}${up ? html`${text ? ' · ' : ''}<span class="unitprice" title="${t('unitPriceTitle')}">${unitPriceText(up)}</span>` : ''}</div>`;
}

/**
 * Unit price of an offer: not for group deals (their products differ in size).
 * @param {Offer} offer
 */
export function offerUnitPrice(offer) {
  return offer.isGroup ? null : unitPrice(offer.priceNow, offer.unitSize);
}

/**
 * The image area, linked to the product page on ah.nl (new tab) when the
 * offer has a product id.
 * @param {Offer} offer
 * @param {any} thumb markup of the image area
 */
export function linkedThumb(offer, thumb) {
  const url = productUrl(offer.id);
  const title = offer.title || '';
  return url
    ? html`<a class="thumblink" href="${url}" target="_blank" rel="noopener noreferrer" title="${t('viewOnAh', title)}" aria-label="${t('viewOnAh', title)}">${thumb}</a>`
    : thumb;
}

/**
 * Cart + like + hide buttons for a card's title row.
 * @param {Offer} offer
 * @param {string} title
 */
export function cardActions(offer, title) {
  const hidden = isHidden(offer), liked = isLiked(offer), carted = inCart(offer);
  const cartLabel = carted ? t('cartRemove') : t('cartAdd');
  // A group deal acts on each of its products: say so.
  const n = groupSize(offer);
  const toggleLabel = n ? (hidden ? t('unhideGroup', n) : t('hideGroup', n)) : (hidden ? t('unhideProduct') : t('hideProduct'));
  const likeLabel = n ? (liked ? t('unlikeGroup', n) : t('likeGroup', n)) : (liked ? t('unlikeProduct') : t('likeProduct'));
  return html`<button type="button" class="cartbtn${carted ? ' on' : ''}" title="${cartLabel}" aria-label="${cartLabel}: ${title}" aria-pressed="${carted ? 'true' : 'false'}">${CART_ICON}</button>
      <button type="button" class="likebtn${liked ? ' on' : ''}" title="${likeLabel}" aria-label="${likeLabel}: ${title}" aria-pressed="${liked ? 'true' : 'false'}">${THUMB_ICON}</button>
      <button type="button" class="hidebtn" title="${toggleLabel}" aria-label="${toggleLabel}: ${title}">${hidden ? '↺' : '✕'}</button>`;
}

/**
 * Hook up a rendered card: the buttons made by cardActions(), and a broken
 * image switching the card to the no-image layout (no inline onerror: the
 * CSP blocks it).
 * @param {HTMLElement} el
 * @param {Offer} offer
 * @param {{start: string|null, end: string|null}|null} [week] the Bonus week the card shows (kept in the cart)
 */
export function wireCard(el, offer, week = null) {
  const hidden = isHidden(offer);
  el.querySelector('.cartbtn')?.addEventListener('click', () => toggleCart(offer, week));
  el.querySelector('.likebtn')?.addEventListener('click', () => toggleLike(offer));
  el.querySelector('.hidebtn')?.addEventListener('click', () => {
    if (hidden) unhideItem(offer); else hideItem(offer);
  });
  const img = el.querySelector('img.thumb');
  if (img) img.addEventListener('error', () => {
    img.closest('.thumbwrap')?.classList.add('noimg');
    img.remove();
  });
}

/**
 * A titled section of cards; put it into a results grid with placeSections().
 * @param {string} title
 * @param {any} titleClass extra classes for the title (e.g. a store colour)
 * @param {HTMLElement[]} cards
 */
export function section(title, titleClass, cards) {
  const g = document.createElement('div');
  g.className = 'group';
  const h = document.createElement('h2');
  h.innerHTML = html`<span class="grouptitle${titleClass}">${title}</span><span>${t('items', cards.length)}</span>`;
  g.appendChild(h);
  for (const c of cards) g.appendChild(c);
  return g;
}

/**
 * Put sections from section() into a results grid, packed by
 * core/layout.packSections: a small section may start in the free columns at
 * the end of the previous one's last row. Grid lines: band b is a title row
 * (2b+1) and a card row (2b+2); a title row without a title has no height.
 * A section's box covers whole bands, so the next section may lie over the
 * free end of its last row (it comes later in the DOM, so it is on top).
 * @param {HTMLElement} grid
 * @param {HTMLElement[]} sections
 * @param {number} cols columns of the grid
 * @param {boolean} dense see packSections
 */
export function placeSections(grid, sections, cols, dense) {
  const cardsOf = (/** @type {HTMLElement} */ g) => /** @type {HTMLElement[]} */ ([...g.children].slice(1));   // after the h2
  const spots = packSections(sections.map(g => cardsOf(g).length), cols, dense);
  sections.forEach((g, i) => {
    const p = spots[i];
    g.style.gridColumn = `${p.col + 1} / span ${p.width}`;
    g.style.gridRow = `${2 * p.band + 1} / span ${2 * p.bands}`;
    cardsOf(g).forEach((c, j) => { c.style.gridRow = String(2 * Math.floor(j / p.width) + 2); });
    grid.appendChild(g);
  });
}

/** Columns of a results grid (CSS --cols; density.js may set it). @param {HTMLElement} grid */
export function gridCols(grid) {
  return parseInt(getComputedStyle(grid).getPropertyValue('--cols'), 10) || 4;
}
