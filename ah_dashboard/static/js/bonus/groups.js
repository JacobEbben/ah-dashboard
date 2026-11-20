// @ts-check
// Category groups for the Bonus view: the user's own named sets of AH bonus
// categories ("Weekly shop" = Zuivel + Brood + Groente), so one click shows
// several categories at once. Made by dragging a category pill onto another
// category or a group (bonus.js), or in the "Category groups" panel (here),
// which also renames, removes and deletes, and works without dragging
// (keyboard, screen readers). Group pills can be dragged to reorder them; the
// order is the order of this list.
//
// A category is in at most one group, and while it is, it has no pill of its
// own (the group stands for it); removing it from the group brings it back.
// Groups are renamed by double-clicking their pill (bonus.js) or in the panel.
//
// Stored as ah_bonus_groups: [{id, name, cats: [...]}] (synced when signed in).
// Category names are AH's; a category AH doesn't have this week is skipped
// when showing a group, but kept in it (it often comes back).
import { $, html, makeFoldable } from '../core/dom.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { parseGroups } from './model.js';

/** @typedef {import('./model.js').CategoryGroup} CategoryGroup */

export const GROUP_PREFIX = '\u0000group:';   // selection key of a group (can't clash with AH names)
const MAX_GROUPS = 30;
const MAX_NAME = 30;
const MAX_CATS = 100;

/** @type {CategoryGroup[]} */
let groups = [];
let onChange = () => {};
let panelSignature = '';

/** @param {unknown} s */
const cleanName = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, MAX_NAME);

function load() {
  return parseGroups(storage.getJSON(KEYS.bonusGroups, []), n => t('groupDefaultName', n),
    { maxGroups: MAX_GROUPS, maxName: MAX_NAME, maxCats: MAX_CATS });
}

function save() {
  if (groups.length) storage.setJSON(KEYS.bonusGroups, groups);
  else storage.remove(KEYS.bonusGroups);
  onChange();
}

export function allGroups() { return groups; }

/** Every category that is in a group (these get no pill of their own). */
export function groupedCats() { return new Set(groups.flatMap(g => g.cats)); }

/** Take categories out of every group (before they go into another one). */
/** @param {string[]} cats @param {string|null} [exceptId] */
function release(cats, exceptId = null) {
  for (const g of groups) if (g.id !== exceptId) g.cats = g.cats.filter(c => !cats.includes(c));
}
/** @param {CategoryGroup} g */
export function groupKey(g) { return GROUP_PREFIX + g.id; }
/** @param {unknown} key @returns {CategoryGroup|null} */
export function groupByKey(key) {
  return typeof key === 'string' && key.startsWith(GROUP_PREFIX)
    ? groups.find(g => g.id === key.slice(GROUP_PREFIX.length)) || null : null;
}

function newId() {
  let id;
  do { id = Math.random().toString(36).slice(2, 10); } while (groups.some(g => g.id === id));
  return id;
}

/** A new group with these categories (null when the maximum is reached). */
/** @param {string[]} [cats] */
export function createGroup(cats = []) {
  if (groups.length >= MAX_GROUPS) return null;
  let n = groups.length + 1;
  while (groups.some(g => g.name === t('groupDefaultName', n))) n++;
  const g = { id: newId(), name: t('groupDefaultName', n), cats: [...new Set(cats)].slice(0, MAX_CATS) };
  release(g.cats);
  groups.push(g);
  save();
  return g;
}

/** @param {string} id @param {string} cat */
export function addToGroup(id, cat) {
  const g = groups.find(x => x.id === id);
  if (!g || g.cats.includes(cat) || g.cats.length >= MAX_CATS) return g;
  release([cat], id);
  g.cats.push(cat);
  save();
  return g;
}

/** @param {string} id @param {string} cat */
function removeFromGroup(id, cat) {
  const g = groups.find(x => x.id === id);
  if (!g) return;
  g.cats = g.cats.filter(c => c !== cat);
  save();
}

/** @param {string} id @param {string} name */
export function renameGroup(id, name) {
  const g = groups.find(x => x.id === id);
  const clean = cleanName(name);
  if (!g || !clean || clean === g.name) return;
  g.name = clean;
  save();
}

export const GROUP_NAME_MAX = MAX_NAME;

/** Put the groups in this order (group ids, first to last); unknown ids are ignored. */
/** @param {string[]} ids */
export function reorderGroups(ids) {
  const byId = new Map(groups.map(g => [g.id, g]));
  const ordered = /** @type {CategoryGroup[]} */ (ids.map(id => byId.get(id)).filter(Boolean));
  const rest = groups.filter(g => !ids.includes(g.id));
  const next = [...ordered, ...rest];
  if (next.every((g, i) => g === groups[i])) return;
  groups = next;
  save();
}

