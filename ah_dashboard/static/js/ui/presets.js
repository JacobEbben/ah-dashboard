// @ts-check
// Presets: named product-type (category) selections ({cats}), shared by both
// views. Each view has its own presets, its own Filters panel and its own
// presets bar: the built-in "All types" first, one part per saved preset,
// then ＋ (opens the Filters panel at the new preset's name). Applying a
// preset changes only the product types.
//
// The order of the presets is the user's own (drag the buttons, or Alt+arrow
// keys), kept next to the presets; new presets go to the end. Double-click a
// preset button (or F2) to rename it. In the Filters panel: choose a preset,
// Update it with the current selection, Delete it, or Save the selection
// under a new name.
import { $, html } from '../core/dom.js';
import { t } from '../core/i18n.js';
import { storage } from '../core/storage.js';
import { onRenameGesture, startRename } from './rename.js';
import { makeReorderable } from './reorder.js';

/** @typedef {{cats?: 'all'|string[]}} CatsSetting */
/** @typedef {Record<string, CatsSetting>} Presets */

/**
 * @typedef {object} PresetOptions
 * @property {string} presetsKey localStorage key of the presets ({name: {cats}})
 * @property {string} orderKey localStorage key of their order ([names])
 * @property {{bar: string, add: string, sel: string, update: string, del: string, name: string, save: string, live: string, panel: string}} ids
 * @property {(s: CatsSetting) => void} apply apply a preset's product types
 * @property {() => CatsSetting} current the current selection (what Save stores)
 * @property {(s: CatsSetting) => boolean} same does a preset equal the current selection?
 * @property {() => void} openPanel unfold the Filters panel
 */

// Built-in "All types" preset: always first in the bar, not stored, not deletable.
// Every product type (also ones that appear later).
const BUILTIN_ALL = Object.freeze({ cats: /** @type {'all'} */ ('all') });

/**
 * Wire one view's presets. Returns ``markActive`` (call after the selection
 * changed) and ``render`` (call after the language changed).
 * @param {PresetOptions} o
 */
