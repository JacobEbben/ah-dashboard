// @ts-check
// Entry point and composition root of the page: starts every module and wires
// the few things that span both views (language, the Refresh button, which
// view loads what). Loaded as an ES module (deferred), so the DOM is ready.
//
// Layers (tests/test_architecture.py checks the imports):
//   core/      dom, storage, i18n, api, events, config, format, unit, offers — no app imports
//   lists/     liked & hidden products                                          — core
//   ui/        cards, density, theme, view, reorder / rename / presets widgets  — core, lists
//   sync/      settings sync for signed-in users                                — core
//   bargains/, bonus/, cart/  the three views (feature folders)              — core, lists, ui
// Features never import each other: they talk through core/events.js.
import { $ } from './core/dom.js';
import { emit, EV } from './core/events.js';
import { applyStaticI18n, getLang, setLang } from './core/i18n.js';
import { adminCsrf, hasStores, initBargains, loadBargains, openStoreSearch, setAdmin } from './bargains/index.js';
import { initBonus, loadBonus } from './bonus/index.js';
import { initCart } from './cart/index.js';
import { initBlacklist } from './lists/blacklist.js';
import { cartHasBonus, initCartList } from './lists/cart.js';
import { initLikes, likedCount } from './lists/likes.js';
import { initListsPanel } from './lists/panel.js';
import { initSync } from './sync/sync.js';
import { initDensity } from './ui/density.js';
import { initTheme } from './ui/theme.js';
import { currentView, initViewSwitch } from './ui/view.js';

function initLanguage() {
  const select = $('lang');
  select.value = getLang();
  select.addEventListener('change', (/** @type {any} */ e) => {
    setLang(e.target.value);
    applyStaticI18n();
    emit(EV.lang);   // every module re-renders its own generated text
  });
}

function start() {
  applyStaticI18n();
  initLanguage();
  initTheme();
  initDensity();
  initBlacklist();
  initLikes();
  initCartList();   // before any card is drawn (cards show whether they are in the cart)
  initListsPanel();
  initBargains();
  initBonus();
  initCart();       // after the bargains: it uses the store list
  // The cart's store bar: its ＋ goes to the Stores panel on the bargains view.
  $('cartStoreManageBtn').addEventListener('click', openStoreSearch);
  // Refresh reloads whichever view is shown; the cart checks its items against both.
  $('go').addEventListener('click', () => {
    if (currentView() === 'bonus') { loadBonus({ force: true }); return; }
    loadBargains();
    if (currentView() === 'cart' && cartHasBonus()) loadBonus({ force: true, quiet: true });
  });
  // Force refresh (admins only): reload the current view's data from AH, bypassing the server's cache.
  // On the cart: the bargains (the Bonus has its own Force refresh on its view).
  $('forceRefresh').addEventListener('click', () => (currentView() === 'bonus'
    ? loadBonus({ fromAh: adminCsrf(), onAdminLost: () => setAdmin(null) })   // this week or next, as shown
    : loadBargains({ force: true })));
  initViewSwitch({ bonus: () => loadBonus() });   // loads the Bonus when first shown

  // Load the saved stores straight away (the server has its own AH login).
  if (hasStores()) loadBargains();
  // Liked products: load this week's Bonus quietly, so the notice can say which are on it.
  if (likedCount()) loadBonus({ quiet: true });
}

// Signed in: pull settings saved on other devices first (may reload once).
if (await initSync()) start();