/** @param {string} id */
function deleteGroup(id) {
  const g = groups.find(x => x.id === id);
  if (!g || !confirm(t('confirmDeleteGroup', g.name))) return;
  groups = groups.filter(x => x.id !== id);
  save();
}

/** Say something to screen readers (e.g. after a drop). */
/** @param {string} text */
export function announce(text) { $('bonusGroupLive').textContent = text; }

/**
 * The "Category groups" panel. ``cats``: this week's categories in AH's order
 * (for the "Add category" lists). Only rebuilt when something changed, so a
 * name being typed keeps its focus.
 */
/** @param {string[]} cats @param {{force?: boolean}} [options] */
export function renderGroupsPanel(cats, { force = false } = {}) {
  const signature = JSON.stringify([groups, cats, document.documentElement.lang]);
  if (!force && signature === panelSignature) return;
  panelSignature = signature;
  $('bonusGroupSummary').textContent = groups.length ? t('catGroupsSummary', groups.length) : '';
  const list = $('bonusGroupList');
  list.innerHTML = groups.length ? '' : html`<span class="smeta">${t('noGroups')}</span>`;
  for (const g of groups) {
    const row = document.createElement('div');
    row.className = 'grouprow';
    const grouped = groupedCats();
    const addable = cats.filter(c => !grouped.has(c));   // a category is in at most one group
    row.innerHTML = html`
      <div class="grouphead">
        <input type="text" class="groupname" value="${g.name}" maxlength="${MAX_NAME}" aria-label="${t('groupName')}" autocomplete="off">
        <select class="groupadd" aria-label="${t('addCategoryTo', g.name)}">
          <option value="">${t('addCategory')}</option>
          ${addable.map(c => html`<option value="${c}">${c}</option>`)}
        </select>
        <button type="button" class="mini danger groupdel">${t('deleteGroup')}</button>
      </div>
      <div class="hiddenlist">
        ${g.cats.length ? g.cats.map(c => html`<span class="hiddenchip${cats.length && !cats.includes(c) ? ' absent' : ''}"><span class="hiddenname">${c}</span><button type="button" class="mini restore" data-cat="${c}" title="${t('removeFromGroup', c)}" aria-label="${t('removeFromGroup', c)}">✕</button></span>`)
          : html`<span class="smeta">${t('groupEmptyCats')}</span>`}
      </div>`;
    const name = /** @type {HTMLInputElement} */ (row.querySelector('.groupname'));
    name.addEventListener('change', () => renameGroup(g.id, name.value));
    name.addEventListener('keydown', e => { if (e.key === 'Enter') name.blur(); });
    row.querySelector('.groupadd')?.addEventListener('change', (/** @type {any} */ e) => {
      if (!e.target.value) return;
      addToGroup(g.id, e.target.value);
      announce(t('addedToGroup', e.target.value, g.name));
    });
    row.querySelector('.groupdel')?.addEventListener('click', () => deleteGroup(g.id));
    row.querySelectorAll('.restore').forEach(el => {
      const b = /** @type {HTMLElement} */ (el);
      b.addEventListener('click', () => removeFromGroup(g.id, b.dataset.cat || ''));
    });
    list.appendChild(row);
  }
}

/** Open the panel and put the cursor in a group's name (after "+ New group"). */
/** @param {string} id */
export function editGroupName(id) {
  const panel = $('bonusGroupPanel');
  if (panel.classList.contains('collapsed')) $('bonusGroupToggle').click();
  const i = groups.findIndex(g => g.id === id);
  const input = /** @type {HTMLInputElement|undefined} */ ($('bonusGroupList').querySelectorAll('.groupname')[i]);
  if (input) { input.focus(); input.select(); }
}

/** @param {() => void} changed */
export function initGroups(changed) {
  groups = load();
  onChange = () => { panelSignature = ''; changed(); };
  const setFolded = makeFoldable($('bonusGroupPanel'), $('bonusGroupToggle'),
    folded => storage.set(KEYS.bonusGroupsFolded, folded ? '1' : '0'));
  setFolded(storage.get(KEYS.bonusGroupsFolded) !== '0');
  $('bonusGroupNew').addEventListener('click', () => {
    const g = createGroup([]);
    if (g) editGroupName(g.id);
  });
  // Another device changed the groups (sync reload) or the language changed.
  window.addEventListener('storage', e => { if (e.key === KEYS.bonusGroups) { groups = load(); onChange(); } });
}
