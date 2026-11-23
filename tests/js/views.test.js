// The views' pure logic: bargains model, Bonus model, settings merge.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { activeInStore, filterOffers, groupKeyFn, IN_STORE_MS, inStoreUntil, isSpecial, knownCats, mergeAcrossStores, sortGroupKeys, sorter, specialSummary }
  from '../../ah_dashboard/static/js/bargains/model.js';
import { categoryGroups, groupMemberRows, groupsToPresets, indexDeals, likedOffers, needsFetch, parseCats, parseLikedPill,
  selectCats, sortOffers } from '../../ah_dashboard/static/js/bonus/model.js';
import { mergePrefs } from '../../ah_dashboard/static/js/sync/merge.js';

const bargain = (id, title, extra = {}) => ({
  kind: 'bargain', id, title, brand: null, unitSize: null, image: null, category: 'Vlees',
  priceNow: 1, priceWas: 2, storeId: 1, storeName: 'Thuis', stock: 1, discount: 35, expires: null,
  markdownType: null, ...extra,
});
const ALL = { all: true, selected: new Set() };
const noFilter = { cats: ALL, minDisc: 0, showHidden: false };
const deps = (over = {}) => ({ isHidden: () => false, ...over });

// ---- bargains ------------------------------------------------------------------------------
test("I'm in this store: only that store, and how many match elsewhere", () => {
  const items = [bargain(1, 'Kip'), bargain(2, 'Brood', { storeId: 2, storeName: 'Werk' }),
    bargain(3, 'Melk', { storeId: 2, storeName: 'Werk', discount: 10 })];
  const r = filterOffers(items, { ...noFilter, storeId: 1 }, deps());
  assert.deepEqual(r.items.map(o => o.id), [1]);
  assert.equal(r.otherStores, 2);
  // Other filters apply first: the count is only what would show elsewhere.
  assert.equal(filterOffers(items, { ...noFilter, storeId: 1, minDisc: 30 }, deps()).otherStores, 1);
  assert.equal(filterOffers(items, noFilter, deps()).otherStores, 0);
});

test("I'm in this store: lasts 2 hours, never past midnight, only for followed stores", () => {
  const noon = new Date(2026, 10, 20, 12, 0);
  assert.equal(inStoreUntil(noon), noon.getTime() + IN_STORE_MS);
  const late = new Date(2026, 10, 20, 23, 0);
  assert.equal(inStoreUntil(late), new Date(2026, 10, 21, 0, 0).getTime());
  const now = noon.getTime();
  assert.equal(activeInStore({ id: 5, until: now + 1000 }, now, [5, 6]), 5);
  assert.equal(activeInStore({ id: 5, until: now - 1 }, now, [5]), null);           // expired
  assert.equal(activeInStore({ id: 7, until: now + 1000 }, now, [5]), null);        // not followed any more
  assert.equal(activeInStore({ id: 5, until: now + IN_STORE_MS + 1 }, now, [5]), null);  // too far ahead: tampered
  assert.equal(activeInStore('5', now, [5]), null);
  assert.equal(activeInStore(null, now, [5]), null);
});

test('bargain filters: types, discount, hidden', () => {
  const items = [bargain(1, 'Kipfilet'), bargain(2, 'Brood', { category: 'Brood', discount: 25 }), bargain(3, 'Crème fraîche')];
  const ids = r => r.items.map(o => o.id);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, cats: { all: false, selected: new Set(['Brood']) } }, deps())), [2]);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, minDisc: 30 }, deps())), [1, 3]);
  const hidden = filterOffers(items, noFilter, deps({ isHidden: o => o.id === 1 }));
  assert.deepEqual(ids(hidden), [2, 3]);
  assert.equal(hidden.hiddenCount, 1);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, showHidden: true }, deps({ isHidden: o => o.id === 1 }))), [1, 2, 3]);
});

test('liking never filters: it only orders ties (liked first)', () => {
  const items = [bargain(1, 'A'), bargain(2, 'B'), bargain(3, 'C')];
  // Older saved settings may still say likedOnly or bonusOnly: both are ignored.
  assert.equal(filterOffers(items, { ...noFilter, likedOnly: true, bonusOnly: true }, deps()).items.length, 3);
  assert.deepEqual(items.slice().sort(sorter('disc', o => o.id === 3)).map(o => o.id)[0], 3);
});

