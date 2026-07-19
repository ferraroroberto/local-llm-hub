/* Machines tab — in-browser SSH terminal shim (issue #309).
 *
 * Minimal connection glue against the documented protocol only:
 *   WS  /admin/api/machines/{id}/terminal
 *   ->  {"type":"input","data":"…"}  |  {"type":"resize","rows":N,"cols":N}
 *   <-  raw terminal text; or a JSON control frame
 *       {"type":"error","message":"…"}  |  {"type":"shutdown"}
 *
 * Deliberately NOT a port of app-launcher's terminal*.js — those modules
 * carry a warm-terminal cache, PC-mirror windows, on-screen keys, and image
 * paste that are welded to that app's own SPA. This file only opens →
 * streams → resizes → closes one xterm instance inside the vendored modal
 * dialog (index.html #machinesTerminalDialog).
 *
 * The terminal companion (an app-launcher `ssh` agent) isn't registered yet
 * this session, so `/admin/api/machines/terminal/status` may legitimately
 * report unavailable — that must degrade to a clean message, never a hang
 * or a thrown error.
 */

import { els } from './state.js';
import { jsonApi, urlWithToken } from './api.js';

let currentTerm = null;
let currentWs = null;
let onWindowResize = null;

function showUnavailable(reason) {
  if (els.machinesTerminalUnavailable) els.machinesTerminalUnavailable.hidden = false;
  if (els.machinesTerminalUnavailableMsg) els.machinesTerminalUnavailableMsg.textContent = reason;
}

function sendResize(term, fit, ws) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try { if (fit) fit.fit(); } catch (_) { /* host not laid out yet */ }
  try { ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols })); } catch (_) { /* ignore */ }
}

function connectTerminal(id) {
  if (!window.Terminal || !window.FitAddon) {
    showUnavailable('Terminal renderer failed to load.');
    return;
  }
  const rootStyle = getComputedStyle(document.documentElement);
  const term = new window.Terminal({
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: 13,
    scrollback: 5000,
    // Theme from the fleet tokens (light/dark), not a hardcoded palette —
    // xterm owns its own rendering, so CSS alone can't restyle it.
    theme: {
      background: rootStyle.getPropertyValue('--code-bg').trim() || '#0d1117',
      foreground: rootStyle.getPropertyValue('--ink').trim() || '#e6edf3',
    },
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(els.machinesTerminalMount);
  try { fit.fit(); } catch (_) { /* mount not sized yet on first paint */ }
  currentTerm = term;

  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsPath = urlWithToken('/admin/api/machines/' + encodeURIComponent(id) + '/terminal');
  const ws = new WebSocket(proto + '//' + window.location.host + wsPath);
  currentWs = ws;

  ws.onopen = function () { sendResize(term, fit, ws); };

  ws.onmessage = function (ev) {
    if (term !== currentTerm) return; // dialog closed / re-opened mid-flight
    const data = ev.data;
    let control = null;
    // The protocol multiplexes raw terminal text and JSON control frames on
    // the same socket; only a parsed object carrying a `type` counts as
    // control — raw output that happens to start with "{" still just prints.
    if (typeof data === 'string' && data.charAt(0) === '{') {
      try {
        const parsed = JSON.parse(data);
        if (parsed && typeof parsed === 'object' && parsed.type) control = parsed;
      } catch (_) { /* not JSON — plain terminal text */ }
    }
    if (control && control.type === 'error') {
      term.writeln('\r\n[terminal] ' + (control.message || 'error'));
      return;
    }
    if (control && control.type === 'shutdown') {
      term.writeln('\r\n[terminal] session ended.');
      try { ws.close(); } catch (_) { /* ignore */ }
      return;
    }
    term.write(data);
  };

  ws.onerror = function () {
    if (term === currentTerm) term.writeln('\r\n[terminal] connection error.');
  };
  ws.onclose = function () {
    if (term === currentTerm) term.writeln('\r\n[terminal] disconnected.');
  };

  term.onData(function (d) {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data: d }));
  });

  onWindowResize = function () {
    if (term !== currentTerm) return;
    sendResize(term, fit, ws);
  };
  window.addEventListener('resize', onWindowResize);
}

/* Open the overlay for one machine. Always checks
 * /admin/api/machines/terminal/status first — connecting straight to the WS
 * and only then discovering it's unavailable would be a silent-looking hang
 * behind the "Connecting…" state that never resolves. */
export async function openMachinesTerminal(id, displayName) {
  if (!els.machinesTerminalDialog) return;
  teardownMachinesTerminal();
  if (els.machinesTerminalTitle) els.machinesTerminalTitle.textContent = 'Terminal — ' + displayName;
  if (els.machinesTerminalUnavailable) els.machinesTerminalUnavailable.hidden = true;
  if (els.machinesTerminalMount) els.machinesTerminalMount.innerHTML = '';
  if (els.machinesTerminalDialog.showModal) els.machinesTerminalDialog.showModal();

  let status;
  try {
    status = await jsonApi('/admin/api/machines/terminal/status');
  } catch (exc) {
    if (String(exc.message) === 'auth required') { els.machinesTerminalDialog.close(); return; }
    showUnavailable('Could not check terminal availability.');
    return;
  }
  if (!status || !status.available) {
    showUnavailable((status && status.reason) || 'The in-browser terminal is not available right now.');
    return;
  }
  connectTerminal(id);
}

/* Tear down the live WS + xterm instance. Idempotent — safe to call before
 * every open and from the dialog's native 'close' event (Esc, the × button,
 * or a programmatic .close() all fire it exactly once). */
function teardownMachinesTerminal() {
  if (currentWs) {
    try {
      currentWs.onopen = null;
      currentWs.onmessage = null;
      currentWs.onerror = null;
      currentWs.onclose = null;
      currentWs.close();
    } catch (_) { /* ignore */ }
    currentWs = null;
  }
  if (currentTerm) {
    try { currentTerm.dispose(); } catch (_) { /* ignore */ }
    currentTerm = null;
  }
  if (onWindowResize) { window.removeEventListener('resize', onWindowResize); onWindowResize = null; }
  if (els.machinesTerminalMount) els.machinesTerminalMount.innerHTML = '';
}

export function wireMachinesTerminal() {
  if (els.machinesTerminalCloseBtn) {
    els.machinesTerminalCloseBtn.addEventListener('click', function () {
      if (els.machinesTerminalDialog && els.machinesTerminalDialog.open) els.machinesTerminalDialog.close();
    });
  }
  // Covers every close path (×, Esc → native 'cancel'+'close', or a
  // programmatic .close() from openMachinesTerminal's own auth-required
  // guard) with one teardown, so the WS/xterm never outlive the dialog.
  if (els.machinesTerminalDialog) {
    els.machinesTerminalDialog.addEventListener('close', teardownMachinesTerminal);
  }
}
