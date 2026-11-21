// @ts-check
// Presets: named product-type selections ({cats}), one button each in the
// preset row under the quick bar, after the built-in "All types". Applying a
// preset changes only the product types; the minimum discount, Liked, Bonus,
// grouping and order stay as they are. (Presets saved by older versions also
// hold those settings; they are ignored and dropped on the next update.)
// The order of the presets is the user's own (drag the buttons, or Alt+arrow
// keys), kept in ah_preset_order next to ah_presets; new presets go to the
// end. Double-click a preset button (or F2) to rename it.
import { $, html } from '../core/dom.js';
import { on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { onRenameGesture, startRename } from '../ui/rename.js';
import { makeReorderable } from '../ui/reorder.js';
import { applyCats, catsSetting, sameCats } from './filters.js';

/** @typedef {import('./filters.js').Settings} Settings */
/** @typedef {Record<string, Settings>} Presets */

// Presets live in a prototype-less object, so any name (even "__proto__" or
// "constructor") is an ordinary key: lookups never hit Object.prototype and
// assignments never change an object's prototype.
/** @returns {Presets} */
function loadPresets() {
  const parsed = storage.getJSON(KEYS.presets, {});
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
function storePresets(p) { storage.setJSON(KEYS.presets, p); }

/** The saved order: preset names, first to last. */
/** @returns {string[]} */
function loadOrder() {
  const raw = storage.getJSON(KEYS.presetOrder, []);
  return Array.isArray(raw) ? raw.filter(n => typeof n === 'string') : [];
}
/** @param {string[]} names */
function storeOrder(names) { storage.setJSON(KEYS.presetOrder, names); }

/** Preset names in the user's order; presets not in it (yet) follow A-Z. */
/** @param {Presets} p */
function sortedNames(p) {
  const names = Object.keys(p);
  const order = loadOrder().filter((n, i, all) => Object.hasOwn(p, n) && all.indexOf(n) === i);
  const rest = names.filter(n => !order.includes(n)).sort((a, b) => a.localeCompare(b, 'nl'));
  return [...order, ...rest];
}

/** @param {string} selected */
function renderPresets(selected) {
  const p = loadPresets();
  renderPresetSelect(p, selected);
  renderPresetBar(p);
}

/** @param {Presets} p @param {string} selected */
function renderPresetSelect(p, selected) {
  const sel = $('presetSel');
  sel.innerHTML = html`<option value="">${t('choosePreset')}</option>`;
  for (const name of sortedNames(p)) {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = selected && p[selected] ? selected : '';
  updatePresetButtons();
}

/** Update and Delete act on the selected preset: only enabled with one selected. */
function updatePresetButtons() {
  const none = !$('presetSel').value;
  $('presetUpdate').disabled = none;
  $('presetDel').disabled = none;
}

// Built-in "All types" preset: always first in the row, not stored, not deletable.
// Every product type (also ones that appear later).
const BUILTIN_ALL = Object.freeze({ cats: /** @type {'all'} */ ('all') });

/** @param {Presets} p */
function renderPresetBar(p) {
  const bar = $('presetBar');
  const names = sortedNames(p);
  bar.innerHTML = '';

  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'segbtn presetbtn builtin';
  all.dataset.builtin = 'all';
  all.dataset.i18n = 'presetAll';
  all.dataset.i18nTitle = 'presetAllTitle';
  all.textContent = t('presetAll');
  all.title = t('presetAllTitle');
  all.addEventListener('click', () => {
    $('presetSel').value = '';
    updatePresetButtons();
    applyCats(BUILTIN_ALL);
  });
  bar.appendChild(all);
  for (const name of names) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'segbtn presetbtn movable';
    b.dataset.preset = name;
    b.textContent = name;
    b.title = t('presetMoveHint');
    b.addEventListener('click', () => {
      const s = loadPresets()[name];
      if (!s) return;
      $('presetSel').value = name;
      updatePresetButtons();
      applyCats(s);
    });
    bar.appendChild(b);
  }
  markActivePreset();
}

/** Highlight the preset whose product types are the current selection. */
function markActivePreset() {
  const bar = $('presetBar');
  const p = loadPresets();
  /** @param {Settings|undefined} s */
  const same = s => !!s && sameCats(s);
  bar.querySelectorAll('.presetbtn').forEach((/** @type {HTMLElement} */ b) => {
    const on = same(b.dataset.builtin === 'all' ? BUILTIN_ALL : p[b.dataset.preset || '']);
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

function savePreset() {
  const nameEl = $('presetName');
  const sel = $('presetSel');
  const name = (nameEl.value.trim() || sel.value).trim();
  if (!name) { nameEl.focus(); return; }
  const p = loadPresets();
  if (p[name] && name !== sel.value && !confirm(t('overwritePreset', name))) return;
  const isNew = !Object.hasOwn(p, name);
  p[name] = catsSetting();
  storePresets(p);
  if (isNew) storeOrder([...sortedNames(p).filter(n => n !== name), name]);   // new: at the end
  renderPresets(name);
  nameEl.value = '';
}

/** Write the current product types into the selected preset (keeps its name). */
function updatePreset() {
  const name = $('presetSel').value;
  const p = loadPresets();
  if (!name || !p[name] || !confirm(t('confirmUpdatePreset', name))) return;
  p[name] = catsSetting();
  storePresets(p);
  renderPresets(name);  // re-marks the active preset button
}

/** Rename a preset, keeping its settings and its place. Returns an error text
 *  when the new name is already in use. */
/** @param {string} oldName @param {string} newName */
function renamePreset(oldName, newName) {
  const p = loadPresets();
  if (!Object.hasOwn(p, oldName)) return '';
  if (Object.hasOwn(p, newName)) return t('presetNameTaken', newName);
  const order = sortedNames(p).map(n => (n === oldName ? newName : n));
  /** @type {Presets} */
  const next = Object.create(null);
  for (const [name, settings] of Object.entries(p)) next[name === oldName ? newName : name] = settings;
  storePresets(next);
  storeOrder(order);
  const sel = $('presetSel').value;
  renderPresets(sel === oldName ? newName : sel);
  $('presetLive').textContent = t('presetRenamed', oldName, newName);
  return '';
}

function deletePreset() {
  const name = $('presetSel').value;
  if (!name || !confirm(t('deletePreset', name))) return;
  const p = loadPresets();
  delete p[name];
  storePresets(p);
  storeOrder(loadOrder().filter(n => n !== name));
  renderPresets('');
}

export function initPresets() {
  $('presetSel').addEventListener('change', (/** @type {any} */ e) => {
    updatePresetButtons();
    const s = loadPresets()[e.target.value];
    if (s) applyCats(s);
  });
  $('presetUpdate').addEventListener('click', updatePreset);
  $('presetSave').addEventListener('click', savePreset);
  $('presetName').addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => { if (e.key === 'Enter') savePreset(); });
  $('presetDel').addEventListener('click', deletePreset);
  // Double-click (or F2 on) a preset button: rename it in place.
  onRenameGesture($('presetBar'), '.presetbtn[data-preset]', pill => {
    const oldName = pill.dataset.preset || '';
    startRename(pill, {
      name: oldName, maxLength: 40, label: t('presetNameLabel'),
      onCommit: name => renamePreset(oldName, name),
    });
  });
  // Drag the preset buttons (or Alt+arrow keys) to put them in your own order.
  makeReorderable($('presetBar'), '.presetbtn[data-preset]', buttons => {
    storeOrder(buttons.map(b => b.dataset.preset || ''));
    renderPresetSelect(loadPresets(), $('presetSel').value);   // the dropdown follows the new order
  }, {
    onKeyboardMove: (button, index, count) => {
      $('presetLive').textContent = t('presetMoved', button.dataset.preset, index + 1, count);
    },
  });
  on(EV.settings, markActivePreset);
  on(EV.lang, () => renderPresets($('presetSel').value));
  renderPresets('');
}
