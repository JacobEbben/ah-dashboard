// @ts-check
// Server-provided settings, embedded in the page as JSON (index.html,
// web/routes.py). No secrets: the CSRF token is the session's own.

/**
 * @typedef {object} AppConfig
 * @property {number} maxStores
 * @property {string[]} syncKeys localStorage keys that follow a signed-in user (the server's list)
 * @property {string|null} user signed-in user, or null
 * @property {string|null} csrf CSRF token for /api/prefs (signed in only)
 */

/** @returns {AppConfig} */
function read() {
  const fallback = { maxStores: 25, syncKeys: [], user: null, csrf: null };
  try {
    const el = typeof document !== 'undefined' ? document.getElementById('app-config') : null;
    return el ? { ...fallback, ...JSON.parse(el.textContent || '{}') } : fallback;
  } catch (e) {
    return fallback;
  }
}

export const config = read();
