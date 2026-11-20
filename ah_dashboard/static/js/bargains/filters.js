// @ts-check
// The bargains' filter settings: the quick strip (min. discount dropdown,
// Remove filters) and the Filters sheet ("liked only", "also in the Bonus",
// group by, order by, the product types; presets are named product-type selections,
// presets.js), plus the name search. The Filters button shows how many
// filters are on. This module owns these settings; when one changes it emits
// EV.refilter (the controller re-renders) and, once saved, EV.settings
// (presets mark the active one).
import { $, setSelect } from '../core/dom.js';
import { emit, EV, on } from '../core/events.js';
import { searchTerms } from '../core/format.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { showHiddenEnabled } from '../lists/blacklist.js';
import { makeSheet } from '../ui/sheet.js';
import { knownCats } from './model.js';

/** @typedef {import('./model.js').Cats} Cats */
/** @typedef {import('./model.js').Filter} Filter */
/** @typedef {import('../core/offers.js').Offer} Offer */
/**
 * Settings as always remembered (ah_settings, synced). Presets store only ``cats``
 * (older presets have the other fields too; they are ignored).
 * @typedef {{minDisc?: string, likedOnly?: boolean, bonusOnly?: boolean, sortBy?: string, groupBy?: string, cats?: 'all'|string[]}} Settings
 */

/** @returns {Cats} */
const ALL_CATS = () => ({ all: true, selected: new Set() });

/** @type {Cats} */
let cats = ALL_CATS();
/** "Only liked bargains" (likedOnly setting; in the sheet). */
let likedOnlyOn = false;
/** "Only bargains also in this week's Bonus" (bonusOnly setting; in the sheet). */
let bonusOnlyOn = false;
/** Minimum discount in percent, as text: one of the strip dropdown's values. */
let minDiscValue = '0';
const MIN_DISCS = ['0', '25', '40', '70'];
/** @type {() => Offer[]} the loaded bargains (set by initFilters) */
let items = () => [];

/** A setting changed: re-render, leaving any overview selection. */
const refilter = () => { syncFilterState(); emit(EV.refilter, { clearFocus: true }); };

/** Filters in the sheet that hide bargains (the strip's discount dropdown shows its own value). */
const sheetFilterCount = () => (cats.all ? 0 : 1) + (likedOnlyOn ? 1 : 0) + (bonusOnlyOn ? 1 : 0);

/** The count on the Filters button, and Remove filters only while something is filtered. */
function syncFilterState() {
  const n = sheetFilterCount();
  const count = $('filterCount');
  count.hidden = n === 0;
  count.textContent = String(n);
  $('filterMenuBtn').setAttribute('aria-label', n ? `${t('filters')}, ${t('filtersActive', n)}` : t('filters'));
  const any = n > 0 || minDiscValue !== '0' || !!searchQuery();
  $('minDiscSel').classList.toggle('active', minDiscValue !== '0');
  $('clearFilters').hidden = !any;
  /** @type {HTMLButtonElement} */ ($('sheetClear')).disabled = !any;
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
export const bonusOnly = () => bonusOnlyOn;

/** @param {boolean} on */
function setBonusOnly(on) {
  bonusOnlyOn = on;
  const b = $('bonusOnlyBtn');
  b.classList.toggle('off', !on);
  b.setAttribute('aria-pressed', String(on));
  if (on) emit(EV.needBonus);   // the filter needs this week's Bonus
}

export const likedOnly = () => likedOnlyOn;

/** @param {boolean} on */
function setLikedOnly(on) {
  likedOnlyOn = on;
  const b = $('likedOnlyBtn');
  b.classList.toggle('off', !on);
  b.setAttribute('aria-pressed', String(on));
}

/** The minimum-discount dropdown. @param {unknown} value */
function setMinDiscValue(value) {
  minDiscValue = MIN_DISCS.includes(String(value)) ? String(value) : '0';
  /** @type {HTMLSelectElement} */ ($('minDiscSel')).value = minDiscValue;
}

/** Current settings as a plain object (what is remembered). @returns {Settings} */
function currentSettings() {
  return {
    minDisc: minDiscValue,
    likedOnly: likedOnly(),
    bonusOnly: bonusOnly(),
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

/** "Remove filters": every product type, any discount, not only liked or
 *  Bonus, no search. Grouping and order stay (they hide nothing). */
function clearFilters() {
  cats = ALL_CATS();
  updateCatChips();
  setMinDiscValue('0');
  setLikedOnly(false);
  setBonusOnly(false);
  $('productSearch').value = '';
  $('productSearchClear').hidden = true;
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
    likedOnly: likedOnly(),
    bonusOnly: bonusOnly(),
    terms: searchTerms($('productSearch').value),
    showHidden: showHiddenEnabled(),
  };
}

export const sortBy = () => String($('sortBy').value);
export const groupBy = () => String($('groupBy').value);
export const searchQuery = () => String($('productSearch').value).trim();

/** @param {boolean} visible */
export function setFilterPanelVisible(visible) {
  $('typesSection').hidden = !visible;
  $('searchBar').hidden = !visible;
}

function initSearch() {
  const input = $('productSearch');
  const clearBtn = $('productSearchClear');
  /** @type {any} */
  let timer = 0;
  const run = () => {
    clearTimeout(timer);
    clearBtn.hidden = !input.value;
    syncFilterState();
    // Typing a search leaves the overview selection.
    emit(EV.refilter, { clearFocus: !!input.value.trim() });
  };
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

/** @param {() => Offer[]} getItems the loaded bargains */
export function initFilters(getItems) {
  items = getItems;
  makeSheet(/** @type {HTMLDialogElement} */ ($('filterSheet')), $('filterMenuBtn'));

  // Restore remembered settings.
  const saved = storage.getJSON(KEYS.settings);
  if (saved && typeof saved === 'object') {
    setMinDiscValue(saved.minDisc);
    setSelect('sortBy', saved.sortBy);
    setSelect('groupBy', saved.groupBy);
    setLikedOnly(saved.likedOnly === true);
    setBonusOnly(saved.bonusOnly === true);
    cats = catsFromSettings(saved);
  } else {
    setMinDiscValue('0');
  }
  updateCatChips();

  initSearch();
  syncFilterState();
  // The labels are translated text: redo them when the language changes.
  on(EV.lang, syncFilterState);
  $('minDiscSel').addEventListener('change', (/** @type {any} */ e) => { setMinDiscValue(e.target.value); refilter(); });
  $('clearFilters').addEventListener('click', clearFilters);
  $('sheetClear').addEventListener('click', clearFilters);
  $('likedOnlyBtn').addEventListener('click', () => { setLikedOnly(!likedOnly()); refilter(); });
  $('bonusOnlyBtn').addEventListener('click', () => { setBonusOnly(!bonusOnly()); refilter(); });
  // Order and grouping keep the overview selection.
  for (const id of ['sortBy', 'groupBy']) $(id).addEventListener('change', () => emit(EV.refilter));
  // "All": every product type, including ones that appear later.
  $('catAll').addEventListener('click', () => { cats = ALL_CATS(); updateCatChips(); refilter(); });
  $('catNone').addEventListener('click', () => { cats = { all: false, selected: new Set() }; updateCatChips(); refilter(); });
}
