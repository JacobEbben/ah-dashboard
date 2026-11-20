// The views' pure logic: bargains model, Bonus model, settings merge.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { filterOffers, groupKeyFn, isSpecial, knownCats, mergeAcrossStores, sortGroupKeys, sorter, specialSummary }
  from '../../ah_dashboard/static/js/bargains/model.js';
import { categoryGroups, groupOffers, indexDeals, likedOffers, needsFetch, parseFilters, parseGroups, passesFilters,
  saving, sortOffers } from '../../ah_dashboard/static/js/bonus/model.js';
import { fold } from '../../ah_dashboard/static/js/core/format.js';
import { mergePrefs } from '../../ah_dashboard/static/js/sync/merge.js';

const bargain = (id, title, extra = {}) => ({
  kind: 'bargain', id, title, brand: null, unitSize: null, image: null, category: 'Vlees',
  priceNow: 1, priceWas: 2, storeId: 1, storeName: 'Thuis', stock: 1, discount: 35, expires: null,
  markdownType: null, ...extra,
});
const ALL = { all: true, selected: new Set() };
const noFilter = { cats: ALL, minDisc: 0, bonusOnly: false, terms: [], showHidden: false };
const deps = (over = {}) => ({ isHidden: () => false, fold, bonus: { status: 'none', deals: new Map() }, ...over });

// ---- bargains ------------------------------------------------------------------------------
test('bargain filters: types, discount, search, hidden', () => {
  const items = [bargain(1, 'Kipfilet'), bargain(2, 'Brood', { category: 'Brood', discount: 25 }), bargain(3, 'Crème fraîche')];
  const ids = r => r.items.map(o => o.id);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, cats: { all: false, selected: new Set(['Brood']) } }, deps())), [2]);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, minDisc: 30 }, deps())), [1, 3]);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, terms: ['creme'] }, deps())), [3]);
  const hidden = filterOffers(items, noFilter, deps({ isHidden: o => o.id === 1 }));
  assert.deepEqual(ids(hidden), [2, 3]);
  assert.equal(hidden.hiddenCount, 1);
  assert.deepEqual(ids(filterOffers(items, { ...noFilter, showHidden: true }, deps({ isHidden: o => o.id === 1 }))), [1, 2, 3]);
});

test('"Liked" keeps only liked bargains; off (or older presets without it) keeps all', () => {
  const items = [bargain(1, 'A'), bargain(2, 'B'), bargain(3, 'C')];
  const isLiked = o => o.id !== 2;
  assert.deepEqual(filterOffers(items, { ...noFilter, likedOnly: true }, deps({ isLiked })).items.map(o => o.id), [1, 3]);
  assert.equal(filterOffers(items, noFilter, deps({ isLiked })).items.length, 3);
  const hiddenLiked = filterOffers(items, { ...noFilter, likedOnly: true }, deps({ isLiked, isHidden: o => o.id === 3 }));
  assert.deepEqual(hiddenLiked.items.map(o => o.id), [1]);   // hidden stays hidden
});

test('"Also in Bonus" filters only once the Bonus is ready, and says why not', () => {
  const items = [bargain(1, 'A'), bargain(2, 'B')];
  const f = { ...noFilter, bonusOnly: true };
  const loading = filterOffers(items, f, deps({ bonus: { status: 'loading', deals: new Map() } }));
  assert.equal(loading.items.length, 2);
  assert.equal(loading.bonusPending, 'loading');
  const failed = filterOffers(items, f, deps({ bonus: { status: 'failed', deals: new Map() } }));
  assert.equal(failed.bonusPending, 'failed');
  const ready = filterOffers(items, f, deps({ bonus: { status: 'ready', deals: new Map([[2, '1+1']]) } }));
  assert.deepEqual(ready.items.map(o => o.id), [2]);
  assert.equal(ready.bonusPending, null);
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

test('Bonus savings and filters', () => {
  assert.equal(saving(offer(1, 'a')), 25);
  assert.equal(saving(offer(1, 'a', { priceWas: null })), 0);
  const f = parseFilters({ types: ['multi', 'bogus'], minSave: 40, sort: 'price' });
  assert.deepEqual(f, { types: ['multi'], minSave: 40, sort: 'price', liked: false });
  assert.deepEqual(parseFilters({ minSave: 33, sort: 'nope' }), { types: 'all', minSave: 0, sort: 'ah', liked: false });
  assert.equal(parseFilters({ liked: true }).liked, true);
  assert.equal(parseFilters({ liked: 'yes' }).liked, false);
  assert.equal(passesFilters(f, offer(1, 'a', { dealType: 'multi', priceNow: 1 })), true);
  assert.equal(passesFilters(f, offer(1, 'a', { dealType: 'percent', priceNow: 1 })), false);
});

test('Bonus order: chosen criterion, then liked, then AH order', () => {
  const list = [offer(1, 'b', { priceNow: 2 }), offer(2, 'a', { priceNow: 1 }), offer(3, 'c', { priceNow: 2 })];
  assert.deepEqual(sortOffers(list, 'price', o => o.id === 3).map(o => o.id), [2, 3, 1]);
  assert.deepEqual(sortOffers(list, 'ah', o => o.id === 3).map(o => o.id), [3, 1, 2]);
  assert.deepEqual(sortOffers(list, 'name', () => false).map(o => o.id), [2, 1, 3]);
});

test('categories in AH order; groups merge their categories, each offer once', () => {
  const items = [offer(1, 'Melk'), offer(2, 'Brood', { category: 'Brood' }), offer(1, 'Melk', { category: 'Brood' })];
  const byCat = categoryGroups(items, ['Brood', 'Zuivel', 'Leeg']);
  assert.deepEqual([...byCat.keys()], ['Brood', 'Zuivel']);
  const offers = groupOffers({ id: 'g', name: 'Ontbijt', cats: ['Zuivel', 'Brood'] }, byCat);
  assert.deepEqual(offers.map(o => o.id), [2, 1]);              // AH order, the duplicate melk once
  assert.deepEqual(likedOffers(items, o => o.id === 1).map(o => o.id), [1]);
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

test('category groups from storage are validated', () => {
  const raw = [
    { id: 'abc', name: '  Ontbijt  ', cats: ['Zuivel', 'Brood', 'Zuivel'] },
    { id: 'abc', name: 'dup id', cats: [] },
    { id: 'BAD!', name: 'bad id', cats: [] },
    { id: 'def', name: '', cats: ['Brood', 'Kaas'] },               // Brood is already in the first group
  ];
  const groups = parseGroups(raw, n => `Group ${n}`, { maxGroups: 30, maxName: 30, maxCats: 100 });
  assert.deepEqual(groups, [
    { id: 'abc', name: 'Ontbijt', cats: ['Zuivel', 'Brood'] },
    { id: 'def', name: 'Group 2', cats: ['Kaas'] },
  ]);
  assert.deepEqual(parseGroups('nonsense', () => '', { maxGroups: 1, maxName: 1, maxCats: 1 }), []);
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
