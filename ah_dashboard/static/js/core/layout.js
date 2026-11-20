// @ts-check
// Packing titled sections of cards into one column grid (no DOM: tested in
// tests/js). The grid is a stack of bands; a band is a title row plus a card
// row. A section starts on a new band at column 0, or, when it fits there
// entirely, in the free columns at the end of the band the previous section
// ended on (also after a section that took several rows). A section never
// wraps around another one, so each stays a readable block.

/**
 * @typedef {object} Placement
 * @property {number} band first band (0-based)
 * @property {number} col first column (0-based)
 * @property {number} width columns it spans
 * @property {number} bands bands it spans (its card rows)
 */

/**
 * Where each section goes, in input order.
 * ``dense``: when the next section doesn't fit in the free columns, a later
 * one that does may go there first (order changes); without it the order is
 * kept exactly (e.g. bargains grouped by discount or store).
 * @param {number[]} sizes cards per section
 * @param {number} cols columns of the grid
 * @param {boolean} dense
 * @returns {Placement[]}
 */
export function packSections(sizes, cols, dense) {
  const n = Math.max(1, Math.floor(cols) || 1);
  const size = (/** @type {number} */ i) => Math.max(1, Math.floor(sizes[i]) || 1);
  /** @type {Placement[]} */
  const out = new Array(sizes.length);
  const todo = sizes.map((_, i) => i);
  let band = 0;
  let col = 0;            // next free column in band ``band``; 0 = start a new band
  while (todo.length) {
    let k = 0;
    if (col > 0) {
      const fits = (/** @type {number} */ i) => size(i) <= n - col;
      if (!fits(todo[0])) {
        k = dense ? todo.findIndex(fits) : -1;
        if (k < 0) { band += 1; col = 0; k = 0; }
      }
    }
    const i = todo.splice(k, 1)[0];
    const count = size(i);
    if (col > 0) {
      out[i] = { band, col, width: count, bands: 1 };
      col += count;
    } else {
      const bands = Math.ceil(count / n);
      out[i] = { band, col: 0, width: Math.min(count, n), bands };
      band += bands - 1;
      col = count - (bands - 1) * n;
    }
    if (col >= n) { band += 1; col = 0; }
  }
  return out;
}
