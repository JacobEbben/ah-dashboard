// @ts-check
// The Bonus view: this week's national Bonus, like the bargains page: the
// presets bar ("All types", the saved presets, ＋) and the Filters panel pick
// the categories (filters.js); the offers of every chosen category are shown
// at once, one section per category in AH's order. The "Liked" pill next to
// the presets bar is a filter: while on, only liked products (saved with the
// categories as ah_bonus_filters {liked, cats}, synced). The search field
// searches every category at once (only liked ones while the Liked filter is
// on). Cards have the same like and hide buttons as the bargains (shared
// lists). Offers are in AH's order, liked ones first. The notice bar says when
// liked products are on Bonus this week.
//
// "This week | Next week" appears once AH has published next week
// (/api/bonus/next): the same view, with next week's offers. Next week never
// feeds the bargains' Bonus tags or the liked-products notice (this week only).
//
// Events: emits EV.bonusStatus (bargain cards' Bonus tag, "Bonus"
// filter); listens to EV.needBonus, EV.lists, EV.cols, EV.lang.
import { api, ApiError, errorText } from '../core/api.js';
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { euro, fmtDate, searchTerms, fold } from '../core/format.js';
import { t } from '../core/i18n.js';
import { productKey, productUrl, safeImageUrl } from '../core/offers.js';
import { KEYS, storage } from '../core/storage.js';
import { isHidden, showHiddenEnabled } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { cardActions, gridCols, linkedThumb, metaLine, placeSections, section, wireCard } from '../ui/cards.js';
import { currentView, setView } from '../ui/view.js';
import { buildCategoryFilter, currentCats, initFilters, setCats } from './filters.js';
import { categoryGroups, groupMemberRows, indexDeals, likedOffers, needsFetch, offerUnitPrice, parseCats, parseLikedPill,
  selectCats, sortOffers } from './model.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{period: {start: string|null, end: string|null}, categories: string[], items: Offer[],
 *             errors: {category: string, code: string, ref: string}[]}} BonusWeek */

/** @type {BonusWeek|null} last /api/bonus answer (this week) */
let data = null;
/** @type {BonusWeek|null} next week's Bonus */
let nextData = null;
/** @type {'current'|'next'} the week shown */
let week = 'current';
/** Next week's availability was asked for (once per page). */
let nextChecked = false;
/** The week shown. */
const shown = () => (week === 'next' ? nextData : data);
/** @type {Promise<void>|null} in-flight request */
let loading = null;
let lastCols = 0;
/** The "Liked" pill: only liked products. */
let likedPill = false;

// ---- the Liked pill and the categories (remembered) -----------------------------------
function saveFilters() {
  const cats = currentCats();
  /** @type {{liked?: true, cats?: string[]}} */
  const saved = {};
  if (likedPill) saved.liked = true;
  if (cats !== 'all') saved.cats = cats;
  if (Object.keys(saved).length) storage.setJSON(KEYS.bonusFilters, saved);
  else storage.remove(KEYS.bonusFilters);
}


// ---- "liked products on Bonus" notice ---------------------------------------------------
function renderNotice() {
  const bar = $('likedNotice');
  const weekStart = data && data.period && data.period.start;
  const items = data && Array.isArray(data.items) ? data.items.filter(o => !isHidden(o)) : [];
  const n = weekStart ? likedOffers(items, isLiked).length : 0;
  bar.hidden = !n || storage.get(KEYS.bonusNoticeSeen) === weekStart;
  if (bar.hidden) { bar.innerHTML = ''; return; }
  bar.innerHTML = html`<span class="focustext">${t('likedOnBonus', n)}</span>
    <span class="cat-actions">
      <button type="button" class="mini" id="likedNoticeShow">${t('likedOnBonusShow')}</button>
      <button type="button" class="mini" id="likedNoticeClose" aria-label="${t('likedOnBonusClose')}" title="${t('likedOnBonusClose')}">✕</button>
    </span>`;
  $('likedNoticeShow').addEventListener('click', () => {
    closeNotice();
    if (currentView() !== 'bonus') setView('bonus');
    if (currentCats() !== 'all') setCats('all');   // every liked product, whatever its category
    setLikedFilter(true);
  });
  $('likedNoticeClose').addEventListener('click', closeNotice);
}

