// @ts-check
// Drag an item onto a target within one container (e.g. a category pill onto
// another pill or a group).
//
// - Mouse / pen: press and move (a plain click still clicks).
// - Touch: press and hold briefly, then drag; moving before that is a normal
//   scroll. A row that scrolls sideways (phones) scrolls along when the finger
//   is near its edge.
// - The dragged item gets the class "dragging", the target under the pointer
//   "droptarget"; nothing else moves. Dropping on a target calls
//   onDrop(item, target); Escape or an interrupted drag cancels.
//
// Pointer events, not the HTML drag-and-drop API (no touch support on most
// phones). Keyboard users get another way to do the same (a form elsewhere).

const MOVE_PX = 6;
const HOLD_MS = 350;
const EDGE_PX = 36;    // auto-scroll zone at the edges of a sideways-scrolling row
const EDGE_STEP = 14;

/**
 * @param {HTMLElement} container
 * @param {string} itemSelector what can be dragged
 * @param {string} targetSelector where it can be dropped
 * @param {(item: HTMLElement, target: HTMLElement) => void} onDrop
 */
export function makeDropDraggable(container, itemSelector, targetSelector, onDrop) {
  /** @type {any} {el, id, x, y, touch, active, timer, target} */
  let drag = null;
  let swallowClick = false;

  /** @param {HTMLElement|null} target */
  function setTarget(target) {
    if (drag.target === target) return;
    if (drag.target) drag.target.classList.remove('droptarget');
    drag.target = target;
    if (target) target.classList.add('droptarget');
  }

  function begin() {
    drag.active = true;
    drag.el.classList.add('dragging');
    container.classList.add('dropping');
    try { drag.el.setPointerCapture(drag.id); } catch (e) { /* still works */ }
  }

  /** @param {boolean} commit */
  function finish(commit) {
    if (!drag) return;
    clearTimeout(drag.timer);
    const { el, active, target } = drag;
    if (target) target.classList.remove('droptarget');
    drag = null;
    el.classList.remove('dragging');
    container.classList.remove('dropping');
    if (!active) return;
    swallowClick = true;
    setTimeout(() => { swallowClick = false; }, 0);
    if (commit && target) onDrop(el, target);
  }

  /** @param {number} x @param {number} y */
  function track(x, y) {
    // Sideways-scrolling row: scroll along near its edges.
    if (container.scrollWidth > container.clientWidth) {
      const r = container.getBoundingClientRect();
      if (x < r.left + EDGE_PX) container.scrollLeft -= EDGE_STEP;
      else if (x > r.right - EDGE_PX) container.scrollLeft += EDGE_STEP;
    }
    const under = document.elementFromPoint(x, y);
    const target = /** @type {HTMLElement|null} */ (under && under.closest(targetSelector));
    setTarget(target && container.contains(target) && target !== drag.el ? target : null);
  }

  container.addEventListener('pointerdown', e => {
    const el = /** @type {Element} */ (e.target).closest(itemSelector);
    if (!el || !container.contains(el) || (e.pointerType === 'mouse' && e.button !== 0)) return;
    finish(false);
    drag = { el, id: e.pointerId, x: e.clientX, y: e.clientY, touch: e.pointerType === 'touch',
      active: false, timer: 0, target: null };
    if (drag.touch) drag.timer = setTimeout(() => { if (drag && drag.el === el) begin(); }, HOLD_MS);
  });

  container.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.active) {
      const moved = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > MOVE_PX;
      if (drag.touch) { if (moved) finish(false); return; }
      if (!moved) return;
      begin();
    }
    e.preventDefault();
    track(e.clientX, e.clientY);
  });

  container.addEventListener('pointerup', e => { if (drag && e.pointerId === drag.id) finish(true); });
  container.addEventListener('pointercancel', e => { if (drag && e.pointerId === drag.id) finish(false); });
  container.addEventListener('touchmove', e => { if (drag && drag.active) e.preventDefault(); }, { passive: false });
  container.addEventListener('contextmenu', e => { if (drag) e.preventDefault(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drag && drag.active) finish(false); });
  container.addEventListener('click', e => {
    if (swallowClick) { e.preventDefault(); e.stopPropagation(); swallowClick = false; }
  }, true);
}
