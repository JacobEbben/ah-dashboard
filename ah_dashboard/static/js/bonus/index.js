// @ts-check
// The Bonus view: this week's national Bonus, one AH bonus category (or one of
// the visitor's category groups) at a time. A row of pills selects it
// (remembered). The "Liked" pill in front is a filter: while on, the view
// shows every liked product on Bonus this week, one section per category
// (filters.liked, saved with the panel's filters). The search field searches
// every category at once (only liked ones while the Liked filter is on). Cards have the same
// like and hide buttons as the bargains (shared lists). The "Filters &
// sorting" panel narrows and orders the offers (ah_bonus_filters, synced). The
// notice bar says when liked products are on Bonus this week.
//
// Events: emits EV.bonusStatus (bargain cards' Bonus tag, "Bonus"
// filter); listens to EV.needBonus, EV.lists, EV.cols, EV.lang.
import { api, errorText } from '../core/api.js';
import { $, html } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { euro, fmtDate, searchTerms, fold } from '../core/format.js';
import { t } from '../core/i18n.js';
import { productKey, safeImageUrl } from '../core/offers.js';
import { KEYS, storage } from '../core/storage.js';
import { isHidden, showHiddenEnabled } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { cardActions, gridCols, linkedThumb, metaLine, placeSections, section, wireCard } from '../ui/cards.js';
import { makeDropDraggable } from '../ui/dragdrop.js';
import { onRenameGesture, startRename } from '../ui/rename.js';
import { makeReorderable } from '../ui/reorder.js';
import { makeSheet } from '../ui/sheet.js';
import { currentView, setView } from '../ui/view.js';
import { GROUP_NAME_MAX, addToGroup, allGroups, announce, createGroup, groupByKey, groupKey, groupedCats,
  initGroups, renameGroup, renderGroupsPanel, reorderGroups } from './groups.js';
import { DEAL_TYPES, SORTS, categoryGroups, defaultFilters, groupOffers, indexDeals, isDefaultFilters,
  likedOffers, needsFetch, offerUnitPrice, parseFilters, passesFilters, sortOffers, typeShown } from './model.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{period: {start: string|null, end: string|null}, categories: string[], items: Offer[],
 *             errors: {category: string, code: string, ref: string}[]}} BonusWeek */

/** @type {BonusWeek|null} last /api/bonus answer */
let data = null;
/** @type {Promise<void>|null} in-flight request */
let loading = null;
let lastCols = 0;
/** @type {string|undefined} shown category or a group key */
let selected;
let filters = defaultFilters();

// ---- filters & sorting --------------------------------------------------------------
function saveFilters() {
  if (isDefaultFilters(filters) && !filters.liked) storage.remove(KEYS.bonusFilters);
  else storage.setJSON(KEYS.bonusFilters, filters);
}

/** Sync the panel's controls and its summary with `filters`. */
function syncFilterControls() {
  document.querySelectorAll('#bonusMinSave .segbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = b.dataset.save === String(filters.minSave);
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  $('bonusSort').value = filters.sort;
  document.querySelectorAll('#bonusTypes .cat').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = typeShown(filters, b.dataset.type);
    b.classList.toggle('off', !on);
    b.setAttribute('aria-pressed', String(on));
  });
  /** @type {HTMLButtonElement} */ ($('bonusFiltersReset')).disabled = isDefaultFilters(filters);
  // The count on the Filters button: the settings that hide offers, plus a non-default order.
  const n = (filters.minSave ? 1 : 0) + (filters.types !== 'all' ? 1 : 0) + (filters.sort !== 'ah' ? 1 : 0);
  const count = $('bonusFilterCount');
  count.hidden = n === 0;
  count.textContent = String(n);
  $('bonusFilterBtn').setAttribute('aria-label',
    n ? `${t('bonusFiltersTitle')}, ${t('filtersActive', n)}` : t('bonusFiltersTitle'));
}

