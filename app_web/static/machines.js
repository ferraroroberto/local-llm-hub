/* Machines tab (issue #309) — fleet status/actions/terminal.
 *
 * Mirrors the telemetry.js tab-lifecycle shape (start/stopMachinesPolls,
 * only polled while the tab is active) and the hub.js card-rendering style
 * (template strings, delegated clicks). Five async lifecycle states
 * (design.md): loading / ready / empty / stale / error — see
 * fetchMachinesStatus() + renderMachinesList().
 */

import { els, state, MACHINES_POLL_MS } from './state.js';
import { jsonApi, postJson, api, toast, escapeHtml, fmtClock, shortGpu } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { openMachinesTerminal, wireMachinesTerminal } from './machines_terminal.js';
import { openDiagnostics, wireDiagnostics, refreshDiagnosticsChip } from './diagnostics.js';

let pollHandle = null;

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
    renderMachinesList();
    // The re-render replaced the card markup, so the diagnostics chip needs
    // repainting from its own (cheap) read.
    refreshDiagnosticsChip();
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
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
    renderMachinesList();
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

// --------------------------------------------------------- render
function stateMeta(s) {
  if (s === 'self') return { cls: 'accent', label: 'This machine' };
  if (s === 'up') return { cls: 'good', label: 'Online' };
  if (s === 'down') return { cls: 'danger', label: 'Offline' };
  if (s === 'dormant') return { cls: '', label: 'Dormant' };
  return { cls: '', label: s || 'unknown' };
}

/* "3d 14h" / "22m" / "45s" — the Machines-card uptime format (distinct from
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

function fmtGb(n) { return Number.isFinite(n) ? n.toFixed(1) : '—'; }
function fmtPct(n) { return Number.isFinite(n) ? Math.round(n) + '%' : '—'; }

/* Resource-pressure level for a gauge fill — a genuine state signal
 * (design.md: status colors signal state), not decoration: accent while
 * healthy, attention past 75%, danger past 90%. */
function gaugeLevel(pct) {
  if (!Number.isFinite(pct)) return 'ok';
  if (pct >= 90) return 'high';
  if (pct >= 75) return 'warn';
  return 'ok';
}

/* One horizontal gauge row: label + value on top, a fixed-size track with a
 * width-driven fill below. All gauges render at the same size so a column of
 * them reads as one instrument cluster (and one-stat-per-line on mobile). */
function gauge(label, pct, valueText) {
  const p = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
  return '<div class="gauge" data-level="' + gaugeLevel(pct) + '">'
    + '<div class="gauge-head"><span class="gauge-label">' + escapeHtml(label) + '</span>'
    + '<span class="gauge-value">' + escapeHtml(valueText) + '</span></div>'
    + '<div class="gauge-track"><div class="gauge-fill" style="width:' + p.toFixed(0) + '%"></div></div>'
    + '</div>';
}

/* Same CPU / RAM / GPU / disk gauges on every machine that reports stats
 * (this host + every reachable peer). Uptime is the meta-row line above, not
 * a gauge (it has no meaningful 0-100 scale). */
function renderStatsBlock(m) {
  const s = m.stats;
  if (!s) return '';
  const gauges = [];
  if (s.cpu && Number.isFinite(s.cpu.percent)) {
    gauges.push(gauge('CPU', s.cpu.percent, fmtPct(s.cpu.percent)));
  }
  if (s.ram) {
    gauges.push(gauge('RAM', s.ram.percent, fmtGb(s.ram.used_gb) + ' / ' + fmtGb(s.ram.total_gb) + ' GB'));
  }
  const gpus = s.gpus || [];
  gpus.forEach(function (g, i) {
    const label = 'GPU' + (gpus.length > 1 ? ' ' + (i + 1) : '') + (g.name ? ' · ' + shortGpu(g.name) : '');
    const value = fmtGb((g.used_mb || 0) / 1024) + ' / ' + fmtGb((g.total_mb || 0) / 1024) + ' GB · ' + fmtPct(g.util_percent) + ' util';
    gauges.push(gauge(label, g.vram_percent, value));
  });
  if (s.disk) {
    gauges.push(gauge('Disk', s.disk.percent, fmtGb(s.disk.used_gb) + ' / ' + fmtGb(s.disk.total_gb) + ' GB'));
  }
  if (!gauges.length) return '';
  return '<div class="machine-stats">' + gauges.join('') + '</div>';
}

/* Every card shows the same four actions in the same order and size —
 * Terminal · Remote · Reboot · Shut down — so the grid reads identically
 * across machines (4-up on desktop, 2×2 on a phone). An action the machine
 * doesn't support is rendered disabled (shaded), never omitted, so the
 * layout stays equal. */