test('the same product at the same discount is one card across stores', () => {
  const items = [
    bargain(1, 'Kip', { storeName: 'B', stock: 2 }),
    bargain(1, 'Kip', { storeName: 'A', stock: 3 }),
    bargain(1, 'Kip', { storeName: 'A', discount: 50 }),
    bargain(null, null), bargain(null, null),                      // no identity: never merged
  ];
  const order = name => ['A', 'B'].indexOf(name);
  const cards = mergeAcrossStores(items, 'cat', order);
  assert.equal(cards.length, 4);
  assert.deepEqual(cards[0].stores.map(s => s.name), ['A', 'B']);
  assert.equal(cards[0].stock, 5);
  assert.equal(mergeAcrossStores(items, 'store', order).length, 5);   // grouped by store: one card per store
});

test('grouping and sorting', () => {
  const key = groupKeyFn('disc', { noDiscount: 'none', special: 'Special', all: 'all' });
  assert.equal(key(bargain(1, 'a')), '-35%');
  assert.equal(key(bargain(1, 'a', { discount: null })), 'none');
  assert.equal(key(bargain(1, 'a', { discount: 0 })), 'Special');
  assert.deepEqual(sortGroupKeys(['-25%', '-50%', 'none'], 'disc', () => 0), ['-50%', '-25%', 'none']);
  assert.deepEqual(knownCats([bargain(1, 'a', { category: 'Zuivel' }), bargain(2, 'b', { category: null })]), ['Overig', 'Zuivel']);
  const liked = new Set([2]);
  const cmp = sorter('disc', o => liked.has(o.id));
  const sorted = [bargain(1, 'a'), bargain(2, 'b'), bargain(3, 'c', { discount: 50 })].sort(cmp);
  assert.deepEqual(sorted.map(o => o.id), [3, 2, 1]);   // biggest discount, then liked first
});

test('the special product is the 0% bargain; hidden ones and other stores order respected', () => {
  const zakje = (store, extra = {}) => bargain(900, 'AH Groente & Fruit zakje', { discount: 0, storeName: store, ...extra });
  assert.equal(isSpecial(zakje('A')), true);
  assert.equal(isSpecial(bargain(1, 'x')), false);
  assert.equal(isSpecial(bargain(1, 'x', { discount: null })), false);   // no discount at all is not special
  const items = [zakje('B'), zakje('A'), bargain(1, 'Kip'), zakje('C', { id: 901 })];
  const order = name => ['A', 'B', 'C'].indexOf(name);
  assert.deepEqual(specialSummary(items, () => false, order),
    { count: 3, titles: ['AH Groente & Fruit zakje'], stores: ['A', 'B', 'C'] });
  assert.equal(specialSummary(items, o => o.id === 900, order).count, 1);   // hidden ones don't count
  assert.equal(specialSummary([bargain(1, 'Kip')], () => false, order).count, 0);
});

// ---- Bonus -----------------------------------------------------------------------------------
const offer = (id, title, extra = {}) => ({
  kind: 'bonus', id, title, brand: null, unitSize: null, image: null, category: 'Zuivel',
  priceNow: 1.5, priceWas: 2, dealType: 'percent', mechanism: '25%', minQty: null, priceFrom: false,
  productCount: null, isGroup: false, products: [], ...extra,
});

test('the stored Liked pill', () => {
  assert.equal(parseLikedPill({ liked: true }), true);
  assert.equal(parseLikedPill({ liked: 'yes' }), false);
  // Older versions stored filters there too: ignored.
  assert.equal(parseLikedPill({ types: ['multi'], minSave: 40, sort: 'price' }), false);
  assert.equal(parseLikedPill(null), false);
});

test('Bonus order: liked first, otherwise AH order', () => {
  const list = [offer(1, 'b'), offer(2, 'a'), offer(3, 'c')];
  assert.deepEqual(sortOffers(list, o => o.id === 3).map(o => o.id), [3, 1, 2]);
  assert.deepEqual(sortOffers(list, () => false).map(o => o.id), [1, 2, 3]);
});

test('categories in AH order; the chosen categories keep that order', () => {
  const items = [offer(1, 'Melk'), offer(2, 'Brood', { category: 'Brood' }), offer(1, 'Melk', { category: 'Brood' })];
  const byCat = categoryGroups(items, ['Brood', 'Zuivel', 'Leeg']);
  assert.deepEqual([...byCat.keys()], ['Brood', 'Zuivel']);
  assert.equal(selectCats(byCat, 'all'), byCat);
  assert.deepEqual([...selectCats(byCat, ['Zuivel', 'Brood', 'Kaas']).keys()], ['Brood', 'Zuivel']);
  assert.deepEqual([...selectCats(byCat, []).keys()], []);
  assert.deepEqual(likedOffers(items, o => o.id === 1).map(o => o.id), [1]);   // a product in two categories once
});

