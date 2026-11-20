// @ts-check
// Liked products (thumbs up). In every sort order, a liked product is shown
// before an unliked one once all of that order's own criteria are equal (e.g.
// the same discount). A group deal counts as liked when ANY of its products is
// liked; liking it likes every product. The liked & hidden panel lists them.
// Changes are announced with EV.lists; the views re-render themselves.
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { memberKeys, productKey } from '../core/offers.js';
import { KEYS } from '../core/storage.js';
import { KeyedList } from './keyed.js';

/** @typedef {import('../core/offers.js').Offer} Offer */

const liked = new KeyedList(KEYS.liked);

export function likedCount() { return liked.size; }

/**
 * Liked: the offer itself, or (a group deal) any one of its products, e.g. a
 * pizza liked among the bargains makes "Alle AH Verse pizza's" liked.
 * @param {Offer} offer
 */
export function isLiked(offer) {
  if (liked.has(productKey(offer))) return true;
  return memberKeys(offer).some(([key]) => liked.has(key));
}

/** Like the offer, or unlike it when it is liked. @param {Offer} offer */
export function toggleLike(offer) {
  const members = memberKeys(offer);
  if (!members.length) return;
  if (isLiked(offer)) {
    liked.delete(productKey(offer));
    for (const [key] of members) liked.delete(key);
  } else {
    for (const [key, title] of members) liked.add(key, title);
  }
  changed();
}

function changed() {
  liked.save();
  emit(EV.lists);
}

/** @param {string} key */
function unlikeKey(key) {
  if (liked.delete(key)) changed();
}

function clearAll() {
  const n = liked.size;
  if (!n || !confirm(t('confirmClearLiked', n))) return;
  liked.clear();
  changed();
}

/** "Liked products (N)": one chip per product, each with a remove button.
 *  Names come from AH data and go through the html tag. */
function renderLikedList() {
  const box = $('likedList');
  const n = liked.size;
  $('likedCount').textContent = n ? `(${n})` : '';
  $('clearLiked').hidden = !n;
  box.innerHTML = n ? '' : html`<span class="smeta">${t('noLiked')}</span>`;
  for (const [key, title] of liked.sorted()) {
    const chip = document.createElement('span');
    chip.className = 'hiddenchip';
    chip.innerHTML = html`<span class="hiddenname">${title}</span><button type="button" class="mini restore" title="${t('unlikeProduct')}" aria-label="${t('unlikeProduct')}: ${title}">✕</button>`;
    chip.querySelector('button')?.addEventListener('click', () => unlikeKey(key));
    box.appendChild(chip);
  }
}

export function initLikes() {
  liked.load();
  $('clearLiked').addEventListener('click', clearAll);
  on(EV.lists, renderLikedList);
  on(EV.lang, renderLikedList);
  renderLikedList();
}
