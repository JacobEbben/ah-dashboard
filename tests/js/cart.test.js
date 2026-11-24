// The cart's pure logic (core/cart.js): entries, checking stored ones, availability, sections.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { activeCartStore, bargainKey, bonusKey, cartSections, cleanEntries, cleanEntry, dayOf, entriesFor, entryStatus,
  indexBargains, indexBonus, MAX_CART, todayOf } from '../../ah_dashboard/static/js/core/cart.js';
import { mergePrefs } from '../../ah_dashboard/static/js/sync/merge.js';
import { allowedImageUrl } from '../../ah_dashboard/static/js/core/offers.js';

const TODAY = '2026-11-24';
const bargain = (id, storeId, extra = {}) => ({
  kind: 'bargain', id, title: `Product ${id}`, unitSize: '500 g', image: 'https://static.ah.nl/x.jpg',
  priceNow: 1.09, priceWas: 2.18, storeId, storeName: `Store ${storeId}`, discount: 50, expires: '2026-11-25',
  ...extra,
});
const bonus = (id, extra = {}) => ({
  kind: 'bonus', id, title: `Deal ${id}`, unitSize: null, image: null, priceNow: 2.5, priceWas: 3.5,
  mechanism: '1+1 gratis', minQty: 2, ...extra,
});
const WEEK = { start: '2026-11-23', end: '2026-11-29' };
const nothing = { bargains: null, bonus: null };

test('a bargain card shown for several stores adds one entry per store; a Bonus deal one', () => {
  const card = { ...bargain(7, 1), stores: [{ id: 1, name: 'A', stock: 2 }, { id: 2, name: 'B', stock: 1 }] };
  const entries = entriesFor(card, { now: 5 });
  assert.deepEqual(entries.map(e => e.key), ['b:1:7', 'b:2:7']);
  assert.deepEqual(entries.map(e => e.storeName), ['A', 'B']);
  assert.equal(entries[0].discount, 50);
  assert.equal(entries[0].expires, '2026-11-25');
  assert.equal(entries[0].added, 5);
  assert.deepEqual(entriesFor(bargain(7, 3), { now: 0 }).map(e => e.key), ['b:3:7']);   // a plain offer: its own store

  const [deal] = entriesFor(bonus(9), { now: 0, week: WEEK });
  assert.equal(deal.key, 'p:9');
  assert.equal(deal.mechanism, '1+1 gratis');
  assert.deepEqual([deal.from, deal.until], ['2026-11-23', '2026-11-29']);
  // A group deal has no product id: keyed by title, like likes.
  assert.equal(entriesFor(bonus(null, { title: "Alle AH pizza's", isGroup: true }), { now: 0 })[0].key, "p:t:alle ah pizza's");
  assert.deepEqual(entriesFor(bargain(7, null), { now: 0 }), []);   // no store: nothing to add
  assert.deepEqual(entriesFor(null, { now: 0 }), []);
});

test('stored entries are checked field by field; odd ones are dropped, keys made again', () => {
  const good = entriesFor(bargain(7, 1), { now: 1 })[0];
  assert.deepEqual(cleanEntry(good), good);
  assert.deepEqual(cleanEntry(JSON.parse(JSON.stringify(good))), good);
  assert.equal(cleanEntry({ ...good, kind: 'other' }), null);
  assert.equal(cleanEntry({ ...good, storeId: 'x' }), null);
  assert.equal(cleanEntry({ ...good, key: 7 }), null);
  assert.equal(cleanEntry({ ...good, id: null, title: '' }), null);       // no identity at all
  assert.equal(cleanEntry({ ...good, key: 'b:99:1' }).key, 'b:1:7');    // the key follows the fields
  const odd = cleanEntry({ ...good, priceNow: NaN, discount: -1, expires: 'soon', title: 'x'.repeat(500) });
  assert.equal(odd.priceNow, null);
  assert.equal(odd.discount, null);
  assert.equal(odd.expires, null);
  assert.equal(odd.title.length, 120);
  assert.equal(cleanEntry('nope'), null);

  // Duplicates: the later one wins; at most MAX_CART, the newest.
  const many = Array.from({ length: MAX_CART + 5 }, (_, i) => entriesFor(bargain(i + 1, 1), { now: i })[0]);
  const cleaned = cleanEntries([...many, many[0]]);
  assert.equal(cleaned.length, MAX_CART);
  assert.equal(cleaned[cleaned.length - 1].key, many[0].key);
  assert.deepEqual(cleanEntries('[]'), []);
});

