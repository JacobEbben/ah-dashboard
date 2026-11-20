// @ts-check
// Formatting of prices and dates.
import { t } from './i18n.js';

/** @param {unknown} v */
export function euro(v) {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  return isNaN(n) ? String(v) : '€ ' + n.toFixed(2);
}

// Parse "YYYY-MM-DD" (optionally followed by a time) as a local calendar day,
// so a date-only value is not shifted by the UTC conversion of new Date().
/**
 * @param {unknown} s
 * @returns {Date|null}
 */
export function parseDay(s) {
  const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})/.exec(String(s));
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  const d = new Date(String(s));
  return isNaN(d.getTime()) ? null : d;
}

/** @param {Date} a @param {Date} b */
export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "14 nov 2026" with month names in the UI language. */
/** @param {unknown} s */
export function fmtDate(s) {
  const d = parseDay(s);
  return d ? `${d.getDate()} ${t('months')[d.getMonth()]} ${d.getFullYear()}` : String(s);
}

/**
 * Case- and accent-insensitive text for searching ("creme" finds "crème").
 * @param {unknown} s
 */
export function fold(s) {
  return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

/**
 * The words of a search query, folded; a text matches when it contains all.
 * @param {string} query
 */
export function searchTerms(query) {
  return fold(query).split(/\s+/).filter(Boolean);
}
