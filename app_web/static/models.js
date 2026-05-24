/* Models tab — per-backend .app-item row (canonical pattern).
 *
 * Layout: left .app-main with title + meta + status badge; right
 * .row-actions column with icon-buttons (start / stop / log / ping).
 * Log pane opens as a sibling <li> below the row, mirroring app-
 * launcher's .jobs-history-li trick — keeps the row's flex context
 * intact while letting the log claim full width.
 */

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
  models.forEach(function (m) {
    const item = buildItem(m);
    root.appendChild(item);
  });
}

function buildItem(m) {
  const li = document.createElement('li');
  li.className = 'app-item';
  li.dataset.id = m.id;

  const glyph = pickGlyph(m);
  const ownership = m.ownership || 'none';
  const reachable = !!m.reachable;
  const adopted = ownership === 'external';
  const pidNote = m.pid && adopted ? ' <span class="muted small">PID ' + m.pid + '</span>' : '';

  const main = document.createElement('div');
  main.className = 'app-main';

  const titleRow = document.createElement('div');
  titleRow.className = 'app-title-row';
  titleRow.innerHTML =
    '<span class="app-title">' + glyph + '<span>' + escapeHtml(m.display_name) + '</span>' + badge(m) + pidNote + '</span>';

  const icons = document.createElement('div');
  icons.className = 'app-icons';
  const buttons = [];
  if (m.controllable) {
    buttons.push({ act: 'start', glyph: '▶', label: 'Start', disabled: ownership !== 'none' });
    buttons.push({ act: 'stop',  glyph: '■', label: 'Stop',  disabled: ownership !== 'ours', danger: true });
  }
  buttons.push({
    act: 'ping', glyph: '📶', label: 'Ping',
    disabled: !reachable && m.backend !== 'claude' && m.backend !== 'gemini',
  });
  if (m.controllable) {
    buttons.push({ act: 'log', glyph: '📜', label: 'Log' });
  }
  if (adopted) {
    buttons.push({ act: 'force-stop', glyph: '💀', label: 'Force stop', danger: true });
  }
  buttons.forEach(function (b) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'icon-btn' + (b.danger ? ' danger' : '');
    btn.dataset.act = b.act;
    btn.disabled = !!b.disabled;
    btn.title = b.label;
    btn.setAttribute('aria-label', b.label);
    btn.textContent = b.glyph;
    btn.addEventListener('click', function () { handleAction(m, b.act, li); });
    icons.appendChild(btn);
  });
  titleRow.appendChild(icons);
  main.appendChild(titleRow);

  const meta = document.createElement('div');
  meta.className = 'app-meta';
  meta.textContent =
    m.backend +
    (m.port ? ' · :' + m.port : '') +
    (m.aliases && m.aliases.length ? ' · ' + m.aliases.join(', ') : '');
  main.appendChild(meta);

  li.appendChild(main);
  return li;
}

function badge(m) {
  if (!m.controllable) return ' <span class="badge">' + escapeHtml(m.backend) + '</span>';
  if (m.ownership === 'ours') return ' <span class="badge good">running</span>';
  if (m.ownership === 'external') return ' <span class="badge warn">adopted</span>';
  return ' <span class="badge">stopped</span>';
}

function pickGlyph(m) {
  if (m.backend === 'claude') return '🌀';
  if (m.backend === 'gemini') return '♊';
  if (m.backend === 'whisper') return '🎙';
  return '🦙';
}

async function handleAction(m, act, item) {
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
    toggleLog(m, item);
  }
}

function toggleLog(m, item) {
  // Find the sibling <li class="model-log-li"> right after this row.
  let logLi = item.nextElementSibling;
  if (logLi && !logLi.classList.contains('model-log-li')) logLi = null;

  if (logLi) {
    logLi.remove();
    if (logStreams[m.id]) { try { logStreams[m.id].close(); } catch (_) {} delete logStreams[m.id]; }
    return;
  }

  logLi = document.createElement('li');
  logLi.className = 'model-log-li';
  const pre = document.createElement('pre');
  pre.className = 'logpane';
  pre.dataset.id = m.id;
  logLi.appendChild(pre);
  item.insertAdjacentElement('afterend', logLi);

  const lines = [];
  logStreams[m.id] = eventStream('/admin/api/models/' + encodeURIComponent(m.id) + '/log/tail', {
    message: function (data) {
      if (typeof data !== 'string') return;
      lines.push(data);
      if (lines.length > 400) lines.shift();
      pre.textContent = lines.join('\n');
      pre.scrollTop = pre.scrollHeight;
    },
  });
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]);
  });
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

export function wireModels() { /* nothing to wire — rows re-render on fetch */ }
