/* Models tab (rows + memory glance).
 *
 * The tab answers "what is loaded and how much room is left": the memory
 * glance (fleet_placement.js — one meter per host) heads it, then one shared
 * row per model (rows.js on the vendored action-row), grouped by the host
 * serving it. A row's face carries an alive badge, a budgeted meta line
 * (kind, remote host, port, footprint), at most one exception chip (no key,
 * failover, adopted, on demand) and one visible verb, Start or Stop. Every
 * other action sits in the row's trailing menu, whose header holds the
 * placement chain and startup policy; Force stop is its last, danger item.
 *
 * Tapping a row opens the model's sheet on a phone, or fills the detail pane
 * beside the list on a wide window. Both render modelBody(): the full meta
 * (process id, device, aliases), placement, the roles it serves, its actions,
 * then one editor slot (the placement editor or the shared transcription
 * dictionary) and the log tail. The 5 s poll repaints only the info part, so
 * an open editor and its unsaved draft survive it.
 */

import { els, state, MODELS_ACTIVE_ONLY_KEY } from './state.js';
import { jsonApi, postJson, putJson, toast, escapeHtml, codeHtml, fmtGb, isAuthError } from './api.js';
import { mountGlossaryEditor } from './glossary.js';
import { icon } from './_vendored/icons/icons.js';
import { createRowMenu } from './_vendored/row-menu/row-menu.js';
import { row, chip, setHeadLine, defineInfo, isWide, onWideChange } from './rows.js';
import { fetchFleetPlacement, renderMemory, hostBodyEl, hostLabel, overCapacityHosts, onFleetPlacement } from './fleet_placement.js';

defineInfo('models-about', 'Models and memory',
  '<p>One row per backend enabled for this host, grouped by the machine serving it. Local '
  + '<code>llama-server</code> and <code>whisper-server</code> backends start and stop individually; '
  + 'the Claude and Gemini rows are subscription-backed and have no managed process.</p>'
  + '<p>Memory shows each machine\u2019s live GPU memory (RAM on a machine with no discrete GPU). '
  + 'Where no live figure exists it falls back to the estimated footprint of the models placed there '
  + 'against the declared ceiling, marked with ~. Tap a machine for what runs there and what it could '
  + 'load. Placement is changed per model with Edit placement, or in <code>config/models.yaml</code>.</p>');

let modelsLoaded = false;
// Set when a read fails after a good load (Hub/Machines track the same not-confirmed state).
let modelsStale = false;

// One controller for the whole list: at most one row's menu is open, and it
// reopens on its rebuilt kebab when the 5 s poll repaints the rows.
const rowMenu = createRowMenu();

export async function fetchModels() {
  try {
    const body = await jsonApi('/admin/api/models');
    state.models = body.models || [];
    state.modelsConfig = body.config || null;
    modelsLoaded = true;
    modelsStale = false;
    renderConfigChip();
    renderModels();
  } catch (_) {
    // Before any load this stays "Reading models…"; after one, flag stale.
    if (modelsLoaded) modelsStale = true;
    renderModels();
  }
}

/* Roles (role → model) — read with the fleet summary on tab open, so each
 * model's sheet names the roles it serves (their one home, as the Hub card
 * no longer shows them). Never polled. */
export async function fetchRoles() {
  try {
    state.roles = await jsonApi('/admin/api/roles');
    renderModels();
  } catch (_) { /* roles stay unshown; the sheets still render without them */ }
}

/* models.yaml config version — the HEAD sha of the file, shown in
 * Settings › About. Every converged hub shows the same sha, so
 * drift between hubs is visible by comparing their /admin pages. */
function renderConfigChip() {
  const el = els.modelsConfigSha;
  if (!el) return;
  const cfg = state.modelsConfig;
  const sha = cfg && cfg.sha && cfg.sha !== 'unknown' ? cfg.sha : '';
  el.textContent = sha || '—';
  el.title = sha
    ? 'models.yaml config version (HEAD sha of config/models.yaml) — the same on every converged hub'
    : '';
}

// A row counts as "running" only if it's a controllable backend that's
// currently running/adopted. Claude/Gemini are excluded outright — they're
// subscription-backed with no on/off state, always "on", so listing them
// here is just noise rather than a signal.
function isActive(m) {
  return m.controllable && (m.ownership === 'ours' || m.ownership === 'external');
}

function findModel(id) {
  return (state.models || []).find(function (m) { return m.id === id; });
}

// --------------------------------------------------------------- row facts
const KIND_GLYPH = {
  whisper: 'radio', tts: 'volume-2', claude: 'sparkles', gemini: 'sparkles',
  typesafe: 'circle-check', comfyui: 'image', image: 'image',
};

function isRemote(m) {
  const localHost = state.status && state.status.host;
  return !!(m.host && localHost && m.host !== localHost);
}

/* The model's kind in plain words; the TypeSafe tile names where its content
 * goes (third-party egress). */
function kindLabel(m) {
  return m.backend === 'typesafe' ? 'typesafe · api.typesafe.ai (US)' : m.backend;
}

/* Exceptions only, most urgent first (the tone map in styles.css). A running
 * model is the avatar's alive badge, never a chip; "stopped" and "eager" are
 * normal states and carry nothing. */
