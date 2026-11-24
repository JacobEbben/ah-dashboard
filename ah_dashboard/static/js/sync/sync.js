// @ts-check
// Settings sync for signed-in users. Signing in is optional; without it
// everything stays in this browser only.
//
// The synced keys (the server's auth/users.SYNC_KEYS, sent in the page's
// app-config as syncKeys: the only list) are uploaded a moment after they change. On page load the server copy is
// fetched: if it is newer than what this browser last synced (another device
// changed something), it replaces the local values and the page reloads once
// to show them. The same check runs again when the tab comes back into view
// (at most every PULL_INTERVAL, and not while a change waits to be uploaded),
// so a tab left open on a phone picks up e.g. a cart filled on a laptop.
// The first sign-in on a device whose account has nothing saved
// yet uploads this device's settings.
//
// When BOTH this browser (unsent changes) and another device changed things
// since the last sync, the two are merged instead of one overwriting the
// other (merge.js): liked and hidden products and presets are combined; for
// the other settings this browser's values win.
import { config } from '../core/config.js';
import { onStorageChange, storage } from '../core/storage.js';
import { mergePrefs } from './merge.js';

const SYNC_KEYS = config.syncKeys;
const META_KEY = 'ah_sync';           // {user, at, dirty}: this browser's sync state (not synced)
const PUSH_DELAY = 1500;
const PULL_INTERVAL = 30 * 1000;     // when the tab is shown again: ask the server at most this often
// Browsers refuse "keepalive" requests (the upload while a page closes) with
// bodies over 64 KiB; larger settings are sent as a normal request instead.
const KEEPALIVE_MAX = 60 * 1024;

/** @type {any} */
let timer = 0;
let applying = false;
let lastPull = 0;
let pulling = false;

/** @returns {{user?: string|null, at?: number, dirty?: boolean}} */
function readMeta() {
  const m = storage.getJSON(META_KEY, {});
  return m && typeof m === 'object' && !Array.isArray(m) ? m : {};
}
/** @param {object} m */
function writeMeta(m) { storage.setQuiet(META_KEY, JSON.stringify(m)); }

/** @returns {Record<string, string>} */
function localPrefs() {
  /** @type {Record<string, string>} */
  const prefs = {};
  for (const k of SYNC_KEYS) { const v = storage.get(k); if (v !== null) prefs[k] = v; }
  return prefs;
}

/**
 * @param {string} method
 * @param {object} [body]
 * @param {boolean} [keepalive]
 */
async function call(method, body, keepalive = false) {
  const text = body ? JSON.stringify(body) : undefined;
  const resp = await fetch('/api/prefs', {
    method,
    credentials: 'same-origin',
    keepalive: keepalive && text !== undefined && text.length <= KEEPALIVE_MAX,
    headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json', 'X-CSRF-Token': config.csrf || '' } : {}) },
    body: text,
  });
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp.json();
}

/** @param {boolean} [keepalive] */
async function push(keepalive = false) {
  clearTimeout(timer);
  timer = 0;
  const meta = readMeta();
  try {
    const { updatedAt } = await call('PUT', { prefs: localPrefs() }, keepalive);
    writeMeta({ user: config.user, at: updatedAt, dirty: false });
  } catch (e) {
    writeMeta({ ...meta, user: config.user, dirty: true });   // try again on the next change or visit
  }
}

function schedulePush() {
  writeMeta({ ...readMeta(), user: config.user, dirty: true });
  clearTimeout(timer);
  timer = setTimeout(push, PUSH_DELAY);
}

/** @param {Record<string, unknown>} prefs */
function apply(prefs) {
  applying = true;
  let differs = false;
  for (const k of SYNC_KEYS) {
    const next = Object.hasOwn(prefs, k) ? String(prefs[k]) : null;
    if (storage.get(k) !== next) {
      differs = true;
      if (next === null) storage.remove(k); else storage.set(k, next);
    }
  }
  applying = false;
  return differs;
}

/** Call first thing on page load. Returns false when the page is reloading. */
export async function initSync() {
  if (!config.user || !config.csrf) {
    if (readMeta().user) writeMeta({});               // signed out: forget the old sync state
    return true;
  }
  onStorageChange(key => { if (!applying && SYNC_KEYS.includes(key)) schedulePush(); });
  // Upload pending changes when the page is hidden (switching apps on a phone,
  // closing the tab): earlier than "pagehide", while a request can still finish.
  // Back to the tab: another device may have changed settings (e.g. the cart) meanwhile.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') { if (timer) push(true); return; }
    if (timer || pulling || Date.now() - lastPull < PULL_INTERVAL) return;
    pulling = true;
    pull().finally(() => { pulling = false; });
  });
  window.addEventListener('pagehide', () => { if (timer) push(true); });
  return pull();
}

/**
 * Compare with the server's copy and take it over (or merge) when another
 * device changed something; reloads the page when that changed anything here.
 * Returns false when the page is reloading.
 */
async function pull() {
  lastPull = Date.now();
  let server;
  try { server = await call('GET'); } catch (e) { return true; }   // offline: keep local settings
  const meta = readMeta();
  const sameUser = meta.user === config.user;
  if (!server.updatedAt) { push(); return true; }                  // account has nothing yet: upload
  if (sameUser && meta.dirty) {
    if (meta.at && server.updatedAt > meta.at) {
      // Changed here AND on another device since the last sync: merge both.
      const differs = apply(mergePrefs(localPrefs(), server.prefs || {}));
      await push();
      if (differs) { window.location.reload(); return false; }
      return true;
    }
    push();                                                        // unsynced local changes win
    return true;
  }
  if (sameUser && meta.at && server.updatedAt <= meta.at) return true;   // already up to date
  const differs = apply(server.prefs || {});
  writeMeta({ user: config.user, at: server.updatedAt, dirty: false });
  if (differs) { window.location.reload(); return false; }
  return true;
}
