// @ts-check
// Alert for the special product "AH Groente & Fruit zakje" (a bargain with a
// 0% discount, model.isSpecial): a bar at the top of the bargains view says
// where it is; clicking the bar (one button) shows exactly those bargains (an overview focus, so
// the filters, e.g. a minimum discount, don't hide it). Hidden products don't
// count; the bar disappears when there is none.
import { $, html } from '../core/dom.js';
import { t } from '../core/i18n.js';
import { isHidden } from '../lists/blacklist.js';
import { focusOn } from './metrics.js';
import { specialSummary } from './model.js';
import { storeIndex } from '../lists/stores.js';

/** @typedef {import('../core/offers.js').Offer} Offer */

/** @param {Offer[]} items all loaded bargains */
export function renderSpecialNotice(items) {
  const bar = $('specialNotice');
  const { count, titles, stores } = specialSummary(items, isHidden, storeIndex);
  bar.hidden = !count;
  if (!count) { bar.innerHTML = ''; return; }
  const name = titles.length ? titles.join(', ') : t('specialName');
  bar.innerHTML = html`<button type="button" class="focusbar specialnotice" id="specialShow" title="${t('specialShow')}">
    <span class="focustext"><span class="specialtag">${t('specialTag')}</span> ${t('specialNotice', name, stores.join(', '))}</span></button>`;
  $('specialShow').addEventListener('click', () => focusOn('special'));
}
