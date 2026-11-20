// @ts-check
// DOM helpers, including safe HTML building.

/**
 * The element with this id. Typed `any`: callers know which element (input,
 * select, …) an id refers to; a missing id is a bug the page shows at once.
 * @param {string} id
 * @returns {any}
 */
export const $ = id => document.getElementById(id);

/** HTML-encode any value for use in element content or a quoted attribute. */
/** @param {unknown} value */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"'`=/]/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    '`': '&#96;', '=': '&#61;', '/': '&#47;',
  }[c]));
}

/** Markup produced by the `html` tag. Only this type is inserted unescaped. */
class SafeHtml {
  /** @param {string} value */
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function encode(value) {
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(encode).join('');
  if (value === null || value === undefined || value === false) return '';
  return esc(value);
}

/**
 * Tagged template that HTML-encodes every interpolated value, numbers
 * included. Nested `html` fragments (and arrays of them) are kept as markup.
 *
 *   el.innerHTML = html`<b>${name}</b>`;
 *
 * @param {TemplateStringsArray} strings
 * @param {...unknown} values
 * @returns {any} a SafeHtml (typed any so it can be assigned to innerHTML)
 */
export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) out += encode(values[i]) + strings[i + 1];
  return new SafeHtml(out);
}

/** Set a <select> to `value` if it has such an option. */
/**
 * @param {string} id
 * @param {unknown} value
 */
export function setSelect(id, value) {
  const el = $(id);
  if (value != null && [...el.options].some(o => o.value === String(value))) el.value = String(value);
}

// All foldable panels form one accordion: opening one folds the others.
/** @type {{panel: HTMLElement, set: (folded: boolean) => void}[]} */
const foldables = [];

/**
 * Wire a fold button to its panel; ``onChange(folded)`` remembers the state.
 * @param {HTMLElement} panel
 * @param {HTMLElement} toggle
 * @param {(folded: boolean) => void} [onChange]
 * @returns {(folded: boolean) => void} sets the folded state
 */
export function makeFoldable(panel, toggle, onChange) {
  /** @param {boolean} folded */
  const set = folded => {
    panel.classList.toggle('collapsed', folded);
    toggle.setAttribute('aria-expanded', String(!folded));
    if (onChange) onChange(folded);
    if (!folded) {
      for (const f of foldables) {
        if (f.panel !== panel && !f.panel.classList.contains('collapsed')) f.set(true);
      }
    }
  };
  foldables.push({ panel, set });
  toggle.addEventListener('click', () => set(!panel.classList.contains('collapsed')));
  return set;
}
