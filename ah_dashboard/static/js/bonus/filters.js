// @ts-check
// The Bonus view's Filters, like the bargains': a fold-out panel with the
// categories (chips, All / None) and the presets, plus the presets bar above
// it ("All types", the saved presets, ＋; ui/presets.js). Presets are named
// category selections, kept apart from the bargains' presets (ah_bonus_presets,
// ah_bonus_preset_order). The selection itself is remembered in
// ah_bonus_filters {cats} (index.js writes that key, with the Liked pill).
// The Filters button shows 1 while only some categories are on.
import { $, makeFoldable } from '../core/dom.js';
import { on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { makePresets } from '../ui/presets.js';
import { groupsToPresets, parseCats } from './model.js';

/** @typedef {import('./model.js').Cats} Cats */

/** @type {Cats} */
let cats = 'all';
/** @type {string[]} the shown week's categories (AH's order) */
let known = [];
let changed = () => {};
let markActive = () => {};

/** The current selection: 'all' or the category names that are on. */
export const currentCats = () => cats;

/** @param {string} cat */
const catShown = cat => cats === 'all' || cats.includes(cat);

/** Sync the chips, the All button and the count on the Filters button. */
function sync() {
  document.querySelectorAll('#bonusCatChips .cat').forEach(el => {
    const b = /** @type {HTMLElement} */ (el);
    const shown = catShown(b.dataset.cat || '');
    b.classList.toggle('off', !shown);
    b.setAttribute('aria-pressed', String(shown));
  });
  const allBtn = $('bonusCatAll');
  allBtn.classList.toggle('active', cats === 'all');
  allBtn.setAttribute('aria-pressed', String(cats === 'all'));
  const n = cats === 'all' ? 0 : 1;
  const count = $('bonusFilterCount');
  count.hidden = n === 0;
  count.textContent = String(n);
  $('bonusFilterToggle').setAttribute('aria-label', n ? `${t('filters')}, ${t('filtersActive', n)}` : t('filters'));
  markActive();
}

/** A new selection: show it and tell the view. @param {Cats} next */
export function setCats(next) {
  cats = next === 'all' ? 'all' : [...new Set(next)].sort((a, b) => a.localeCompare(b, 'nl'));
  sync();
  changed();
}

/** @param {string} cat */
function toggleCat(cat) {
  // Leaving "all": start from the categories of the shown week.
  const sel = new Set(cats === 'all' ? known : cats);
  if (sel.has(cat)) sel.delete(cat); else sel.add(cat);
  setCats([...sel]);
}

/** One chip per category of the shown week (rebuilt only when they change). @param {string[]} categories */
export function buildCategoryFilter(categories) {
  $('bonusCatSection').hidden = !categories.length;
  if (JSON.stringify(categories) === JSON.stringify(known)) return;
  known = [...categories];
  const box = $('bonusCatChips');
  box.innerHTML = '';
  for (const c of known) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'cat';
    b.dataset.cat = c;
    b.textContent = c;
    b.addEventListener('click', () => toggleCat(c));
    box.appendChild(b);
  }
  sync();
}

/** @param {{cats?: unknown}} s */
const asCats = s => parseCats(s && s.cats);

/** Category groups made by an older version become presets once (when there are no Bonus presets yet). */
function migrateGroups() {
  const raw = storage.getJSON(KEYS.bonusGroups, null);
  if (raw === null) return;
  if (storage.get(KEYS.bonusPresets) === null) {
    const { presets, order } = groupsToPresets(raw);
    if (order.length) {
      storage.setJSON(KEYS.bonusPresets, presets);
      storage.setJSON(KEYS.bonusPresetOrder, order);
    }
  }
  storage.remove(KEYS.bonusGroups);
  storage.remove(KEYS.bonusCat);   // the category or group shown by the old view
}

/** @param {Cats} saved the remembered selection @param {() => void} onChange */
export function initFilters(saved, onChange) {
  cats = saved;
  migrateGroups();
  const setFolded = makeFoldable($('bonusFilterPanel'), $('bonusFilterToggle'),
    folded => storage.set(KEYS.bonusFiltersFolded, folded ? '1' : '0'));
  setFolded(storage.get(KEYS.bonusFiltersFolded) !== '0');
  const presets = makePresets({
    presetsKey: KEYS.bonusPresets,
    orderKey: KEYS.bonusPresetOrder,
    ids: { bar: 'bonusPresetBar', add: 'bonusPresetAddBtn', sel: 'bonusPresetSel', update: 'bonusPresetUpdate',
      del: 'bonusPresetDel', name: 'bonusPresetName', save: 'bonusPresetSave', live: 'bonusPresetLive',
      panel: 'bonusFilterPanel' },
    apply: s => setCats(asCats(s)),
    current: () => ({ cats }),
    same: s => {
      const a = asCats(s);
      if (a === 'all' || cats === 'all') return a === cats;
      return a.length === cats.length && a.every(c => cats.includes(c));
    },
    openPanel: () => setFolded(false),
  });
  markActive = presets.markActive;
  changed = onChange;
  sync();
  $('bonusCatAll').addEventListener('click', () => setCats('all'));
  $('bonusCatNone').addEventListener('click', () => setCats([]));
  on(EV.lang, () => { presets.render(); sync(); });
}
