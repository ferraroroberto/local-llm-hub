/* Models tab — per-backend tile with start/stop/health/log/ping. */

import { els, state } from './state.js';
import { jsonApi, postJson, eventStream, toast } from './api.js';

const logStreams = {}; // id -> EventSource

export async function fetchModels() {
  try {
    const body = await jsonApi('/admin/api/models');
    state.models = body.models || [];
    renderModels();
  } catch (_) { /* ignore */ }
}

function renderModels() {
  const root = els.modelsList;
  if (!root) return;
  root.innerHTML = '';
  const models = state.models || [];
  if (els.modelsEmpty) els.modelsEmpty.hidden = models.length > 0;
  models.forEach(function (m) { root.appendChild(buildCard(m)); });
}

function buildCard(m) {
  const card = document.createElement('div');
  card.className = 'model-card';
  card.dataset.id = m.id;

  const glyph = pickGlyph(m);
  const ownership = m.ownership || 'none';
  const reachable = !!m.reachable;
  const stateBadge = badge(m);

  // Build the action button row. "Force stop" only shows when the
  // process is adopted — clicking it taskkill's whoever holds the port.
  const adopted = ownership === 'external';
  const pidNote = m.pid && adopted ? ' <span class="muted small">PID ' + m.pid + '</span>' : '';

  card.innerHTML =
    '<div class="model-title">' + glyph + '<span>' + escapeHtml(m.display_name) + '</span>' + stateBadge + pidNote + '</div>' +
    '<div class="model-meta">' +
      escapeHtml(m.backend) + (m.port ? ' · :' + m.port : '') +
      (m.aliases && m.aliases.length ? ' · ' + escapeHtml(m.aliases.join(', ')) : '') +
    '</div>' +
    '<div class="model-actions">' +
      (m.controllable ? (
        '<button type="button" class="btn small primary" data-act="start" ' + (ownership !== 'none' ? 'disabled' : '') + '>▶ Start</button>' +
        '<button type="button" class="btn small" data-act="stop" ' + (ownership !== 'ours' ? 'disabled' : '') + '>■ Stop</button>' +
        (adopted ? '<button type="button" class="btn small danger" data-act="force-stop">💀 Force stop</button>' : '')
      ) : '') +
      '<button type="button" class="btn small" data-act="ping" ' + (!reachable && m.backend !== 'claude' && m.backend !== 'gemini' ? 'disabled' : '') + '>📶 Ping</button>' +
      (m.controllable ? '<button type="button" class="btn small ghost" data-act="log">📜 Log</button>' : '') +
    '</div>' +
    '<pre class="logpane model-log" data-id="' + m.id + '" hidden></pre>';

  card.querySelectorAll('button[data-act]').forEach(function (btn) {
    btn.addEventListener('click', function () { handleAction(m, btn.dataset.act, card); });
  });
  return card;
}

function badge(m) {
  if (!m.controllable) return '<span class="badge">' + escapeHtml(m.backend) + '</span>';
  if (m.ownership === 'ours') return '<span class="badge" style="border-color:rgba(74,222,128,.4);color:#4ade80">running</span>';
  if (m.ownership === 'external') return '<span class="badge" style="border-color:rgba(251,191,36,.4);color:#fbbf24">adopted</span>';
  return '<span class="badge">stopped</span>';
}

function pickGlyph(m) {
  if (m.backend === 'claude') return '🌀';
  if (m.backend === 'gemini') return '♊';
  if (m.backend === 'whisper') return '🎙';
  return '🦙';
}

async function handleAction(m, act, card) {
  if (act === 'start') {
    try {
      await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/start', {});
      toast('Starting ' + m.display_name + '…', 'good');
      await sleep(800);
      fetchModels();
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'stop') {
    try {
      await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/stop', {});
      toast('Stopped ' + m.display_name, 'good');
      await sleep(400);
      fetchModels();
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'force-stop') {
    if (!window.confirm('Force-kill the process on :' + m.port + '? This taskkills whoever holds the port (PID ' + (m.pid || '?') + ').')) return;
    try {
      await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/force-stop', {});
      toast('Force-stopped ' + m.display_name, 'good');
      await sleep(400);
      fetchModels();
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'ping') {
    try {
      const body = await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/ping', {});
      toast(m.display_name + ' · ' + body.status + ' · ' + body.latency_ms + ' ms', body.ok ? 'good' : 'error');
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'log') {
    toggleLog(m, card);
  }
}

function toggleLog(m, card) {
  const pane = card.querySelector('pre.model-log');
  if (!pane) return;
  if (!pane.hidden) {
    pane.hidden = true;
    if (logStreams[m.id]) { try { logStreams[m.id].close(); } catch (_) {} delete logStreams[m.id]; }
    return;
  }
  pane.hidden = false;
  pane.textContent = '';
  const lines = [];
  logStreams[m.id] = eventStream('/admin/api/models/' + encodeURIComponent(m.id) + '/log/tail', {
    message: function (data) {
      if (typeof data !== 'string') return;
      lines.push(data);
      if (lines.length > 400) lines.shift();
      pane.textContent = lines.join('\n');
      pane.scrollTop = pane.scrollHeight;
    },
  });
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]);
  });
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

export function wireModels() { /* nothing to wire — cards re-render on fetch */ }
