/* Hub tab — status card, services, install panel, live request stream,
 * counters, errors, log tail. The four diagnostic surfaces are vendored
 * disclosure cards, folded by default (#215).
 */

import { els, state } from './state.js';
import { jsonApi, postJson, eventStream, toast, escapeHtml, fmtClock, fmtSecs, tokPair } from './api.js';
import { langfuseTraceUrl, fetchTelemetryHealth } from './telemetry.js';
import { icon } from './_vendored/icons/icons.js';

// --------------------------------------------------------- status / urls
export async function fetchHubStatus() {
  try {
    const body = await jsonApi('/admin/api/hub/status');
    state.status = body;
    if (els.hubPid) els.hubPid.textContent = body.pid || '—';
    if (els.hubUptime) els.hubUptime.textContent = fmtUptime(body.uptime_s);
    setHubLive('good', 'up');
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    setHubLive('danger', 'unreachable');
  }
}

function setHubLive(kind, text) {
  if (!els.hubLiveStatus) return;
  els.hubLiveStatus.classList.remove('good', 'warn', 'danger');
  if (kind) els.hubLiveStatus.classList.add(kind);
  if (els.hubLiveStatusText) els.hubLiveStatusText.textContent = text;
}

function fmtUptime(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  if (seconds < 60) return Math.floor(seconds) + 's';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.floor(seconds % 60) + 's';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h + 'h ' + m + 'm';
}

// --------------------------------------------------------- counters
export async function fetchCounters() {
  try {
    const body = await jsonApi('/admin/api/hub/counters');
    state.counters = body.counters || [];
    renderCounters();
  } catch (_) { /* ignore */ }
}