test('a bargain is gone when its store loaded without it, or past its date; unknown when its store did not load', () => {
  const [entry] = entriesFor(bargain(7, 1), { now: 0 });
  assert.equal(entryStatus(entry, nothing, TODAY).status, 'unknown');           // nothing loaded yet
  const loaded = { bargains: indexBargains([bargain(7, 1, { discount: 70 })], [1, 2]), bonus: null };
  const here = entryStatus(entry, loaded, TODAY);
  assert.equal(here.status, 'ok');
  assert.equal(here.current.discount, 70);                                      // the latest discount
  const goneLoaded = { bargains: indexBargains([bargain(8, 1)], [1]), bonus: null };
  assert.equal(entryStatus(entry, goneLoaded, TODAY).status, 'gone');
  const failed = { bargains: indexBargains([], [2]), bonus: null };             // store 1 failed
  assert.equal(entryStatus(entry, failed, TODAY).status, 'unknown');
  assert.equal(entryStatus({ ...entry, expires: '2026-11-23' }, loaded, TODAY).status, 'gone');
  // The same product at another store is another bargain.
  const elsewhere = { bargains: indexBargains([bargain(7, 2)], [1, 2]), bonus: null };
  assert.equal(entryStatus(entry, elsewhere, TODAY).status, 'gone');
});

test('a Bonus deal is gone after its week, or when a complete week lacks it; next week is upcoming', () => {
  const [deal] = entriesFor(bonus(9), { now: 0, week: WEEK });
  assert.equal(entryStatus(deal, nothing, TODAY).status, 'unknown');
  assert.equal(entryStatus(deal, { bargains: null, bonus: indexBonus([bonus(9)], true) }, TODAY).status, 'ok');
  assert.equal(entryStatus(deal, { bargains: null, bonus: indexBonus([bonus(1)], true) }, TODAY).status, 'gone');
  // A category failed: a missing deal may be in it.
  assert.equal(entryStatus(deal, { bargains: null, bonus: indexBonus([bonus(1)], false) }, TODAY).status, 'unknown');
  assert.equal(entryStatus(deal, nothing, '2026-11-30').status, 'gone');        // its week ended
  const [next] = entriesFor(bonus(9), { now: 0, week: { start: '2026-11-30', end: '2026-12-06' } });
  assert.equal(entryStatus(next, { bargains: null, bonus: indexBonus([], true) }, TODAY).status, 'upcoming');
});

test('sections: stores in the visitor order, the Bonus last, gone items last; the store filter', () => {
  const entries = [
    ...entriesFor(bonus(9), { now: 0, week: WEEK }),
    ...entriesFor(bargain(2, 5, { title: 'Brood' }), { now: 0 }),
    ...entriesFor(bargain(3, 5, { title: 'Appel' }), { now: 0 }),
    ...entriesFor(bargain(4, 6, { title: 'Kaas' }), { now: 0 }),
    ...entriesFor(bargain(5, 99, { title: 'Melk' }), { now: 0 }),      // a store no longer followed
  ];
  const latest = { bargains: indexBargains([bargain(2, 5), bargain(4, 6)], [5, 6]), bonus: indexBonus([bonus(9)], true) };
  const o = { today: TODAY, storeId: null, storeOrder: [6, 5], bonusTitle: 'Bonus' };
  const all = cartSections(entries, latest, o);
  assert.deepEqual(all.sections.map(s => s.storeId), [6, 5, 99, null]);
  assert.equal(all.sections[3].storeName, 'Bonus');
  // Store 5: Appel is gone (store loaded without it), so after Brood although A comes first.
  assert.deepEqual(all.sections[1].rows.map(r => [r.entry.title, r.status]), [['Brood', 'ok'], ['Appel', 'gone']]);
  assert.deepEqual([all.shown, all.gone, all.otherStores], [5, 1, 0]);

  const inStore = cartSections(entries, latest, { ...o, storeId: 5 });
  assert.deepEqual(inStore.sections.map(s => s.storeId), [5, null]);           // the Bonus is everywhere
  assert.deepEqual([inStore.shown, inStore.otherStores], [3, 2]);
});

test("the cart's own store filter applies only to a followed store", () => {
  assert.equal(activeCartStore('5', [5, 6]), 5);
  assert.equal(activeCartStore('7', [5, 6]), null);     // that store was removed: all stores
  assert.equal(activeCartStore(null, [5]), null);
  assert.equal(activeCartStore('5x', [5]), null);
});

