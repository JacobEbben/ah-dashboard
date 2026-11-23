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
//   bargains/, bonus/  the two views (feature folders)                         — core, lists, ui
// Features never import each other: they talk through core/events.js.
import { $ } from './core/dom.js';
import { emit, EV } from './core/events.js';
import { applyStaticI18n, getLang, setLang } from './core/i18n.js';
import { adminCsrf, hasStores, initBargains, loadBargains, setAdmin } from './bargains/index.js';
import { initBonus, loadBonus } from './bonus/index.js';
import { initBlacklist } from './lists/blacklist.js';
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
  initListsPanel();
  initBargains();
  initBonus();
  // Refresh reloads whichever view is shown.
  $('go').addEventListener('click', () => (currentView() === 'bonus' ? loadBonus({ force: true }) : loadBargains()));
  // Force refresh (admins only): reload the current view's data from AH, bypassing the server's cache.
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
