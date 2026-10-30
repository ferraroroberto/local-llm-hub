/* The shared confirm dialog — one vendored native <dialog> (_vendored/modal)
 * that Models (Force stop), Machines (Reboot / Shut down) and Diagnostics
 * (Delete capture) all await through confirmDialog(), so no tab owns a
 * listener on its buttons.
 */

import { els } from './state.js';

// Resolves the promise of the confirm currently on screen; null when none is.
let settle = null;
let wired = false;

function wire() {
  if (wired) return;
  wired = true;
  els.confirmDialogBtn.addEventListener('click', function () {
    if (settle) settle(true);
    els.confirmDialog.close();
  });
  els.confirmDialogCloseBtn.addEventListener('click', function () { els.confirmDialog.close(); });
  // Close button, Esc and a confirm all end in 'close'; a confirm has already
  // settled true by then, so only a dismissal reaches here with `settle` set.
  els.confirmDialog.addEventListener('close', function () {
    if (settle) settle(false);
  });
}

/* Ask the user to confirm a destructive action. Resolves true on confirm,
 * false on the close button or Esc; a second call while one is open dismisses
 * the first. */
export function confirmDialog({ title, body, confirmLabel }) {
  wire();
  if (settle) settle(false);
  return new Promise(function (resolve) {
    settle = function (ok) {
      settle = null;
      resolve(ok);
    };
    els.confirmDialogTitle.textContent = title;
    els.confirmDialogBody.textContent = body;
    els.confirmDialogBtn.textContent = confirmLabel;
    if (!els.confirmDialog.open) els.confirmDialog.showModal();
  });
}
