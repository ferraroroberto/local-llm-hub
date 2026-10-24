/* Usage log wiring: which views and controls the owner uses, and on
 * which device class — the contract is _vendored/usage-log/README.md.
 *
 * The desktop is loopback and bypasses the bearer gate; a phone on the tunnel
 * or tailnet does not, so the one endpoint carries the token or its events
 * would 401 silently and the log would only ever describe the desktop. The
 * vendored client takes it through its `headers` option (a function, so a
 * rotated token is read on every send). Nothing here replaces window.fetch or
 * navigator.sendBeacon, and no other request is touched.
 */

import { initUsageLog } from './_vendored/usage-log/usage-log.js';
import { authHeaders } from './api.js';

// The sub-app is mounted at /admin (see app_web/server.py); the path is
// same-origin, which is all the vendored client accepts.
export const USAGE_ENDPOINT = '/admin/api/usage/events';

// Start the logger once, before the nav is wired so the restored tab counts
// as the first view. Returns { trackView }, handed to onTabChange().
export function wireUsageLog() {
  return initUsageLog({
    endpoint: USAGE_ENDPOINT,
    headers: function () { return authHeaders(); },
  });
}