function renderCounters() {
  const rows = state.counters || [];
  const tbl = els.countersTable;
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="6" class="muted small">No requests yet.</td>';
    tbody.appendChild(tr);
    return;
  }
  rows.forEach(function (r) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td class="td-trunc" title="' + escapeHtml(r.key) + '">' + escapeHtml(r.key) + '</td>' +
      '<td>' + r.requests + '</td>' +
      '<td>' + r.errors + '</td>' +
      '<td>' + fmtSecs(r.p50_ms) + '</td>' +
      '<td>' + fmtSecs(r.p95_ms) + '</td>' +
      '<td>' + tokPair(r.in_tok, r.out_tok) + '</td>';
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------- live requests
function prependRequest(rec) {
  // Dedup by ts. The SSE seed (20 most-recent records) is re-sent every
  // time the EventSource (re)connects — and the browser auto-reconnects
  // after network blips, a tab-switch round-trip, or any uvicorn keepalive
  // drop. Without dedup each reconnect duplicates whatever's still in the
  // server-side ring. `ts` is the middleware's wall-clock float seconds,
  // unique per request because the recording `finally` only fires once.
  const isDup = function (arr) {
    return rec && rec.ts != null && arr.some(function (r) { return r.ts === rec.ts; });
  };
  if (!isDup(state.liveRequests)) {
    state.liveRequests = [rec].concat(state.liveRequests).slice(0, 50);
    renderRequests();
  }
  if (rec.status >= 400 && !isDup(state.recentErrors)) {
    state.recentErrors = [rec].concat(state.recentErrors).slice(0, 50);
    renderErrors();
  }
}

// A trace deep-link should only render when clicking it would actually
// reach a Langfuse trace: the hub itself up, Docker up, and Langfuse
// reachable. Otherwise the link would land on a connection error — so we
// hide it (the row still shows tokens, just no `trace` affordance). Same
// signals the Services card uses (issue #27).
function traceLinkReady() {
  const svc = state.services || {};
  const docker = svc.docker || {};
  const lf = svc.langfuse || {};
  return !!state.status && docker.running === true && lf.reachable === true;
}

function renderRequests() {
  const items = state.liveRequests || [];
  const traceUp = traceLinkReady();
  const list = els.liveRequestsList;
  if (!list) return;
  if (els.liveRequestsBadge) els.liveRequestsBadge.textContent = items.length;
  if (els.liveRequestsEmpty) els.liveRequestsEmpty.hidden = items.length > 0;
  list.innerHTML = '';
  items.forEach(function (r) {
    const li = document.createElement('li');
    const cls = r.status >= 500 ? 'err' : r.status >= 400 ? 'warn' : 'ok';
    // Identical deep-link to the Telemetry tab: shared langfuseTraceUrl()
    // derives the client-reachable Langfuse host (Tailscale/LAN/localhost
    // transparent) + project_id, opened in a new tab. Only shown when the
    // stack is up (see traceLinkReady).
    const traceCol = (r.trace_id && traceUp)
      ? ('<a href="' + langfuseTraceUrl(r.trace_id) + '" target="_blank" rel="noopener" title="' + escapeHtml(r.trace_id) + '">trace</a>')
      : '';
    li.innerHTML =
      '<span class="muted">' + fmtClock(r.ts) + '</span>' +
      '<span>' + escapeHtml(r.model || '(no model)') + ' <span class="muted">' + escapeHtml(r.backend || '') + '</span></span>' +
      '<span class="req-status ' + cls + '">' + r.status + ' · ' + r.latency_ms + ' ms</span>' +
      '<span class="muted">' + (r.in_tok || 0) + ' / ' + (r.out_tok || 0) + ' tok ' + traceCol + '</span>';
    list.appendChild(li);
  });
}

function renderErrors() {
  const items = state.recentErrors || [];
  const list = els.recentErrorsList;
  if (!list) return;
  if (els.recentErrorsBadge) els.recentErrorsBadge.textContent = items.length;
  if (els.recentErrorsEmpty) els.recentErrorsEmpty.hidden = items.length > 0;
  list.innerHTML = '';
  items.forEach(function (r) {
    const li = document.createElement('li');
    li.innerHTML =
      '<span class="muted">' + fmtClock(r.ts) + '</span>' +
      '<span>' + escapeHtml(r.model || '(no model)') + ' <span class="muted">' + escapeHtml(r.backend || '') + '</span></span>' +
      '<span class="req-status err">' + r.status + '</span>' +
      '<span class="muted">' + escapeHtml((r.error_detail || '').slice(0, 80)) + '</span>';
    list.appendChild(li);
  });
}

// --------------------------------------------------------- log tail
let logBuf = [];

function appendLogLine(line) {
  if (state.logPaused) return;
  logBuf.push(line);
  if (logBuf.length > 800) logBuf = logBuf.slice(-800);
  const pre = els.hubLog;
  if (!pre) return;
  pre.textContent = logBuf.join('\n');
  pre.scrollTop = pre.scrollHeight;
}

// --------------------------------------------------------- streams
export function startHubStreams() {
  stopHubStreams();
  state.hubStreamCtl = eventStream('/admin/api/hub/requests/stream', {
    message: function (data) {
      if (!data || typeof data !== 'object') return;
      prependRequest(data);
    },
  });
  state.hubLogStreamCtl = eventStream('/admin/api/hub/log/tail', {
    message: function (data) {
      if (typeof data === 'string') appendLogLine(data);
    },
  });
}

export function stopHubStreams() {
  if (state.hubStreamCtl) { try { state.hubStreamCtl.close(); } catch (_) {} state.hubStreamCtl = null; }
  if (state.hubLogStreamCtl) { try { state.hubLogStreamCtl.close(); } catch (_) {} state.hubLogStreamCtl = null; }
}

// --------------------------------------------------------- install panel
export async function fetchInstallStatus() {
  try {
    const body = await jsonApi('/admin/api/install/status');
    state.installRows = body.checks || [];
    renderInstall(body);
  } catch (_) { /* ignore */ }
}

function renderInstall(body) {
  if (!els.installRows || !els.installSummary) return;
  const checks = body.checks || [];
  const overall = body.worst_status || 'ok';
  els.installSummary.textContent = checks.length + ' checks · overall ' + overall;
  els.installSummary.className = 'collapse-count overall-' + overall;
  els.installRows.innerHTML = '';
  checks.forEach(function (c) {
    const row = document.createElement('div');
    row.className = 'install-row install-' + c.status;
    const glyph = c.status === 'ok' ? icon('circle-check')
      : c.status === 'warn' ? icon('triangle-alert')
      : c.status === 'missing' ? icon('circle-help')
      : icon('circle-x');
    row.innerHTML =
      '<span class="install-glyph">' + glyph + '</span>' +
      '<span class="install-label">' + escapeHtml(c.label) + '</span>' +
      '<span class="install-detail muted small">' + escapeHtml(c.detail || '') + '</span>';
    if (c.fix_id && (c.status === 'missing' || c.status === 'error')) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ghost-btn';
      btn.innerHTML = icon('wrench') + escapeHtml(c.fix_label || 'Fix');
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        btn.textContent = 'Running…';
        try {
          await postJson('/admin/api/install/fix', { fix_id: c.fix_id });
          toast('Fixed ' + c.id, 'good');
          await fetchInstallStatus();
        } catch (exc) {
          toast(String(exc.message || exc), 'error');
          btn.disabled = false;
          btn.innerHTML = icon('wrench') + 'Retry';
        }
      });
      row.appendChild(btn);
    }
    els.installRows.appendChild(row);
  });
}

