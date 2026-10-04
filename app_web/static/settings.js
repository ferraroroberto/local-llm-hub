/* Settings: the home-head gear on every pane opens one dialog holding the
 * vendored text-size control. Persisted under `llmhub.textsize`; the
 * pre-paint boot script in index.html stamps it before first paint, and
 * bindTextSize() re-stamps and paints the selected step here.
 */

import { bindTextSize } from './_vendored/text-size/text-size.js';

const APP_PREFIX = 'llmhub';

export function wireSettings() {
  const dialog = document.getElementById('settingsDialog');
  const closeBtn = document.getElementById('settingsCloseBtn');
  const control = document.getElementById('textSizeControl');
  if (!dialog || !dialog.showModal) return;
  if (control) bindTextSize(control, APP_PREFIX);
  document.querySelectorAll('.home-settings').forEach(function (btn) {
    btn.addEventListener('click', function () { dialog.showModal(); });
  });
  if (closeBtn) closeBtn.addEventListener('click', function () { dialog.close(); });
}
