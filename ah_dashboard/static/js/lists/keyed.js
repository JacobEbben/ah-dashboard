// @ts-check
// A product list kept in localStorage as [{key, title}] (liked and hidden
// products), newest last, capped. Keys are productKey()s (AH product ids),
// so an entry applies in both views and at every store.
import { storage } from '../core/storage.js';

const MAX_ENTRIES = 2000;   // keep localStorage small even after years of use

export class KeyedList {
  /** @param {string} storageKey */
  constructor(storageKey) {
    this.storageKey = storageKey;
    /** @type {Map<string, string>} key -> title */
    this.map = new Map();
  }

  load() {
    const raw = storage.getJSON(this.storageKey, []);
    this.map = new Map();
    for (const e of Array.isArray(raw) ? raw : []) {
      if (e && typeof e.key === 'string' && e.key) this.map.set(e.key, String(e.title || e.key).slice(0, 120));
    }
  }

  save() {
    const entries = [...this.map].slice(-MAX_ENTRIES).map(([key, title]) => ({ key, title }));
    storage.setJSON(this.storageKey, entries);
  }

  get size() { return this.map.size; }

  /** @param {string} key */
  has(key) { return this.map.has(key); }

  /** Add (or move to the end as newest). @param {string} key @param {string} title */
  add(key, title) {
    this.map.delete(key);
    this.map.set(key, title);
  }

  /** @param {string} key @returns {boolean} whether it was there */
  delete(key) { return this.map.delete(key); }

  clear() { this.map.clear(); }

  /** Entries sorted by title (Dutch collation). @returns {[string, string][]} */
  sorted() { return [...this.map].sort((a, b) => a[1].localeCompare(b[1], 'nl')); }
}
