/* Machines tab (issue #309; rows + sheet since #736) — fleet status/actions/terminal.
 *
 * Mirrors the Code tab's tab-lifecycle shape (start/stopMachinesPolls,
 * only polled while the tab is active). Five async lifecycle states
 * (design.md): loading / ready / empty / stale / error — see
 * fetchMachinesStatus() + renderMachines().
 *
 * Layout (#736, the shared parts in rows.js): one row per machine whose face
 * answers "is it up" (the avatar's alive badge, the uptime) and carries at
 * most one verb (Wake on a down machine, Start hub on a machine whose own hub
 * is down). Tapping a row opens the machine's sheet on a phone, or fills the
 * detail pane beside the list on a wide window; both render machineBody():
 * gauges, Terminal and Remote, connection, the machine's own hub (Sync, the
 * out-of-sync state, Start hub), then Reboot and Shut down last, behind the
 * confirm dialog. The pane header line names "N offline" or the stale age,
 * else the machine count.
 */

import { els, state, MACHINES_POLL_MS } from './state.js';
import { jsonApi, postJson, api, toast, escapeHtml, fmtClock, shortGpu, fmtGbValue, isAuthError } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { row, chip, setHeadLine, infoButton, defineInfo, isWide, onWideChange } from './rows.js';
import { openMachinesTerminal, wireMachinesTerminal } from './machines_terminal.js';
import { openDiagnostics, wireDiagnostics, refreshDiagnosticsChip } from './diagnostics.js';

let pollHandle = null;

defineInfo('peer-hub', 'Its own hub',
  '<p>This machine runs its own copy of the hub. Its diagnostics capture runs inside that hub, '
  + 'so open that machine’s admin to start a capture or read its runs.</p>'
  + '<p>Sync updates that hub to this hub’s build and restarts it. Start hub brings a stopped '
  + 'hub back up. Both run over a restricted remote command.</p>');

// --------------------------------------------------------- fetch + lifecycle
export async function fetchMachinesStatus() {
  try {
    const body = await jsonApi('/admin/api/machines/status');
    state.machinesStatus = body;
    state.machinesLastUpdated = Date.now();
    state.machinesDataState = (body.machines || []).length ? 'ready' : 'empty';
    // A fresh read has landed — whatever power action prompted a recheck is
    // now reflected (or will be on the very next poll), so clear every
    // pending "rechecking…" mark regardless of which id it was for.
    state.machinesRecheckIds = {};
    renderMachines();
    // The re-render replaced the Diagnostics row, so its meta line needs
    // repainting from its own (cheap) read.
    refreshDiagnosticsChip();
  } catch (exc) {
    if (isAuthError(exc)) return;
    if (state.machinesStatus) {
      // Good data exists from an earlier fetch — stale, not error (design.md:
      // preserve + label last-known content, disable freshness-sensitive
      // actions, never treat it as actionable).
      state.machinesDataState = 'stale';
    } else {
      state.machinesDataState = 'error';
      // Sanitized — never surface exc.message (hostnames/exception text)
      // in user-facing copy; logs already have the detail via jsonApi's throw.
      state.machinesErrorMsg = 'Could not read machine status.';
    }
    renderMachines();
  }
}

export function startMachinesPolls() {
  if (pollHandle) return;
  fetchMachinesStatus();
  pollHandle = setInterval(function () { fetchMachinesStatus(); }, MACHINES_POLL_MS);
}

export function stopMachinesPolls() {
  if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
}

// --------------------------------------------------------- formatting
/* "3d 14h" / "22m" / "45s" — the Machines uptime format (distinct from
 * hub.js's fmtUptime, which stops at hours for the Hub card's own process). */
function fmtUptimeHuman(seconds) {
  if (!Number.isFinite(seconds)) return null;
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h < 24) return h + 'h' + (remM ? ' ' + remM + 'm' : '');
  const d = Math.floor(h / 24);
  const remH = h % 24;
  return d + 'd' + (remH ? ' ' + remH + 'h' : '');
}