function renderActions(m, isStale) {
  const a = m.actions || {};
  const busy = !!state.machinesBusyIds[m.id];
  const rechecking = !!state.machinesRecheckIds[m.id];
  // Power actions are freshness-sensitive (design.md "stale" contract): a
  // reboot/shutdown decision must never fire against data we already know
  // is out of date.
  const disablePower = isStale || busy || rechecking;
  const powerLabel = function (verb, base) {
    if (busy) return verb === 'reboot' ? 'Rebooting…' : 'Shutting down…';
    if (rechecking) return 'Rechecking…';
    return base;
  };
  function btn(action, iconName, label, available, danger, extraDisabled, extraClass) {
    const disabled = !available || extraDisabled;
    return '<button type="button" class="ghost-btn machine-action' + (danger ? ' danger' : '')
      + (extraClass ? ' ' + extraClass : '') + '"'
      + ' data-action="' + action + '"' + (disabled ? ' disabled' : '') + '>'
      + icon(iconName) + '<span>' + escapeHtml(label) + '</span></button>';
  }
  const btns = [
    btn('terminal', 'terminal', 'Terminal', a.ssh_terminal, false, false),
    btn('rdp', 'monitor-smartphone', 'Remote', a.rdp, false, false),
    btn('reboot', 'rotate-ccw', powerLabel('reboot', 'Reboot'), a.reboot, true, disablePower),
    btn('shutdown', 'power', powerLabel('shutdown', 'Shut down'), a.shutdown, true, disablePower),
  ];
  // Wake-on-LAN: the single action a powered-off machine can take, so it
  // inverts the four above (which all need the machine already up) and is
  // offered ONLY when the host is MAC-equipped (a.wake) and actually
  // down/dormant. It is non-destructive — a magic packet, never a confirm —
  // so it is not a danger button. Unlike the power actions it is NOT gated on
  // freshness (isStale): sending a packet to a machine we last saw down is
  // always safe. The recheck guard is the only in-flight lock, so a second
  // click can't fire while the first wake is being confirmed. It spans the
  // full rail on its own row (see .machine-action--wake) rather than sitting
  // as a fifth unequal cell.
  if (a.wake && (m.state === 'down' || m.state === 'dormant')) {
    btns.push(btn('wake', 'power', rechecking ? 'Rechecking…' : 'Wake', true, false, rechecking, 'machine-action--wake'));
  }
  return '<div class="machine-actions">' + btns.join('') + '</div>';
}

/* Diagnostics entry row — only on the machine you are looking at, because a
 * capture runs inside *this* hub's own process. A peer's captures are started
 * from that peer's own /admin (its hub owns its sampler), so offering the row
 * here would be a button that can't do what it says.
 *
 * Deliberately one compact row, not another gauge cluster: the card stays a
 * glance surface and the detail lives behind the drill-in.
 *
 * A peer that runs its own hub (m.runs_hub) gets a plain-text footnote
 * instead of the button, so the missing row reads as "go look over there"
 * rather than "this machine has no diagnostics". Managed-only peers
 * (openclaw, gaming — runs_hub false) get neither: they have no /admin to
 * point to. */
function renderDiagnosticsRow(m) {
  if (m.is_host) {
    return '<button type="button" class="diag-entry" data-action="diagnostics">'
      + '<span class="diag-entry-main">' + icon('stethoscope')
      + '<span>Diagnostics</span></span>'
      + '<span class="hub-live-status" data-diag-chip><span class="dot"></span><span>—</span></span>'
      + icon('chevron-right', 'diag-entry-chevron')
      + '</button>';
  }
  if (m.runs_hub) {
    return '<p class="muted small diag-hint">Diagnostics run on ' + escapeHtml(m.display_name || m.id)
      + '’s own hub — open its /admin there to view them.</p>';
  }
  return '';
}

function renderMachineCard(m, isStale) {
  const st = stateMeta(m.state);
  const uptime = fmtUptimeHuman(m.uptime_seconds);
  const metaParts = [];
  if (m.role) metaParts.push('<span>' + escapeHtml(m.role) + '</span>');
  if (uptime) metaParts.push('<span>Uptime ' + uptime + '</span>');
  // "via tailnet" (#396): the liveness probe found this peer on its Tailscale
  // name, not its LAN address — the wired path is silently dead. Falls back to
  // the plain capability chip when the LAN path is healthy (or the box is down).
  if (m.via_tailscale) metaParts.push('<span class="machine-via-tailnet">' + icon('signal') + ' via tailnet</span>');
  else if (m.has_tailscale) metaParts.push('<span>' + icon('signal') + ' Tailscale</span>');
  return '<section class="card machine-card' + (isStale ? ' is-stale' : '') + '" data-machine-id="' + escapeHtml(m.id) + '">'
    + '<div class="card-header"><div class="hub-title-block">'
    + '<h2>' + icon(m.icon || 'monitor') + escapeHtml(m.display_name || m.id) + '</h2>'
    + '<span class="hub-live-status ' + st.cls + '"><span class="dot"></span><span>' + escapeHtml(st.label) + '</span></span>'
    + '</div></div>'
    + (metaParts.length ? '<p class="machine-meta-row muted small">' + metaParts.join('') + '</p>' : '')
    + (m.detail ? '<p class="muted small">' + escapeHtml(m.detail) + '</p>' : '')
    + renderStatsBlock(m)
    + renderDiagnosticsRow(m)
    + renderActions(m, isStale)
    + '</section>';
}