function exceptionChips(m) {
  const out = [];
  if (m.backend === 'typesafe' && !m.key_configured) out.push({ text: 'no key', tone: 'attention' });
  if (m.failover) out.push({ text: 'failover', tone: 'attention' });
  if (m.ownership === 'external') out.push({ text: 'adopted' });
  if (m.placement && m.placement.startup === 'on_demand') out.push({ text: 'on demand' });
  return out;
}

/* Budgeted meta line: remote host, port, footprint. The footprint is
 * the registry's static est_vram_mb, only on rows that load a model process
 * of their own (a virtual alias and subscription rows declare none). */
function metaText(m) {
  const bits = [];
  if (isRemote(m)) bits.push('on ' + m.host);
  if (m.port) bits.push(':' + m.port);
  if (m.placement && m.placement.est_vram_mb) bits.push('~' + fmtGb(m.placement.est_vram_mb));
  return bits.join(' · ');
}

/* The full meta for the sheet / detail pane: everything the row face drops. */
function fullMetaPairs(m) {
  const pairs = [['Kind', kindLabel(m)]];
  if (m.host) pairs.push(['Served by', m.host + (isRemote(m) ? '' : ' (this machine)')]);
  if (m.failover) pairs.push(['Prefers', m.preferred_host || '—']);
  if (m.port) pairs.push(['Port', ':' + m.port]);
  if (m.pid && m.ownership === 'external') pairs.push(['Process', String(m.pid)]);
  if (m.device) pairs.push(['Device', m.device]);
  // Aliases are ids a caller sends as `model`: a list of identifiers (COMP-11).
  if (m.aliases && m.aliases.length) pairs.push(['Aliases', m.aliases]);
  if (m.placement && m.placement.est_vram_mb) pairs.push(['Footprint', '~' + fmtGb(m.placement.est_vram_mb)]);
  return pairs;
}

/* Roles this model serves, primary or as a fallback (GET /admin/api/roles). */
function rolesFor(m) {
  const roles = (state.roles && state.roles.roles) || {};
  const out = [];
  Object.keys(roles).forEach(function (key) {
    const r = roles[key] || {};
    const label = roleLabel(key);
    if (r.model_id === m.id) out.push(label);
    else if ((r.fallback || []).indexOf(m.id) !== -1) out.push(label + ' (fallback)');
  });
  return out;
}

function roleLabel(key) {
  return String(key).split('.').map(function (part) {
    return part.split('_').map(function (w) {
      return w ? w.charAt(0).toUpperCase() + w.slice(1) : w;
    }).join(' ');
  }).join(' · ');
}

/* The one visible verb (action-row): Start while the model is down, Stop once
 * this hub owns the process. On the row it is a plain glyph-only icon-button
 * (play / square); the wide detail pane's head keeps the
 * worded button and draws Stop outlined (label, outline). An adopted
 * process is not this hub's to stop gracefully, so Start stays disabled and
 * Force stop lives in the menu. */
function powerVerb(m) {
  if (!m.controllable) return null;
  const ownership = m.ownership || 'none';
  const name = escapeHtml(m.display_name);
  if (ownership === 'ours') {
    return {
      attrs: ' data-act="stop" aria-label="Stop ' + name + '"', tip: 'Stop ' + m.display_name,
      track: 'model-stop', label: 'Stop', glyph: 'square', outline: true,
    };
  }
  return {
    attrs: ' data-act="start" aria-label="Start ' + name + '"',
    tip: ownership !== 'none' ? 'Adopted from another process — use Force stop in the menu' : 'Start ' + m.display_name,
    track: 'model-start', label: 'Start', glyph: 'play', disabled: ownership !== 'none',
  };
}

// --------------------------------------------------------------- the menu
/* Menu items and their rules: Ping (none for the billed
 * third-party decision backend; disabled when the backend does not answer),
 * the shared transcription dictionary on speech-to-text rows, Edit placement
 * on the write host only, the model log for a managed process, and Force stop
 * of an adopted process last, in danger. */
function menuItems(m) {
  const items = [];
  if (m.backend !== 'typesafe') {
    items.push({
      act: 'ping', glyph: 'signal', label: 'Ping',
      disabled: !m.reachable && m.backend !== 'claude' && m.backend !== 'gemini',
    });
  }
  if (m.backend === 'whisper') items.push({ act: 'dictionary', glyph: 'book-open', label: 'Transcription dictionary' });
  if (canEditPlacement(m)) items.push({ act: 'edit-placement', glyph: 'wrench', label: 'Edit placement', cls: 'placement-edit-btn' });
  if (m.controllable) items.push({ act: 'log', glyph: 'scroll-text', label: 'Model log' });
  if (m.ownership === 'external') items.push({ act: 'force-stop', glyph: 'skull', label: 'Force stop', danger: true });
  return items;
}

/* Startup policy, in words: always on (eager, the quiet default) or on demand,
 * reading its live state honestly — loaded while the backend answers, else
 * idle (stopped by the idle watchdog or never yet demanded). */
function startupLabel(m, p) {
  if (p.startup !== 'on_demand') return 'Always on';
  return m.reachable ? 'on demand · loaded' : 'on demand · idle';
}