/* The stale age on the header line: "40s" / "3m" / "2h". */
function fmtAge(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return s + 's';
  const m = Math.round(s / 60);
  if (m < 60) return m + 'm';
  return Math.round(m / 60) + 'h';
}

function fmtPct(n) { return Number.isFinite(n) ? Math.round(n) + '%' : '—'; }

/* Wi-Fi RSSI (dBm) -> a plain-English band (#397). Conventional Wi-Fi
 * thresholds (Excellent/Good/Fair/Weak); the raw dBm value is always shown
 * alongside it. */
function signalQuality(dbm) {
  if (!Number.isFinite(dbm)) return null;
  if (dbm >= -60) return 'Excellent';
  if (dbm >= -70) return 'Good';
  if (dbm >= -80) return 'Fair';
  return 'Weak';
}

// --------------------------------------------------------- machine facts
function isDown(m) { return m.state === 'down'; }

/* The avatar's corner badge means alive and nothing else (design.md avatar):
 * up for this machine and every reachable peer, down for one that should
 * answer and does not, none for a dormant box (legitimately off). */
function badgeFor(m) {
  if (m.state === 'self' || m.state === 'up') return 'up';
  if (m.state === 'down') return 'down';
  return null;
}

/* The meta line's lead: the state in words (the badge is decoration, so the
 * text must say it too), then the uptime. */
function leadFor(m) {
  const uptime = fmtUptimeHuman(m.uptime_seconds);
  if (m.state === 'self') return 'This machine' + (uptime ? ' · up ' + uptime : '');
  if (m.state === 'up') return uptime ? 'Up ' + uptime : 'Up';
  if (m.state === 'dormant') return 'Dormant';
  return m.detail && m.detail !== 'Offline' ? m.detail : 'Offline';
}

function hubDown(m) { return !!(m.hub && !m.hub.reachable); }
function hubOutOfSync(m) { return !!(m.hub && m.hub.reachable && m.hub.git_sha_match === false); }

/* Exceptions only, in the tone map: a healthy machine carries no chip. */
function exceptionChips(m) {
  const out = [];
  if (m.state === 'up' && hubDown(m)) out.push({ text: 'hub down', tone: 'danger' });
  if (hubOutOfSync(m)) out.push({ text: 'out of sync', tone: 'attention' });
  if (m.via_tailscale) out.push({ text: 'via tailnet', tone: 'attention' });
  if (m.flaky) out.push({ text: 'flaky link', tone: 'attention' });
  return out;
}

function findMachine(id) {
  const list = (state.machinesStatus && state.machinesStatus.machines) || [];
  return list.find(function (m) { return m.id === id; });
}

// --------------------------------------------------------- the row
/* Wake-on-LAN is the one action a powered-off machine can take, so a down or
 * dormant machine with a MAC shows it as the row's visible verb. It is
 * non-destructive (a magic packet, never a confirm) and NOT gated on
 * freshness: sending a packet to a machine last seen down is always safe; the
 * recheck guard is its only in-flight lock. A reachable machine whose own hub
 * is down shows Start hub instead (the bootstrap the Hub tab offered, #181). */
function rowVerb(m) {
  const a = m.actions || {};
  const name = m.display_name || m.id;
  const rechecking = !!state.machinesRecheckIds[m.id];
  if (a.wake && (m.state === 'down' || m.state === 'dormant')) {
    return {
      attrs: ' data-machine-action="wake" aria-label="Wake ' + escapeHtml(name) + '"',
      label: rechecking ? 'Rechecking…' : 'Wake', glyph: 'power', disabled: rechecking,
    };
  }
  if (m.state === 'up' && hubDown(m)) {
    const busy = !!state.machinesHubBusyIds[m.id];
    return {
      attrs: ' data-machine-action="bootstrap" aria-label="Start the hub on ' + escapeHtml(name) + '"',
      label: busy ? 'Starting…' : 'Start hub', glyph: 'play', disabled: busy,
    };
  }
  return null;
}

