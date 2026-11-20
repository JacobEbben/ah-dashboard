// @ts-check
// "Overview" in the Stores panel: figures per selected store for all loaded
// bargains (not affected by the filters): number of products, units in stock,
// liked, hidden, ending today, average discount and a count per discount level.
// The "All stores" row counts each product once per discount, like the cards.
//
// Every non-zero number is clickable: it shows exactly the items it counts
// (the "focus", owned by this module), bypassing the normal filters, until
// "Back to filters" or a filter change. Clicks emit EV.refilter; the bargains
// controller asks focusItems() what to show.
import { $, html } from '../core/dom.js';
import { emit, EV } from '../core/events.js';
import { parseDay, sameDay } from '../core/format.js';
import { t } from '../core/i18n.js';
import { productKey } from '../core/offers.js';
import { isHidden } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { isSpecial } from './model.js';
import { colorClass, myStores } from './stores.js';

/** @typedef {import('../core/offers.js').Offer} Offer */
/** @typedef {{store: number|null, kind: string, level?: number}} Focus a clicked number */

/** @type {Focus|null} */
let focus = null;
/** @type {Offer[]} */
let allItems = [];

/** Leave the overview selection (a filter changed). */
export function clearFocus() { focus = null; }

/** @param {Offer} o */
const pct = o => (o.discount == null || isNaN(Number(o.discount)) ? null : Number(o.discount));

/** @param {Offer} o */
function endsToday(o) {
  const day = o.expires ? parseDay(o.expires) : null;
  return !!day && sameDay(day, new Date());
}

// What each clickable column selects.
/** @type {Record<string, (o: Offer, f: Focus) => boolean>} */
const KINDS = {
  products: () => true,
  units: o => Number(o.stock) > 0,
  liked: o => isLiked(o),
  hidden: o => isHidden(o),
  today: endsToday,
  level: (o, f) => pct(o) === f.level,
  special: isSpecial,                  // the special-product alert's button
};

/** @param {number|null} storeId */
const storeItems = storeId => (storeId == null ? allItems : allItems.filter(o => Number(o.storeId) === storeId));

/** @param {Offer[]} items @param {number} units */
function stats(items, units) {
  /** @type {Map<number, number>} */
  const levels = new Map();
  let discSum = 0, discN = 0;
  for (const it of items) {
    const p = pct(it);
    if (p == null) continue;
    levels.set(p, (levels.get(p) || 0) + 1);
    discSum += p; discN += 1;
  }
  return {
    products: items.length,
    units,
    liked: items.filter(isLiked).length,
    hidden: items.filter(isHidden).length,
    today: items.filter(endsToday).length,
    avg: discN ? Math.round(discSum / discN) : null,
    levels,
  };
}

/** @param {Offer[]} items */
const unitsOf = items => items.reduce((s, o) => s + (Number(o.stock) || 0), 0);

// ---- focus (a clicked number) -------------------------------------------------
/**
 * Items selected by the clicked number, or null when nothing is selected.
 * @param {Offer[]} items all loaded bargains
 * @returns {Offer[]|null}
 */
export function focusItems(items) {
  allItems = items;
  const f = focus;
  if (!f) return null;
  if (f.store != null && !myStores().some(s => s.id === f.store)) {  // store removed
    focus = null;
    return null;
  }
  const match = KINDS[f.kind];
  return match ? storeItems(f.store).filter(it => match(it, f)) : null;
}

/** @type {Record<string, string>} */
const KIND_LABELS = { products: 'mProducts', units: 'mUnits', liked: 'mLiked', hidden: 'mHidden', today: 'mToday',
  special: 'mSpecial' };

/** @param {Focus} f */
function focusLabel(f) {
  const what = f.kind === 'level' ? `-${f.level}%` : t(KIND_LABELS[f.kind]);
  const store = f.store == null ? t('mTotal') : (myStores().find(s => s.id === f.store) || { name: '' }).name;
  return t('focusAt', what, store);
}

/** The bar above the results while a number from the overview is selected. */
export function renderFocusBar() {
  const bar = $('focusBar');
  const f = focus;
  bar.hidden = !f;
  if (!f) { bar.innerHTML = ''; return; }
  bar.innerHTML = html`<span class="focustext"><span class="smeta">${t('focusShowing')}</span> <b>${focusLabel(f)}</b></span><button type="button" class="mini" id="focusClear">${t('focusClear')}</button>`;
  $('focusClear').addEventListener('click', () => emit(EV.refilter, { clearFocus: true }));
}

