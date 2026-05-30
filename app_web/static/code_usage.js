/* Claude Code usage tab (issues #20, #50).
 *
 * Polls /admin/api/code/usage/summary?period=<period> every 30 s while
 * the tab is visible.  Data comes from ~/.claude/projects/**\/*.jsonl —
 * no subprocess, no proxy.
 *
 * Reuses: jsonApi from api.js, els + state from state.js,
 *         .card, .counters, .card-list.dense, .badge, .empty from styles.css
 *
 * Charts (issue #50): requires Chart.js 4.x loaded globally via CDN before
 * this module executes (see the <script> tag just before main.js in index.html).
 */

import { els, state } from './state.js';
import { jsonApi } from './api.js';

// ---------------------------------------------------------------------------
// Chart constants (issue #50)
// ---------------------------------------------------------------------------

// Fixed order and colours for model families. Colours match CSS variables
// (--accent, --good, --warn, --muted) hardcoded here because Chart.js canvas
// cannot read CSS custom properties.
const MODEL_PALETTE = [
  { key: 'Haiku',  bg: 'rgba(74,138,243,0.50)',  border: 'rgba(74,138,243,0.85)'  },
  { key: 'Sonnet', bg: 'rgba(76,175,80,0.50)',   border: 'rgba(76,175,80,0.85)'   },
  { key: 'Opus',   bg: 'rgba(240,161,0,0.50)',   border: 'rgba(240,161,0,0.85)'   },
  { key: 'Other',  bg: 'rgba(154,154,154,0.30)', border: 'rgba(154,154,154,0.65)' },
];
const KNOWN_FAMILIES = new Set(['Haiku', 'Sonnet', 'Opus']);

// Retained Chart.js instances — destroyed before each recreation to prevent leaks.
let _chartInput  = null;
let _chartOutput = null;
let _chartReqs   = null;

const POLL_MS = 30_000;
let _pollHandle = null;

// ---------------------------------------------------------------------------
// Public lifecycle — called from main.js
// ---------------------------------------------------------------------------

export function wireCodeUsage() {
  // Period toggle (Day / Week / Month / All)
  if (els.cldPeriodSeg) {
    els.cldPeriodSeg.addEventListener('click', function (e) {
      const btn = e.target.closest('button[data-period]');
      if (!btn) return;
      const next = btn.dataset.period;
      if (next === state.cldPeriod) return;     // nothing to do
      state.cldPeriod = next;
      els.cldPeriodSeg.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('active', b === btn);
      });
      // Immediate re-fetch so the switch feels instant.
      fetchSummary().catch(function () {});
    });
  }
}

export function startCodeUsagePolls() {
  fetchSummary().catch(function () {});
  _pollHandle = setInterval(function () {
    fetchSummary().catch(function () {});
  }, POLL_MS);
}

export function stopCodeUsagePolls() {
  if (_pollHandle !== null) {
    clearInterval(_pollHandle);
    _pollHandle = null;
  }
}

// ---------------------------------------------------------------------------
// Data fetch
// ---------------------------------------------------------------------------