function machineRow(m) {
  const wide = isWide();
  return row({
    cls: 'machine-row',
    attrs: ' data-machine-id="' + escapeHtml(m.id) + '"',
    glyph: m.icon || 'monitor',
    badge: badgeFor(m),
    title: m.display_name || m.id,
    lead: leadFor(m),
    // One chip on the face (design.md action-row: a chip closes the line, for
    // an exception); the sheet lists every one.
    chips: exceptionChips(m).slice(0, 1),
    meta: m.role,
    selected: wide && state.machinesSelectedId === m.id,
    mainAttrs: ' data-machine-action="open"' + (wide ? '' : ' aria-haspopup="dialog"'),
    verb: rowVerb(m),
  });
}

// --------------------------------------------------------- the sheet body
/* Resource-pressure level for a gauge fill — a genuine state signal: accent
 * while healthy, attention past 75%, danger past 90%. */
function gaugeLevel(pct) {
  if (!Number.isFinite(pct)) return 'ok';
  if (pct >= 90) return 'high';
  if (pct >= 75) return 'warn';
  return 'ok';
}

function gauge(label, pct, valueText) {
  const p = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
  return '<div class="gauge" data-level="' + gaugeLevel(pct) + '">'
    + '<div class="gauge-head"><span class="gauge-label">' + escapeHtml(label) + '</span>'
    + '<span class="gauge-value">' + escapeHtml(valueText) + '</span></div>'
    + '<div class="gauge-track"><div class="gauge-fill" style="width:' + p.toFixed(0) + '%"></div></div>'
    + '</div>';
}

/* CPU / RAM / GPU / disk on every machine that reports stats (this host +
 * every reachable peer); two columns on a wide window (.wide-cols). */
function statsSection(m) {
  const s = m.stats;
  if (!s) return '';
  const gauges = [];
  if (s.cpu && Number.isFinite(s.cpu.percent)) {
    gauges.push(gauge('CPU', s.cpu.percent, fmtPct(s.cpu.percent)));
  }
  if (s.ram) {
    gauges.push(gauge('RAM', s.ram.percent, fmtGbValue(s.ram.used_gb) + ' / ' + fmtGbValue(s.ram.total_gb) + ' GB'));
  }
  const gpus = s.gpus || [];
  gpus.forEach(function (g, i) {
    const label = 'GPU' + (gpus.length > 1 ? ' ' + (i + 1) : '') + (g.name ? ' · ' + shortGpu(g.name) : '');
    const value = fmtGbValue((g.used_mb || 0) / 1024) + ' / ' + fmtGbValue((g.total_mb || 0) / 1024) + ' GB · ' + fmtPct(g.util_percent) + ' util';
    gauges.push(gauge(label, g.vram_percent, value));
  });
  if (s.disk) {
    gauges.push(gauge('Disk', s.disk.percent, fmtGbValue(s.disk.used_gb) + ' / ' + fmtGbValue(s.disk.total_gb) + ' GB'));
  }
  if (!gauges.length) return '';
  return '<div class="sheet-section"><div class="machine-stats wide-cols">' + gauges.join('') + '</div></div>';
}

function actionButton(action, glyph, label, available) {
  return '<button type="button" class="button-tint" data-machine-action="' + action + '"'
    + (available ? '' : ' disabled') + '>' + icon(glyph) + '<span>' + escapeHtml(label) + '</span></button>';
}

/* Terminal and Remote. On a wide window Terminal sits in the detail pane's
 * header instead, so it is drawn once. */
function toolsSection(m, wide) {
  const a = m.actions || {};
  const btns = [];
  if (!wide) btns.push(actionButton('terminal', 'terminal', 'Terminal', a.ssh_terminal));
  btns.push(actionButton('rdp', 'monitor-smartphone', 'Remote', a.rdp));
  return '<div class="sheet-section"><div class="sheet-buttons">' + btns.join('') + '</div></div>';
}