/** Closed for this Bonus week (it comes back for the next week). */
function closeNotice() {
  const weekStart = data && data.period && data.period.start;
  if (weekStart) storage.set(KEYS.bonusNoticeSeen, weekStart);
  renderNotice();
}

/** Status line; ``busy`` adds the rotating loading icon. */
function setStatus(text = '', isError = false, busy = false) {
  const el = $('bonusStatus');
  el.className = isError ? 'status error' : busy ? 'status busy' : 'status';
  el.textContent = text;
}

const cols = () => gridCols($('bonusResults'));

// ---- cards and the pill row --------------------------------------------------------------
/** @param {Offer} o */
function card(o) {
  const img = safeImageUrl(o.image);
  const title = o.title || '';
  const thumb = html`
    <div class="thumbwrap${img ? '' : ' noimg'}">
      ${img ? html`<img class="thumb" src="${img}" alt="" loading="lazy">` : ''}
      ${o.mechanism ? html`<div class="badges"><span class="badge disc mech">${o.mechanism}</span></div>` : ''}
    </div>`;
  const meta = [o.brand, o.unitSize].filter(Boolean).join(' · ');
  const now = o.priceNow, was = o.priceWas, minQty = o.minQty || 0, count = o.productCount || 0;
  const el = document.createElement('div');
  el.className = isHidden(o) ? 'card ishidden' : 'card';
  el.innerHTML = html`
    ${linkedThumb(o, thumb)}
    <div class="titlerow"><div class="title">${title}</div>${cardActions(o, title)}</div>
    ${metaLine(meta, offerUnitPrice(o))}
    ${groupLine(o, count)}
    <div class="prices">
      <span class="now">${now != null ? euro(now) : ''}</span>
      ${was != null && was !== now ? html`<span class="was">${euro(was)}</span>` : ''}
      ${now != null && minQty > 1 ? html`<span class="peritem">${t('perItemAt', minQty)}</span>` : ''}
    </div>`;
  wireCard(el, o);
  const toggle = el.querySelector('.grouptoggle');
  if (toggle) toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    const key = productKey(o);
    if (open) openGroups.add(key); else openGroups.delete(key);
    toggle.setAttribute('aria-expanded', String(open));
    const list = /** @type {HTMLElement|null} */ (el.querySelector('.grouplist'));
    if (list) list.hidden = !open;
  });
  return el;
}

// ---- products of a group deal ----------------------------------------------------------
// A group deal ("Alle AH Verse pizza's") comes with its products (id + title,
// from the server). The "N products" line under the title is a button that
// unfolds them, each linked to ah.nl. Open lists stay open across re-renders.
/** @type {Set<string>} */
const openGroups = new Set();
/** The most products the server lists per group deal (ah/mapping.py MAX_GROUP_PRODUCTS). */
const MAX_LISTED = 200;

/** @param {Offer} o @param {number} count */
function groupLine(o, count) {
  if (!o.isGroup) return '';
  const members = groupMemberRows(o.products);
  // The number of listed products, so the label matches the list. AH's own count only
  // without a list, or when the list is cut off (the server lists at most 200).
  const n = members.length && members.length < MAX_LISTED ? members.length : Math.max(count, members.length);
  const label = n > 1 ? t('bonusGroupN', n) : t('bonusGroup');
  if (members.length < 2) return n !== 1 ? html`<div class="unit">${label}</div>` : '';
  const open = openGroups.has(productKey(o));
  const items = members.map(m => {
    const url = productUrl(m.id);
    return url
      ? html`<li><a href="${url}" target="_blank" rel="noopener noreferrer" title="${t('viewOnAh', m.label)}">${m.label}</a></li>`
      : html`<li>${m.label}</li>`;
  });
  return html`<button type="button" class="grouptoggle unit" aria-expanded="${String(open)}" title="${t('bonusGroupShow')}">${label}<span class="groupcaret" aria-hidden="true">▾</span></button>
    <div class="grouplist"${open ? '' : html` hidden`}>
      <ul>${items}</ul>
    </div>`;
}