async function fetchSummary() {
  try {
    const body = await jsonApi('/admin/api/code/usage/summary?period=' + state.cldPeriod);
    state.cldSummary = body;
    render(body);
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    setFreshness('error fetching data');
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render(body) {
  if (!body) return;
  renderCounters(body);
  renderDeltas(body);
  renderCharts(body);
  renderModelTable(body.by_model || []);
  renderProjectTable(body.by_project || []);
  renderSessions(body.recent_sessions || []);
  setFreshness('updated ' + new Date().toLocaleTimeString());
}

function renderCounters(body) {
  if (!body) return;
  const bucket = body.totals || {};
  set(els.cldRequests, fmtNum(bucket.requests));
  set(els.cldInputTok, fmtTok(
    (bucket.input_tokens || 0) + (bucket.cache_creation_tokens || 0)
  ));
  set(els.cldOutputTok, fmtTok(bucket.output_tokens));
  set(els.cldCacheRead, fmtTok(bucket.cache_read_tokens));
}

function renderModelTable(rows) {
  const tbody = els.cldModelTable && els.cldModelTable.querySelector('tbody');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '';
    if (els.cldModelEmpty) els.cldModelEmpty.hidden = false;
    return;
  }
  if (els.cldModelEmpty) els.cldModelEmpty.hidden = true;
  tbody.innerHTML = rows.map(function (r) {
    const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
    return '<tr>' +
      '<td>' + esc(r.model) + '</td>' +
      '<td>' + fmtNum(r.requests) + '</td>' +
      '<td>' + fmtTok(totalIn) + '</td>' +
      '<td>' + fmtTok(r.output_tokens) + '</td>' +
      '<td class="muted">' + fmtTok(r.cache_read_tokens) + '</td>' +
      '</tr>';
  }).join('');
}

function renderProjectTable(rows) {
  const tbody = els.cldProjectTable && els.cldProjectTable.querySelector('tbody');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '';
    if (els.cldProjectEmpty) els.cldProjectEmpty.hidden = false;
    return;
  }
  if (els.cldProjectEmpty) els.cldProjectEmpty.hidden = true;
  tbody.innerHTML = rows.map(function (r) {
    const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
    return '<tr>' +
      '<td>' + esc(r.project || r.project_key) + '</td>' +
      '<td>' + fmtNum(r.requests) + '</td>' +
      '<td>' + fmtTok(totalIn) + '</td>' +
      '<td>' + fmtTok(r.output_tokens) + '</td>' +
      '</tr>';
  }).join('');
}

function renderSessions(sessions) {
  if (!els.cldSessionsList) return;
  const badge = els.cldSessionsBadge;
  const empty = els.cldSessionsEmpty;

  if (badge) badge.textContent = sessions.length;
  if (!sessions.length) {
    els.cldSessionsList.innerHTML = '';
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;

  els.cldSessionsList.innerHTML = sessions.map(function (s) {
    const totalIn = (s.input_tokens || 0) + (s.cache_creation_tokens || 0);
    const firstTs = fmtTs(s.first_ts);
    const lastTs = fmtTs(s.last_ts);
    const proj = esc(s.project || s.project_key || '');
    const model = esc(modelShort(s.model || ''));
    const sid = esc((s.session_id || '').slice(0, 8));
    return '<li>' +
      '<span class="cld-sess-time">' + lastTs + '</span>' +
      '<span class="cld-sess-project">' + proj + '</span>' +
      '<span class="cld-sess-model">' + model + '</span>' +
      '<div class="cld-sess-meta">' +
        '<code>' + sid + '</code>' +
        ' · ' + firstTs + ' → ' + lastTs +
        ' · ' + fmtNum(s.requests) + ' req' +
        ' · ' + fmtTok(totalIn) + '↑ ' + fmtTok(s.output_tokens) + '↓' +
      '</div>' +
      '</li>';
  }).join('');
}

// ---------------------------------------------------------------------------
// Delta badges (issue #50)
// ---------------------------------------------------------------------------

function renderDeltas(body) {
  const prev = body.prev_totals;        // absent when period === 'all'
  const curr = body.totals || {};
  const hide = state.cldPeriod === 'all' || !prev;

  function apply(el, c, p) {
    if (!el) return;
    if (hide || p === 0) { el.hidden = true; return; }
    el.hidden = false;
    const pct = Math.round((c - p) / p * 100);
    el.className = pct > 0 ? 'cld-delta up' : pct < 0 ? 'cld-delta down' : 'cld-delta';
    el.textContent = pct > 0 ? '+' + pct + '% ↑' : pct < 0 ? pct + '% ↓' : '±0%';
  }

  const prevIn  = prev ? (prev.input_tokens || 0) + (prev.cache_creation_tokens || 0) : 0;
  const currIn  = (curr.input_tokens || 0) + (curr.cache_creation_tokens || 0);
  const prevOut = prev ? (prev.output_tokens || 0) : 0;
  const prevReq = prev ? (prev.requests || 0) : 0;

  apply(els.cldDeltaRequests,  curr.requests || 0,      prevReq);
  apply(els.cldDeltaInputTok,  currIn,                   prevIn);
  apply(els.cldDeltaOutputTok, curr.output_tokens || 0,  prevOut);
}

// ---------------------------------------------------------------------------
// Trend charts (issue #50)
// ---------------------------------------------------------------------------

function renderCharts(body) {
  const card = els.cldChartsCard;
  if (!card) return;

  const ts = body.time_series;
  if (!ts || !ts.length || state.cldPeriod === 'all') {
    card.hidden = true;
    _destroyCharts();
    return;
  }

  card.hidden = false;
  const norm = _normalizeTs(ts);
  const labels = norm.map(function (b) { return b.label; });

  _chartInput  = _makeChart(els.cldChartInput,  _chartInput,  labels, norm, 'input_tokens',  true);
  _chartOutput = _makeChart(els.cldChartOutput, _chartOutput, labels, norm, 'output_tokens', true);
  _chartReqs   = _makeChart(els.cldChartReqs,   _chartReqs,   labels, norm, 'requests',      false);
}

function _normalizeTs(ts) {
  return ts.map(function (b) {
    var models = {};
    Object.entries(b.models).forEach(function (_ref) {
      var k = _ref[0], v = _ref[1];
      var key = KNOWN_FAMILIES.has(k) ? k : 'Other';
      if (!models[key]) models[key] = { input_tokens: 0, output_tokens: 0, requests: 0 };
      models[key].input_tokens  += v.input_tokens  || 0;
      models[key].output_tokens += v.output_tokens || 0;
      models[key].requests      += v.requests      || 0;
    });
    return { label: b.label, models: models };
  });
}

function _destroyCharts() {
  if (_chartInput)  { _chartInput.destroy();  _chartInput  = null; }
  if (_chartOutput) { _chartOutput.destroy(); _chartOutput = null; }
  if (_chartReqs)   { _chartReqs.destroy();   _chartReqs   = null; }
}

function _makeChart(canvas, existing, labels, ts, field, isTok) {
  if (!canvas) return existing;

  var datasets = [];
  MODEL_PALETTE.forEach(function (p) {
    var values = ts.map(function (b) { return (b.models[p.key] || {})[field] || 0; });
    if (values.every(function (v) { return v === 0; })) return;
    datasets.push({
      label: p.key,
      data: values,
      fill: true,
      backgroundColor: p.bg,
      borderColor: p.border,
      borderWidth: 1,
      tension: 0.2,
      pointRadius: ts.length > 10 ? 0 : 3,
      pointHoverRadius: 4,
    });
  });

  if (existing) { existing.destroy(); }

  /* global Chart */
  return new Chart(canvas, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: _chartOptions(isTok),
  });
}