function networkText(net) {
  if (!net || net.wireless == null) return '';
  if (net.wireless === false) return 'Wired';
  const bits = ['Wi-Fi'];
  if (net.ssid) bits.push(net.ssid);
  const q = signalQuality(net.signal_dbm);
  if (Number.isFinite(net.signal_dbm)) bits.push(Math.round(net.signal_dbm) + ' dBm' + (q ? ' (' + q + ')' : ''));
  return bits.join(' · ');
}

/* Address, MAC, link and tailnet state behind one row (a disclosure). The
 * address and MAC are config-sourced, so they show even on a down peer. */
function connectionSection(m, connOpen) {
  const net = networkText(m.network);
  const q = m.network && signalQuality(m.network.signal_dbm);
  const weak = q === 'Weak' || q === 'Fair';
  const tailnet = m.via_tailscale ? 'Reached via tailnet' : (m.has_tailscale ? 'Available' : '');
  const pairs = [];
  if (m.ip) pairs.push(['Address', m.ip, null]);
  if (m.mac) pairs.push(['MAC', m.mac, null]);
  if (net) pairs.push(['Network', net, weak ? 'attention' : null]);
  if (tailnet) pairs.push(['Tailnet', tailnet, m.via_tailscale ? 'attention' : null]);
  if (m.flaky) pairs.push(['Link', 'Flaky (answered on retry)', 'attention']);
  if (!pairs.length) return '';
  const summaryValue = m.via_tailscale ? 'via tailnet'
    : (net ? net.split(' · ')[0] : (m.ip || ''));
  const summaryTone = m.via_tailscale || m.flaky || weak ? ' data-tone="attention"' : '';
  return '<div class="sheet-section"><details class="sheet-disclosure"' + (connOpen ? ' open' : '') + '>'
    + '<summary class="sheet-row">' + icon('signal')
    + '<span class="sheet-row-label">Connection</span>'
    + '<span class="sheet-row-value"' + summaryTone + '>' + escapeHtml(summaryValue) + '</span>'
    + icon('chevron-right', 'sheet-chevron') + '</summary>'
    + '<dl class="sheet-pairs">' + pairs.map(function (p) {
      return '<dt>' + escapeHtml(p[0]) + '</dt><dd' + (p[2] ? ' data-tone="' + p[2] + '"' : '') + '>'
        + escapeHtml(p[1]) + '</dd>';
    }).join('') + '</dl></details></div>';
}

/* A peer that runs its own hub (#736, moved from the Hub tab's Services card):
 * its state, both build ids when they differ, then Sync (reachable) or Start
 * hub (down). git_sha_match is null until both sides answer; only an
 * explicit false is "out of sync" (#181). */
function hubSection(m) {
  const h = m.hub;
  if (!h) return '';
  const busy = !!state.machinesHubBusyIds[m.id];
  let value = 'In sync';
  let tone = '';
  if (!h.reachable) { value = 'Down'; tone = 'danger'; }
  else if (h.git_sha_match === false) { value = 'Out of sync'; tone = 'attention'; }
  else if (h.git_sha_match == null) { value = 'Build not compared'; }
  const ids = h.reachable && h.git_sha_match === false
    ? '<dl class="sheet-pairs"><dt>This hub</dt><dd>' + escapeHtml(h.local_git_sha || '?') + '</dd>'
      + '<dt>That hub</dt><dd data-tone="attention">' + escapeHtml(h.remote_git_sha || '?') + '</dd></dl>'
    : '';
  const action = h.reachable
    ? '<button type="button" class="button-tint" data-machine-action="sync"' + (busy ? ' disabled' : '') + '>'
      + icon('refresh-cw') + '<span>' + (busy ? 'Syncing…' : 'Sync') + '</span></button>'
    : '<button type="button" class="button-tint" data-machine-action="bootstrap"' + (busy ? ' disabled' : '') + '>'
      + icon('play') + '<span>' + (busy ? 'Starting…' : 'Start hub') + '</span></button>';
  return '<div class="sheet-section machine-hub">'
    + '<div class="sheet-row">' + icon('layers') + '<span class="sheet-row-label">Hub</span>'
    + '<span class="sheet-row-value"' + (tone ? ' data-tone="' + tone + '"' : '') + '>' + escapeHtml(value) + '</span>'
    + infoButton('peer-hub', 'About this machine’s own hub') + '</div>'
    + ids
    + '<div class="sheet-row">' + action + '</div>'
    + '</div>';
}