export function makePresets(o) {
  const el = /** @param {keyof PresetOptions['ids']} k */ k => $(o.ids[k]);

  // Presets live in a prototype-less object, so any name (even "__proto__" or
  // "constructor") is an ordinary key: lookups never hit Object.prototype and
  // assignments never change an object's prototype.
  /** @returns {Presets} */
  function loadPresets() {
    const parsed = storage.getJSON(o.presetsKey, {});
    /** @type {Presets} */
    const out = Object.create(null);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [name, settings] of Object.entries(parsed)) {
        if (settings && typeof settings === 'object') out[name] = settings;
      }
    }
    return out;
  }
  /** @param {Presets} p */
  const storePresets = p => storage.setJSON(o.presetsKey, p);

  /** The saved order: preset names, first to last. @returns {string[]} */
  function loadOrder() {
    const raw = storage.getJSON(o.orderKey, []);
    return Array.isArray(raw) ? raw.filter(n => typeof n === 'string') : [];
  }
  /** @param {string[]} names */
  const storeOrder = names => storage.setJSON(o.orderKey, names);

  /** Preset names in the user's order; presets not in it (yet) follow A-Z. @param {Presets} p */
  function sortedNames(p) {
    const names = Object.keys(p);
    const order = loadOrder().filter((n, i, all) => Object.hasOwn(p, n) && all.indexOf(n) === i);
    const rest = names.filter(n => !order.includes(n)).sort((a, b) => a.localeCompare(b, 'nl'));
    return [...order, ...rest];
  }

  const selected = () => /** @type {HTMLSelectElement} */ (el('sel')).value;

  /** @param {string} name */
  function render(name) {
    const p = loadPresets();
    renderSelect(p, name);
    renderBar(p);
  }

  /** @param {Presets} p @param {string} name */
  function renderSelect(p, name) {
    const sel = /** @type {HTMLSelectElement} */ (el('sel'));
    sel.innerHTML = html`<option value="">${t('choosePreset')}</option>`;
    for (const n of sortedNames(p)) {
      const opt = document.createElement('option');
      opt.value = n;
      opt.textContent = n;
      sel.appendChild(opt);
    }
    sel.value = name && p[name] ? name : '';
    updateButtons();
  }

  /** Update and Delete act on the selected preset: only enabled with one selected. */
  function updateButtons() {
    const none = !selected();
    /** @type {HTMLButtonElement} */ (el('update')).disabled = none;
    /** @type {HTMLButtonElement} */ (el('del')).disabled = none;
  }

  /** @param {string} name */
  function choose(name) {
    /** @type {HTMLSelectElement} */ (el('sel')).value = name;
    updateButtons();
  }

  /** @param {Presets} p */
  function renderBar(p) {
    const bar = el('bar');
    bar.innerHTML = '';
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'segbtn presetbtn builtin';
    all.dataset.builtin = 'all';
    all.textContent = t('presetAll');
    all.title = t('presetAllTitle');
    all.addEventListener('click', () => { choose(''); o.apply(BUILTIN_ALL); });
    bar.appendChild(all);
    for (const name of sortedNames(p)) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'segbtn presetbtn movable';
      b.dataset.preset = name;
      b.textContent = name;
      b.title = t('presetMoveHint');
      b.addEventListener('click', () => {
        const s = loadPresets()[name];
        if (!s) return;
        choose(name);
        o.apply(s);
      });
      bar.appendChild(b);
    }
    markActive();
  }

  /** Highlight the preset whose product types are the current selection. */
  function markActive() {
    const p = loadPresets();
    el('bar').querySelectorAll('.presetbtn').forEach((/** @type {HTMLElement} */ b) => {
      const s = b.dataset.builtin === 'all' ? BUILTIN_ALL : p[b.dataset.preset || ''];
      const on = !!s && o.same(s);
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  function save() {
    const nameEl = /** @type {HTMLInputElement} */ (el('name'));
    const name = (nameEl.value.trim() || selected()).trim();
    if (!name) { nameEl.focus(); return; }
    const p = loadPresets();
    if (p[name] && name !== selected() && !confirm(t('overwritePreset', name))) return;
    const isNew = !Object.hasOwn(p, name);
    p[name] = o.current();
    storePresets(p);
    if (isNew) storeOrder([...sortedNames(p).filter(n => n !== name), name]);   // new: at the end
    render(name);
    nameEl.value = '';
  }

  /** Write the current product types into the selected preset (keeps its name). */
  function update() {
    const name = selected();
    const p = loadPresets();
    if (!name || !p[name] || !confirm(t('confirmUpdatePreset', name))) return;
    p[name] = o.current();
    storePresets(p);
    render(name);   // re-marks the active preset button
  }

  /** Rename a preset, keeping its settings and its place. Returns an error text
   *  when the new name is already in use. @param {string} oldName @param {string} newName */
  function rename(oldName, newName) {
    const p = loadPresets();
    if (!Object.hasOwn(p, oldName)) return '';
    if (Object.hasOwn(p, newName)) return t('presetNameTaken', newName);
    const order = sortedNames(p).map(n => (n === oldName ? newName : n));
    /** @type {Presets} */
    const next = Object.create(null);
    for (const [name, settings] of Object.entries(p)) next[name === oldName ? newName : name] = settings;
    storePresets(next);
    storeOrder(order);
    const sel = selected();
    render(sel === oldName ? newName : sel);
    el('live').textContent = t('presetRenamed', oldName, newName);
    return '';
  }

  function remove() {
    const name = selected();
    if (!name || !confirm(t('deletePreset', name))) return;
    const p = loadPresets();
    delete p[name];
    storePresets(p);
    storeOrder(loadOrder().filter(n => n !== name));
    render('');
  }

  // ＋ at the end of the presets bar: open the Filters panel at the new preset's name.
  el('add').addEventListener('click', () => {
    o.openPanel();
    el('name').focus({ preventScroll: true });
    el('panel').scrollIntoView({ block: 'nearest' });
  });
  el('sel').addEventListener('change', () => {
    updateButtons();
    const s = loadPresets()[selected()];
    if (s) o.apply(s);
  });
  el('update').addEventListener('click', update);
  el('save').addEventListener('click', save);
  el('name').addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => { if (e.key === 'Enter') save(); });
  el('del').addEventListener('click', remove);
  // Double-click (or F2 on) a preset button: rename it in place.
  onRenameGesture(el('bar'), '.presetbtn[data-preset]', pill => {
    const oldName = pill.dataset.preset || '';
    startRename(pill, {
      name: oldName, maxLength: 40, label: t('presetNameLabel'),
      onCommit: name => rename(oldName, name),
    });
  });
  // Drag the preset buttons (or Alt+arrow keys) to put them in your own order.
  makeReorderable(el('bar'), '.presetbtn[data-preset]', buttons => {
    storeOrder(buttons.map(b => b.dataset.preset || ''));
    renderSelect(loadPresets(), selected());   // the dropdown follows the new order
  }, {
    onKeyboardMove: (button, index, count) => {
      el('live').textContent = t('presetMoved', button.dataset.preset, index + 1, count);
    },
  });
  render('');

  return { markActive, render: () => render(selected()) };
}