/** The "Liked" pill on or off (remembered). @param {boolean} on */
function setLikedFilter(on) {
  if (likedPill === on) return;
  likedPill = on;
  if (on && !$('likedNotice').hidden) closeNotice();   // seen them
  saveFilters();
  renderBonus();
}

/** The Liked pill: on or off, with how many liked products are on Bonus. @param {number} count */
function renderLikedPill(count) {
  const b = $('bonusLikedBtn');
  b.hidden = false;
  b.classList.toggle('active', likedPill);
  b.setAttribute('aria-pressed', String(likedPill));
  b.innerHTML = html`${t('likedPill')}<span class="catcount">${count}</span>`;
}

// ---- the view -------------------------------------------------------------------------------
function renderBonus() {
  renderNotice();
  const results = $('bonusResults');
  const summary = $('bonusSummary');
  results.innerHTML = '';
  summary.textContent = '';
  $('bonusLikedBtn').hidden = true;
  const d = shown();
  $('bonusSearchBar').hidden = !d;
  const categories = d && Array.isArray(d.categories) ? d.categories : [];
  buildCategoryFilter(categories);
  if (!d) return;
  const all = Array.isArray(d.items) ? d.items : [];
  if (!all.length) { summary.textContent = t('bonusNone'); return; }

  // Hidden products: left out unless "Show hidden products" is on.
  const hiddenCount = new Set(all.filter(isHidden).map(productKey)).size;
  const items = showHiddenEnabled() ? all : all.filter(o => !isHidden(o));
  /** @param {string} text */
  const withHidden = text => (hiddenCount ? text.replace(/\.$/, '') + ' · ' + t('hiddenCount', hiddenCount) + '.' : text);
  if (!items.length) { summary.textContent = withHidden(t('bonusNone')); return; }

  const liked = likedOffers(items, isLiked);
  renderLikedPill(liked.length);
  /** One section per category (AH's order), liked first in each. @param {Offer[]} list */
  const show = list => placeSections(results,
    [...categoryGroups(list, categories)].map(([name, offers]) => section(name, '', sortOffers(offers, isLiked).map(card))),
    cols(), true);
  lastCols = cols();

  // Search: matches from every category (only liked ones while the Liked filter is on).
  const query = $('bonusSearch').value.trim();
  const terms = searchTerms(query);
  if (terms.length) {
    const matches = (likedPill ? liked : items).filter(o => {
      const text = fold([o.title, o.brand].filter(Boolean).join(' '));
      return terms.every(w => text.includes(w));
    });
    summary.textContent = withHidden(matches.length ? t('bonusSearchSummary', matches.length, query) : t('bonusNoSearchMatch', query));
    show(matches);
    return;
  }

  // The chosen categories (Filters, presets), and the Liked filter on top.
  const chosen = selectCats(categoryGroups(items, categories), currentCats());
  const offers = [...chosen.values()].flat().filter(o => !likedPill || isLiked(o));
  if (likedPill) {
    const list = likedOffers(offers, () => true);   // a product in several categories once
    summary.textContent = withHidden(list.length ? t('bonusLikedSummary', list.length)
      : currentCats() === 'all' ? t('bonusLikedNone') : t('bonusNoMatch'));
    show(list);
    return;
  }
  if (!offers.length) { summary.textContent = withHidden(t('bonusNoMatch')); return; }
  const p = d.period || { start: null, end: null };
  summary.textContent = withHidden(t('bonusSummary', offers.length, new Set(offers.map(o => o.category)).size,
    p.start ? fmtDate(p.start) : '', p.end ? fmtDate(p.end) : ''));
  show(offers);
}

