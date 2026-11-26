// @ts-check
// Merging this browser's settings with the server's copy, when BOTH changed
// since the last sync (see sync.js): liked and hidden products, the cart and
// presets are combined (what was removed from the cart today too); for every other setting this browser's value wins.
// Pure functions (no storage, no DOM), so they can be tested on their own.
import { mergeRemoved } from '../core/cart.js';

/** @typedef {Record<string, string>} Prefs localStorage key -> text value */

/**
 * @param {string|null|undefined} text
 * @param {any} fallback
 */
function parse(text, fallback) {
  try { return text == null ? fallback : JSON.parse(text); } catch (e) { return fallback; }
}

/**
 * [{key, title, …}] lists (liked / hidden / the cart): everything from both, by key.
 * @param {string|null} localText
 * @param {string|null} serverText
 */
export function mergeKeyed(localText, serverText) {
  const local = parse(localText, []), server = parse(serverText, []);
  if (!Array.isArray(local) || !Array.isArray(server)) return localText ?? serverText;
  const seen = new Set(local.filter(e => e && typeof e.key === 'string').map(e => e.key));
  const extra = server.filter(e => e && typeof e.key === 'string' && !seen.has(e.key));
  return JSON.stringify([...extra, ...local]);   // newest (this browser's) last
}

/**
 * Presets {name: settings}: both; on the same name this browser's wins.
 * @param {string|null} localText
 * @param {string|null} serverText
 */
export function mergePresets(localText, serverText) {
  const local = parse(localText, {}), server = parse(serverText, {});
  /** @param {unknown} v */
  const ok = v => !!v && typeof v === 'object' && !Array.isArray(v);
  if (!ok(local) || !ok(server)) return localText ?? serverText;
  return JSON.stringify({ ...server, ...local });
}

/**
 * What was removed from the cart today ({day, keys}): both on the same day, else the later day's.
 * @param {string|null} localText
 * @param {string|null} serverText
 */
export function mergeCartRemoved(localText, serverText) {
  const merged = mergeRemoved(parse(localText, null), parse(serverText, null));
  return merged ? JSON.stringify(merged) : localText ?? serverText;
}

/** @type {Record<string, (local: string|null, server: string|null) => string|null>} */
const MERGERS = { ah_liked: mergeKeyed, ah_blacklist: mergeKeyed, ah_cart: mergeKeyed, ah_presets: mergePresets,
  ah_cart_removed: mergeCartRemoved };

/**
 * This browser's settings combined with the server's.
 * @param {Prefs} local
 * @param {Prefs} server
 * @returns {Prefs}
 */
export function mergePrefs(local, server) {
  const out = { ...local };
  for (const [key, merge] of Object.entries(MERGERS)) {
    const value = merge(local[key] ?? null, server[key] ?? null);
    if (value != null) out[key] = value;
  }
  return out;
}
