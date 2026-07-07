/* Models tab — per-backend .app-item row (canonical pattern).
 *
 * Process control only: start / stop / ping / force-stop. Per-model log
 * tailing was tried in #10 but pulled back — adopted backends have no
 * captured stdout, and the central Hub log tab already shows every
 * request that flows through the hub. Detailed per-backend telemetry
 * belongs in a future dedicated tab (#4: OpenTelemetry + Langfuse).
 */

import { els, state } from './state.js';
import { jsonApi, postJson, toast, escapeHtml } from './api.js';
import { mountGlossaryEditor } from './glossary.js';
import { icon } from './_vendored/icons/icons.js';

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
  const ownership = m.ownership || 'none';
  const reachable = !!m.reachable;
  const adopted = ownership === 'external';
  const localHost = state.status && state.status.host;
  const remote = !!(m.host && localHost && m.host !== localHost);
  const pidNote = m.pid && adopted ? ' <span class="muted small">PID ' + m.pid + '</span>' : '';
  // Host badge (#181): every remote-owned tile gets one, independent of
  // whether a PID happens to be shown — the PID note alone (adopted-only)
  // isn't enough to tell "qwen3.5-9b"/"parakeet-tdt-0.6b-v3" apart from a
  // purely local row.
  const hostNote = remote ? ' <span class="muted small">on ' + escapeHtml(m.host) + '</span>' : '';

  // Rebuild the .app-main block in place rather than wiping the whole <li>,
  // so an open dictionary panel (a sibling, below) survives the 5 s poll
  // with its unsaved edits intact.
  let main = li.querySelector(':scope > .app-main');
  if (main) {
    main.replaceChildren();
  } else {
    main = document.createElement('div');
    main.className = 'app-main';
  }

  const titleRow = document.createElement('div');
  titleRow.className = 'app-title-row';
  // Badge + adopted-PID note live OUTSIDE .app-title: that span ellipsises
  // long display names (whisper-large-v3-turbo, gemma4-26b-a4b-it), and a
  // pill nested inside it gets pushed past the clip boundary and vanishes.
  // As siblings in the title row they are never clipped.
  titleRow.innerHTML =
    '<span class="app-title"><span class="app-name">' + escapeHtml(m.display_name) + '</span></span>' + badge(m) + pidNote + hostNote;

  const icons = document.createElement('div');
  icons.className = 'app-icons';
  const buttons = [];
  if (m.controllable) {
    buttons.push({ act: 'start', glyph: icon('play'), label: 'Start', disabled: ownership !== 'none' });
    buttons.push({ act: 'stop',  glyph: icon('square'), label: 'Stop',  disabled: ownership !== 'ours', danger: true });
  }
  buttons.push({
    act: 'ping', glyph: icon('signal'), label: 'Ping',
    disabled: !reachable && m.backend !== 'claude' && m.backend !== 'gemini',
  });
  if (adopted) {
    buttons.push({ act: 'force-stop', glyph: icon('skull'), label: 'Force stop', danger: true });
  }
  if (m.backend === 'whisper') {
    // The transcription dictionary is shared by every whisper backend, so
    // the same editor opens from any whisper row.
    buttons.push({ act: 'dictionary', glyph: icon('book-open'), label: 'Transcription dictionary' });
  }
  const panelOpen = !!li.querySelector(':scope > .glossary-panel:not([hidden])');
  buttons.forEach(function (b) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'icon-btn' + (b.danger ? ' danger' : '');
    if (b.act === 'dictionary' && panelOpen) btn.classList.add('active');
    btn.dataset.act = b.act;
    btn.disabled = !!b.disabled;
    btn.title = b.label;
    btn.setAttribute('aria-label', b.label);
    btn.innerHTML = b.glyph;
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

  // Keep .app-main as the first child so any dictionary panel stays below it.
  const panel = li.querySelector(':scope > .glossary-panel');
  if (panel) {
    li.insertBefore(main, panel);
  } else if (main.parentNode !== li) {
    li.appendChild(main);
  }
}

function badge(m) {
  if (!m.controllable) return ' <span class="badge">' + escapeHtml(m.backend) + '</span>';
  if (m.ownership === 'ours') return ' <span class="badge good">running</span>';
  if (m.ownership === 'external') return ' <span class="badge warn">adopted</span>';
  return ' <span class="badge">stopped</span>';
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
  } else if (act === 'dictionary') {
    toggleDictionaryPanel(m);
  }
}

// Open/close the shared transcription-dictionary editor under a whisper row.
//
// There is exactly one dictionary, so only one editor is ever open at a
// time: opening it on any whisper row first closes any other open panel.
// That makes it visually unambiguous that Turbo and Translate edit the
// same list — you can't have two side-by-side that look independent.
function toggleDictionaryPanel(m) {
  const root = els.modelsList;
  if (!root) return;
  const li = root.querySelector('.app-item[data-id="' + cssEscape(m.id) + '"]');
  if (!li) return;
  const alreadyOpen = !!li.querySelector(':scope > .glossary-panel');
  closeAllDictionaryPanels(root);
  // Clicking the row whose panel was open just closes it (toggle off).
  if (alreadyOpen) return;
  const panel = document.createElement('div');
  panel.className = 'glossary-panel';
  li.appendChild(panel);
  mountGlossaryEditor(panel);
  const btn = li.querySelector('.icon-btn[data-act="dictionary"]');
  if (btn) btn.classList.add('active');
}

function closeAllDictionaryPanels(root) {
  root.querySelectorAll('.glossary-panel').forEach(function (p) { p.remove(); });
  root.querySelectorAll('.icon-btn[data-act="dictionary"].active')
    .forEach(function (b) { b.classList.remove('active'); });
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

/* escapeHtml lives in api.js (sibling dedup, #211). */

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

export function wireModels() { /* nothing to wire — rows re-render on fetch */ }