function startupHtml(m, p) {
  if (p.startup === 'on_demand') {
    const idleNote = p.idle_unload_minutes
      ? 'Loads on first request; unloads after ' + p.idle_unload_minutes + ' min idle'
      : 'Loads on first request; stays up until stopped';
    return '<span class="chip startup-policy" title="' + escapeHtml(idleNote) + '">'
      + startupLabel(m, p) + '</span>';
  }
  return '<span class="startup-policy muted" title="Autostarted and kept resident">Always on</span>';
}

/* Placement intent: the host chain in priority order, the effective
 * owner highlighted and cpu-resident tiers marked, then the startup
 * policy. Subscription rows carry no placement and get nothing. */
function placementHtml(m) {
  const p = m.placement;
  if (!p) return '';
  const chain = (p.chain || []).map(function (entry, i) {
    const owner = entry.id === m.host;
    const title = (owner ? 'Active owner' : (i === 0 ? 'Preferred host' : 'Fallback host'))
      + (entry.cpu ? ' — CPU-resident on this host (holds no GPU VRAM)' : '');
    return (i ? '<span class="placement-arrow" aria-hidden="true">' + icon('chevron-right') + '</span>' : '')
      + '<span class="place-pill' + (owner ? ' owner' : '') + '" title="' + escapeHtml(title) + '">'
      + escapeHtml(entry.id + (entry.cpu ? ' · cpu' : '')) + '</span>';
  }).join('');
  return '<div class="placement"><div class="placement-line">'
    + (chain ? '<span class="placement-chain">' + chain + '</span>' : '')
    + startupHtml(m, p) + '</div></div>';
}

/* The menu's one header line (the vendored row-menu takes plain text, not
 * markup): the host chain in priority order with the effective owner and
 * cpu-resident tiers marked, the startup policy, then the roles served. */
function menuHeader(m) {
  const parts = [];
  const p = m.placement;
  if (p) {
    const chain = (p.chain || []).map(function (entry) {
      return entry.id + (entry.cpu ? ' (cpu)' : '') + (entry.id === m.host ? ' \u25cf' : '');
    }).join(' \u2192 ');
    parts.push((chain ? chain + ' \u00b7 ' : '') + startupLabel(m, p));
  }
  const roles = rolesFor(m);
  if (roles.length) parts.push('Role: ' + roles.join(', '));
  return parts.join(' \u00b7 ');
}

/* The row's trailing kebab. The menu itself is the vendored row-menu, built and
 * attached after the rows are painted (attachRowMenus); `data-menu` is the
 * menu's content, so a change to it repaints the row and re-attaches. */
function menuHtml(m) {
  const items = menuItems(m);
  const header = menuHeader(m);
  if (!items.length && !header) return '';
  const sig = JSON.stringify([header, items.map(function (it) { return [it.act, !!it.disabled]; })]);
  return '<button type="button" class="icon-button row-kebab" data-menu="' + escapeHtml(sig) + '"'
    + ' title="More actions" aria-label="More actions for ' + escapeHtml(m.display_name) + '">'
    + icon('ellipsis-vertical') + '</button>';
}

/* Hand each painted kebab to the vendored row-menu. A tap acts on the live
 * model, not the one the row was painted from. */
function attachRowMenus(root) {
  root.querySelectorAll('.model-row').forEach(function (li) {
    const kebab = li.querySelector('.row-kebab');
    const m = findModel(li.dataset.id);
    if (!kebab || !m) return;
    const items = menuItems(m).map(function (it) {
      return {
        label: it.label,
        glyph: it.glyph,
        disabled: it.disabled,
        danger: it.danger,
        dataset: { act: it.act },
        onTap: function () {
          const live = findModel(li.dataset.id);
          if (live) handleAction(live, it.act);
        },
      };
    });
    rowMenu.attach(m.id, kebab, items, { header: menuHeader(m) });
  });
  rowMenu.endRender();
}

// --------------------------------------------------------------- render
function modelRow(m) {
  const wide = isWide();
  const sel = state.modelsSelected;
  return row({
    cls: 'model-row',
    attrs: ' data-id="' + escapeHtml(m.id) + '"',
    glyph: KIND_GLYPH[m.backend] || 'cpu',
    badge: isActive(m) ? 'up' : null,
    title: m.display_name,
    lead: kindLabel(m),
    chips: exceptionChips(m).slice(0, 1),
    meta: metaText(m),
    selected: wide && !!sel && sel.kind === 'model' && sel.id === m.id,
    mainAttrs: ' data-act="open"' + (wide ? '' : ' aria-haspopup="dialog"'),
    verb: powerVerb(m),
    trail: menuHtml(m),
  });
}

function renderHeadLine(models) {
  const el = els.modelsHeadLine;
  if (!modelsLoaded) { setHeadLine(el, [], 'Reading models…'); return; }
  const parts = [];
  if (modelsStale) parts.push({ text: 'not confirmed', tone: 'attention' });
  const failover = models.filter(function (m) { return m.failover; }).length;
  if (failover) parts.push({ text: failover + ' failover', tone: 'attention' });
  const over = overCapacityHosts();
  if (over) parts.push({ text: over + ' over capacity', tone: 'danger' });
  const running = models.filter(isActive).length;
  setHeadLine(el, parts, running + ' running');
}

