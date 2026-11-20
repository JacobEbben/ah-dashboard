// @ts-check
// App-wide events: a module says that something happened without knowing who
// reacts, so modules don't import each other just to trigger a re-render.
// (Replaces the document-level "ah:*" events and direct cross-feature calls.)
//
//   on(EV.lists, () => …)      emit(EV.lists)
//
// Handlers run synchronously, in the order they were added; one failing
// handler does not stop the others.

/** Every event name, in one place. */
export const EV = Object.freeze({
  /** Liked or hidden products changed, or "Show hidden products" was toggled. */
  lists: 'lists',
  /** The UI language changed: re-render generated text. */
  lang: 'lang',
  /** Cards per row changed (header slider): grids re-render their spans. */
  cols: 'cols',
  /** The saved store list changed. detail: {reload: boolean} (true: a store was added). */
  stores: 'stores',
  /** The bargains view must apply its filters again (a setting or the overview selection changed). */
  refilter: 'refilter',
  /** The bargain filter settings were applied or saved (presets mark the active one). */
  settings: 'settings',
  /** Something needs this week's Bonus (the bargains' "Bonus" filter): load it quietly. */
  needBonus: 'needBonus',
  /** The Bonus loaded or failed. detail: {status: 'loading'|'ready'|'failed', deals: Map<number, string>}. */
  bonusStatus: 'bonusStatus',
});

/** @typedef {(detail?: any) => void} Handler */

/** @type {Map<string, Handler[]>} */
const handlers = new Map();

/**
 * @param {string} name one of EV
 * @param {Handler} fn
 * @returns {() => void} removes the handler again
 */
export function on(name, fn) {
  const list = handlers.get(name) || [];
  list.push(fn);
  handlers.set(name, list);
  return () => handlers.set(name, (handlers.get(name) || []).filter(f => f !== fn));
}

/**
 * @param {string} name one of EV
 * @param {any} [detail]
 */
export function emit(name, detail) {
  for (const fn of handlers.get(name) || []) {
    try { fn(detail); } catch (e) { console.error(`handler for "${name}" failed`, e); }
  }
}