function _chartOptions(isTok) {
  var gridColor  = 'rgba(42,47,66,0.8)';  // --border
  var tickColor  = '#9a9a9a';              // --muted
  var fgColor    = '#f3f3f3';              // --fg
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: {
        stacked: true,
        grid: { color: gridColor },
        ticks: { color: tickColor, font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
      },
      y: {
        stacked: true,
        beginAtZero: true,
        grid: { color: gridColor },
        ticks: {
          color: tickColor,
          font: { size: 11 },
          callback: isTok ? function (v) { return fmtTok(v); } : undefined,
        },
      },
    },
    plugins: {
      legend: {
        position: 'bottom',
        labels: { color: fgColor, boxWidth: 12, padding: 12, font: { size: 11 } },
      },
      tooltip: {
        callbacks: isTok ? {
          label: function (ctx) {
            return ' ' + ctx.dataset.label + ': ' + fmtTok(ctx.parsed.y);
          },
        } : {},
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function set(el, text) {
  if (el) el.textContent = text;
}

function setFreshness(text) {
  if (els.cldFreshness) els.cldFreshness.textContent = text;
}

function fmtNum(n) {
  if (n === undefined || n === null) return '—';
  return Number(n).toLocaleString();
}

function fmtTok(n) {
  if (!n) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

function fmtTs(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return iso.slice(11, 16); }
}

function modelShort(m) {
  const lm = m.toLowerCase();
  if (lm.includes('opus')) return 'Opus';
  if (lm.includes('sonnet')) return 'Sonnet';
  if (lm.includes('haiku')) return 'Haiku';
  return m.length > 20 ? m.slice(0, 20) + '…' : m;
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