function renderScope() {
  if (!els.modelsScope) return;
  els.modelsScope.querySelectorAll('.segmented-item').forEach(function (b) {
    b.setAttribute('aria-pressed', String((b.dataset.scope === 'running') === state.modelsActiveOnly));
  });
}

/* Rows grouped by the host serving them, local host first. */
function groupedRowsHtml(visible) {
  const localHost = state.status && state.status.host;
  const groups = {};
  const order = [];
  visible.forEach(function (m) {
    const key = m.host || '';
    if (!groups[key]) { groups[key] = []; order.push(key); }
    groups[key].push(m);
  });
  order.sort(function (a, b) {
    if (a === localHost) return -1;
    if (b === localHost) return 1;
    return a.localeCompare(b);
  });
  return order.map(function (key) {
    return '<li class="group-overline" role="presentation">' + escapeHtml(hostLabel(key) || 'Other') + '</li>'
      + groups[key].map(modelRow).join('');
  }).join('');
}

let lastListHtml = '';

export function renderModels() {
  const root = els.modelsList;
  if (!root) return;
  const models = state.models || [];
  const visible = state.modelsActiveOnly ? models.filter(isActive) : models;
  renderHeadLine(models);
  renderScope();
  renderMemory();

  if (els.modelsEmpty) {
    els.modelsEmpty.hidden = visible.length > 0;
    const msg = els.modelsEmpty.querySelector('.empty-state-message');
    if (msg) {
      msg.innerHTML = models.length === 0
        ? 'No models enabled for this host — check <code>config/models.yaml</code>.'
        : 'No models running right now — choose All to see every model.';
    }
  }

  ensureSelection(visible);
  // Repaint only when something changed, so the 5 s poll keeps focus where it
  // is; an open menu reopens on its row's new kebab (the vendored row-menu).
  const html = groupedRowsHtml(visible);
  if (html !== lastListHtml) {
    const focused = document.activeElement && root.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused ? [focused.closest('.model-row') && focused.closest('.model-row').dataset.id,
      focused.classList.contains('row-kebab') ? 'kebab' : focused.dataset.act] : null;
    root.innerHTML = html;
    lastListHtml = html;
    attachRowMenus(root);
    if (focusKey && focusKey[0]) {
      const again = root.querySelector('.model-row[data-id="' + cssEscape(focusKey[0]) + '"] '
        + (focusKey[1] === 'kebab' ? '.row-kebab' : focusKey[1] ? '[data-act="' + focusKey[1] + '"]' : '.action-row-main'));
      if (again) again.focus();
    }
  }
  renderDetail();
  renderSheet();
}

// --------------------------------------------------------------- sheet / detail body
function actionButton(act, glyph, label, opts) {
  const o = opts || {};
  return '<button type="button" class="button-tint' + (o.cls ? ' ' + o.cls : '') + '" data-act="' + act + '"'
    + (o.disabled ? ' disabled' : '') + '>' + icon(glyph) + '<span>' + escapeHtml(label) + '</span></button>';
}

function modelInfoHtml(m) {
  const chips = exceptionChips(m);
  const roles = rolesFor(m);
  const pairs = fullMetaPairs(m);
  if (roles.length) pairs.push(['Roles', roles.join(', ')]);
  const items = menuItems(m).filter(function (it) { return !it.danger; });
  const buttons = items.map(function (it) {
    return actionButton(it.act, it.glyph, it.label, { disabled: it.disabled, cls: it.cls });
  }).join('');
  const force = m.ownership === 'external'
    ? '<div class="sheet-section sheet-danger"><button type="button" class="sheet-danger-row" data-act="force-stop">'
      + icon('skull') + '<span>Force stop</span></button></div>'
    : '';
  return '<div class="sheet-section">'
    + (chips.length ? '<div class="sheet-chips">' + chips.map(function (c) { return chip(c.text, c.tone); }).join('') + '</div>' : '')
    + '<dl class="sheet-pairs">' + pairs.map(function (p) {
      const value = Array.isArray(p[1]) ? p[1].map(codeHtml).join(', ') : escapeHtml(p[1]);
      return '<dt>' + escapeHtml(p[0]) + '</dt><dd>' + value + '</dd>';
    }).join('') + '</dl></div>'
    + (m.placement ? '<div class="sheet-section"><p class="sheet-label">Placement</p>' + placementHtml(m) + '</div>' : '')
    + (buttons ? '<div class="sheet-section"><div class="sheet-buttons sheet-buttons--flow">' + buttons + '</div></div>' : '')
    + force;
}

/* One body per container: the info part repaints on every poll, the editor
 * slot and the log tail below it are left alone, so an open editor keeps its
 * unsaved draft (the guarantee inline panels give). */
function fillModelBody(container, m) {
  let body = container.querySelector(':scope > .model-body');
  if (!body || body.dataset.id !== m.id) {
    container.replaceChildren();
    body = document.createElement('div');
    body.className = 'model-body';
    body.dataset.id = m.id;
    body.innerHTML = '<div class="model-info"></div><div class="model-editor"></div>'
      + '<div class="model-log sheet-section" hidden></div>';
    container.appendChild(body);
  }
  body.querySelector('.model-info').innerHTML = modelInfoHtml(m);
  return body;
}

