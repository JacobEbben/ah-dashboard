// @ts-check
// Safe localStorage wrapper: per-browser conveniences only. Every access is
// guarded because storage can be unavailable (private mode, blocked cookies).
// Keys must never change: renaming one wipes every visitor's settings.
// ALL localStorage access goes through this module (it tells the settings sync).

export const KEYS = {
  settings: 'ah_settings',
  presets: 'ah_presets',
  presetOrder: 'ah_preset_order',  // preset names in the user's own order
  stores: 'ah_stores',
  lang: 'ah_lang',
  view: 'ah_view',                // 'bargains' | 'bonus'
  bonusCat: 'ah_bonus_cat',       // old: the Bonus category shown; removed on load (bonus/filters.js)
  bonusFilters: 'ah_bonus_filters',  // Bonus view: {liked, cats} (the Liked pill, the categories; older versions kept other filters here: ignored)
  bonusFiltersFolded: 'ah_bonus_filters_folded',   // the Bonus view's Filters panel
  bonusPresets: 'ah_bonus_presets',            // Bonus presets {name: {cats}}
  bonusPresetOrder: 'ah_bonus_preset_order',   // Bonus preset names in the user's own order
  bonusGroups: 'ah_bonus_groups',  // old: category groups [{id, name, cats}]; turned into presets once, then removed
  bonusGroupsFolded: 'ah_bonus_groups_folded',   // old; unused, kept so the name stays reserved
  bonusNoticeSeen: 'ah_bonus_notice',  // start date of the Bonus week whose "liked on Bonus" notice was closed
  theme: 'ah_theme',              // 'dark' (absent = light, the default)
  filtersFolded: 'ah_filters_folded',             // bargains' Filters panel
  storesFolded: 'ah_stores_folded',
  listsFolded: 'ah_lists_folded',     // "Liked & hidden products" panel
  blacklist: 'ah_blacklist',      // hidden products [{key, title}]
  liked: 'ah_liked',              // liked products [{key, title}]
  showHidden: 'ah_show_hidden',   // "Show hidden products" toggle
  inStore: 'ah_instore',         // "I'm in this store": {id, until}; per browser, not synced, expires
  cart: 'ah_cart',                // the cart: [{key, kind, title, …}] (core/cart.js), synced
  cartRemoved: 'ah_cart_removed', // removed from the cart today: {day, keys} (core/cart.removedOn), synced
  cartMinDisc: 'ah_cart_min_disc', // the cart's "🛒 N%+": the minimum discount, e.g. '70' (core/cart.cartMinDiscount), synced
  cartStore: 'ah_cart_store',     // the cart's store filter: a store id (absent = all stores); per browser, not synced
  cols: 'ah_cols',                // cards per row, '1'..'8' (absent = auto); per browser, not synced
};

// Listeners told about every change (the settings sync uses this).
/** @type {((key: string) => void)[]} */
const listeners = [];
/** @param {(key: string) => void} fn */
export function onStorageChange(fn) { listeners.push(fn); }
/** @param {string} key */
function changed(key) { for (const fn of listeners) { try { fn(key); } catch (e) { /* ignore */ } } }

export const storage = {
  /** @param {string} key @returns {string|null} */
  get(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  },
  // Listeners only hear about real changes (the app rewrites some values with
  // the same content on start; that must not trigger a settings upload).
  /** @param {string} key @param {string} value */
  set(key, value) {
    if (this.get(key) === String(value)) return;
    try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    changed(key);
  },
  /** @param {string} key */
  remove(key) {
    if (this.get(key) === null) return;
    try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
    changed(key);
  },
  // Bookkeeping that must not count as a settings change (e.g. the sync's
  // own state): written without telling the listeners.
  /** @param {string} key @param {string} value */
  setQuiet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  },
  /**
   * @param {string} key
   * @param {any} [fallback]
   * @returns {any}
   */
  getJSON(key, fallback = null) {
    const raw = this.get(key);
    if (raw === null) return fallback;
    try { return JSON.parse(raw); } catch (e) { return fallback; }
  },
  /** @param {string} key @param {unknown} value */
  setJSON(key, value) {
    this.set(key, JSON.stringify(value));
  },
};