/** @param {'loading'|'ready'|'failed'} status */
function announceStatus(status) {
  emit(EV.bonusStatus, { status, deals: status === 'ready' && data ? indexDeals(data.items || []) : new Map() });
}

/**
 * Load the Bonus (the server caches it). ``force`` reloads even if loaded;
 * an incomplete copy (some categories failed) is fetched again too, so the
 * page picks up the server's successful retry (needsFetch).
 * ``quiet``: in the background for the bargain cards' Bonus tag (no status
 * text, no busy button; a failure is retried when the Bonus view is shown).
 */
export async function loadBonus({ force = false, quiet = false, fromAh = /** @type {string|null} */ (null),
  onAdminLost = () => {} } = {}) {
  if (!quiet) checkNext();
  if (week === 'next' && !quiet) return loadNext({ force: force || !!fromAh, fromAh, onAdminLost });
  if (loading) return loading;
  if (!fromAh && !needsFetch(data, force)) { if (!quiet) renderBonus(); return; }
  const buttons = [$('go'), $('forceRefresh')];
  if (!quiet) {
    setStatus(t(fromAh ? 'bonusForceLoading' : 'loading'), false, true);
    buttons.forEach(b => { b.disabled = true; });
  }
  if (!data) announceStatus('loading');
  loading = (async () => {
    try {
      // fromAh: the admin's CSRF token; the server reloads the week from AH first.
      data = /** @type {BonusWeek} */ (await (fromAh ? api.forceFetchBonus(fromAh) : api.fetchBonus()));
      const errs = Array.isArray(data.errors) ? data.errors : [];
      if (week === 'next') {
        // Next week is shown: its own status line stays.
      } else if (errs.length) {
        const refs = errs.map(e => e.ref).filter(Boolean).slice(0, 3).join(', ');
        setStatus(t('bonusPartial', errs.map(e => e.category).join(', ')) + (refs ? ' ' + t('errorRef', refs) : ''), true);
      } else {
        setStatus('');
      }
      renderBonus();
      announceStatus('ready');
    } catch (err) {
      if (!quiet) setStatus(errorText(err), true);
      // Admin session ended (expired / logged out elsewhere): hide the button.
      if (fromAh && err instanceof ApiError && (err.code === 'admin_required' || err.code === 'csrf_invalid')) onAdminLost();
      if (!data) announceStatus('failed');   // the bargains' Bonus filter can't apply: they say so
    } finally {
      if (!quiet) buttons.forEach(b => { b.disabled = false; });
      loading = null;
    }
  })();
  return loading;
}

// ---- next week -------------------------------------------------------------------------------
/** @type {Promise<void>|null} */
let loadingNext = null;

/**
 * Load next week's Bonus. ``force``: ask the server again; ``fromAh``: the
 * admin's CSRF token, and the server reloads it from AH first (Force refresh).
 * @param {{force?: boolean, fromAh?: string|null, onAdminLost?: () => void}} [opts]
 */
async function loadNext({ force = false, fromAh = null, onAdminLost = () => {} } = {}) {
  if (loadingNext) return loadingNext;
  if (nextData && !force) { renderBonus(); noteNext(); return; }
  const buttons = [$('go'), $('forceRefresh')];
  setStatus(t(fromAh ? 'bonusForceLoading' : 'bonusNextLoading'), false, true);
  buttons.forEach(b => { b.disabled = true; });
  loadingNext = (async () => {
    try {
      nextData = /** @type {BonusWeek} */ (await (fromAh ? api.forceFetchBonusNext(fromAh) : api.fetchBonusNext()));
      if (week === 'next') { renderBonus(); noteNext(); }
    } catch (err) {
      setStatus(errorText(err), true);
      if (fromAh && err instanceof ApiError && (err.code === 'admin_required' || err.code === 'csrf_invalid')) onAdminLost();
      if (err instanceof ApiError && err.code === 'bonus_next_unavailable') {
        showWeekSwitch(false);
        showWeek('current');
        setStatus(errorText(err), true);
      }
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      loadingNext = null;
    }
  })();
  return loadingNext;
}