function fillHostBody(container, hostId) {
  container.replaceChildren(hostBodyEl(hostId));
}

function selectionTitle(sel) {
  if (!sel) return '';
  if (sel.kind === 'host') return hostLabel(sel.id);
  const m = findModel(sel.id);
  return m ? m.display_name : '';
}

/* On a wide window one detail is always showing: keep the open one, else the
 * first visible model. */
function ensureSelection(visible) {
  if (!isWide()) return;
  const sel = state.modelsSelected;
  if (sel && (sel.kind === 'host' || findModel(sel.id))) return;
  if (visible.length) state.modelsSelected = { kind: 'model', id: visible[0].id };
}

function renderDetail() {
  const pane = els.modelsDetail;
  if (!pane || !isWide()) return;
  const sel = state.modelsSelected;
  const m = sel && sel.kind === 'model' ? findModel(sel.id) : null;
  if (!sel || (sel.kind === 'model' && !m)) {
    pane.innerHTML = '<div class="empty-state"><svg class="icon empty-state-icon" aria-hidden="true"><use href="#i-boxes"></use></svg>'
      + '<p class="empty-state-message">Select a model.</p></div>';
    return;
  }
  let head = pane.querySelector(':scope > .wide-detail-head');
  let slot = pane.querySelector(':scope > .wide-detail-body');
  if (!head || !slot) {
    pane.replaceChildren();
    head = document.createElement('div');
    head.className = 'wide-detail-head';
    slot = document.createElement('div');
    slot.className = 'wide-detail-body';
    pane.appendChild(head);
    pane.appendChild(slot);
  }
  const verb = m ? powerVerb(m) : null;
  head.innerHTML = '<h2>' + escapeHtml(selectionTitle(sel)) + '</h2>'
    + (verb ? '<button type="button" class="' + (verb.outline ? 'ghost-btn' : 'button-tint') + '"' + verb.attrs + (verb.disabled ? ' disabled' : '') + '>'
      + icon(verb.glyph) + '<span>' + verb.label + '</span></button>' : '');
  head.dataset.id = m ? m.id : '';
  if (m) fillModelBody(slot, m);
  else fillHostBody(slot, sel.id);
}

function renderSheet() {
  const dlg = els.modelSheet;
  if (!dlg || !dlg.open) return;
  const sel = state.modelsSelected;
  if (!sel) { dlg.close(); return; }
  if (sel.kind === 'model') {
    const m = findModel(sel.id);
    if (!m) { dlg.close(); return; }
    els.modelSheetTitle.textContent = m.display_name;
    fillModelBody(els.modelSheetBody, m);
  } else {
    els.modelSheetTitle.textContent = hostLabel(sel.id);
    fillHostBody(els.modelSheetBody, sel.id);
  }
}

/* Open a model's or a machine's detail: the sheet on a phone, the detail pane
 * on a wide window. Returns the container its body lives in. */
export function openSelection(kind, id) {
  state.modelsSelected = { kind: kind, id: id };
  if (isWide()) {
    lastListHtml = '';
    renderModels();
    return els.modelsDetail.querySelector('.wide-detail-body');
  }
  if (!els.modelSheet.open) {
    els.modelSheetBody.replaceChildren();
    els.modelSheet.showModal();
  }
  renderSheet();
  return els.modelSheetBody;
}

function bodyFor(m) {
  const container = openSelection('model', m.id);
  return container && container.querySelector('.model-body');
}

// --------------------------------------------------------------- placement editor
/* Edits a model's declared placement — hosts chain (reorder / add / remove /
 * cpu tier), startup policy, idle-unload window — and PUTs it to
 * /admin/api/models/<id>/placement, which validates hard (schema + the
 * VRAM budget) and then writes through to git (comment-preserving models.yaml
 * edit, config-bot commit, push). It mounts in the model's editor slot, which
 * the 5 s poll never repaints. Only offered where the server says this hub
 * may write (config.write_enabled — tower only). */
function canEditPlacement(m) {
  return !!(state.modelsConfig && state.modelsConfig.write_enabled &&
    m.placement && m.placement.editable !== false);
}

function closeEditors() {
  document.querySelectorAll('.model-editor').forEach(function (slot) { slot.replaceChildren(); });
}

function openEditor(m, kind) {
  const body = bodyFor(m);
  if (!body) return;
  const slot = body.querySelector('.model-editor');
  const current = slot.firstElementChild;
  closeEditors();
  if (current && current.dataset.kind === kind) return; // the same item toggles it off
  const panel = document.createElement('div');
  panel.dataset.kind = kind;
  panel.className = kind === 'placement' ? 'placement-editor' : 'glossary-panel';
  slot.appendChild(panel);
  if (kind === 'placement') mountPlacementEditor(panel, m);
  else mountGlossaryEditor(panel);
  panel.scrollIntoView({ block: 'nearest' });
}

