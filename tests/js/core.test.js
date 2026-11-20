// Core modules: unit prices, offer identity, formatting, events, translations.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { emit, on } from '../../ah_dashboard/static/js/core/events.js';
import { fold, parseDay, searchTerms } from '../../ah_dashboard/static/js/core/format.js';
import { I18N } from '../../ah_dashboard/static/js/core/i18n.js';
import { groupSize, memberKeys, productKey, productUrl, safeImageUrl } from '../../ah_dashboard/static/js/core/offers.js';
import { compareUnit, parseUnitSize, unitPrice } from '../../ah_dashboard/static/js/core/unit.js';

test('pack sizes that can be read without guessing', () => {
  assert.deepEqual(parseUnitSize('500 g'), { amount: 0.5, basis: 'kg' });
  assert.deepEqual(parseUnitSize('1,5 l'), { amount: 1.5, basis: 'l' });
  assert.deepEqual(parseUnitSize('6 x 33 cl'), { amount: 1.98, basis: 'l' });
  assert.deepEqual(parseUnitSize('ca. 650 g'), { amount: 0.65, basis: 'kg' });
  assert.deepEqual(parseUnitSize('10 stuks'), { amount: 10, basis: 'piece' });
  assert.deepEqual(parseUnitSize('per kg'), { amount: 1, basis: 'kg' });
});

test('ambiguous pack sizes give no unit price', () => {
  for (const text of ['250 g + 50 g gratis', '4-6 stuks', '1/2 kg', 'per bos', '', null, '999 kg']) {
    assert.equal(parseUnitSize(text), null, String(text));
  }
});

test('unit price: per kg / l, per piece only for packs of two or more', () => {
  assert.deepEqual(unitPrice(2.49, '500 g'), { value: 4.98, basis: 'kg' });
  assert.equal(unitPrice(1.0, '1 stuk'), null);
  assert.equal(unitPrice(null, '500 g'), null);
  const kg = unitPrice(1, '1 kg'), piece = unitPrice(1, '4 stuks');
  assert.ok(compareUnit(kg, piece) < 0 && compareUnit(piece, null) < 0 && compareUnit(null, null) === 0);
});

test('product identity is the webshop id, else the title', () => {
  assert.equal(productKey({ id: 123, title: 'Kip' }), '123');
  assert.equal(productKey({ id: null, title: ' Alle Hak ' }), 't:alle hak');
  assert.equal(productKey({ id: 'x', title: '' }), 't:');
});

test('a group deal stands for its products', () => {
  const group = { id: null, title: 'Alle pizza', products: [{ id: 1, title: 'A' }, { id: 2, title: 'B' }, { id: 'x', title: 'bad' }] };
  assert.deepEqual(memberKeys(group), [['1', 'A'], ['2', 'B']]);
  assert.equal(groupSize(group), 2);
  assert.deepEqual(memberKeys({ id: 5, title: 'Melk' }), [['5', 'Melk']]);
  assert.equal(groupSize({ id: 5, title: 'Melk' }), 0);
  assert.deepEqual(memberKeys({ id: null, title: '' }), []);
});

test('links and images: only safe URLs', () => {
  assert.equal(productUrl(42), 'https://www.ah.nl/producten/product/wi42');
  assert.equal(productUrl('42/../x'), null);
  assert.equal(safeImageUrl('https://static.ah.nl/a.jpg'), 'https://static.ah.nl/a.jpg');
  assert.equal(safeImageUrl('javascript:alert(1)'), null);
  assert.equal(safeImageUrl('http://example.com/a.jpg'), null);
});

test('search folding ignores case and accents', () => {
  assert.equal(fold('Crème BRÛLÉE'), 'creme brulee');
  assert.deepEqual(searchTerms('  Crème  fraîche '), ['creme', 'fraiche']);
  assert.equal(parseDay('2026-11-18').getDate(), 18);
  assert.equal(parseDay('nonsense'), null);
});

test('events reach every handler, also after one fails', () => {
  const seen = [];
  const offA = on('test-event', d => seen.push(['a', d]));
  on('test-event', () => { throw new Error('boom'); });
  const offC = on('test-event', d => seen.push(['c', d]));
  const error = console.error;
  console.error = () => {};
  try { emit('test-event', 1); } finally { console.error = error; }
  offA(); offC();
  emit('test-event', 2);
  assert.deepEqual(seen, [['a', 1], ['c', 1]]);
});

test('every UI string exists in English and Dutch', () => {
  const en = Object.keys(I18N.en).sort(), nl = Object.keys(I18N.nl).sort();
  assert.deepEqual(en.filter(k => !nl.includes(k)), [], 'missing in nl');
  assert.deepEqual(nl.filter(k => !en.includes(k)), [], 'missing in en');
  for (const k of en) assert.equal(typeof I18N.en[k], typeof I18N.nl[k], `${k}: text in one language, function in the other`);
});
