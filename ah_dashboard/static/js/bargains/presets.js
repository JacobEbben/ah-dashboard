// @ts-check
// The bargains' presets: named product-type selections in the presets bar
// under the quick bar (ui/presets.js does the work; the Bonus view has its own).
// Applying a preset changes only the product types; the minimum discount,
// grouping and order stay as they are. (Presets saved by older versions also
// hold those settings; they are ignored and dropped on the next update.)
import { on, EV } from '../core/events.js';
import { KEYS } from '../core/storage.js';
import { makePresets } from '../ui/presets.js';
import { applyCats, catsSetting, openFilterPanel, sameCats } from './filters.js';

export function initPresets() {
  const presets = makePresets({
    presetsKey: KEYS.presets,
    orderKey: KEYS.presetOrder,
    ids: { bar: 'presetBar', add: 'presetAddBtn', sel: 'presetSel', update: 'presetUpdate', del: 'presetDel',
      name: 'presetName', save: 'presetSave', live: 'presetLive', panel: 'filterPanel' },
    apply: applyCats,
    current: catsSetting,
    same: sameCats,
    openPanel: openFilterPanel,
  });
  on(EV.settings, presets.markActive);
  on(EV.lang, presets.render);
}