function renderMachinesList() {
  const ds = state.machinesDataState;
  if (els.machinesLoading) els.machinesLoading.hidden = ds !== 'loading';
  if (els.machinesError) els.machinesError.hidden = ds !== 'error';
  if (els.machinesErrorMsg && ds === 'error') {
    els.machinesErrorMsg.textContent = state.machinesErrorMsg || 'Could not read machine status.';
  }
  const showList = ds === 'ready' || ds === 'stale' || ds === 'empty';
  if (els.machinesList) els.machinesList.hidden = !showList;
  if (els.machinesStaleNote) {
    if (ds === 'stale' && state.machinesLastUpdated) {
      els.machinesStaleNote.hidden = false;
      els.machinesStaleNote.textContent = 'Last updated ' + fmtClock(state.machinesLastUpdated / 1000) + ' · live data unavailable';
    } else {
      els.machinesStaleNote.hidden = true;
    }
  }
  if (!els.machinesList) return;
  const status = state.machinesStatus;
  if (!showList || !status) { els.machinesList.innerHTML = ''; return; }
  const machines = status.machines || [];
  if (!machines.length) {
    els.machinesList.innerHTML = '<div class="empty-state"><svg class="icon empty-state-icon" aria-hidden="true"><use href="#i-server"></use></svg>'
      + '<p class="empty-state-message">No machines configured.</p></div>';
    return;
  }
  const isStale = ds === 'stale';
  els.machinesList.innerHTML = machines.map(function (m) { return renderMachineCard(m, isStale); }).join('');
}

function findMachine(id) {
  const list = (state.machinesStatus && state.machinesStatus.machines) || [];
  return list.find(function (m) { return m.id === id; });
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
  renderMachinesList();
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
    renderMachinesList();
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
    if (String(exc.message) === 'auth required') return;
    toast('Could not fetch the Remote Desktop file for ' + displayName + '.', 'error');
  }
}

// --------------------------------------------------------- wake-on-LAN
/* Fire a magic packet at a powered-off machine, then mark it for recheck so
 * the card shows "Rechecking…" until the next status read reflects the (hoped)
 * boot. Non-destructive, so — unlike reboot/shutdown — there is no confirm
 * dialog and no danger styling; the click goes straight to the endpoint. */
async function wakeMachine(id, displayName) {
  try {
    await postJson('/admin/api/machines/' + encodeURIComponent(id) + '/wake', {});
    toast('Wake packet sent to ' + displayName + '.', 'good');
    state.machinesRecheckIds = Object.assign({}, state.machinesRecheckIds);
    state.machinesRecheckIds[id] = true;
    renderMachinesList();
    fetchMachinesStatus();
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    // The wake endpoint returns a clean, user-safe detail (no-MAC / clean send
    // failure), so surface it directly; fall back to a sanitized line if the
    // throw carried no message.
    toast(exc.message || 'Could not send the wake packet to ' + displayName + '.', 'error');
  }
}

// --------------------------------------------------------- wiring
function onMachinesListClick(ev) {
  const btn = ev.target.closest('button[data-action]');
  if (!btn || btn.disabled) return;
  const card = btn.closest('.machine-card');
  const id = card && card.dataset ? card.dataset.machineId : '';
  const machine = findMachine(id);
  if (!machine) return;
  const displayName = machine.display_name || id;
  const action = btn.dataset.action;
  if (action === 'diagnostics') {
    openDiagnostics();
  } else if (action === 'terminal') {
    openMachinesTerminal(id, displayName);
  } else if (action === 'rdp') {
    downloadRdp(id, displayName);
  } else if (action === 'wake') {
    wakeMachine(id, displayName);
  } else if (action === 'reboot' || action === 'shutdown') {
    openConfirm(id, displayName, action);
  }
}

export function wireMachines() {
  if (els.machinesRetryBtn) {
    els.machinesRetryBtn.addEventListener('click', function () {
      state.machinesDataState = 'loading';
      renderMachinesList();
      fetchMachinesStatus();
    });
  }
  if (els.machinesList) els.machinesList.addEventListener('click', onMachinesListClick);
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