// --------------------------------------------------------- services card (issue #27)
export async function fetchServicesStatus() {
  try {
    const body = await jsonApi('/admin/api/services/status');
    state.services = body;
    renderServices();
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    // Probe error itself — render an "unreachable" state. `probeFailed`
    // tells renderServices() this isn't a real launchable=false verdict
    // (we never got far enough to check the install path), so it must
    // not show the "Docker Desktop install not found" hint — that would
    // be a fabricated claim about install state from a fetch failure.
    state.services = { docker: { running: false, error: String(exc.message || exc) },
                       langfuse: { reachable: false, error: '' },
                       launchable: false, platform: '', probeFailed: true };
    renderServices();
  }
}

function setStatusPill(rootEl, textEl, kind, text) {
  if (!rootEl) return;
  rootEl.classList.remove('good', 'warn', 'danger');
  if (kind) rootEl.classList.add(kind);
  if (textEl) textEl.textContent = text;
}

function renderServices() {
  const body = state.services;
  if (!body) return;
  const docker = body.docker || {};
  const lf = body.langfuse || {};

  const dockerKind = docker.running ? 'good' : 'danger';
  const dockerLabel = docker.running ? 'up' : 'down';
  setStatusPill(els.dockerStatus, els.dockerStatusText, dockerKind, dockerLabel);
  if (els.dockerDetail) {
    els.dockerDetail.textContent = docker.running
      ? (docker.server_version ? 'engine ' + docker.server_version : '')
      : (docker.error || '');
  }

  // Langfuse: "up" when reachable, "partial" when Docker is up but Langfuse
  // isn't (containers starting / never launched), "down" otherwise.
  let lfKind = 'danger';
  let lfLabel = 'down';
  if (lf.reachable) { lfKind = 'good'; lfLabel = 'up'; }
  else if (docker.running) { lfKind = 'warn'; lfLabel = 'down'; }
  setStatusPill(els.langfuseStatus, els.langfuseStatusText, lfKind, lfLabel);
  if (els.langfuseDetail) {
    els.langfuseDetail.textContent = lf.reachable ? '' : (lf.error || '');
  }

  // AgentsView (#280): optional external indexer feeding the Code tab's
  // AGY vendor. "not installed" is a warn, not a danger — the hub is fully
  // functional without it.
  const av = body.agentsview || {};
  const avEnabled = !!av.host;
  let avKind = 'danger';
  let avLabel = 'down';
  if (av.reachable) { avKind = 'good'; avLabel = 'up'; }
  else if (!avEnabled) { avKind = 'warn'; avLabel = 'disabled'; }
  else if (av.installed === false) { avKind = 'warn'; avLabel = 'not installed'; }
  setStatusPill(els.agentsviewStatus, els.agentsviewStatusText, avKind, avLabel);
  if (els.agentsviewDetail) {
    els.agentsviewDetail.textContent = av.reachable
      ? (av.version ? av.version : '')
      : (av.installed === false ? 'see docs/code-usage-agentsview.md' : (av.error || ''));
  }
  if (els.agentsviewStartBtn) {
    els.agentsviewStartBtn.hidden = !(avEnabled && !av.reachable && av.installed);
    els.agentsviewStartBtn.disabled = state.agentsviewStarting;
    els.agentsviewStartBtn.innerHTML = state.agentsviewStarting
      ? 'Starting…'
      : icon('play') + 'Start';
  }

  // Mac Mini (#179): the pill itself doesn't factor into the overall
  // status/launch-button logic above — it tells the Mac's own story.
  const macMini = body.mac_mini;
  if (els.macMiniRow) els.macMiniRow.hidden = !macMini;
  if (macMini) {
    const mmKind = macMini.reachable ? 'good' : 'danger';
    const mmLabel = macMini.reachable ? 'up' : 'down';
    setStatusPill(els.macMiniStatus, els.macMiniStatusText, mmKind, mmLabel);
    if (els.macMiniDetail) {
      // git_sha_match is null until both sides answer; only warn on an
      // explicit false, never on "haven't compared yet" (#181).
      const outOfSync = macMini.reachable && macMini.git_sha_match === false;
      els.macMiniDetail.innerHTML = !macMini.reachable
        ? escapeHtml(macMini.error || '')
        : outOfSync
          ? '<span class="badge warn">out of sync</span> ' +
            escapeHtml(macMini.remote_git_sha || '?') + ' vs ' + escapeHtml(macMini.local_git_sha || '?')
          : '';
    }
    // Wake/Sync (#181): mirrors the Docker/Langfuse launch-button pattern —
    // one action visible at a time depending on reachability.
    if (els.macMiniWakeBtn) {
      els.macMiniWakeBtn.hidden = macMini.reachable;
      els.macMiniWakeBtn.disabled = state.macMiniBusy;
      els.macMiniWakeBtn.innerHTML = state.macMiniBusy
        ? 'Waking…'
        : icon('play') + 'Wake';
    }
    if (els.macMiniSyncBtn) {
      els.macMiniSyncBtn.hidden = !macMini.reachable;
      els.macMiniSyncBtn.disabled = state.macMiniBusy;
      els.macMiniSyncBtn.innerHTML = state.macMiniBusy
        ? 'Syncing…'
        : icon('refresh-cw') + 'Sync';
    }
  }

  // Overall pill summarises both.
  let overallKind = 'good';
  let overallText = 'all up';
  if (!docker.running && !lf.reachable) { overallKind = 'danger'; overallText = 'both down'; }
  else if (!docker.running) { overallKind = 'danger'; overallText = 'docker down'; }
  else if (!lf.reachable) { overallKind = 'warn'; overallText = 'langfuse down'; }
  else if (avEnabled && !av.reachable) { overallKind = 'warn'; overallText = 'agentsview down'; }
  setStatusPill(els.servicesOverall, els.servicesOverallText, overallKind, overallText);

  // Launch button + hint visibility.
  const anyDown = !docker.running || !lf.reachable;
  const showActions = anyDown && body.launchable && !state.servicesLaunching;
  if (els.servicesActions) els.servicesActions.hidden = !(anyDown && body.launchable);
  if (els.servicesHint) {
    if (body.probeFailed) {
      // Status probe itself failed — we don't know the install state,
      // so surface the real error instead of guessing.
      els.servicesHint.textContent = 'Status check failed: ' + (docker.error || 'unknown error') + '.';
      els.servicesHint.hidden = false;
    } else if (anyDown && !body.launchable) {
      const hint = body.platform === 'darwin'
        ? 'Start Docker manually: `open -a Docker`, then `./start_langfuse.sh`.'
        : body.platform === 'linux'
          ? 'Start Docker manually: `sudo systemctl start docker`, then `./start_langfuse.sh`.'
          : 'Docker Desktop install not found — install from docker.com.';
      els.servicesHint.textContent = hint;
      els.servicesHint.hidden = false;
    } else {
      els.servicesHint.hidden = true;
    }
  }
  if (els.servicesLaunchBtn) {
    els.servicesLaunchBtn.disabled = !showActions;
    els.servicesLaunchBtn.innerHTML = state.servicesLaunching
      ? 'Launching… (up to ~90s)'
      : icon('rocket') + 'Launch Docker + Langfuse';
  }
}

