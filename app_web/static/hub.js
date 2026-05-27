/* Hub tab — status strip, density toggle, live request stream, log tail,
 * install panel. Renders to both Compact and Expanded DOM trees; CSS
 * controls which is visible.
 */

import { els, state, DENSITY_KEY } from './state.js';
import { jsonApi, postJson, eventStream, toast } from './api.js';

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
  [els.countersTable, els.countersTableExp].forEach(function (tbl) {
    if (!tbl) return;
    const tbody = tbl.querySelector('tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!rows.length) {
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="7" class="muted small">No requests yet.</td>';
      tbody.appendChild(tr);
      return;
    }
    rows.forEach(function (r) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(r.key) + '</td>' +
        '<td>' + r.requests + '</td>' +
        '<td>' + r.errors + '</td>' +
        '<td>' + r.p50_ms + ' ms</td>' +
        '<td>' + r.p95_ms + ' ms</td>' +
        '<td>' + r.in_tok + '</td>' +
        '<td>' + r.out_tok + '</td>';
      tbody.appendChild(tr);
    });
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

function renderRequests() {
  const items = state.liveRequests || [];
  const lists = [
    { list: els.liveRequestsList, badge: els.liveRequestsBadge, empty: els.liveRequestsEmpty },
    { list: els.liveRequestsListExp, badge: els.liveRequestsBadgeExp, empty: els.liveRequestsEmptyExp },
  ];
  lists.forEach(function (g) {
    if (!g.list) return;
    if (g.badge) g.badge.textContent = items.length;
    if (g.empty) g.empty.hidden = items.length > 0;
    g.list.innerHTML = '';
    items.forEach(function (r) {
      const li = document.createElement('li');
      const cls = r.status >= 500 ? 'err' : r.status >= 400 ? 'warn' : 'ok';
      const traceCol = r.trace_id ? ('<a href="#trace/' + r.trace_id + '" title="' + r.trace_id + '">trace</a>') : '';
      li.innerHTML =
        '<span class="muted">' + fmtClock(r.ts) + '</span>' +
        '<span>' + escapeHtml(r.model || '(no model)') + ' <span class="muted">' + escapeHtml(r.backend || '') + '</span></span>' +
        '<span class="req-status ' + cls + '">' + r.status + ' · ' + r.latency_ms + ' ms</span>' +
        '<span class="muted">' + (r.in_tok || 0) + ' / ' + (r.out_tok || 0) + ' tok ' + traceCol + '</span>';
      g.list.appendChild(li);
    });
  });
}

function renderErrors() {
  const items = state.recentErrors || [];
  const lists = [
    { list: els.recentErrorsList, badge: els.recentErrorsBadge, empty: els.recentErrorsEmpty },
    { list: els.recentErrorsListExp, badge: els.recentErrorsBadgeExp, empty: els.recentErrorsEmptyExp },
  ];
  lists.forEach(function (g) {
    if (!g.list) return;
    if (g.badge) g.badge.textContent = items.length;
    if (g.empty) g.empty.hidden = items.length > 0;
    g.list.innerHTML = '';
    items.forEach(function (r) {
      const li = document.createElement('li');
      li.innerHTML =
        '<span class="muted">' + fmtClock(r.ts) + '</span>' +
        '<span>' + escapeHtml(r.model || '(no model)') + ' <span class="muted">' + escapeHtml(r.backend || '') + '</span></span>' +
        '<span class="req-status err">' + r.status + '</span>' +
        '<span class="muted">' + escapeHtml((r.error_detail || '').slice(0, 80)) + '</span>';
      g.list.appendChild(li);
    });
  });
}

// --------------------------------------------------------- log tail
let logBuf = [];

function appendLogLine(line) {
  if (state.logPaused) return;
  logBuf.push(line);
  if (logBuf.length > 800) logBuf = logBuf.slice(-800);
  const text = logBuf.join('\n');
  [els.hubLog, els.hubLogExp].forEach(function (pre) {
    if (!pre) return;
    pre.textContent = text;
    pre.scrollTop = pre.scrollHeight;
  });
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
  els.installSummary.className = 'muted small overall-' + overall;
  els.installRows.innerHTML = '';
  checks.forEach(function (c) {
    const row = document.createElement('div');
    row.className = 'install-row install-' + c.status;
    const glyph = c.status === 'ok' ? '✅' : c.status === 'warn' ? '⚠️' : c.status === 'missing' ? '❓' : '❌';
    row.innerHTML =
      '<span class="install-glyph">' + glyph + '</span>' +
      '<span class="install-label">' + escapeHtml(c.label) + '</span>' +
      '<span class="install-detail muted small">' + escapeHtml(c.detail || '') + '</span>';
    if (c.fix_id && (c.status === 'missing' || c.status === 'error')) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ghost-btn';
      btn.textContent = '🔧 ' + (c.fix_label || 'Fix');
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
          btn.textContent = '🔧 Retry';
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
    // Probe error itself — render an "unreachable" state.
    state.services = { docker: { running: false, error: String(exc.message || exc) },
                       langfuse: { reachable: false, error: '' },
                       launchable: false, platform: '' };
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

  // Overall pill summarises both.
  let overallKind = 'good';
  let overallText = 'all up';
  if (!docker.running && !lf.reachable) { overallKind = 'danger'; overallText = 'both down'; }
  else if (!docker.running) { overallKind = 'danger'; overallText = 'docker down'; }
  else if (!lf.reachable) { overallKind = 'warn'; overallText = 'langfuse down'; }
  setStatusPill(els.servicesOverall, els.servicesOverallText, overallKind, overallText);

  // Launch button + hint visibility.
  const anyDown = !docker.running || !lf.reachable;
  const showActions = anyDown && body.launchable && !state.servicesLaunching;
  if (els.servicesActions) els.servicesActions.hidden = !(anyDown && body.launchable);
  if (els.servicesHint) {
    if (anyDown && !body.launchable) {
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
    els.servicesLaunchBtn.textContent = state.servicesLaunching
      ? 'Launching… (up to ~90s)'
      : '🚀 Launch Docker + Langfuse';
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

// --------------------------------------------------------- density toggle
function applyDensity(density) {
  state.density = density;
  if (!els.app) return;
  els.app.classList.remove('density-compact', 'density-expanded');
  els.app.classList.add('density-' + density);
  if (els.hubDensity) {
    els.hubDensity.querySelectorAll('button').forEach(function (b) {
      b.classList.toggle('active', b.dataset.density === density);
    });
  }
  try { localStorage.setItem(DENSITY_KEY, density); } catch (_) {}
}

function loadDensity() {
  let d = 'compact';
  try { d = localStorage.getItem(DENSITY_KEY) || 'compact'; } catch (_) {}
  if (d !== 'expanded') d = 'compact';
  applyDensity(d);
}

function setCompactSection(section) {
  state.compactSection = section;
  if (!els.hubCompactTabs || !els.hubCompactCard) return;
  els.hubCompactTabs.querySelectorAll('button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.section === section);
  });
  els.hubCompactCard.querySelectorAll('.compact-section').forEach(function (s) {
    s.classList.toggle('active', s.dataset.section === section);
  });
}

// --------------------------------------------------------- wire buttons
export function wireHub() {
  loadDensity();
  setCompactSection('live');

  if (els.hubDensity) {
    els.hubDensity.addEventListener('click', function (ev) {
      const btn = ev.target.closest('button[data-density]');
      if (!btn) return;
      applyDensity(btn.dataset.density);
    });
  }
  if (els.hubCompactTabs) {
    els.hubCompactTabs.addEventListener('click', function (ev) {
      const btn = ev.target.closest('button[data-section]');
      if (!btn) return;
      setCompactSection(btn.dataset.section);
    });
  }

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

  // Pause buttons live in both Compact and Expanded modes — keep them in
  // sync so flipping density preserves the user's pause preference.
  function togglePause() {
    state.logPaused = !state.logPaused;
    const label = state.logPaused ? '▶ Resume' : '⏸ Pause';
    [els.hubLogPauseBtn, els.hubLogPauseBtnExp].forEach(function (b) {
      if (b) b.textContent = label;
    });
  }
  if (els.hubLogPauseBtn) els.hubLogPauseBtn.addEventListener('click', togglePause);
  if (els.hubLogPauseBtnExp) els.hubLogPauseBtnExp.addEventListener('click', togglePause);

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

  // Sparklines: lightweight inline-SVG renderer driven by /admin/api/hub/stats.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    renderSparklines();
  }, 2500);

  // Services card — Docker + Langfuse status. Cheaper than the sparkline
  // sweep (two small probes, each capped at 2 s) so 5 s is plenty.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    if (state.servicesLaunching) return;
    fetchServicesStatus().catch(function () {});
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
  const w = 140, h = 28;
  let path = '';
  if (series.length >= 2) {
    const step = w / (series.length - 1);
    series.forEach(function (v, i) {
      const x = i * step;
      const y = h - (v / max) * h;
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    });
  }
  // Stroke pulls from the live --accent token so the sparkline tracks
  // any future palette change without a hand-edit here.
  root.innerHTML =
    '<div class="sparkline-label"><span>' + escapeHtml(g.label) + '</span>' +
    '<span>' + (Number.isFinite(g.value) ? Math.round(g.value) + '%' : '—') + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
    (path ? '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="1.5"/>' : '') +
    '</svg>';
  return root;
}

function shortGpu(name) {
  if (!name) return '';
  return name.replace('NVIDIA ', '').replace('GeForce ', '').trim();
}

function fmtClock(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toTimeString().slice(0, 8);
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]);
  });
}