function filtersChanged() {
  saveFilters();
  syncFilterControls();
  renderBonus();
}

/** @param {string} type */
function toggleType(type) {
  const sel = new Set(filters.types === 'all' ? DEAL_TYPES : filters.types);
  if (sel.has(type)) sel.delete(type); else sel.add(type);
  filters.types = sel.size === DEAL_TYPES.length ? 'all' : DEAL_TYPES.filter(x => sel.has(x));
  filtersChanged();
}

function initFilterPanel() {
  makeSheet(/** @type {HTMLDialogElement} */ ($('bonusFilterSheet')), $('bonusFilterBtn'));
  filters = parseFilters(storage.getJSON(KEYS.bonusFilters, null));
  document.querySelectorAll('#bonusMinSave .segbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    b.addEventListener('click', () => { filters.minSave = Number(b.dataset.save) || 0; filtersChanged(); });
  });
  $('bonusSort').addEventListener('change', (/** @type {any} */ e) => { filters.sort = SORTS.includes(e.target.value) ? e.target.value : 'ah'; filtersChanged(); });
  document.querySelectorAll('#bonusTypes .cat').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    b.addEventListener('click', () => toggleType(b.dataset.type || 'other'));
  });
  // Reset is the panel's: it leaves the Liked pill as it is.
  $('bonusFiltersReset').addEventListener('click', () => { filters = { ...defaultFilters(), liked: filters.liked }; filtersChanged(); });
  syncFilterControls();
}

// ---- "liked products on Bonus" notice ---------------------------------------------------
function renderNotice() {
  const bar = $('likedNotice');
  const week = data && data.period && data.period.start;
  const items = data && Array.isArray(data.items) ? data.items.filter(o => !isHidden(o)) : [];
  const n = week ? likedOffers(items, isLiked).length : 0;
  bar.hidden = !n || storage.get(KEYS.bonusNoticeSeen) === week;
  if (bar.hidden) { bar.innerHTML = ''; return; }
  bar.innerHTML = html`<span class="focustext">${t('likedOnBonus', n)}</span>
    <span class="cat-actions">
      <button type="button" class="mini" id="likedNoticeShow">${t('likedOnBonusShow')}</button>
      <button type="button" class="mini" id="likedNoticeClose" aria-label="${t('likedOnBonusClose')}" title="${t('likedOnBonusClose')}">✕</button>
    </span>`;
  $('likedNoticeShow').addEventListener('click', () => {
    closeNotice();
    if (currentView() !== 'bonus') setView('bonus');
    setLikedFilter(true);
  });
  $('likedNoticeClose').addEventListener('click', closeNotice);
}