function mountPlacementEditor(panel, m) {
  const p = m.placement || {};
  const ed = {
    panel: panel,
    m: m,
    draft: {
      chain: (p.chain || []).map(function (e) { return { id: e.id, cpu: !!e.cpu }; }),
      startup: p.startup === 'on_demand' ? 'on_demand' : 'eager',
      idle: p.idle_unload_minutes || null,
    },
    errorMsg: '',
    saving: false,
  };
  renderPlacementEditor(ed);
}

function fleetHosts() {
  const cfg = state.modelsConfig;
  return (cfg && cfg.fleet_hosts) || [];
}

function movePlacementHost(ed, i, delta) {
  const chain = ed.draft.chain;
  const j = i + delta;
  if (j < 0 || j >= chain.length) return;
  const tmp = chain[i];
  chain[i] = chain[j];
  chain[j] = tmp;
  renderPlacementEditor(ed);
}

async function savePlacement(ed) {
  const draft = ed.draft;
  ed.errorMsg = '';
  ed.saving = true;
  renderPlacementEditor(ed);
  try {
    const body = await putJson(
      '/admin/api/models/' + encodeURIComponent(ed.m.id) + '/placement',
      {
        hosts: draft.chain,
        startup: draft.startup,
        idle_unload_minutes:
          draft.startup === 'on_demand' && draft.idle ? Number(draft.idle) : null,
      }
    );
    if (body.changed) {
      toast('Committed ' + (body.commit || '') + ' — syncing satellites', 'good');
    } else {
      toast('No changes — placement already matches', 'good');
    }
    closeEditors();
    fetchModels();
  } catch (exc) {
    // Inline, not just a toast: the validation detail (e.g. the VRAM
    // overcommit arithmetic) is the whole point of the rejection.
    ed.errorMsg = String(exc.message || exc);
    ed.saving = false;
    renderPlacementEditor(ed);
  }
}

function renderPlacementEditor(ed) {
  const panel = ed.panel;
  panel.replaceChildren();

  const note = document.createElement('p');
  note.className = 'pe-note muted small';
  note.textContent =
    'Edits are validated (schema + VRAM budget), then committed to config/models.yaml and pushed — satellites sync automatically.';
  panel.appendChild(note);

  const title = document.createElement('div');
  title.className = 'opt-group-title';
  title.textContent = 'Hosts chain — priority order';
  panel.appendChild(title);

  const list = document.createElement('div');
  list.className = 'pe-chain';
  ed.draft.chain.forEach(function (entry, i) { list.appendChild(placementHostRow(ed, entry, i)); });
  panel.appendChild(list);

  const addRow = placementAddRow(ed);
  if (addRow) panel.appendChild(addRow);

  const policyTitle = document.createElement('div');
  policyTitle.className = 'opt-group-title';
  policyTitle.textContent = 'Lifecycle';
  panel.appendChild(policyTitle);
  panel.appendChild(placementPolicy(ed));

  const err = document.createElement('div');
  err.className = 'pe-error';
  err.setAttribute('role', 'alert');
  err.hidden = !ed.errorMsg;
  err.textContent = ed.errorMsg;
  panel.appendChild(err);

  const actions = document.createElement('div');
  actions.className = 'pe-actions';
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'ghost-btn primary pe-save';
  saveBtn.innerHTML = icon('save') + (ed.saving ? 'Saving…' : 'Save & commit');
  saveBtn.disabled = ed.saving;
  saveBtn.addEventListener('click', function () { savePlacement(ed); });
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'ghost-btn pe-cancel';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.addEventListener('click', closeEditors);
  actions.appendChild(saveBtn);
  actions.appendChild(cancelBtn);
  panel.appendChild(actions);
}

function placementHostRow(ed, entry, i) {
  const chain = ed.draft.chain;
  const hostRow = document.createElement('div');
  hostRow.className = 'pe-host-row';
  hostRow.dataset.host = entry.id;

  const name = document.createElement('span');
  name.className = 'pe-host-name';
  name.textContent = entry.id;
  hostRow.appendChild(name);

  const cpuLabel = document.createElement('label');
  cpuLabel.className = 'pe-cpu-label muted small';
  const cpu = document.createElement('input');
  cpu.type = 'checkbox';
  cpu.className = 'pe-cpu';
  cpu.checked = entry.cpu;
  cpu.addEventListener('change', function () { entry.cpu = cpu.checked; });
  cpuLabel.appendChild(cpu);
  cpuLabel.appendChild(document.createTextNode(' cpu tier'));
  cpuLabel.title = 'Degraded CPU-offload last resort (#342) — holds no VRAM';
  hostRow.appendChild(cpuLabel);

  const controls = [
    { cls: 'pe-up', glyph: 'chevron-up', label: 'Move up', disabled: i === 0, fn: function () { movePlacementHost(ed, i, -1); } },
    { cls: 'pe-down', glyph: 'chevron-down', label: 'Move down', disabled: i === chain.length - 1, fn: function () { movePlacementHost(ed, i, 1); } },
    { cls: 'pe-remove', glyph: 'x', label: 'Remove host', danger: true, fn: function () { chain.splice(i, 1); renderPlacementEditor(ed); } },
  ];
  controls.forEach(function (c) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'icon-button ' + c.cls + (c.danger ? ' danger' : '');
    b.title = c.label;
    b.setAttribute('aria-label', c.label);
    b.disabled = !!c.disabled;
    b.innerHTML = icon(c.glyph);
    b.addEventListener('click', c.fn);
    hostRow.appendChild(b);
  });
  return hostRow;
}