/* Reboot and Shut down: last, in danger-text, each behind the confirm dialog.
 * Freshness-sensitive (design.md "stale"): never fired against data already
 * known to be out of date, nor while one is in flight or being rechecked. An
 * action the machine does not support is disabled, never omitted. */
function dangerSection(m, isStale) {
  const a = m.actions || {};
  const busy = !!state.machinesBusyIds[m.id];
  const rechecking = !!state.machinesRecheckIds[m.id];
  const blocked = isStale || busy || rechecking;
  function label(verb, base) {
    if (busy) return verb === 'reboot' ? 'Rebooting…' : 'Shutting down…';
    if (rechecking) return 'Rechecking…';
    return base;
  }
  function btn(action, glyph, text, available) {
    return '<button type="button" class="sheet-danger-row" data-machine-action="' + action + '"'
      + (!available || blocked ? ' disabled' : '') + '>' + icon(glyph) + '<span>' + escapeHtml(text) + '</span></button>';
  }
  return '<div class="sheet-section sheet-danger">'
    + btn('reboot', 'rotate-ccw', label('reboot', 'Reboot'), a.reboot)
    + btn('shutdown', 'power', label('shutdown', 'Shut down'), a.shutdown)
    + '</div>';
}

function machineBody(m, isStale, wide, connOpen) {
  const chips = exceptionChips(m);
  const metaBits = [leadFor(m)];
  if (m.role) metaBits.push(m.role);
  return '<div class="machine-body" data-machine-id="' + escapeHtml(m.id) + '">'
    + '<div class="sheet-section">'
    + '<p class="sheet-meta">' + escapeHtml(metaBits.join(' · ')) + '</p>'
    + (chips.length ? '<div class="sheet-chips">' + chips.map(function (c) { return chip(c.text, c.tone); }).join('') + '</div>' : '')
    + '</div>'
    + statsSection(m)
    + toolsSection(m, wide)
    + connectionSection(m, connOpen)
    + hubSection(m)
    + dangerSection(m, isStale)
    + '</div>';
}

// --------------------------------------------------------- render
function renderHeadLine(machines) {
  const ds = state.machinesDataState;
  const el = els.machinesHeadLine;
  if (ds === 'loading') { setHeadLine(el, [], 'Reading machines…'); return; }
  if (ds === 'error') { setHeadLine(el, [{ text: 'Status unavailable', tone: 'danger' }]); return; }
  const parts = [];
  const down = machines.filter(isDown).length;
  if (down) parts.push({ text: down + ' offline', tone: 'danger' });
  let title = '';
  if (ds === 'stale' && state.machinesLastUpdated) {
    parts.push({ text: 'stale ' + fmtAge(Date.now() - state.machinesLastUpdated) });
    title = 'Last updated ' + fmtClock(state.machinesLastUpdated / 1000) + ' · live data unavailable';
  }
  const fact = machines.length ? machines.length + (machines.length === 1 ? ' machine' : ' machines') : 'No machines';
  setHeadLine(el, parts, fact, title);
}

/* On a wide window one machine's detail is always showing: keep the open one,
 * else the first that is down, else the first. */
function ensureSelection(machines) {
  if (!isWide() || !machines.length) return;
  if (state.machinesSelectedId && findMachine(state.machinesSelectedId)) return;
  const first = machines.find(isDown) || machines[0];
  state.machinesSelectedId = first.id;
}

function connOpenIn(container) {
  const d = container && container.querySelector('details.sheet-disclosure');
  return !!(d && d.open);
}