test('category selections from storage or a preset', () => {
  assert.equal(parseCats(undefined), 'all');
  assert.equal(parseCats('all'), 'all');
  assert.deepEqual(parseCats(['Kaas', 'Kaas', '', 3, 'Vis']), ['Kaas', 'Vis']);
  assert.deepEqual(parseCats([]), []);
});

test('an incomplete Bonus is fetched again; a complete one only on Refresh', () => {
  assert.equal(needsFetch(null, false), true);
  assert.equal(needsFetch({ errors: [] }, false), false);
  assert.equal(needsFetch({ errors: [] }, true), true);
  assert.equal(needsFetch({ errors: [{ category: 'Vlees' }] }, false), true);
});

test('deal index covers group members', () => {
  const deals = indexDeals([offer(1, 'a'), offer(null, 'Alle', { isGroup: true, mechanism: '1+1', products: [{ id: 7, title: 'x' }] })]);
  assert.deepEqual([...deals], [[1, '25%'], [7, '1+1']]);
});

test('old category groups become presets', () => {
  const { presets, order } = groupsToPresets([
    { id: 'abc', name: '  Ontbijt  ', cats: ['Zuivel', 'Brood', 'Zuivel'] },
    { id: 'def', name: 'Ontbijt', cats: ['Kaas'] },                  // same name: skipped
    { id: 'ghi', name: '', cats: ['Vis'] },                          // no name: skipped
    { id: 'jkl', name: 'Leeg', cats: 'nonsense' },
  ]);
  assert.deepEqual(order, ['Ontbijt', 'Leeg']);
  assert.deepEqual({ ...presets }, { Ontbijt: { cats: ['Brood', 'Zuivel'] }, Leeg: { cats: [] } });
  const none = groupsToPresets('nonsense');
  assert.deepEqual([{ ...none.presets }, none.order], [{}, []]);
});

// ---- settings sync ----------------------------------------------------------------------------
test('merging settings changed on two devices', () => {
  const local = { ah_liked: JSON.stringify([{ key: '1', title: 'A' }]), ah_lang: 'nl',
    ah_presets: JSON.stringify({ Mine: { minDisc: '50' }, Both: { minDisc: '10' } }) };
  const server = { ah_liked: JSON.stringify([{ key: '2', title: 'B' }, { key: '1', title: 'A' }]), ah_lang: 'en',
    ah_blacklist: JSON.stringify([{ key: '9', title: 'X' }]),
    ah_presets: JSON.stringify({ Theirs: { minDisc: '25' }, Both: { minDisc: '99' } }) };
  const merged = mergePrefs(local, server);
  assert.deepEqual(JSON.parse(merged.ah_liked).map(e => e.key), ['2', '1']);   // both, this browser's last
  assert.equal(merged.ah_lang, 'nl');                                         // other settings: local wins
  assert.deepEqual(JSON.parse(merged.ah_blacklist), [{ key: '9', title: 'X' }]);
  assert.deepEqual(JSON.parse(merged.ah_presets), { Theirs: { minDisc: '25' }, Both: { minDisc: '10' }, Mine: { minDisc: '50' } });
});

test('groupMemberRows: one row per product; same titles get their size, then a number', () => {
  const rows = groupMemberRows([
    { id: 1, title: 'AH Pindakaas', size: '350 g' },
    { id: 2, title: 'AH Pindakaas', size: '600 g' },
    { id: 3, title: 'AH Hagelslag', size: '400 g' },
    { id: 4, title: 'AH Cola', size: null },
    { id: 5, title: 'AH Cola' },
    { id: 6, title: 'AH Pindakaas', size: '350 g' },
    { id: 7, title: '  ' },
    { id: 3, title: 'AH Hagelslag', size: '400 g' },
  ]);
  assert.deepEqual(rows, [
    { id: 1, label: 'AH Pindakaas · 350 g (1)' },
    { id: 2, label: 'AH Pindakaas · 600 g' },
    { id: 3, label: 'AH Hagelslag' },
    { id: 4, label: 'AH Cola (1)' },
    { id: 5, label: 'AH Cola (2)' },
    { id: 6, label: 'AH Pindakaas · 350 g (2)' },
  ]);
  assert.deepEqual(groupMemberRows(null), []);
});
