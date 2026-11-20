// @ts-check
// Rename a pill in place: double-click it (or press F2 while it has focus) and
// it turns into a text field with the same look. Enter or leaving the field
// saves; Escape cancels. Used for Bonus category groups and bargain presets.
//
// onCommit(newName) returns '' / undefined when saved, or an error message:
// the field then stays open and shows the message (e.g. "name in use").
// The caller re-renders the row after a save; after a cancel the pill comes back.

/**
 * @param {HTMLElement} pill
 * @param {{name: string, maxLength?: number, label?: string, onCommit: (name: string) => string|undefined|void}} options
 */
export function startRename(pill, { name, maxLength = 40, label, onCommit }) {
  if (!pill || !pill.parentNode || pill.dataset.renaming) return;
  pill.dataset.renaming = '1';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'presetbtn renameinput';
  input.value = name;
  input.maxLength = maxLength;
  input.size = Math.max(6, Math.min(maxLength, name.length + 2));
  input.autocomplete = 'off';
  input.spellcheck = false;
  if (label) input.setAttribute('aria-label', label);
  pill.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const close = () => {
    done = true;
    delete pill.dataset.renaming;
    if (input.parentNode) input.replaceWith(pill);
    pill.focus();
  };
  const commit = () => {
    if (done) return;
    const value = input.value.replace(/\s+/g, ' ').trim();
    if (!value || value === name) { close(); return; }
    const error = onCommit(value);
    if (error) {
      input.setCustomValidity(error);
      input.title = error;
      input.reportValidity();
      input.focus();
      return;
    }
    done = true;          // saved: the caller has re-rendered the row
    if (input.parentNode) input.replaceWith(pill);
  };
  input.addEventListener('input', () => { input.setCustomValidity(''); input.title = ''; input.size = Math.max(6, input.value.length + 2); });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') { e.preventDefault(); close(); }
  });
  input.addEventListener('blur', () => {
    // Leaving with an invalid name: give up on it (keep the old name).
    if (!done && input.validationMessage) { close(); return; }
    commit();
  });
}

/**
 * Wire double-click and F2 on the pills of a row: ``selector`` finds the
 * renameable pills, ``open(pill)`` starts the rename. Uses the click count of
 * the second click (``detail``), so it still works when the first click
 * re-rendered the row.
 */
/**
 * @param {HTMLElement} container
 * @param {string} selector
 * @param {(pill: HTMLElement) => void} open
 */
export function onRenameGesture(container, selector, open) {
  container.addEventListener('click', e => {
    if (e.detail !== 2) return;
    const pill = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest(selector));
    if (pill && container.contains(pill)) { e.preventDefault(); open(pill); }
  });
  container.addEventListener('keydown', e => {
    if (e.key !== 'F2') return;
    const pill = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest(selector));
    if (pill && container.contains(pill)) { e.preventDefault(); open(pill); }
  });
}