function renderDetail(isStale) {
  const pane = els.machinesDetail;
  if (!pane) return;
  const m = state.machinesSelectedId ? findMachine(state.machinesSelectedId) : null;
  if (!m) {
    pane.innerHTML = '<div class="empty-state"><svg class="icon empty-state-icon" aria-hidden="true"><use href="#i-server"></use></svg>'
      + '<p class="empty-state-message">Select a machine.</p></div>';
    return;
  }
  const a = m.actions || {};
  const connOpen = connOpenIn(pane);
  pane.innerHTML = '<div class="wide-detail-head"><h2>' + icon(m.icon || 'monitor') + escapeHtml(m.display_name || m.id) + '</h2>'
    + '<span data-machine-id="' + escapeHtml(m.id) + '">' + actionButton('terminal', 'terminal', 'Terminal', a.ssh_terminal) + '</span></div>'
    + machineBody(m, isStale, true, connOpen);
}

function renderSheet(isStale) {
  const dlg = els.machineSheet;
  if (!dlg || !dlg.open) return;
  const m = state.machinesSelectedId ? findMachine(state.machinesSelectedId) : null;
  if (!m) { dlg.close(); return; }
  els.machineSheetTitle.textContent = m.display_name || m.id;
  els.machineSheetBody.innerHTML = machineBody(m, isStale, false, connOpenIn(els.machineSheetBody));
}

function renderDiagCard(machines) {
  const card = els.machinesDiagCard;
  if (!card) return;
  const host = machines.find(function (m) { return m.is_host; });
  card.hidden = !host;
  if (!host) { card.innerHTML = ''; return; }
  card.innerHTML = '<ul class="action-rows">' + row({
    cls: 'diag-row',
    glyph: 'stethoscope',
    title: 'Diagnostics',
    lead: 'This machine',
    mainCls: 'diag-entry',
    mainAttrs: ' data-machine-action="diagnostics" aria-haspopup="dialog"',
    metaAttrs: ' data-diag-chip',
  }) + '</ul>';
}

function renderMachines() {
  const ds = state.machinesDataState;
  if (els.machinesLoading) els.machinesLoading.hidden = ds !== 'loading';
  if (els.machinesError) els.machinesError.hidden = ds !== 'error';
  if (els.machinesErrorMsg && ds === 'error') {
    els.machinesErrorMsg.textContent = state.machinesErrorMsg || 'Could not read machine status.';
  }
  const status = state.machinesStatus;
  const machines = (status && status.machines) || [];
  renderHeadLine(machines);
  const showList = (ds === 'ready' || ds === 'stale' || ds === 'empty') && !!status;
  if (!els.machinesList) return;
  els.machinesList.hidden = !showList;
  if (!showList) {
    els.machinesList.innerHTML = '';
    if (els.machinesDiagCard) els.machinesDiagCard.hidden = true;
    return;
  }
  if (!machines.length) {
    els.machinesList.classList.remove('action-list');
    els.machinesList.innerHTML = '<div class="empty-state"><svg class="icon empty-state-icon" aria-hidden="true"><use href="#i-server"></use></svg>'
      + '<p class="empty-state-message">No machines configured.</p></div>';
    renderDiagCard(machines);
    renderDetail(false);
    return;
  }
  const isStale = ds === 'stale';
  ensureSelection(machines);
  els.machinesList.classList.add('action-list');
  els.machinesList.classList.toggle('is-stale', isStale);
  els.machinesList.innerHTML = '<ul class="action-rows" aria-label="Machines">'
    + machines.map(machineRow).join('') + '</ul>';
  renderDiagCard(machines);
  renderDetail(isStale);
  renderSheet(isStale);
}

// --------------------------------------------------------- open a machine
function openMachine(id) {
  state.machinesSelectedId = id;
  if (isWide()) { renderMachines(); return; }
  const m = findMachine(id);
  if (!m || !els.machineSheet) return;
  els.machineSheetBody.innerHTML = '';
  if (!els.machineSheet.open) els.machineSheet.showModal();
  renderSheet(state.machinesDataState === 'stale');
}