async function onServicesLaunchClick() {
  if (state.servicesLaunching) return;
  state.servicesLaunching = true;
  renderServices();
  try {
    const result = await postJson('/admin/api/services/launch', {});
    const steps = (result && result.steps) || [];
    const summary = steps.map(function (s) { return s.name + ':' + s.status; }).join(' · ');
    if (result.ok) {
      toast('Services launched · ' + summary, 'good');
    } else {
      const first = steps.find(function (s) { return s.status === 'error'; });
      const detail = first ? (first.name + ': ' + first.detail) : summary;
      toast('Launch failed — ' + detail, 'error');
    }
  } catch (exc) {
    toast('Launch failed: ' + (exc.message || exc), 'error');
  } finally {
    state.servicesLaunching = false;
    await fetchServicesStatus();
  }
}

async function onMacMiniAction(action, pastTense) {
  if (state.macMiniBusy) return;
  state.macMiniBusy = true;
  renderServices();
  try {
    // Sourced from the last /admin/api/services/status response, not
    // hardcoded here — the backend's src/host_profile.py owns the id
    // (issue #245: was duplicated as a JS literal with no shared source
    // of truth). Only reachable once state.services.mac_mini is truthy
    // (renderServices() keeps the wake/sync buttons hidden until then),
    // so mac_mini_host_id is always populated by the time this fires.
    const hostId = (state.services && state.services.mac_mini_host_id) || '';
    await postJson('/admin/api/hosts/' + hostId + '/' + action, {});
    toast('Mac Mini ' + pastTense, 'good');
  } catch (exc) {
    toast('Mac Mini ' + action + ' failed: ' + (exc.message || exc), 'error');
  } finally {
    state.macMiniBusy = false;
    await fetchServicesStatus();
  }
}

