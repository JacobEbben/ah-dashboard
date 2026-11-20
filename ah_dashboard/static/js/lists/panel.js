// @ts-check
// "Liked & hidden products" panel: foldable, with a count of both lists in
// its header. The lists themselves are rendered by likes.js and blacklist.js.
import { $, makeFoldable } from '../core/dom.js';
import { on, EV } from '../core/events.js';
import { t } from '../core/i18n.js';
import { KEYS, storage } from '../core/storage.js';
import { hiddenCount } from './blacklist.js';
import { likedCount } from './likes.js';

/** Header text, e.g. "· 3 liked · 2 hidden" (empty when both lists are empty). */
function renderListsSummary() {
  const el = $('listsSummary');
  if (!el) return;
  const liked = likedCount(), hidden = hiddenCount();
  el.textContent = liked || hidden ? t('listsSummary', liked, hidden) : '';
}

export function initListsPanel() {
  const setFolded = makeFoldable($('listsPanel'), $('listsToggle'),
    folded => storage.set(KEYS.listsFolded, folded ? '1' : '0'));
  setFolded(storage.get(KEYS.listsFolded) !== '0');   // folded unless opened before
  on(EV.lists, renderListsSummary);
  on(EV.lang, renderListsSummary);
  renderListsSummary();
}