/** Closed for this Bonus week (it comes back for the next week). */
function closeNotice() {
  const week = data && data.period && data.period.start;
  if (week) storage.set(KEYS.bonusNoticeSeen, week);
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
    ${o.isGroup && count !== 1 ? html`<div class="unit">${count > 1 ? t('bonusGroupN', count) : t('bonusGroup')}</div>` : ''}
    <div class="prices">
      <span class="now">${now != null ? html`${o.priceFrom ? html`<span class="from">${t('priceFrom')}</span>` : ''}${euro(now)}` : ''}</span>
      ${was != null && was !== now ? html`<span class="was">${euro(was)}</span>` : ''}
      ${now != null && minQty > 1 ? html`<span class="peritem">${t('perItemAt', minQty)}</span>` : ''}
    </div>`;
  wireCard(el, o);
  return el;
}

/** @param {string|undefined} name */
const catLabel = name => (groupByKey(name) || { name }).name || '';

/** The "Liked" filter on or off (remembered with the panel's filters). @param {boolean} on */
function setLikedFilter(on) {
  if (filters.liked === on) return;
  filters.liked = on;
  if (on && !$('likedNotice').hidden) closeNotice();   // seen them
  saveFilters();
  renderBonus();
}

// Small "stack" icon on group pills (static markup).
const GROUP_ICON = html`<svg class="groupicon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/></svg>`;

/**
 * The pill row. Category pills can be dragged onto another category (new
 * group) or a group (add to it); group pills can be dragged to reorder them.
 * In front: the "Liked" filter pill (with how many liked products are on
 * Bonus), then a separator.
 * @param {{key: string, kind: 'group'|'cat', label: string, count: number}[]} pills
 * @param {string|undefined|null} active
 * @param {number} likedCount
 */
function renderCategoryBar(pills, active, likedCount) {
  const bar = $('bonusCats');
  bar.innerHTML = '';
  bar.hidden = false;
  const liked = document.createElement('button');
  liked.type = 'button';
  liked.id = 'bonusLikedBtn';
  liked.className = 'presetbtn catpill likedpill' + (filters.liked ? ' active' : '');
  liked.dataset.kind = 'liked';
  liked.title = t('bonusLikedTitle');
  liked.setAttribute('aria-pressed', String(filters.liked));
  liked.innerHTML = html`${t('likedOnlyPill')}<span class="catcount">${likedCount}</span>`;
  liked.addEventListener('click', () => setLikedFilter(!filters.liked));
  const sep = document.createElement('span');
  sep.className = 'quicksep';
  sep.setAttribute('aria-hidden', 'true');
  bar.append(liked, sep);
  for (const p of pills) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'presetbtn catpill' + (p.kind === 'group' ? ' grouppill movable' : '');
    b.dataset.kind = p.kind;
    if (p.kind === 'cat') { b.dataset.cat = p.key; b.title = t('dragCatHint'); }
    if (p.kind === 'group') { b.dataset.group = p.key; b.title = t('groupPillHint'); }
    const on = p.key === active;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
    b.innerHTML = html`${p.kind === 'group' ? GROUP_ICON : ''}${p.label}<span class="catcount">${p.count}</span>`;
    b.addEventListener('click', () => selectCategory(p.key));
    bar.appendChild(b);
  }
}

/** Dropping a category pill (dragdrop.js) onto another pill. @param {HTMLElement} item @param {HTMLElement} target */
function dropCategory(item, target) {
  const cat = item.dataset.cat || '';
  let g = null;
  if (target.dataset.kind === 'group') {
    const group = groupByKey(target.dataset.group);
    if (group) g = addToGroup(group.id, cat);
  } else if (target.dataset.kind === 'cat') {
    g = createGroup([target.dataset.cat || '', cat]);
  }
  if (!g) return;
  announce(t('addedToGroup', cat, g.name));
  selectCategory(groupKey(g));   // show the group straight away
}

/** A category or group pill: show it (this ends the Liked filter and a search). @param {string} name */
function selectCategory(name) {
  const input = $('bonusSearch');
  const searching = !!input.value.trim();
  if (name === selected && !searching && !filters.liked) return;
  selected = name;
  storage.set(KEYS.bonusCat, name);   // a category or a group key
  if (filters.liked) { filters.liked = false; saveFilters(); }
  if (searching) { input.value = ''; $('bonusSearchClear').hidden = true; }
  renderBonus();
  // Keep the category row in view (e.g. after scrolling down a long category);
  // the active button is scrolled into view within the row on phones.
  const bar = $('bonusCats');
  if (bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ block: 'start' });
  const active = bar.querySelector('.catpill.active');
  if (active) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

/**
 * Titled sections of offers into the results grid, packed so small ones share
 * a row (order may change to fill free columns: packSections "dense").
 * @param {Iterable<[string, Offer[]]>} parts title and its offers @param {number} n columns
 */
const showSections = (parts, n) =>
  placeSections($('bonusResults'), [...parts].map(([title, list]) => section(title, '', list.map(card))), n, true);

