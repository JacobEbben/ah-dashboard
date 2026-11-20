// @ts-check
// The bargains view's controller: loads the bargains of the saved stores,
// owns them, and is the one place that re-renders the view. The other
// bargains modules (filters, presets, stores, metrics) only emit events:
//
//   EV.refilter     a setting or the overview selection changed
//   EV.lists        liked / hidden products changed
//   EV.stores       the store list changed ({reload}: a store was added)
//   EV.bonusStatus  this week's Bonus loaded (cards' Bonus tag, "Bonus" filter)
//   EV.cols, EV.lang, window resize
//
// No token handling: the server uses its own AH login.
import { api, ApiError, errorText } from '../core/api.js';
import { $ } from '../core/dom.js';
import { emit, on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { storage } from '../core/storage.js';
import { isHidden } from '../lists/blacklist.js';
import { isLiked } from '../lists/likes.js';
import { buildCategoryFilter, currentFilter, groupBy, initFilters, saveSettings, setFilterPanelVisible,
  sortBy } from './filters.js';
import { clearFocus, focusItems, renderFocusBar, renderMetrics } from './metrics.js';
import { filterOffers } from './model.js';
import { initPresets } from './presets.js';
import { renderSpecialNotice } from './special.js';
import { clearResults, currentCols, render } from './render.js';
import { initStores, myStores, openStoresPanel, storeRefs } from './stores.js';

/** @typedef {import('../core/offers.js').Offer} Offer */

/** @type {Offer[]} the loaded bargains (all stores) */
let allItems = [];
/** @type {{csrf: string}|null} while signed in as admin */
let admin = null;
/** This week's Bonus as far as known (EV.bonusStatus). */
let bonus = { status: 'none', deals: /** @type {Map<number, string>} */ (new Map()) };
let lastCols = 0;

/** Status line; ``busy`` adds the rotating loading icon. */
function setStatus(text = '', isError = false, busy = false) {
  const el = $('status');
  el.className = isError ? 'status error' : busy ? 'status busy' : 'status';
  el.textContent = text;
}

/** While loading, the current results stay in place (dimmed), so nothing jumps. @param {boolean} busy */
function setBusy(busy) {
  const results = $('results');
  results.classList.toggle('busy', busy);
  results.setAttribute('aria-busy', busy ? 'true' : 'false');
  $('summary').classList.toggle('busy', busy);
}

/**
 * Apply the settings and draw the view. Without bargains only the settings
 * are saved and the overview shows its hint.
 */
function refresh() {
  saveSettings();
  renderMetrics(allItems);
  renderSpecialNotice(allItems);   // also after a language switch or hiding the product
  if (!allItems.length) return;
  const common = { groupBy: groupBy(), sortBy: sortBy(), bonusDeals: bonus.deals };
  // A number clicked in the overview: show exactly those items (hidden ones
  // too, dimmed), ignoring the filters.
  const focused = focusItems(allItems);
  renderFocusBar();
  if (focused) { lastCols = render(focused, { ...common, focused: true }); return; }
  const result = filterOffers(allItems, currentFilter(), { isHidden });
  lastCols = render(result.items, { ...common, hiddenCount: result.hiddenCount,
    otherStores: result.otherStores });
}

/** Status text for per-store failures ('' when none). @param {any[]} errs */
function storeErrorsText(errs) {
  if (!errs.length) return '';
  /** @param {any} e */
  const ref = e => (e.ref ? ' ' + t('errorRef', e.ref) : '');
  const authFailed = errs.find(e => e.code === 'ah_auth_failed');
  // The server's AH login is gone: one message, not one per store.
  if (authFailed) return t('ah_auth_failed') + ref(authFailed);
  // Per store: why (not known yet / busy / failed), translated; details are in the server log.
  /** @param {any} e */
  const why = e => (e.code === 'store_unknown' || e.code === 'store_busy' ? t(e.code) : t('store_failed'));
  return t('someFailed') + '\n' + errs.map(e => `${e.store}: ${why(e)}${ref(e)}`).join('\n');
}

/** Show these bargains (replacing what was loaded). @param {Offer[]} items */
function setItems(items) {
  allItems = items;
  buildCategoryFilter(allItems);
  setFilterPanelVisible(allItems.length > 0);
  if (allItems.length) refresh();
  else { clearResults(); renderMetrics(allItems); renderSpecialNotice(allItems); }
}

export async function loadBargains({ force = false } = {}) {
  if (!myStores().length) {
    setStatus(t('noStoresYet'), true);
    openStoresPanel();
    return;
  }
  const buttons = [$('go'), $('forceRefresh')];
  buttons.forEach(b => { b.disabled = true; });
  setStatus(t('loading'), false, true);
  setBusy(true);
  try {
    const data = force && admin
      ? await api.forceFetchBargains(storeRefs(), admin.csrf)
      : await api.fetchBargains(storeRefs());
    setItems(data.items || []);
    const errs = data.storeErrors || [];
    setStatus(storeErrorsText(errs), errs.length > 0);
    // In the background: this week's Bonus, so cards can say "also in the Bonus".
    if (allItems.length) emit(EV.needBonus);
  } catch (err) {
    clearResults();
    renderSpecialNotice([]);
    setFilterPanelVisible(false);
    setStatus(errorText(err), true);
    // Admin session ended (expired / logged out elsewhere): hide the button.
    if (err instanceof ApiError && (err.code === 'admin_required' || err.code === 'csrf_invalid')) setAdmin(null);
  } finally {
    setBusy(false);
    buttons.forEach(b => { b.disabled = false; });
  }
}

/** Force refresh is admin-only: show the button only with an admin session. @param {{csrf: string}|null} value */
function setAdmin(value) {
  admin = value;
  $('forceRefresh').hidden = !admin;
}

async function refreshAdminState() {
  try {
    const s = await api.session();
    setAdmin(s.admin && s.csrf ? { csrf: s.csrf } : null);
  } catch (e) {
    setAdmin(null);
  }
}

/** The store list changed. @param {{reload?: boolean}} [detail] */
function storesChanged(detail) {
  if (detail && detail.reload) { loadBargains(); return; }
  // Removed or renamed: drop and relabel the loaded bargains instead of fetching again.
  const names = new Map(myStores().map(s => [s.id, s.name]));
  setItems(allItems.filter(o => names.has(Number(o.storeId)))
    .map(o => ({ ...o, storeName: names.get(Number(o.storeId)) || o.storeName })));
}

export function hasStores() { return myStores().length > 0; }

export function initBargains() {
  initStores();
  initFilters(() => allItems);   // restores remembered settings before presets mark the active one
  initPresets();
  setAdmin(null);                // hidden until /api/session confirms an admin login
  // Older versions kept the visitor's AH token in the browser; it is no longer used.
  storage.remove('ah_token');
  $('forceRefresh').addEventListener('click', () => loadBargains({ force: true }));

  on(EV.refilter, (/** @type {{clearFocus?: boolean}|undefined} */ d) => {
    if (d && d.clearFocus) clearFocus();
    refresh();
  });
  on(EV.lists, () => { if (allItems.length) refresh(); });   // likes break ties / "Liked" filter, hidden ones go
  on(EV.lang, refresh);
  on(EV.stores, storesChanged);
  on(EV.bonusStatus, (/** @type {{status: string, deals: Map<number, string>}} */ d) => {
    bonus = d;
    if (allItems.length) refresh();
  });
  // The column count changes with the window width or the cards-per-row
  // slider: re-render so the section spans stay right.
  const rerender = () => { if (allItems.length && currentCols() !== lastCols) refresh(); };
  window.addEventListener('resize', rerender);
  on(EV.cols, rerender);
  refreshAdminState();           // shows Force refresh for a signed-in admin
  renderMetrics(allItems);
}