// --------------------------------------------------------- destructive confirm
let pendingConfirm = null; // { id, displayName, action }

function openConfirm(id, displayName, action) {
  pendingConfirm = { id: id, displayName: displayName, action: action };
  if (els.machinesConfirmTitle) {
    els.machinesConfirmTitle.textContent = action === 'reboot' ? 'Reboot machine' : 'Shut down machine';
  }
  if (els.machinesConfirmBody) {
    els.machinesConfirmBody.textContent = action === 'reboot'
      ? 'Reboot "' + displayName + '"? Anything running there right now will be interrupted.'
      : 'Shut down "' + displayName + '"? It will need to be turned back on manually.';
  }
  if (els.machinesConfirmBtn) {
    els.machinesConfirmBtn.textContent = action === 'reboot' ? 'Reboot' : 'Shut down';
    els.machinesConfirmBtn.disabled = false;
  }
  if (els.machinesConfirmDialog && els.machinesConfirmDialog.showModal) {
    els.machinesConfirmDialog.showModal();
  }
}

function closeConfirm() {
  pendingConfirm = null;
  if (els.machinesConfirmDialog && els.machinesConfirmDialog.open) els.machinesConfirmDialog.close();
}

async function onConfirmClick() {
  if (!pendingConfirm) return;
  const id = pendingConfirm.id;
  const displayName = pendingConfirm.displayName;
  const action = pendingConfirm.action;
  closeConfirm();
  state.machinesBusyIds = Object.assign({}, state.machinesBusyIds);
  state.machinesBusyIds[id] = true;
  renderMachines();
  try {
    await postJson('/admin/api/machines/' + encodeURIComponent(id) + '/' + action, {});
    toast((action === 'reboot' ? 'Reboot' : 'Shutdown') + ' scheduled on ' + displayName + '.', 'good');
  } catch (exc) {
    // Sanitized — never surface exc.message (may carry raw infra detail
    // from a 502/exception) in this toast.
    toast((action === 'reboot' ? 'Reboot' : 'Shutdown') + ' failed on ' + displayName + '.', 'error');
  } finally {
    const nextBusy = Object.assign({}, state.machinesBusyIds);
    delete nextBusy[id];
    state.machinesBusyIds = nextBusy;
    state.machinesRecheckIds = Object.assign({}, state.machinesRecheckIds);
    state.machinesRecheckIds[id] = true;
    renderMachines();
    fetchMachinesStatus();
  }
}

