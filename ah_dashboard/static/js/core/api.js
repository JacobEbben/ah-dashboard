// @ts-check
// Calls to this app's backend (/api/*). No credentials: the server uses its
// own AH login (configured server-side) for every request to AH.
import { t, hasKey } from './i18n.js';

export class ApiError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   * @param {number} status
   * @param {string} [ref]
   */
  constructor(code, message, status, ref) {
    super(message);
    this.code = code;
    this.status = status;
    this.ref = ref;  // server log reference for support/debugging
  }
}

/**
 * @param {string} path
 * @param {{method?: string, body?: unknown, csrf?: string}} [options]
 * @returns {Promise<any>}
 */
async function request(path, { method = 'GET', body, csrf } = {}) {
  /** @type {Record<string, string>} */
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (csrf) headers['X-CSRF-Token'] = csrf;
  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',  // send the admin session cookie (same site only)
  });
  let data = null;
  try { data = await resp.json(); } catch (e) { /* non-JSON */ }
  if (!resp.ok) {
    const err = (data && data.error) || {};
    if (resp.status === 401 && err.code === 'login_required') {
      window.location.assign('/login');   // session ended (signed out, disabled, expired)
    }
    throw new ApiError(err.code || `http_${resp.status}`, err.message || `HTTP ${resp.status}`, resp.status, err.ref);
  }
  return data || {};
}

export const api = {
  /** @param {string} postcode */
  searchStores: postcode =>
    request('/api/stores?postcode=' + encodeURIComponent(postcode)),
  /** Bargains (offers) from the server's 5-minute cache. @param {{id: number, name: string}[]} stores */
  fetchBargains: stores =>
    request('/api/bargains', { method: 'POST', body: { stores } }),
  /**
   * Reload the stores from AH now. Admin only: needs the session's CSRF token.
   * @param {{id: number, name: string}[]} stores
   * @param {string} csrf
   */
  forceFetchBargains: (stores, csrf) =>
    request('/api/bargains/force', { method: 'POST', body: { stores }, csrf }),
  /** This week's national Bonus: {period, categories, items, errors}. */
  fetchBonus: () => request('/api/bonus'),
  /** {admin: bool, csrf?: string} for the current browser session. */
  session: () => request('/api/session'),
};

/**
 * User-facing text for an error: translated if the code is known, otherwise a
 * generic message. A server log reference is appended when there is one.
 */
/** @param {unknown} err */
export function errorText(err) {
  let text;
  if (err instanceof ApiError) text = hasKey(err.code) ? t(err.code) : (err.message || t('unknown'));
  else text = t('requestFailed') + t('unknown');  // network failure etc.; no raw details
  const ref = err instanceof ApiError ? err.ref : undefined;
  return ref ? `${text} ${t('errorRef', ref)}` : text;
}
