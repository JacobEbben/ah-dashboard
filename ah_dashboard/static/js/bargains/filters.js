// @ts-check
// The bargains' filter settings: the minimum-discount buttons, "Ends today", "Liked" / "Not liked"
// (one or the other), Remove filters,
// and the Filters panel ("also in the Bonus", group by, order by, the product
// types; presets are named product-type selections, presets.js). The Filters
// button shows how many of the panel's filters are on. Liking a product is
// a filter only with "Liked" on; otherwise liked products only come first among equals (model.sorter). This module owns these settings; when one changes it emits
// EV.refilter (the controller re-renders) and, once saved, EV.settings
// (presets mark the active one).
import { $, makeFoldable, setSelect } from '../core/dom.js';
import { emit, EV, on } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { showHiddenEnabled } from '../lists/blacklist.js';
import { knownCats } from './model.js';
import { inStoreId } from '../lists/stores.js';
import { todayOf } from '../core/cart.js';

/** @typedef {import('./model.js').Cats} Cats */
/** @typedef {import('./model.js').Filter} Filter */
/** @typedef {import('../core/offers.js').Offer} Offer */
/**
 * Settings as always remembered (ah_settings, synced). Older ones may still have likedOnly (an old, removed
 * filter): ignored, so it can't switch on the new one (``liked``) by itself. Presets store only ``cats``
 * (older presets have the other fields too; they are ignored).
 * ``notLiked``: "Not liked" (only products you haven't liked); never on together with ``liked``.
 * @typedef {{minDisc?: string, endsToday?: boolean, liked?: boolean, notLiked?: boolean, sortBy?: string, groupBy?: string,
 *   cats?: 'all'|string[]}} Settings
 */

/** @returns {Cats} */
const ALL_CATS = () => ({ all: true, selected: new Set() });

/** @type {Cats} */
let cats = ALL_CATS();
/** Minimum discount in percent, as text: one of the discount buttons' values. */
let minDiscValue = '0';
const MIN_DISCS = ['0', '25', '40', '70'];
/** "Ends today": only bargains whose last day is today. */
let endsToday = false;
/** "Liked": only liked products. */
let likedOnly = false;
/** "Not liked": only products not liked (never on together with "Liked"). */
let notLikedOnly = false;
/** @type {() => Offer[]} the loaded bargains (set by initFilters) */
let items = () => [];

/** A setting changed: re-render, leaving any overview selection. */
const refilter = () => { syncFilterState(); emit(EV.refilter, { clearFocus: true }); };

/** Filters in the panel that hide bargains (the discount buttons show their own state). */
const panelFilterCount = () => (cats.all ? 0 : 1);

/** The count on the Filters button; Remove filters is enabled only while something is filtered. */
function syncFilterState() {
  const n = panelFilterCount();
  const count = $('filterCount');
  count.hidden = n === 0;
  count.textContent = String(n);
  $('filterToggle').setAttribute('aria-label', n ? `${t('filters')}, ${t('filtersActive', n)}` : t('filters'));
  /** @type {HTMLButtonElement} */ ($('clearFilters')).disabled = n === 0 && minDiscValue === '0' && !endsToday && !likedOnly
    && !notLikedOnly;
}

// ---- product types -------------------------------------------------------------------
// Settings store the product types that are ON: cats: 'all' (every type, also
// ones that only appear later) or cats: [names] (only those).

/** @param {Settings|null|undefined} s @returns {Cats} */
export function catsFromSettings(s) {
  if (s && s.cats === 'all') return ALL_CATS();
  if (s && Array.isArray(s.cats)) return { all: false, selected: new Set(s.cats.map(String)) };
  return ALL_CATS();
}

/** The current selection in settings form (also what a preset stores). @returns {{cats: 'all'|string[]}} */
export function catsSetting() {
  if (cats.all) return { cats: 'all' };
  return { cats: [...cats.selected].sort((a, b) => a.localeCompare(b, 'nl')) };
}

/** Does a preset's product-type selection equal the current one? @param {Settings} s */
export function sameCats(s) {
  const a = catsFromSettings(s);
  if (a.all || cats.all) return a.all === cats.all;
  return a.selected.size === cats.selected.size && [...a.selected].every(k => cats.selected.has(k));
}

/** @param {string} cat */
const catShown = cat => cats.all || cats.selected.has(cat);

/** Sync the chips and the "All" button with the selection. */
function updateCatChips() {
  document.querySelectorAll('#cats .cat').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = catShown(b.dataset.cat || '');
    b.classList.toggle('off', !on);
    b.setAttribute('aria-pressed', String(on));
  });
  const allBtn = $('catAll');
  allBtn.classList.toggle('active', cats.all);
  allBtn.setAttribute('aria-pressed', String(cats.all));
}

/** @param {string} cat */
function toggleCat(cat) {
  // Leaving "all" mode: start from the types present now.
  const sel = cats.all ? new Set(knownCats(items())) : new Set(cats.selected);
  if (sel.has(cat)) sel.delete(cat); else sel.add(cat);
  cats = { all: false, selected: sel };
  updateCatChips();
  refilter();
}

/** One chip per product type in the loaded bargains. @param {Offer[]} list */
export function buildCategoryFilter(list) {
  const box = $('cats');
  box.innerHTML = '';
  for (const c of knownCats(list)) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'cat';
    b.dataset.cat = c;
    b.textContent = c;
    b.addEventListener('click', () => toggleCat(c));
    box.appendChild(b);
  }
  updateCatChips();
}