// The "add host" picker, or null when every fleet host is already in the chain.
function placementAddRow(ed) {
  const inChain = {};
  ed.draft.chain.forEach(function (e) { inChain[e.id] = true; });
  const addable = fleetHosts().filter(function (h) { return !inChain[h.id]; });
  if (!addable.length) return null;
  const addRow = document.createElement('div');
  addRow.className = 'pe-add-row';
  const sel = document.createElement('select');
  sel.className = 'pe-add-select';
  sel.setAttribute('aria-label', 'Host to add to the chain');
  addable.forEach(function (h) {
    const opt = document.createElement('option');
    opt.value = h.id;
    opt.textContent = h.id + (h.vram_mb ? ' · ' + fmtGb(h.vram_mb) + ' VRAM' : '');
    sel.appendChild(opt);
  });
  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'ghost-btn pe-add-btn';
  addBtn.innerHTML = icon('plus') + 'Add host';
  addBtn.addEventListener('click', function () {
    if (!sel.value) return;
    ed.draft.chain.push({ id: sel.value, cpu: false });
    renderPlacementEditor(ed);
  });
  addRow.appendChild(sel);
  addRow.appendChild(addBtn);
  return addRow;
}

function placementPolicy(ed) {
  const draft = ed.draft;
  const policy = document.createElement('div');
  policy.className = 'pe-policy';

  const startupLabel = document.createElement('label');
  startupLabel.className = 'pe-field';
  startupLabel.appendChild(document.createTextNode('Startup'));
  const startupSel = document.createElement('select');
  startupSel.className = 'pe-startup';
  [
    { v: 'eager', t: 'always on — load at startup' },
    { v: 'on_demand', t: 'on demand — load on first request' },
  ].forEach(function (o) {
    const opt = document.createElement('option');
    opt.value = o.v;
    opt.textContent = o.t;
    if (draft.startup === o.v) opt.selected = true;
    startupSel.appendChild(opt);
  });
  startupSel.addEventListener('change', function () {
    draft.startup = startupSel.value;
    renderPlacementEditor(ed);
  });
  startupLabel.appendChild(startupSel);
  policy.appendChild(startupLabel);

  const idleLabel = document.createElement('label');
  idleLabel.className = 'pe-field';
  idleLabel.appendChild(document.createTextNode('Idle unload (min)'));
  const idle = document.createElement('input');
  idle.type = 'number';
  idle.min = '1';
  idle.step = '1';
  idle.className = 'pe-idle';
  idle.placeholder = 'never';
  idle.value = draft.idle == null ? '' : String(draft.idle);
  idle.disabled = draft.startup !== 'on_demand';
  idle.title = draft.startup === 'on_demand'
    ? 'Minutes without a request before the idle watchdog unloads the backend (empty = stays up)'
    : 'Only applies to on-demand rows';
  idle.addEventListener('input', function () {
    const v = parseInt(idle.value, 10);
    draft.idle = Number.isFinite(v) && v > 0 ? v : null;
  });
  idleLabel.appendChild(idle);
  policy.appendChild(idleLabel);
  return policy;
}

// --------------------------------------------------------------- the log tail
async function showLog(m) {
  const body = bodyFor(m);
  if (!body) return;
  const box = body.querySelector('.model-log');
  box.hidden = false;
  box.innerHTML = '<div class="sheet-row"><span class="sheet-row-label">Log</span>'
    + '<span class="sheet-row-value"></span>'
    + '<button type="button" class="button-tint" data-act="log">' + icon('refresh-cw') + '<span>Refresh</span></button></div>'
    + '<pre class="model-log-tail">Reading…</pre>';
  const pre = box.querySelector('pre');
  try {
    const res = await jsonApi('/admin/api/models/' + encodeURIComponent(m.id) + '/log?limit=200');
    const lines = res.lines || [];
    pre.textContent = lines.length ? lines.join('\n') : 'No log yet — this backend has not started.';
    pre.scrollTop = pre.scrollHeight;
  } catch (exc) {
    if (isAuthError(exc)) return;
    pre.textContent = 'Could not read the log.';
  }
  box.scrollIntoView({ block: 'nearest' });
}

// --------------------------------------------------------------- actions
// Pauses after a Start / Stop / Force stop request (none of them measured; they
// predate their names and are kept as they were):
//  - SETTLE: before re-reading the model list, so the backend's new state has
//    reached the hub (a spawn takes longer to show than a stop).
//  - MEMORY: before the memory re-read, long enough for the backend to take its
//    memory (a start loads the weights) or give it back (a stop).
const START_SETTLE_MS = 800;
const STOP_SETTLE_MS = 400;
const START_MEMORY_MS = 4000;
const STOP_MEMORY_MS = 1500;

/* Memory is a live-probe read (seconds), so it is never polled: it is read on
 * tab open and once after a Start / Stop / Force stop settles, long enough
 * for the backend to take or release its memory. */
function refreshMemorySoon(ms) {
  setTimeout(function () { fetchFleetPlacement(); }, ms);
}

let pendingForceStop = null;