function onMacMiniWakeClick() { return onMacMiniAction('bootstrap', 'woken up'); }
function onMacMiniSyncClick() { return onMacMiniAction('sync', 'synced'); }

async function onAgentsviewStartClick() {
  if (state.agentsviewStarting) return;
  state.agentsviewStarting = true;
  renderServices();
  try {
    const result = await postJson('/admin/api/services/agentsview/launch', {});
    if (result.ok) {
      toast('AgentsView started', 'good');
    } else {
      const first = (result.steps || []).find(function (s) { return s.status === 'error'; });
      toast('AgentsView start failed — ' + (first ? first.detail : 'unknown'), 'error');
    }
  } catch (exc) {
    toast('AgentsView start failed: ' + (exc.message || exc), 'error');
  } finally {
    state.agentsviewStarting = false;
    await fetchServicesStatus();
  }
}

// --------------------------------------------------------- wire buttons
export function wireHub() {
  if (els.hubRestartBtn) {
    els.hubRestartBtn.addEventListener('click', async function () {
      try {
        await postJson('/admin/api/hub/restart', {});
        toast('Restarting hub… reload in 3-5s.', 'good');
        setTimeout(function () { window.location.reload(); }, 4500);
      } catch (exc) {
        toast('Restart failed: ' + (exc.message || exc), 'error');
      }
    });
  }

  function togglePause() {
    state.logPaused = !state.logPaused;
    if (els.hubLogPauseBtn) {
      els.hubLogPauseBtn.innerHTML = state.logPaused
        ? icon('play') + 'Resume'
        : icon('pause') + 'Pause';
    }
  }
  if (els.hubLogPauseBtn) els.hubLogPauseBtn.addEventListener('click', togglePause);

  if (els.installFixAllBtn) {
    els.installFixAllBtn.addEventListener('click', async function () {
      els.installFixAllBtn.disabled = true;
      const original = els.installFixAllBtn.textContent;
      els.installFixAllBtn.textContent = 'Running…';
      try {
        await postJson('/admin/api/install/fix-all', {});
        toast('Fix-all complete.', 'good');
        await fetchInstallStatus();
      } catch (exc) {
        toast(String(exc.message || exc), 'error');
      } finally {
        els.installFixAllBtn.disabled = false;
        els.installFixAllBtn.textContent = original;
      }
    });
  }
  if (els.installRefreshBtn) {
    els.installRefreshBtn.addEventListener('click', function () { fetchInstallStatus(); });
  }

  if (els.servicesLaunchBtn) {
    els.servicesLaunchBtn.addEventListener('click', onServicesLaunchClick);
  }
  if (els.macMiniWakeBtn) {
    els.macMiniWakeBtn.addEventListener('click', onMacMiniWakeClick);
  }
  if (els.macMiniSyncBtn) {
    els.macMiniSyncBtn.addEventListener('click', onMacMiniSyncClick);
  }
  if (els.agentsviewStartBtn) {
    els.agentsviewStartBtn.addEventListener('click', onAgentsviewStartClick);
  }

  // Sparklines: lightweight inline-SVG renderer driven by /admin/api/hub/stats.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    renderSparklines();
  }, 2500);

  // Services card — Docker + Langfuse status. Cheaper than the sparkline
  // sweep (two small probes, each capped at 2 s) so 5 s is plenty.
  // Also refresh telemetry health here so the live-request trace links can
  // resolve Langfuse's project_id without the user first visiting the
  // Telemetry tab — the deep-link URL is then byte-identical across tabs.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    if (state.servicesLaunching) return;
    fetchServicesStatus().catch(function () {});
    fetchTelemetryHealth().catch(function () {});
  }, 5000);
}

