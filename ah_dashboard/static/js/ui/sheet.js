// @ts-check
// A settings sheet: a modal <dialog> opened by a button. On phones the CSS puts
// it at the bottom of the screen (a "bottom sheet"), on wider screens it is a
// centred panel. Settings in it apply at once, so the results behind it are
// already up to date when it closes.
//
// The browser's own modal dialog does the hard parts: focus stays inside while
// it is open, Escape closes it, and the page behind can't be clicked. This
// module adds: close buttons ([data-close]), closing by a tap beside the panel
// (on the backdrop), aria-expanded on the opener, and focus back on the opener.

/**
 * @param {HTMLDialogElement} dialog
 * @param {HTMLElement} opener
 * @param {{onOpen?: () => void}} [options]
 */
export function makeSheet(dialog, opener, { onOpen } = {}) {
  const open = () => {
    if (dialog.open) return;
    if (onOpen) onOpen();
    dialog.showModal();
    opener.setAttribute('aria-expanded', 'true');
  };
  opener.addEventListener('click', open);
  dialog.addEventListener('close', () => {
    opener.setAttribute('aria-expanded', 'false');
    opener.focus();
  });
  dialog.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => dialog.close()));
  // A click on the dialog element itself (not on its contents) is a click on
  // the backdrop: the panel's children cover the whole dialog box. Only a press
  // that also started there counts, so selecting text and releasing outside
  // doesn't close it.
  let downOnBackdrop = false;
  dialog.addEventListener('pointerdown', e => { downOnBackdrop = e.target === dialog; });
  dialog.addEventListener('click', e => {
    if (e.target === dialog && downOnBackdrop) dialog.close();
    downOnBackdrop = false;
  });
  return { open, close: () => dialog.close() };
}