function openForceStopConfirm(m) {
  pendingForceStop = m;
  els.machinesConfirmTitle.textContent = 'Force stop';
  els.machinesConfirmBody.textContent = 'Force stop "' + m.display_name
    + '"? Another process started it, so the hub ends whatever holds its port. Anything it is doing now is lost.';
  els.machinesConfirmBtn.textContent = 'Force stop';
  els.machinesConfirmBtn.disabled = false;
  els.machinesConfirmBtn.dataset.for = 'models';
  els.machinesConfirmDialog.showModal();
}

async function onForceStopConfirmed() {
  const m = pendingForceStop;
  pendingForceStop = null;
  els.machinesConfirmDialog.close();
  if (!m) return;
  try {
    await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/force-stop', {});
    toast('Force-stopped ' + m.display_name, 'good');
    await sleep(STOP_SETTLE_MS);
    fetchModels();
    refreshMemorySoon(STOP_MEMORY_MS);
  } catch (exc) { toast(String(exc.message || exc), 'error'); }
}

async function handleAction(m, act) {
  if (act === 'open') {
    openSelection('model', m.id);
  } else if (act === 'start') {
    try {
      await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/start', {});
      toast('Starting ' + m.display_name + '…', 'good');
      await sleep(START_SETTLE_MS);
      fetchModels();
      refreshMemorySoon(START_MEMORY_MS);
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'stop') {
    try {
      await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/stop', {});
      toast('Stopped ' + m.display_name, 'good');
      await sleep(STOP_SETTLE_MS);
      fetchModels();
      refreshMemorySoon(STOP_MEMORY_MS);
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'force-stop') {
    openForceStopConfirm(m);
  } else if (act === 'ping') {
    try {
      const body = await postJson('/admin/api/models/' + encodeURIComponent(m.id) + '/ping', {});
      toast(m.display_name + ' · ' + body.status + ' · ' + body.latency_ms + ' ms', body.ok ? 'good' : 'error');
    } catch (exc) { toast(String(exc.message || exc), 'error'); }
  } else if (act === 'dictionary') {
    openEditor(m, 'dictionary');
  } else if (act === 'edit-placement') {
    openEditor(m, 'placement');
  } else if (act === 'log') {
    showLog(m);
  }
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

// --------------------------------------------------------------- wiring
function onModelsClick(ev) {
  const btn = ev.target.closest('[data-act]');
  if (!btn || btn.disabled) return;
  const holder = btn.closest('.model-row, .model-body, .wide-detail-head');
  const id = holder && (holder.dataset.id || '');
  const m = id ? findModel(id) : null;
  if (!m) return;
  handleAction(m, btn.dataset.act);
}

export function wireModels() {
  try {
    const stored = localStorage.getItem(MODELS_ACTIVE_ONLY_KEY);
    if (stored !== null) state.modelsActiveOnly = stored === 'true';
  } catch (_) { /* private mode */ }
  renderScope();

  // A memory row opens that machine's running / loadable summary.
  if (els.modelsMemoryList) {
    els.modelsMemoryList.addEventListener('click', function (ev) {
      const main = ev.target.closest('[data-host-id]');
      if (main) openSelection('host', main.dataset.hostId);
    });
  }
  onFleetPlacement(function () { lastListHtml = ''; renderModels(); });
  [els.modelsList, els.modelsDetail, els.modelSheetBody].forEach(function (el) {
    if (el) el.addEventListener('click', onModelsClick);
  });
  if (els.modelSheetCloseBtn) {
    els.modelSheetCloseBtn.addEventListener('click', function () { els.modelSheet.close(); });
  }
  // Closing the sheet (×, Escape) discards an open editor's draft, like any
  // staged editor (design.md dense collection).
  if (els.modelSheet) {
    els.modelSheet.addEventListener('close', function () { els.modelSheetBody.replaceChildren(); });
  }
  if (els.modelsScope) {
    els.modelsScope.addEventListener('click', function (ev) {
      const seg = ev.target.closest('.segmented-item');
      if (!seg) return;
      state.modelsActiveOnly = seg.dataset.scope === 'running';
      try {
        localStorage.setItem(MODELS_ACTIVE_ONLY_KEY, String(state.modelsActiveOnly));
      } catch (_) { /* private mode */ }
      renderModels();
    });
  }
  // Force stop shares the Machines confirm dialog (a vendored modal, never
  // the browser's confirm()); this listener acts only for a Models request.
  if (els.machinesConfirmBtn) {
    els.machinesConfirmBtn.addEventListener('click', function (ev) {
      if (els.machinesConfirmBtn.dataset.for !== 'models') return;
      ev.stopImmediatePropagation();
      onForceStopConfirmed();
    }, true);
  }
  if (els.machinesConfirmDialog) {
    els.machinesConfirmDialog.addEventListener('close', function () {
      delete els.machinesConfirmBtn.dataset.for;
      pendingForceStop = null;
    });
  }
  // Crossing the wide breakpoint swaps the sheet for the detail pane (or back).
  onWideChange(function (wide) {
    if (wide && els.modelSheet && els.modelSheet.open) els.modelSheet.close();
    lastListHtml = '';
    if (state.models) renderModels();
  });
}
