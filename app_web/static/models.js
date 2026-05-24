/* Models tab — per-backend .app-item row (canonical pattern).
 *
 * Process control only: start / stop / ping / force-stop. Per-model log
 * tailing was tried in #10 but pulled back — adopted backends have no
 * captured stdout, and the central Hub log tab already shows every
 * request that flows through the hub. Detailed per-backend telemetry
 * belongs in a future dedicated tab (#4: OpenTelemetry + Langfuse).
 */

import { els, state } from './state.js';
import { jsonApi, postJson, toast } from './api.js';

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
  const models = state.models || [];
  if (els.modelsEmpty) els.modelsEmpty.hidden = models.length > 0;

  // Diff-update so the row identity survives the 5 s poll. Reusing the
  // existing <li> per model id avoids the DOM churn (and any focus /
  // selection loss) of a full innerHTML rebuild.
  const existing = {};
  Array.prototype.forEach.call(root.children, function (node) {
    if (node.classList && node.classList.contains('app-item') && node.dataset.id) {
      existing[node.dataset.id] = node;
    }
  });

  const frag = document.createDocumentFragment();
  models.forEach(function (m) {
    const prev = existing[m.id];
    if (prev) {
      fillItem(prev, m);
      frag.appendChild(prev);
    } else {
      frag.appendChild(buildItem(m));
    }
  });
  root.replaceChildren(frag);
}

function buildItem(m) {
  const li = document.createElement('li');
  li.className = 'app-item';
  li.dataset.id = m.id;
  fillItem(li, m);
  return li;
}

function fillItem(li, m) {
  const glyph = pickGlyph(m);
  const ownership = m.ownership || 'none';
  const reachable = !!m.reachable;
  const adopted = ownership === 'external';
  const pidNote = m.pid && adopted ? ' <span class="muted small">PID ' + m.pid + '</span>' : '';

  li.replaceChildren();

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
    btn.addEventListener('click', function () { handleAction(m, b.act); });
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

async function handleAction(m, act) {
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
  }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]);
  });
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

export function wireModels() { /* nothing to wire — rows re-render on fetch */ }
