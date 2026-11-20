// @ts-check
// Cards per row: "Auto" (the CSS media queries pick 1-6 columns for the screen)
// until the header slider is moved, then that fixed number, 1-8. A fixed number is capped to
// what fits (a card is never narrower than MIN_CARD), so 8 on a phone becomes
// 2; the saved choice stays, so the desktop still gets its 8.
//
// Card text, badges, buttons and spacing scale with the column width: the
// result grids get --cs = (column width chosen) / (column width Auto would
// give), so picking the Auto number of columns looks exactly like Auto, fewer
// columns give bigger cards, more give smaller ones (within MIN/MAX_SCALE).
//
// Stored per browser (ah_cols), not synced: what suits a phone doesn't suit a PC.
import { $ } from '../core/dom.js';
import { emit, EV } from '../core/events.js';
import { storage, KEYS } from '../core/storage.js';

const MIN_COLS = 1;
const MAX_COLS = 8;
const MIN_CARD = 130;     // px: narrowest card that still reads well (at the smallest scale)
const MIN_SCALE = 0.72;
const MAX_SCALE = 1.4;

/** @returns {HTMLElement[]} */
const grids = () => [$('results'), $('bonusResults')];

/** @type {number|null} */
let chosen = null;        // null = auto, else 1..8

function load() {
  const n = parseInt(storage.get(KEYS.cols), 10);
  return n >= MIN_COLS && n <= MAX_COLS ? n : null;
}

/** The columns the CSS gives in Auto mode at this window width (same breakpoints as app.css). */
function autoCols() {
  const w = window.innerWidth;
  if (w <= 420) return 1;
  if (w <= 700) return 2;
  if (w <= 1000) return 3;
  if (w >= 1900) return 6;
  if (w >= 1500) return 5;
  return 4;
}
const gapAt = () => (window.innerWidth <= 700 ? 12 : 18);

/** Width available to the grids: the page column (the same for both views, also the hidden one). */
function gridWidth() {
  const wrap = /** @type {HTMLElement} */ (document.querySelector('.wrap'));
  const cs = getComputedStyle(wrap);
  return wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
}

/** Most columns that fit: cards at least MIN_CARD wide (at the smallest gap). */
/** @param {number} width */
function maxFit(width) {
  const gap = gapAt() * MIN_SCALE;
  return Math.max(MIN_COLS, Math.min(MAX_COLS, Math.floor((width + gap) / (MIN_CARD + gap))));
}

function apply() {
  const width = gridWidth();
  const fit = maxFit(width);
  const range = $('colsRange');
  range.max = String(Math.max(fit, 2));
  const auto = autoCols();
  const cols = chosen === null ? auto : Math.min(chosen, fit);
  range.value = String(cols);
  $('colsValue').textContent = String(cols);

  for (const g of grids()) {
    if (chosen === null) {           // the stylesheet decides
      g.style.removeProperty('--cols');
      g.style.removeProperty('--cs');
      continue;
    }
    const gap = gapAt();
    /** @param {number} w @param {number} n */
    const colW = (w, n) => (w - gap * (n - 1)) / n;
    const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, colW(width, cols) / colW(width, auto)));
    g.style.setProperty('--cols', String(cols));
    g.style.setProperty('--cs', scale.toFixed(3));
  }
  // The views size their category sections in columns: tell them to re-render if needed.
  emit(EV.cols);
}

/** @param {number} n */
function choose(n) {
  chosen = n;
  storage.set(KEYS.cols, String(n));
  apply();
}

export function initDensity() {
  chosen = load();
  const range = $('colsRange');
  range.addEventListener('input', () => choose(parseInt(range.value, 10)));
  // Window resized, or a scrollbar came or went: measure again (once per frame).
  let frame = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(apply);
  }).observe(/** @type {HTMLElement} */ (document.querySelector('.wrap')));
  window.addEventListener('storage', e => { if (e.key === KEYS.cols) { chosen = load(); apply(); } });
  apply();
}