/** @param {number|null} store @param {string} kind @param {number|undefined} level */
function select(store, kind, level) {
  const same = focus && focus.store === store && focus.kind === kind && focus.level === level;
  focus = same ? null : { store, kind, level };  // clicking the active number again: back
  emit(EV.refilter);
  if (focus) {
    const bar = $('focusBar');
    if (bar.scrollIntoView) bar.scrollIntoView({ block: 'start' });
  }
}

/**
 * Show exactly the items of one kind at every store (bypassing the filters),
 * e.g. the special product from its alert. Not a toggle.
 * @param {string} kind
 */
export function focusOn(kind) {
  focus = { store: null, kind };
  emit(EV.refilter);
  const bar = $('focusBar');
  if (bar.scrollIntoView) bar.scrollIntoView({ block: 'start' });
}

let listening = false;
/** @param {HTMLElement} box */
function listen(box) {
  if (listening) return;
  listening = true;
  box.addEventListener('click', e => {
    const b = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest('button.mcell'));
    if (!b || !box.contains(b)) return;
    const store = b.dataset.store === 'all' ? null : Number(b.dataset.store);
    const level = b.dataset.level === undefined ? undefined : Number(b.dataset.level);
    select(store, b.dataset.kind || '', level);
  });
}

// ---- table ---------------------------------------------------------------------
/** The overview table. @param {Offer[]} items all loaded bargains */
export function renderMetrics(items) {
  allItems = items;
  const box = $('storeMetrics');
  if (!box) return;
  listen(box);
  const stores = myStores();
  if (!items.length || !stores.length) {
    box.innerHTML = html`<span class="smeta">${t('metricsHint')}</span>`;
    return;
  }

  /** @type {any[]} */
  const rows = stores.map(s => {
    const own = storeItems(s.id);
    return { id: String(s.id), name: s.name, color: colorClass(s.color), ...stats(own, unitsOf(own)) };
  });
  if (rows.length > 1) {
    /** @type {Map<string, Offer>} */
    const unique = new Map();
    for (const o of items) {
      const key = productKey(o) + '|' + pct(o);
      if (!unique.has(key)) unique.set(key, o);
    }
    rows.push({ id: 'all', name: t('mTotal'), color: null, total: true,
      ...stats([...unique.values()], unitsOf(items)) });
  }

  const levels = [...new Set(rows.flatMap(r => [...r.levels.keys()]))].sort((a, b) => b - a);
  const f = focus;
  /** @param {any} row @param {string} kind @param {number} [level] */
  const isActive = (row, kind, level) => !!f && (f.store == null ? 'all' : String(f.store)) === row.id &&
    f.kind === kind && f.level === level;
  // A number; non-zero numbers are buttons that show the counted items.
  /** @param {any} row @param {string} kind @param {number} n @param {number} [level] */
  const cell = (row, kind, n, level) => {
    if (!n) return html`<span class="zero">–</span>`;
    const label = kind === 'level' ? `-${level}%` : t(KIND_LABELS[kind]);
    return html`<button type="button" class="mcell${isActive(row, kind, level) ? ' active' : ''}" data-store="${row.id}" data-kind="${kind}" ${level === undefined ? '' : html`data-level="${level}"`} title="${t('focusAt', label, row.name)}" aria-pressed="${isActive(row, kind, level) ? 'true' : 'false'}">${n}</button>`;
  };

  box.innerHTML = html`
    <div class="metricswrap">
      <table class="metrics">
        <thead><tr>
          <th scope="col" class="mname">${t('mStore')}</th>
          <th scope="col">${t('mProducts')}</th>
          <th scope="col">${t('mUnits')}</th>
          <th scope="col">${t('mLiked')}</th>
          <th scope="col">${t('mHidden')}</th>
          <th scope="col">${t('mToday')}</th>
          <th scope="col">${t('mAvg')}</th>
          ${levels.map(l => html`<th scope="col" class="mlevel"><span class="lvl">-${l}%</span></th>`)}
        </tr></thead>
        <tbody>
          ${rows.map(r => html`<tr class="${r.total ? 'mtotal' : ''}">
            <th scope="row" class="mname">${r.color ? html`<span class="dot ${r.color}"></span>` : ''}${r.name}</th>
            <td>${cell(r, 'products', r.products)}</td>
            <td>${cell(r, 'units', r.units)}</td>
            <td>${cell(r, 'liked', r.liked)}</td>
            <td>${cell(r, 'hidden', r.hidden)}</td>
            <td>${cell(r, 'today', r.today)}</td>
            <td class="mavg">${r.avg == null ? html`<span class="zero">–</span>` : html`${r.avg}%`}</td>
            ${levels.map(l => html`<td>${cell(r, 'level', r.levels.get(l) || 0, l)}</td>`)}
          </tr>`)}
        </tbody>
      </table>
    </div>`;
}