// ---- the view -------------------------------------------------------------------------------
function renderBonus() {
  syncFilterControls();   // the Filters button's label (also after a language switch)
  renderNotice();
  const results = $('bonusResults');
  const summary = $('bonusSummary');
  results.innerHTML = '';
  summary.textContent = '';
  $('bonusCats').hidden = true;
  $('bonusSearchBar').hidden = !data;
  renderGroupsPanel(data && Array.isArray(data.categories) ? data.categories : []);
  if (!data) return;
  const all = Array.isArray(data.items) ? data.items : [];
  if (!all.length) { summary.textContent = t('bonusNone'); return; }

  // Hidden products: left out unless "Show hidden products" is on.
  const hiddenCount = new Set(all.filter(isHidden).map(productKey)).size;
  const visible = showHiddenEnabled() ? all : all.filter(o => !isHidden(o));
  /** @param {string} text */
  const withHidden = text => (hiddenCount ? text.replace(/\.$/, '') + ' · ' + t('hiddenCount', hiddenCount) + '.' : text);
  const items = visible.filter(o => passesFilters(filters, o));
  if (!items.length) {
    summary.textContent = withHidden(visible.length ? t('bonusNoFilterMatch') : t('bonusNone'));
    return;
  }

  const categories = Array.isArray(data.categories) ? data.categories : [];
  const byCat = categoryGroups(items, categories);   // category -> its offers
  const liked = likedOffers(items, isLiked);
  const grouped = groupedCats();
  /** @param {Offer[]} list */
  const sorted = list => sortOffers(list, filters.sort, isLiked);
  /** @type {{key: string, kind: 'group'|'cat', label: string, count: number}[]} */
  const pills = [
    ...allGroups().map(g => ({ key: groupKey(g), kind: /** @type {const} */ ('group'), label: g.name, count: groupOffers(g, byCat).length })),
    // A category in a group has no pill of its own: the group stands for it.
    ...[...byCat].filter(([name]) => !grouped.has(name))
      .map(([name, list]) => ({ key: name, kind: /** @type {const} */ ('cat'), label: name, count: list.length })),
  ];
  const n = cols();
  lastCols = n;

  // Search: matches from every category, one section per category.
  const query = $('bonusSearch').value.trim();
  const terms = searchTerms(query);
  if (terms.length) {
    renderCategoryBar(pills, null, liked.length);
    const matches = (filters.liked ? liked : items).filter(o => {
      const text = fold([o.title, o.brand].filter(Boolean).join(' '));
      return terms.every(w => text.includes(w));
    });
    summary.textContent = withHidden(matches.length ? t('bonusSearchSummary', matches.length, query) : t('bonusNoSearchMatch', query));
    showSections([...categoryGroups(matches, categories)].map(([name, list]) => [name, sorted(list)]), n);
    return;
  }

  // The Liked filter: every liked product on Bonus, one section per category.
  if (filters.liked) {
    renderCategoryBar(pills, null, liked.length);
    summary.textContent = withHidden(liked.length ? t('bonusLikedSummary', liked.length) : t('bonusLikedNone'));
    showSections([...categoryGroups(liked, categories)].map(([name, list]) => [name, sorted(list)]), n);
    return;
  }

  // What can be shown: a group, or a category with its own pill.
  /** @param {string|null|undefined} key */
  const usable = key => !!groupByKey(key) || (!!key && byCat.has(key) && !grouped.has(key));
  if (!usable(selected)) {
    const saved = storage.get(KEYS.bonusCat);
    const first = pills[0];
    selected = saved && usable(saved) ? saved : first ? first.key : undefined;
  }
  renderCategoryBar(pills, selected, liked.length);

  const p = data.period || { start: null, end: null };
  summary.textContent = withHidden(t('bonusSummary', items.length, byCat.size,
    p.start ? fmtDate(p.start) : '', p.end ? fmtDate(p.end) : ''));

  const group = groupByKey(selected);
  if (group) {
    // A group: the offers of all its categories together, in one section named
    // after the group. Sorting works across the group.
    const offers = groupOffers(group, byCat);
    if (!offers.length) { summary.textContent = withHidden(t('groupEmpty', group.name)); return; }
    showSections([[group.name, sorted(offers)]], n);
    return;
  }
  const list = sorted((selected && byCat.get(selected)) || []);
  if (selected !== undefined) showSections([[catLabel(selected), list]], n);
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
export async function loadBonus({ force = false, quiet = false } = {}) {
  if (loading) return loading;
  if (!needsFetch(data, force)) { if (!quiet) renderBonus(); return; }
  if (!quiet) { setStatus(t('loading'), false, true); $('go').disabled = true; }
  if (!data) announceStatus('loading');
  loading = (async () => {
    try {
      data = /** @type {BonusWeek} */ (await api.fetchBonus());
      const errs = Array.isArray(data.errors) ? data.errors : [];
      if (errs.length) {
        const refs = errs.map(e => e.ref).filter(Boolean).slice(0, 3).join(', ');
        setStatus(t('bonusPartial', errs.map(e => e.category).join(', ')) + (refs ? ' ' + t('errorRef', refs) : ''), true);
      } else {
        setStatus('');
      }
      renderBonus();
      announceStatus('ready');
    } catch (err) {
      if (!quiet) setStatus(errorText(err), true);
      if (!data) announceStatus('failed');   // the bargains' Bonus filter can't apply: they say so
    } finally {
      if (!quiet) $('go').disabled = false;
      loading = null;
    }
  })();
  return loading;
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
  initFilterPanel();
  initGroups(() => renderBonus());
  // Double-click (or F2 on) a group pill: rename it in place.
  onRenameGesture($('bonusCats'), '.catpill[data-kind="group"]', pill => {
    const g = groupByKey(pill.dataset.group);
    if (!g) return;
    startRename(pill, {
      name: g.name, maxLength: GROUP_NAME_MAX, label: t('groupName'),
      onCommit: name => { renameGroup(g.id, name); announce(t('groupRenamed', name)); },
    });
  });
  // Drag a category pill onto another category (new group) or onto a group (add to it).
  makeDropDraggable($('bonusCats'), '.catpill[data-kind="cat"]',
    '.catpill[data-kind="cat"], .catpill[data-kind="group"]', dropCategory);
  // Drag group pills (or Alt+arrow keys) to put the groups in your own order.
  makeReorderable($('bonusCats'), '.catpill[data-kind="group"]', pills => {
    reorderGroups(pills.map(b => groupByKey(b.dataset.group)).filter(g => g !== null).map(g => g.id));
  }, {
    onKeyboardMove: (pill, index, count) => {
      const g = groupByKey(pill.dataset.group);
      if (g) announce(t('groupMoved', g.name, index + 1, count));
      // The row was re-rendered: keep the keyboard focus on the moved group.
      // (Compared directly: group keys start with a control character that a CSS selector can't hold.)
      const again = /** @type {HTMLElement[]} */ ([...$('bonusCats').querySelectorAll('.catpill[data-kind="group"]')])
        .find(b => b.dataset.group === pill.dataset.group);
      if (again) again.focus();
    },
  });
  const rerender = () => { if (data && !$('bonusView').hidden && cols() !== lastCols) renderBonus(); };
  window.addEventListener('resize', rerender);
  on(EV.cols, () => { if (data && cols() !== lastCols) renderBonus(); });
  // The bargains need this week's Bonus (their "Bonus" filter, or
  // their Bonus tags after loading): load it quietly; a failed load is tried again.
  on(EV.needBonus, () => { if (!bonusReady) loadBonus({ quiet: true }); });
  on(EV.bonusStatus, (/** @type {{status: string}} */ d) => { bonusReady = d.status === 'ready'; });
  on(EV.lists, () => { if (data) renderBonus(); });   // liked / hidden changed (either view)
  on(EV.lang, renderBonus);
}