// --------------------------------------------------------- RDP download
async function downloadRdp(id, displayName) {
  try {
    const res = await api('/admin/api/machines/' + encodeURIComponent(id) + '/rdp');
    if (!res.ok) {
      let detail = 'HTTP ' + res.status;
      try { const body = await res.json(); detail = (body && body.detail) || detail; } catch (_) { /* not JSON */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/i.exec(cd);
    const filename = (match && match[1]) || (displayName.replace(/[^\w.-]+/g, '_') + '.rdp');
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    toast('Downloading ' + filename, 'good');
  } catch (exc) {
    if (isAuthError(exc)) return;
    toast('Could not fetch the Remote Desktop file for ' + displayName + '.', 'error');
  }
}

// --------------------------------------------------------- wake-on-LAN
/* Fire a magic packet at a powered-off machine, then mark it for recheck so
 * the row shows "Rechecking…" until the next status read reflects the (hoped)
 * boot. Non-destructive, so — unlike reboot/shutdown — there is no confirm
 * dialog and no danger styling; the click goes straight to the endpoint. */
async function wakeMachine(id, displayName) {
  try {
    await postJson('/admin/api/machines/' + encodeURIComponent(id) + '/wake', {});
    toast('Wake packet sent to ' + displayName + '.', 'good');
    state.machinesRecheckIds = Object.assign({}, state.machinesRecheckIds);
    state.machinesRecheckIds[id] = true;
    renderMachines();
    fetchMachinesStatus();
  } catch (exc) {
    if (isAuthError(exc)) return;
    // The wake endpoint returns a clean, user-safe detail (no-MAC / clean send
    // failure), so surface it directly; fall back to a sanitized line if the
    // throw carried no message.
    toast(exc.message || 'Could not send the wake packet to ' + displayName + '.', 'error');
  }
}

// --------------------------------------------------------- peer hub
/* Start a peer's stopped hub (bootstrap) or update it to this hub's build and
 * restart it (sync) — the same endpoints the Hub tab's peer rows call. */
async function peerHubAction(id, displayName, action) {
  if (state.machinesHubBusyIds[id]) return;
  state.machinesHubBusyIds = Object.assign({}, state.machinesHubBusyIds);
  state.machinesHubBusyIds[id] = true;
  renderMachines();
  try {
    await postJson('/admin/api/hosts/' + encodeURIComponent(id) + '/' + action, {});
    toast(displayName + (action === 'sync' ? ' hub synced.' : ' hub started.'), 'good');
  } catch (exc) {
    if (isAuthError(exc)) return;
    toast((action === 'sync' ? 'Sync' : 'Start hub') + ' failed on ' + displayName + '.', 'error');
  } finally {
    const next = Object.assign({}, state.machinesHubBusyIds);
    delete next[id];
    state.machinesHubBusyIds = next;
    fetchMachinesStatus();
  }
}

// --------------------------------------------------------- wiring
function onMachinesClick(ev) {
  const btn = ev.target.closest('[data-machine-action]');
  if (!btn || btn.disabled) return;
  const action = btn.dataset.machineAction;
  if (action === 'diagnostics') { openDiagnostics(); return; }
  const holder = btn.closest('[data-machine-id]') || ev.currentTarget.querySelector('[data-machine-id]');
  const id = holder && holder.dataset ? holder.dataset.machineId : '';
  const machine = findMachine(id);
  if (!machine) return;
  const displayName = machine.display_name || id;
  if (action === 'open') openMachine(id);
  else if (action === 'terminal') openMachinesTerminal(id, displayName);
  else if (action === 'rdp') downloadRdp(id, displayName);
  else if (action === 'wake') wakeMachine(id, displayName);
  else if (action === 'bootstrap' || action === 'sync') peerHubAction(id, displayName, action);
  else if (action === 'reboot' || action === 'shutdown') openConfirm(id, displayName, action);
}

export function wireMachines() {
  if (els.machinesRetryBtn) {
    els.machinesRetryBtn.addEventListener('click', function () {
      state.machinesDataState = 'loading';
      renderMachines();
      fetchMachinesStatus();
    });
  }
  [els.machinesList, els.machinesDiagCard, els.machinesDetail, els.machineSheetBody].forEach(function (el) {
    if (el) el.addEventListener('click', onMachinesClick);
  });
  if (els.machineSheetCloseBtn) {
    els.machineSheetCloseBtn.addEventListener('click', function () { els.machineSheet.close(); });
  }
  // Crossing the wide breakpoint swaps the sheet for the detail pane (or
  // back): an open sheet hands its machine to the pane.
  onWideChange(function (wide) {
    if (wide && els.machineSheet && els.machineSheet.open) els.machineSheet.close();
    if (state.machinesStatus) renderMachines();
  });
  wireDiagnostics();
  if (els.machinesConfirmCloseBtn) els.machinesConfirmCloseBtn.addEventListener('click', closeConfirm);
  if (els.machinesConfirmBtn) els.machinesConfirmBtn.addEventListener('click', onConfirmClick);
  if (els.machinesConfirmDialog) {
    // Esc also fires 'cancel' on a native <dialog> — drop the pending
    // action so a later stray click on a since-hidden button can't fire.
    els.machinesConfirmDialog.addEventListener('cancel', function () { pendingConfirm = null; });
  }
  wireMachinesTerminal();
}
