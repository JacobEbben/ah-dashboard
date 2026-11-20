// @ts-check
// Unit price ("€ 4.98 / kg") from a price and AH's pack-size text
// ("500 g", "1,5 l", "6 x 33 cl", "10 stuks", "ca. 650 g", "per kg").
// Only sizes that can be read without guessing give a unit price; anything
// else (ranges, "250 g + 50 g gratis", "per bos", ...) gives none.

// Unit word -> [basis, factor to the basis unit].
/** @type {Record<string, ['kg'|'l'|'piece', number]>} */
const UNITS = {
  g: ['kg', 0.001], gr: ['kg', 0.001], gram: ['kg', 0.001], grams: ['kg', 0.001],
  kg: ['kg', 1], kilo: ['kg', 1], kilogram: ['kg', 1],
  ml: ['l', 0.001], cl: ['l', 0.01], dl: ['l', 0.1],
  l: ['l', 1], lt: ['l', 1], ltr: ['l', 1], liter: ['l', 1], litre: ['l', 1],
  st: ['piece', 1], stk: ['piece', 1], stuk: ['piece', 1], stuks: ['piece', 1],
};
// Largest believable pack per basis (anything bigger is probably misread).
/** @type {Record<string, number>} */
const MAX = { kg: 50, l: 50, piece: 500 };

const NUM = '(\\d+(?:\\.\\d+)?)';
const MULTI_RE = new RegExp(`^${NUM}\\s*[x×]\\s*${NUM}\\s*([a-z]+)$`);
const SINGLE_RE = new RegExp(`^${NUM}\\s*([a-z]+)$`);

/** {amount, basis} ("kg" | "l" | "piece") from a pack-size text, or null. */
/**
 * @param {unknown} text
 * @returns {{amount: number, basis: 'kg'|'l'|'piece'}|null}
 */
export function parseUnitSize(text) {
  let s = String(text || '').toLowerCase().trim();
  if (!s || /[+]|\d\s*-\s*\d|\//.test(s)) return null;       // "250 g + 50 g", "4-6 stuks", "1/2"
  s = s.replace(/,/g, '.')
    .replace(/^(ca\.?|circa|±|los|per)\s+/, '')
    .replace(/^(ca\.?|circa|±|per)\s+/, '')                    // "los per kg"
    .replace(/\.$/, '')
    .trim();
  let amount, word;
  let m = s.match(MULTI_RE);
  if (m) {
    amount = Number(m[1]) * Number(m[2]);
    word = m[3];
  } else if ((m = s.match(SINGLE_RE))) {
    amount = Number(m[1]);
    word = m[2];
  } else if (UNITS[s]) {                                     // "kg", "stuk" (after "per")
    amount = 1;
    word = s;
  } else {
    return null;
  }
  const unit = UNITS[word];
  if (!unit || !(amount > 0)) return null;
  const [basis, factor] = unit;
  const total = amount * factor;
  if (!(total > 0) || total > MAX[basis]) return null;
  return { amount: total, basis };
}

/**
 * {value, basis} price per kg / l / piece, or null. Per piece only for packs
 * of 2 or more (for one piece the price already is the unit price).
 */
/**
 * @param {unknown} price
 * @param {unknown} sizeText
 * @returns {{value: number, basis: 'kg'|'l'|'piece'}|null}
 */
export function unitPrice(price, sizeText) {
  const p = Number(price);
  if (price == null || !(p > 0)) return null;
  const size = parseUnitSize(sizeText);
  if (!size || (size.basis === 'piece' && size.amount < 2)) return null;
  return { value: p / size.amount, basis: size.basis };
}

/** Sort key: kg/l prices first (cheapest first), then per piece, then none. */
/** @param {{value: number, basis: string}|null} up @returns {[number, number]} */
export function unitSortKey(up) {
  if (!up) return [2, Infinity];
  return [up.basis === 'piece' ? 1 : 0, up.value];
}

/** @param {{value: number, basis: string}|null} a @param {{value: number, basis: string}|null} b */
export function compareUnit(a, b) {
  const ka = unitSortKey(a), kb = unitSortKey(b);
  if (ka[0] !== kb[0]) return ka[0] - kb[0];
  return ka[1] === kb[1] ? 0 : ka[1] - kb[1];
}