// ---- the other settings -----------------------------------------------------------------
/** The minimum-discount buttons. @param {unknown} value */
function setMinDiscValue(value) {
  minDiscValue = MIN_DISCS.includes(String(value)) ? String(value) : '0';
  document.querySelectorAll('#minDisc .segbtn').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const on = b.dataset.disc === minDiscValue;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

/** The "Ends today" button. @param {unknown} on */
function setEndsToday(on) {
  endsToday = on === true;
  const b = $('expToday');
  b.classList.toggle('active', endsToday);
  b.setAttribute('aria-pressed', String(endsToday));
}

/** The "Liked" button. @param {unknown} on */
function setLikedOnly(on) {
  likedOnly = on === true;
  const b = $('likedOnly');
  b.classList.toggle('active', likedOnly);
  b.setAttribute('aria-pressed', String(likedOnly));
}

/** The "Not liked" button. @param {unknown} on */
function setNotLikedOnly(on) {
  notLikedOnly = on === true;
  const b = $('notLikedOnly');
  b.classList.toggle('active', notLikedOnly);
  b.setAttribute('aria-pressed', String(notLikedOnly));
}

/** Current settings as a plain object (what is remembered). @returns {Settings} */
function currentSettings() {
  return {
    minDisc: minDiscValue,
    endsToday,
    liked: likedOnly,
    notLiked: notLikedOnly,
    sortBy: $('sortBy').value,
    groupBy: $('groupBy').value,
    ...catsSetting(),
  };
}

/** Apply a preset: only its product types; every other setting stays. @param {Settings} s */
export function applyCats(s) {
  cats = catsFromSettings(s);
  updateCatChips();
  refilter();
}

/** "Remove filters": every product type, any discount, any end date, liked or not.
 *  Grouping and order stay (they hide nothing), and so do the stores and
 *  "I'm in this store" (where you are, not a filter). */
function clearFilters() {
  cats = ALL_CATS();
  updateCatChips();
  setMinDiscValue('0');
  setEndsToday(false);
  setLikedOnly(false);
  setNotLikedOnly(false);
  refilter();
}

/** Remember the settings and let presets mark the active one. */
export function saveSettings() {
  storage.setJSON(KEYS.settings, currentSettings());   // always remembered (only real changes are written)
  emit(EV.settings);
}

/** What the filters ask for now (for model.filterOffers). @returns {Filter} */
export function currentFilter() {
  return {
    cats,
    minDisc: Number(minDiscValue) || 0,
    endsOn: endsToday ? todayOf(new Date()) : null,   // today at each render (a page open past midnight follows)
    likedOnly,
    notLikedOnly,
    showHidden: showHiddenEnabled(),
    storeId: inStoreId(),
  };
}

export const sortBy = () => String($('sortBy').value);
export const groupBy = () => String($('groupBy').value);

/** @param {boolean} visible */
export function setFilterPanelVisible(visible) {
  $('typesSection').hidden = !visible;
}

/** @type {(folded: boolean) => void} */
let setFiltersFolded = () => {};
export function openFilterPanel() { setFiltersFolded(false); }

/** @param {() => Offer[]} getItems the loaded bargains */
export function initFilters(getItems) {
  items = getItems;
  // A fold-out panel; opening it folds the other panels (core/dom.makeFoldable). Remembered per browser.
  setFiltersFolded = makeFoldable($('filterPanel'), $('filterToggle'),
    folded => storage.set(KEYS.filtersFolded, folded ? '1' : '0'));
  setFiltersFolded(storage.get(KEYS.filtersFolded) !== '0');

  // Restore remembered settings.
  const saved = storage.getJSON(KEYS.settings);
  if (saved && typeof saved === 'object') {
    setMinDiscValue(saved.minDisc);
    setEndsToday(saved.endsToday);
    setLikedOnly(saved.liked);
    setNotLikedOnly(saved.notLiked === true && saved.liked !== true);   // both on (edited by hand): Liked wins
    setSelect('sortBy', saved.sortBy);
    setSelect('groupBy', saved.groupBy);
    cats = catsFromSettings(saved);
  } else {
    setMinDiscValue('0');
  }
  updateCatChips();

  syncFilterState();
  // The labels are translated text: redo them when the language changes.
  on(EV.lang, syncFilterState);
  document.querySelectorAll('#minDisc .segbtn').forEach(el => el.addEventListener('click', () => {
    setMinDiscValue(/** @type {HTMLElement} */ (el).dataset.disc);
    refilter();
  }));
  $('expToday').addEventListener('click', () => { setEndsToday(!endsToday); refilter(); });
  // "Liked" and "Not liked" exclude each other: turning one on turns the other off.
  $('likedOnly').addEventListener('click', () => { setLikedOnly(!likedOnly); setNotLikedOnly(false); refilter(); });
  $('notLikedOnly').addEventListener('click', () => { setNotLikedOnly(!notLikedOnly); setLikedOnly(false); refilter(); });
  $('clearFilters').addEventListener('click', clearFilters);
  // Order and grouping keep the overview selection.
  for (const id of ['sortBy', 'groupBy']) $(id).addEventListener('change', () => emit(EV.refilter));
  // "All": every product type, including ones that appear later.
  $('catAll').addEventListener('click', () => { cats = ALL_CATS(); updateCatChips(); refilter(); });
  $('catNone').addEventListener('click', () => { cats = { all: false, selected: new Set() }; updateCatChips(); refilter(); });
}
