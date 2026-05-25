/* Claude Code usage tab (issue #20).
 *
 * Polls /admin/api/code/usage/summary?period=<period> every 30 s while
 * the tab is visible.  Data comes from ~/.claude/projects/**\/*.jsonl —
 * no subprocess, no proxy.
 *
 * Reuses: jsonApi from api.js, els + state from state.js,
 *         .card, .counters, .card-list.dense, .badge, .empty from styles.css
 */

import { els, state } from './state.js';
import { jsonApi } from './api.js';

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
