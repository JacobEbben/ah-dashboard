// @ts-check
// Drawing the bargains: grouped sections of product cards and the summary
// line. Gets everything it shows as arguments (the controller decides what).
import { $, html } from '../core/dom.js';
import { euro, fmtDate, parseDay, sameDay } from '../core/format.js';
import { t } from '../core/i18n.js';
import { safeImageUrl } from '../core/offers.js';
import { unitPrice } from '../core/unit.js';
import { isHidden } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { cardActions, gridCols, linkedThumb, metaLine, placeSections, section, wireCard } from '../ui/cards.js';
import { catKey, groupKeyFn, isLeavingAssortment, isSpecial, mergeAcrossStores, sortGroupKeys, sorter } from './model.js';
import { storeByName, storeClass, storeIndex } from './stores.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {import('./model.js').Card} Card */
/**
 * @typedef {object} RenderOptions
 * @property {string} groupBy
 * @property {string} sortBy
 * @property {Map<number, string>} bonusDeals product id -> Bonus deal text
 * @property {number} [hiddenCount]
 * @property {boolean} [focused] showing an overview selection
 * @property {number} [otherStores] "I'm in this store": matching bargains at the other stores
 */

export function clearResults() {
  $('results').innerHTML = '';
  $('summary').textContent = '';
}

export function currentCols() { return gridCols($('results')); }

/**
 * @param {Offer[]} offers
 * @param {RenderOptions} o
 * @returns {number} the column count used
 */
export function render(offers, o) {
  const resultsEl = $('results');
  const summaryEl = $('summary');
  // "… 3 stores · 2 hidden." : notes go before the sentence's full stop.
  const notes = [
    ...(o.hiddenCount ? [t('hiddenCount', o.hiddenCount)] : []),
    ...(o.otherStores ? [t('otherStores', o.otherStores)] : []),
  ];
  /** @param {string} text */
  const withNotes = text => (notes.length ? text.replace(/\.$/, '') + ' · ' + notes.join(' · ') + '.' : text);
  resultsEl.innerHTML = '';
  const cols = currentCols();
  if (!offers.length) {
    summaryEl.textContent = o.focused ? t('focusNone') : withNotes(t('noMatch'));
    return cols;
  }

  const cards = mergeAcrossStores(offers, o.groupBy, storeIndex);
  // Discount and store groups must keep their order, so a later small group
  // may not jump ahead into free columns (packSections "dense") in those modes.
  const dense = o.groupBy !== 'disc' && o.groupBy !== 'store';

  const keyOf = groupKeyFn(o.groupBy, { noDiscount: t('noDiscount'), special: t('specialTag'), all: t('allBargains') });
  // A Map, not a plain object: group keys come from AH data and store names,
  // and names like "constructor" or "__proto__" would hit Object.prototype.
  /** @type {Map<string, Card[]>} */
  const groups = new Map();
  for (const c of cards) {
    const key = keyOf(c);
    const list = groups.get(key);
    if (list) list.push(c); else groups.set(key, [c]);
  }
  const cmp = sorter(o.sortBy, isLiked);
  for (const list of groups.values()) list.sort(cmp);
  const keys = sortGroupKeys([...groups.keys()], o.groupBy, storeIndex);

  const nCats = new Set(cards.map(catKey)).size;
  const nStores = new Set(cards.flatMap(c => c.stores.map(s => s.name))).size;
  summaryEl.textContent = withNotes(t('summary', cards.length, nCats, nStores));

  // Small groups share a row, also the free end of a bigger group's last row.
  placeSections(resultsEl, keys.map(key => {
    const list = groups.get(key) || [];
    // Store groups use the store's colour; otherwise the theme's text colour.
    const headClass = o.groupBy === 'store' && storeByName(key) ? ` storecolor ${storeClass(key)}` : '';
    return section(key, headClass, list.map(c => card(c, o.bonusDeals)));
  }), cols, dense);
  return cols;
}

function removedBadge() {
  const label = t('leaving');
  return html`<span class="removed" title="${label}" aria-label="${label}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></span>`;
}

/**
 * "Bonus · deal" note when this week's Bonus also has the product.
 * @param {Card} c
 * @param {Map<number, string>} deals
 */
function bonusNote(c, deals) {
  const id = Number(c.id);
  if (!id || !deals.has(id)) return '';
  const deal = deals.get(id);
  const label = deal ? t('alsoInBonusDeal', deal) : t('alsoInBonus');
  return html`<div class="bonusnote" title="${label}"><span class="bonustag">${t('viewBonus')}</span>${deal ? html`<span class="bonusdeal">${deal}</span>` : ''}</div>`;
}

/**
 * @param {Card} c
 * @param {Map<number, string>} deals
 */
function card(c, deals) {
  const img = safeImageUrl(c.image);

  // Discount + per-store stock badges, overlaid on the bottom of the image.
  // The special product (0% discount, model.isSpecial) says "Special" instead of "-0%".
  const discount = isSpecial(c) ? html`<span class="badge disc special">${t('specialTag')}</span>`
    : c.discount != null ? html`<span class="badge disc">-${c.discount}%</span>` : html`<span></span>`;
  const stockBadges = c.stores.filter(s => s.stock != null).map(s =>
    html`<span class="badge stock ${storeClass(s.name)}" title="${t('inStock', s.name, s.stock)}">${s.stock}</span>`);
  const storeTags = c.stores.filter(s => s.name).map(s =>
    html`<span class="store ${storeClass(s.name)}">${s.name}</span>`);

  let expiry = '';
  if (c.expires) {
    const d = parseDay(c.expires);
    expiry = d && sameDay(d, new Date())
      ? html`<span class="exp today">${t('today')}</span>`
      : html`<span class="exp">${fmtDate(c.expires)}</span>`;
  }

  const thumb = html`
    <div class="thumbwrap${img ? '' : ' noimg'}">
      <div class="storetags">${storeTags}</div>
      ${isLeavingAssortment(c) ? removedBadge() : ''}
      ${img ? html`<img class="thumb" src="${img}" alt="" loading="lazy">` : ''}
      <div class="badges">${discount}${stockBadges.length ? html`<span class="stocks">${stockBadges}</span>` : ''}</div>
    </div>`;
  const title = c.title || '(untitled)';

  // Like and hide buttons; a hidden product shown via "Show hidden products"
  // is dimmed and gets a restore button instead.
  const el = document.createElement('div');
  el.className = isHidden(c) ? 'card ishidden' : 'card';
  el.innerHTML = html`
    ${linkedThumb(c, thumb)}
    <div class="titlerow">
      <div class="title">${title}</div>
      ${cardActions(c, title)}
    </div>
    ${metaLine(c.unitSize || '', unitPrice(c.priceNow, c.unitSize))}
    ${bonusNote(c, deals)}
    <div class="prices">
      <span class="now">${c.priceNow != null ? euro(c.priceNow) : ''}</span>
      ${c.priceWas != null && c.priceNow != null && c.priceWas > c.priceNow ? html`<span class="was" title="${t('regularPrice')}">${euro(c.priceWas)}</span>` : ''}
      ${expiry}
    </div>`;
  wireCard(el, c);
  return el;
}
