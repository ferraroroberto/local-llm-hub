/* Hub tab — start/stop/restart, live request stream, log tail, install panel. */

import { els, state } from './state.js';
import { api, jsonApi, postJson, eventStream, toast, fmtAge } from './api.js';

// --------------------------------------------------------- status / urls
export async function fetchHubStatus() {
  try {
    const body = await jsonApi('/admin/api/hub/status');
    state.status = body;
    els.hubStatusDot.classList.remove('warn', 'err');
    els.hubStatusDot.classList.add('ok');
    els.hubStatusText.textContent = 'running · uptime ' + fmtUptime(body.uptime_s);
    if (els.hubLocalUrl) {
      els.hubLocalUrl.textContent = body.local_url || '—';
      els.hubLocalUrl.href = body.local_url || '#';
    }
    if (els.hubLanUrl) {
      els.hubLanUrl.textContent = body.lan_url || 'no LAN route';
      els.hubLanUrl.href = body.lan_url || '#';
    }
    if (els.hubPid) els.hubPid.textContent = body.pid || '—';
    if (els.hubUptime) els.hubUptime.textContent = fmtUptime(body.uptime_s);
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    els.hubStatusDot.classList.remove('ok');
    els.hubStatusDot.classList.add('err');
    els.hubStatusText.textContent = 'unreachable';
  }
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
  const tbody = els.countersTable && els.countersTable.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  const rows = state.counters || [];
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
}

// --------------------------------------------------------- live requests
function prependRequest(rec) {
  state.liveRequests = [rec].concat(state.liveRequests).slice(0, 50);
  renderRequests();
  if (rec.status >= 400) {
    state.recentErrors = [rec].concat(state.recentErrors).slice(0, 50);
    renderErrors();
  }
}

function renderRequests() {
  const list = els.liveRequestsList;
  if (!list) return;
  const items = state.liveRequests || [];
  els.liveRequestsBadge.textContent = items.length;
  els.liveRequestsEmpty.hidden = items.length > 0;
  list.innerHTML = '';
  items.forEach(function (r) {
    const li = document.createElement('li');
    const cls = r.status >= 500 ? 'err' : r.status >= 400 ? 'warn' : 'ok';
    const traceCol = r.trace_id ? ('<a href="#trace/' + r.trace_id + '" title="' + r.trace_id + '">trace</a>') : '';
    li.innerHTML =
      '<span class="muted">' + fmtClock(r.ts) + '</span>' +
      '<span>' + escapeHtml(r.model || '(no model)') + ' <span class="muted">' + escapeHtml(r.backend || '') + '</span></span>' +
      '<span class="req-status ' + cls + '">' + r.status + ' · ' + r.latency_ms + ' ms</span>' +
      '<span class="muted">' + (r.in_tok || 0) + ' / ' + (r.out_tok || 0) + ' tok ' + traceCol + '</span>';
    list.appendChild(li);
  });
}

function renderErrors() {
  const list = els.recentErrorsList;
  if (!list) return;
  const items = state.recentErrors || [];
  els.recentErrorsBadge.textContent = items.length;
  els.recentErrorsEmpty.hidden = items.length > 0;
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
  if (els.hubLog) {
    els.hubLog.textContent = logBuf.join('\n');
    els.hubLog.scrollTop = els.hubLog.scrollHeight;
  }
}

// --------------------------------------------------------- streams
export function startHubStreams() {
  stopHubStreams();
  // Request SSE
  state.hubStreamCtl = eventStream('/admin/api/hub/requests/stream', {
    message: function (data) {
      if (!data || typeof data !== 'object') return;
      prependRequest(data);
    },
  });
  // Log SSE
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
      btn.className = 'btn small';
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

// --------------------------------------------------------- wire buttons
export function wireHub() {
  if (els.hubStopBtn) {
    els.hubStopBtn.addEventListener('click', async function () {
      if (!window.confirm('Stop the hub? The admin page will go offline. Use the tray to restart.')) return;
      try {
        await postJson('/admin/api/hub/stop', {});
        toast('Stopping hub…', 'good');
      } catch (exc) {
        toast('Stop failed: ' + (exc.message || exc), 'error');
      }
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
  if (els.hubLogPauseBtn) {
    els.hubLogPauseBtn.addEventListener('click', function () {
      state.logPaused = !state.logPaused;
      els.hubLogPauseBtn.textContent = state.logPaused ? '▶ Resume' : '⏸ Pause';
    });
  }
  if (els.installFixAllBtn) {
    els.installFixAllBtn.addEventListener('click', async function () {
      els.installFixAllBtn.disabled = true;
      els.installFixAllBtn.textContent = 'Running…';
      try {
        await postJson('/admin/api/install/fix-all', {});
        toast('Fix-all complete.', 'good');
        await fetchInstallStatus();
      } catch (exc) {
        toast(String(exc.message || exc), 'error');
      } finally {
        els.installFixAllBtn.disabled = false;
        els.installFixAllBtn.textContent = '🔧 Fix all';
      }
    });
  }
  if (els.installRefreshBtn) {
    els.installRefreshBtn.addEventListener('click', function () { fetchInstallStatus(); });
  }

  // Sparklines: lightweight inline-SVG renderer driven by /admin/api/hub/stats.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    renderSparklines();
  }, 2500);
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
  root.innerHTML =
    '<div class="sparkline-label"><span>' + escapeHtml(g.label) + '</span>' +
    '<span>' + (Number.isFinite(g.value) ? Math.round(g.value) + '%' : '—') + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
    (path ? '<path d="' + path + '" fill="none" stroke="#d97757" stroke-width="1.5"/>' : '') +
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