/** Status line in next-week mode: which week this is, and that AH can still change it. */
function noteNext() {
  const p = nextData && nextData.period;
  if (week !== 'next' || !p) return;
  const errs = Array.isArray(nextData && nextData.errors) ? (nextData && nextData.errors) || [] : [];
  setStatus(errs.length ? t('bonusPartial', errs.map(e => e.category).join(', '))
    : t('bonusNextNote', p.start ? fmtDate(p.start) : '', p.end ? fmtDate(p.end) : ''), errs.length > 0);
}

/** Show this week or next week. @param {'current'|'next'} which */
function showWeek(which) {
  week = which === 'next' ? 'next' : 'current';
  document.querySelectorAll('#bonusWeekSeg .segbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = b.dataset.week === week;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  if (week === 'next') { loadNext(); return; }
  setStatus('');
  renderBonus();
  if (!data) loadBonus();
}

/** @param {boolean} visible */
function showWeekSwitch(visible) {
  $('bonusWeekSeg').hidden = !visible;
}

/** Show the switch once AH has published next week (asked once per page, when the Bonus is shown). */
async function checkNext() {
  if (nextChecked) return;
  nextChecked = true;
  try {
    const s = await api.bonusNextStatus();
    showWeekSwitch(!!(s && s.available));
  } catch (e) {
    showWeekSwitch(false);   // e.g. AH unreachable: asked again on the next page load
  }
}

let bonusReady = false;

function initSearch() {
  const input = $('bonusSearch');
  const clearBtn = $('bonusSearchClear');
  /** @type {any} */
  let timer = 0;
  const run = () => { clearTimeout(timer); clearBtn.hidden = !input.value; renderBonus(); };
  input.addEventListener('input', () => {
    clearBtn.hidden = !input.value;
    clearTimeout(timer);
    timer = setTimeout(run, 150);
  });
  input.addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => {
    if (e.key === 'Escape' && input.value) { e.preventDefault(); input.value = ''; run(); }
  });
  clearBtn.addEventListener('click', () => { input.value = ''; run(); input.focus(); });
}

export function initBonus() {
  initSearch();
  const saved = storage.getJSON(KEYS.bonusFilters, null);
  likedPill = parseLikedPill(saved);
  initFilters(parseCats(saved && saved.cats), () => { saveFilters(); renderBonus(); });
  $('bonusLikedBtn').addEventListener('click', () => setLikedFilter(!likedPill));
  const rerender = () => { if (shown() && !$('bonusView').hidden && cols() !== lastCols) renderBonus(); };
  window.addEventListener('resize', rerender);
  on(EV.cols, () => { if (shown() && cols() !== lastCols) renderBonus(); });
  // The bargains need this week's Bonus (their "Bonus" filter, or
  // their Bonus tags after loading): load it quietly; a failed load is tried again.
  on(EV.needBonus, () => { if (!bonusReady) loadBonus({ quiet: true }); });
  on(EV.bonusStatus, (/** @type {{status: string}} */ d) => { bonusReady = d.status === 'ready'; });
  on(EV.lists, () => { if (shown()) renderBonus(); });   // liked / hidden changed (either view)
  on(EV.lang, () => { renderBonus(); noteNext(); });
  $('bonusWeekSeg').addEventListener('click', e => {
    const b = /** @type {HTMLElement|null} */ (e.target instanceof Element ? e.target.closest('.segbtn') : null);
    if (b && b.dataset.week && b.dataset.week !== week) showWeek(b.dataset.week === 'next' ? 'next' : 'current');
  });
}
