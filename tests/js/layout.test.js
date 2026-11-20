// core/layout.packSections: where the titled sections of a results grid go.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { packSections } from '../../ah_dashboard/static/js/core/layout.js';

const spot = (band, col, width, bands = 1) => ({ band, col, width, bands });

test('small sections share a row', () => {
  assert.deepEqual(packSections([1, 2], 4, false), [spot(0, 0, 1), spot(0, 1, 2)]);
});

test('the next section starts in the free end of a multi-row section', () => {
  // 9 cards in 4 columns: rows of 4, 4, 1; the 3 cards fit beside the last one.
  assert.deepEqual(packSections([9, 3], 4, false), [spot(0, 0, 4, 3), spot(2, 1, 3)]);
});

test('a section that does not fit entirely starts a new band, never wraps', () => {
  assert.deepEqual(packSections([3, 2], 4, false), [spot(0, 0, 3), spot(1, 0, 2)]);
  assert.deepEqual(packSections([5, 6], 4, false), [spot(0, 0, 4, 2), spot(2, 0, 4, 2)]);
});

test('a full last row moves the next section to a new band', () => {
  assert.deepEqual(packSections([8, 1], 4, false), [spot(0, 0, 4, 2), spot(2, 0, 1)]);
});

test('dense lets a later section fill free columns; ordered keeps the order', () => {
  // Groente 1, Fruit 2, Vlees 9, Vis 1 in 4 columns.
  assert.deepEqual(packSections([1, 2, 9, 1], 4, true),
    [spot(0, 0, 1), spot(0, 1, 2), spot(1, 0, 4, 3), spot(0, 3, 1)]);
  assert.deepEqual(packSections([1, 2, 9, 1], 4, false),
    [spot(0, 0, 1), spot(0, 1, 2), spot(1, 0, 4, 3), spot(3, 1, 1)]);
});

test('one column: every section on its own bands', () => {
  assert.deepEqual(packSections([2, 1], 1, true), [spot(0, 0, 1, 2), spot(2, 0, 1)]);
});

test('odd input stays sane', () => {
  assert.deepEqual(packSections([], 4, true), []);
  assert.deepEqual(packSections([0], 0, true), [spot(0, 0, 1)]);
});