test('cart images only from the allowed image hosts (the CSP img-src)', () => {
  const AH = ['https://static.ah.nl'];
  assert.equal(allowedImageUrl('https://static.ah.nl/dam/x.jpg', AH), 'https://static.ah.nl/dam/x.jpg');
  for (const bad of ['https://evil.example/x.png', 'http://static.ah.nl/x.jpg', 'https://static.ah.nl.evil.example/x',
    'https://static.ah.nl@evil.example/x', 'https://evilstatic.ah.nl/x', 'https://static.ah.nl:8443/x',
    'javascript:alert(1)', '//static.ah.nl/x', 'data:image/png;base64,AAAA', '', null, 42]) {
    assert.equal(allowedImageUrl(bad, AH), null, String(bad));
  }
  assert.equal(allowedImageUrl('https://static.ah.nl/x', []), null);              // no hosts: no images
  const wild = ['https://*.ah.nl'];
  assert.equal(allowedImageUrl('https://img.static.ah.nl/x', wild), 'https://img.static.ah.nl/x');
  assert.equal(allowedImageUrl('https://ah.nl/x', wild), null);                    // *. means subdomains only
  assert.equal(allowedImageUrl('https://evilah.nl/x', wild), null);
  assert.equal(allowedImageUrl('https://static.ah.nl/x', ['not a url', 'http://static.ah.nl']), null);

  // Adding and reading back (e.g. a synced entry from another device) drop foreign images.
  const offer = bargain(7, 1, { image: 'https://evil.example/track.png' });
  assert.equal(entriesFor(offer, { now: 0, imageHosts: AH })[0].image, null);
  const ok = entriesFor(bargain(7, 1, { image: 'https://static.ah.nl/x.jpg' }), { now: 0, imageHosts: AH })[0];
  assert.equal(ok.image, 'https://static.ah.nl/x.jpg');
  assert.equal(cleanEntry({ ...ok, image: 'https://evil.example/t.png' }, AH).image, null);
  assert.equal(cleanEntry(ok, AH).image, 'https://static.ah.nl/x.jpg');
  assert.equal(cleanEntry(ok).image, null);                                         // no hosts given: dropped
  assert.equal(cleanEntries([{ ...ok, image: 'https://static.ah.nl/' + 'a'.repeat(600) }], AH)[0].image, null);
});

test('dates', () => {
  assert.equal(dayOf('2026-11-24T10:00:00'), '2026-11-24');
  assert.equal(dayOf('24-11-2026'), null);
  assert.equal(dayOf(null), null);
  assert.equal(todayOf(new Date(2026, 0, 5, 23, 59)), '2026-01-05');
  assert.equal(bargainKey(3, { id: 7 }), 'b:3:7');
  assert.equal(bonusKey({ id: 7 }), 'p:7');
});

test('the cart follows a signed-in user: both devices keep what they added', () => {
  const a = JSON.stringify([{ key: 'p:1', kind: 'bonus', title: 'A' }]);
  const b = JSON.stringify([{ key: 'p:2', kind: 'bonus', title: 'B' }]);
  const merged = JSON.parse(mergePrefs({ ah_cart: a }, { ah_cart: b }).ah_cart);
  assert.deepEqual(merged.map(e => e.key), ['p:2', 'p:1']);
});

test('the cart button: a card for two stores adds both, and removes both when tapped again', async () => {
  const { cartEntries, cartHasBonus, clearCart, inCart, removeFromCart, toggleCart } =
    await import('../../ah_dashboard/static/js/lists/cart.js');
  const { on, EV } = await import('../../ah_dashboard/static/js/core/events.js');
  let changes = 0;
  const off = on(EV.cart, () => { changes++; });
  const card = { ...bargain(7, 1), stores: [{ id: 1, name: 'A', stock: 2 }, { id: 2, name: 'B', stock: 1 }] };
  toggleCart(card);
  assert.deepEqual(cartEntries().map(e => e.key), ['b:1:7', 'b:2:7']);
  assert.ok(inCart(card) && inCart(bargain(7, 2)) && !inCart(bargain(7, 3)));
  // Only one store's entry left (the other was removed in the cart view): still "in the cart"; a tap removes it.
  removeFromCart(['b:2:7']);
  assert.ok(inCart(card));
  toggleCart(card);
  assert.equal(cartEntries().length, 0);
  toggleCart(bonus(9), WEEK);
  assert.ok(cartHasBonus());
  assert.equal(cartEntries()[0].until, '2026-11-29');
  clearCart();
  assert.equal(cartEntries().length, 0);
  assert.equal(changes, 5);
  off();
});
