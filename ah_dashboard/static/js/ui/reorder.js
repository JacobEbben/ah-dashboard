// @ts-check
// Reorder the items of a row by dragging or with the keyboard.
//
// - Mouse / pen: press and move (a plain click still clicks).
// - Touch: press and hold briefly, then drag. Moving the finger before that is
//   a normal scroll, so swiping across the row still scrolls the page.
// - Keyboard: Alt+Left / Alt+Right moves the focused item.
//
// Pointer events (not the HTML drag-and-drop API, which doesn't work with touch
// on most phones). The item moves live within the row while dragging; when it
// is dropped, `onReorder(items)` gets the items in their new order. A drag
// interrupted by the browser (pointercancel) or Escape puts everything back.

const MOVE_PX = 6;      // mouse: movement before a press becomes a drag
const HOLD_MS = 350;    // touch: hold time before a press becomes a drag

/**
 * @param {HTMLElement} container
 * @param {string} selector the movable items
 * @param {(items: HTMLElement[]) => void} onReorder
 * @param {{onKeyboardMove?: (el: HTMLElement, index: number, count: number) => void}} [options]
 */
export function makeReorderable(container, selector, onReorder, { onKeyboardMove } = {}) {
  /** @type {any} {el, id, x, y, touch, active, timer, before} */
  let drag = null;
  let swallowClick = false; // the click that follows a drop must not activate the item

  /** @returns {HTMLElement[]} */
  const items = () => /** @type {HTMLElement[]} */ ([...container.querySelectorAll(selector)]);

  function begin() {
    drag.active = true;
    drag.el.classList.add('dragging');
    container.classList.add('reordering');
    try { drag.el.setPointerCapture(drag.id); } catch (e) { /* not supported: still works */ }
  }

  /** @param {boolean} commit */
  function finish(commit) {
    if (!drag) return;
    clearTimeout(drag.timer);
    const { el, active, before } = drag;
    drag = null;
    el.classList.remove('dragging');
    container.classList.remove('reordering');
    if (!active) return;
    swallowClick = true;
    setTimeout(() => { swallowClick = false; }, 0);   // only the click of this very press
    const now = items();
    if (!commit) {                                     // put everything back
      for (const item of before) container.appendChild(item);
      return;
    }
    if (now.some((item, i) => item !== before[i])) onReorder(now);
  }

  /** @param {number} x @param {number} y */
  function moveTo(x, y) {
    for (const other of items()) {
      if (other === drag.el) continue;
      const r = other.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
      const after = x > r.left + r.width / 2;
      const ref = after ? other.nextElementSibling : other;
      if (ref !== drag.el && drag.el.nextElementSibling !== ref) container.insertBefore(drag.el, ref);
      return;
    }
  }

  container.addEventListener('pointerdown', e => {
    const el = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest(selector));
    if (!el || !container.contains(el) || (e.pointerType === 'mouse' && e.button !== 0)) return;
    finish(false);
    drag = { el, id: e.pointerId, x: e.clientX, y: e.clientY, touch: e.pointerType === 'touch',
      active: false, timer: 0, before: items() };
    if (drag.touch) drag.timer = setTimeout(() => { if (drag && drag.el === el) begin(); }, HOLD_MS);
  });

  container.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.active) {
      const moved = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > MOVE_PX;
      if (drag.touch) { if (moved) finish(false); return; }   // moved before the hold: a scroll
      if (!moved) return;
      begin();
    }
    e.preventDefault();
    moveTo(e.clientX, e.clientY);
  });

  container.addEventListener('pointerup', e => { if (drag && e.pointerId === drag.id) finish(true); });
  container.addEventListener('pointercancel', e => { if (drag && e.pointerId === drag.id) finish(false); });
  // While dragging by touch, the page must not scroll (needs a non-passive listener).
  container.addEventListener('touchmove', e => { if (drag && drag.active) e.preventDefault(); }, { passive: false });
  // No long-press menu on the item being held.
  container.addEventListener('contextmenu', e => { if (drag) e.preventDefault(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drag && drag.active) finish(false); });
  container.addEventListener('click', e => {
    if (swallowClick) { e.preventDefault(); e.stopPropagation(); swallowClick = false; }
  }, true);

  // Keyboard: Alt+Left / Alt+Right.
  container.addEventListener('keydown', e => {
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    const el = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest(selector));
    if (!el) return;
    const list = items();
    const i = list.indexOf(el);
    const j = e.key === 'ArrowLeft' ? i - 1 : i + 1;
    if (i < 0 || j < 0 || j >= list.length) return;
    e.preventDefault();
    container.insertBefore(el, e.key === 'ArrowLeft' ? list[j] : list[j].nextElementSibling);
    el.focus();
    const now = items();
    onReorder(now);
    if (onKeyboardMove) onKeyboardMove(el, now.indexOf(el), now.length);
  });
}