async function renderSparklines() {
  let stats;
  try {
    stats = await jsonApi('/admin/api/hub/stats');
  } catch (_) { return; }
  const container = els.hubSparklines;
  if (!container) return;
  container.innerHTML = '';
  const history = stats.history || [];
  const groups = [
    { label: 'RAM', value: stats.ram && stats.ram.percent, series: history.map(function (h) { return h.ram_percent; }) },
  ];
  if (stats.gpus && stats.gpus.length) {
    const g0 = stats.gpus[0];
    groups.push({ label: 'VRAM ' + shortGpu(g0.name), value: g0.vram_percent, series: history.map(function (h) { return h.gpu0_vram_percent; }) });
    groups.push({ label: 'GPU util', value: g0.util_percent, series: history.map(function (h) { return h.gpu0_util_percent; }) });
  }
  groups.forEach(function (g) {
    container.appendChild(buildSparkline(g));
  });
}

function buildSparkline(g) {
  const root = document.createElement('div');
  root.className = 'sparkline';
  const series = (g.series || []).filter(function (v) { return v !== null && v !== undefined && !isNaN(v); });
  const max = 100;
  const w = 140, h = 64;
  let path = '';
  if (series.length >= 2) {
    const step = w / (series.length - 1);
    series.forEach(function (v, i) {
      const x = i * step;
      const y = h - (v / max) * h;
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    });
  }
  // Energy-tab chart look (#215): a 2px accent line over a translucent
  // accent area (fill closed down to the baseline). Both pull from the live
  // --accent token so the tiles re-theme for free.
  const area = path ? path + 'L' + w + ',' + h + ' L0,' + h + ' Z' : '';
  root.innerHTML =
    '<div class="sparkline-label"><span>' + escapeHtml(g.label) + '</span>' +
    '<span>' + (Number.isFinite(g.value) ? Math.round(g.value) + '%' : '—') + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
    (area ? '<path d="' + area + '" fill="color-mix(in srgb, var(--accent) 18%, transparent)" stroke="none"/>' : '') +
    (path ? '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>' : '') +
    '</svg>';
  return root;
}

function shortGpu(name) {
  if (!name) return '';
  return name.replace('NVIDIA ', '').replace('GeForce ', '').trim();
}

/* escapeHtml / fmtClock live in api.js (sibling dedup, #211). */
