/* Langfuse helpers: the OTel stack's health, the client-reachable Langfuse
 * address, one trace's prompt and completion preview, and the thumbs score.
 *
 * Split out of the retired OTel pane's telemetry.js; the Hub's
 * Langfuse sheet and its Activity trace sheet are the callers now.
 */

import { state } from './state.js';
import { jsonApi, postJson, toast, escapeHtml, isAuthError } from './api.js';

// --------------------------------------------------------- health
/* GET /admin/api/telemetry/health into state.otelHealth. A failed read is
 * its own state (otelHealthFailed), so a reader never shows the last good
 * value as current. */
export async function fetchOtelHealth() {
  try {
    state.otelHealth = await jsonApi('/admin/api/telemetry/health');
    state.otelHealthFailed = false;
  } catch (exc) {
    if (isAuthError(exc)) return;
    state.otelHealthFailed = true;
  }
}

// --------------------------------------------------------- addresses
export function langfuseUiBase() {
  // Pick the host the user's browser can actually reach Langfuse at.
  //
  //   1. If an explicit LANGFUSE_PUBLIC_URL is configured, use it
  //      verbatim. Right for Cloudflare-tunnel setups where the
  //      Langfuse UI lives on its own tunneled hostname.
  //   2. Otherwise reuse the hostname the SPA itself was loaded from
  //      and swap the port for Langfuse's. Works automatically across
  //      localhost / LAN / Tailscale because the hub and Langfuse run
  //      on the same machine — the hostname the client used to reach
  //      :8000 also reaches :3000.
  const h = state.otelHealth || {};
  if (h.langfuse_public_url) return h.langfuse_public_url;
  const port = h.langfuse_port || 3000;
  const loc = window.location;
  return loc.protocol + '//' + loc.hostname + ':' + port;
}

export function langfuseTraceUrl(traceId) {
  const h = state.otelHealth || {};
  const base = langfuseUiBase();
  const pid = h.langfuse_project_id || '';
  // Langfuse v3 path includes the project_id. When we haven't resolved
  // it yet (offline, missing keys, first probe pending) fall back to
  // the host root — Langfuse routes to the right project after login.
  if (pid) {
    return base + '/project/' + encodeURIComponent(pid) +
      '/traces/' + encodeURIComponent(traceId);
  }
  return base + '/trace/' + encodeURIComponent(traceId);
}

// --------------------------------------------------------- one trace
/* Fill `target` with a trace's id and its prompt and completion from
 * Langfuse, or the offline fallback message when the stack can't answer. */
export async function hydrateTraceDetail(target, traceId) {
  let body;
  try {
    body = await jsonApi('/admin/api/telemetry/trace/' + encodeURIComponent(traceId));
  } catch (exc) {
    target.innerHTML = '<span class="muted">detail fetch failed: '
      + escapeHtml(exc.message || 'unknown') + '</span>';
    return;
  }
  const lf = body.langfuse || {};
  const parts = [];
  parts.push('<div class="trace-detail-row"><span class="muted">Trace</span><code>'
    + escapeHtml(body.trace_id) + '</code></div>');

  if (lf.available) {
    parts.push('<div class="trace-detail-section"><div class="muted">Prompt</div>'
      + '<pre class="trace-detail-pre">' + escapeHtml(stringifyPayload(lf.input)) + '</pre></div>');
    parts.push('<div class="trace-detail-section"><div class="muted">Completion</div>'
      + '<pre class="trace-detail-pre">' + escapeHtml(stringifyPayload(lf.output)) + '</pre></div>');
  } else {
    const msg = lf.fetch_error || 'Langfuse stack offline — start it for full prompt/completion bodies.';
    parts.push('<div class="trace-detail-section muted">' + escapeHtml(msg) + '</div>');
  }
  target.innerHTML = parts.join('');
}

function stringifyPayload(value) {
  if (value == null) return '(empty)';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); }
  catch (_) { return String(value); }
}

// --------------------------------------------------------- feedback
export async function postFeedback(traceId, value) {
  try {
    await postJson('/admin/api/trace/' + encodeURIComponent(traceId) + '/feedback', {
      thumbs: value,
    });
    toast(value > 0 ? 'Thumbs up sent' : (value < 0 ? 'Thumbs down sent' : 'noted'), 'good');
  } catch (exc) {
    toast('feedback failed: ' + (exc.message || 'unknown'), 'error');
  }
}
