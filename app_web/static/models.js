/* Models tab — per-backend .app-item row (canonical pattern).
 *
 * Process control only: start / stop / ping / force-stop. Per-model log
 * tailing was tried in #10 but pulled back — adopted backends have no
 * captured stdout, and the central Hub log tab already shows every
 * request that flows through the hub. Detailed per-backend telemetry
 * belongs in a future dedicated tab (#4: OpenTelemetry + Langfuse).
 */

import { els, state, MODELS_ACTIVE_ONLY_KEY } from './state.js';
import { jsonApi, postJson, putJson, toast, escapeHtml, fmtGb } from './api.js';
import { mountGlossaryEditor } from './glossary.js';
import { icon } from './_vendored/icons/icons.js';
import { setSwitch } from './_vendored/switch/switch.js';

export async function fetchModels() {
  try {
    const body = await jsonApi('/admin/api/models');
    state.models = body.models || [];
    state.modelsConfig = body.config || null;
    renderConfigChip();
    renderModels();
  } catch (_) { /* ignore */ }
}

/* models.yaml config-version chip (#424) — the HEAD sha of the file, shown
 * in the card header. Every converged hub shows the same sha, so drift
 * between hubs is visible by comparing their /admin pages at a glance. */
function renderConfigChip() {
  const el = els.modelsConfigSha;
  if (!el) return;
  const cfg = state.modelsConfig;
  const sha = cfg && cfg.sha && cfg.sha !== 'unknown' ? cfg.sha : '';
  el.textContent = sha ? 'Config version ' + sha : '';
  el.title = sha
    ? 'models.yaml config version (HEAD sha of config/models.yaml) — the same on every converged hub'
    : '';
}

// A row counts as "active" only if it's a controllable backend that's
// currently running/adopted. Claude/Gemini are excluded outright — they're
// subscription-backed with no on/off state, always "on", so listing them
// here is just noise rather than a signal (#266).
function isActive(m) {
  return m.controllable && (m.ownership === 'ours' || m.ownership === 'external');
}

function renderModels() {
  const root = els.modelsList;
  if (!root) return;
  const models = state.models || [];
  const visible = state.modelsActiveOnly ? models.filter(isActive) : models;

  if (els.modelsEmpty) {
    els.modelsEmpty.hidden = visible.length > 0;
    const msg = els.modelsEmpty.querySelector('.empty-state-message');
    if (msg) {
      msg.innerHTML = models.length === 0
        ? 'No models enabled for this host — check <code>config/models.yaml</code>.'
        : 'No active models right now — turn off “Active only” to see the full list.';
    }
  }

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
  visible.forEach(function (m) {
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
  // The badge lives OUTSIDE .app-title: that span ellipsises long display
  // names (whisper-large-v3-turbo, gemma4-26b-a4b-it), and a pill nested
  // inside it gets pushed past the clip boundary and vanishes. As a sibling
  // in the title row it is never clipped. PID/host details moved to the
  // meta line (#215) — as title-row extras they crushed the name on phones.
  titleRow.innerHTML =
    '<span class="app-title"><span class="app-name">' + escapeHtml(m.display_name) + '</span></span>' + badge(m);

  // An open "More actions" menu must survive the 5 s poll's rebuild.
  const menuWasOpen = !!li.querySelector(':scope > .app-main .row-menu[open]');
  const icons = document.createElement('div');
  icons.className = 'app-icons';
  const buttons = [];
  if (m.controllable) {
    // One power control (action-row: a single leading toggle): Start while the
    // model is down, Stop once this hub owns the process.
    buttons.push(ownership === 'ours'
      ? { act: 'stop',  glyph: icon('square'), label: 'Stop' }
      : { act: 'start', glyph: icon('play'), label: 'Start', disabled: ownership !== 'none' });
  }
  buttons.forEach(function (b) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ghost-btn';
    btn.dataset.act = b.act;
    btn.disabled = !!b.disabled;
    btn.title = b.label;
    btn.setAttribute('aria-label', b.label);
    btn.innerHTML = b.glyph + escapeHtml(b.label);
    btn.addEventListener('click', function () { handleAction(m, b.act); });
    icons.appendChild(btn);
  });

  // Everything else lives in one trailing menu (action-row: destructive and
  // rarely-used actions stay out of the row): force-stop of an adopted
  // process, the shared transcription dictionary (any whisper row opens the
  // same editor), and the placement editor on the write host (#424).
  const panelOpen = !!li.querySelector(':scope > .glossary-panel:not([hidden])');
  const editorOpen = !!li.querySelector(':scope > .placement-editor');
  const menuItems = [];
  // Ping is a word in the menu, not a bare signal-bars glyph on the row (#647).
  // No ping for the TypeSafe tile (#611): every probe is a billed vendor
  // evaluation — the Playground's Decision card is where Jev gets exercised.
  if (m.backend !== 'typesafe') {
    menuItems.push({
      act: 'ping', glyph: icon('signal'), label: 'Ping',
      disabled: !reachable && m.backend !== 'claude' && m.backend !== 'gemini',
    });
  }
  if (adopted) {
    menuItems.push({ act: 'force-stop', glyph: icon('skull'), label: 'Force stop', danger: true });
  }
  if (m.backend === 'whisper') {
    menuItems.push({ act: 'dictionary', glyph: icon('book-open'), label: 'Transcription dictionary', active: panelOpen });
  }
  if (canEditPlacement(m)) {
    menuItems.push({ act: 'edit-placement', glyph: icon('wrench'), label: 'Edit placement', active: editorOpen, cls: 'placement-edit-btn' });
  }
  if (menuItems.length) icons.appendChild(buildRowMenu(m, menuItems, menuWasOpen));
  titleRow.appendChild(icons);
  main.appendChild(titleRow);

  const meta = document.createElement('div');
  meta.className = 'app-meta';
  // Host note (#181) + adopted PID live here with the port (#215): in the
  // title row they wrapped the tile to two title lines and squeezed the
  // name to nothing on a phone ("parakeet … on mac-mini-m4").
  meta.textContent =
    m.backend +
    // Third-party egress (#611) — name where the content goes.
    (m.backend === 'typesafe' ? ' · api.typesafe.ai (US)' : '') +
    (m.port ? ' · :' + m.port : '') +
    (remote ? ' on ' + m.host : '') +
    // Dynamic host-chain fallback (#342): flag a model currently served
    // off its preferred host, naming where it normally lives.
    (m.failover ? ' · failover (prefers ' + m.preferred_host + ')' : '') +
    (m.pid && adopted ? ' · PID ' + m.pid : '') +
    // Resolved TTS device (cpu/cuda/mps), reported once the backend has
    // finished loading and answered its own /health — see
    // app_web/routers/models.py's _probe_device (#371). Omitted (not
    // guessed) for stopped/loading rows and backends with no device concept.
    (m.device ? ' · ' + m.device : '') +
    (m.aliases && m.aliases.length ? ' · ' + m.aliases.join(', ') : '') +
    // Model footprint (#436) — the registry's static est_vram_mb, the same
    // figure the removed #434 size chip showed, back as a meta-line suffix
    // per user review. Truthy-only: shown only for rows that actually load
    // a model process of their own — virtual aliases sharing another row's
    // process (qwen35_4b_nothink) and CPU/ANE rows declare 0 and show
    // nothing; subscription rows carry no placement block at all.
    (m.placement && m.placement.est_vram_mb
      ? ' · ~' + fmtGb(m.placement.est_vram_mb) : '');
  main.appendChild(meta);

  // Placement card (#423) — declared intent under the runtime meta; the
  // edit affordance (#424) rides it on the write host.
  const placement = buildPlacement(m);
  if (placement) main.appendChild(placement);

  // Keep .app-main as the first child so any inline panel (dictionary or
  // placement editor — both siblings, so they survive the 5 s poll) stays
  // below it.
  const panel = li.querySelector(':scope > .glossary-panel, :scope > .placement-editor');
  if (panel) {
    li.insertBefore(main, panel);
  } else if (main.parentNode !== li) {
    li.appendChild(main);
  }
}

/* ---------- read-only placement card (#423) ----------
 * Renders the declared placement *intent* from config/models.yaml (the Phase 1
 * #422 registry fields the API now carries per row): the host chain in
 * priority order with the effective owner highlighted and cpu-resident tiers
 * marked (effective device per host — #434), and a startup-policy badge that
 * reads the live state for on-demand rows. No size chip and no budget bar
 * (#434): host capacity lives on the Fleet summary card, and the old
 * per-card bar repeated the same machine fact on every card.
 * Subscription rows (claude/gemini) carry no `placement` key and get nothing.
 * On the single write host (#424) the row's "More actions" menu opens the
 * inline editor. */
function buildPlacement(m) {
  const p = m.placement;
  if (!p) return null;
  const wrap = document.createElement('div');
  wrap.className = 'placement';

  const line = document.createElement('div');
  line.className = 'placement-line';

  const chain = p.chain || [];
  if (chain.length) {
    const chainEl = document.createElement('span');
    chainEl.className = 'placement-chain';
    chain.forEach(function (entry, i) {
      if (i) {
        const sep = document.createElement('span');
        sep.className = 'placement-arrow';
        sep.innerHTML = icon('chevron-right');
        sep.setAttribute('aria-hidden', 'true');
        chainEl.appendChild(sep);
      }
      const owner = entry.id === m.host;
      const pill = document.createElement('span');
      pill.className = 'place-pill' + (owner ? ' owner' : '');
      pill.textContent = entry.id + (entry.cpu ? ' · cpu' : '');
      pill.title = (owner ? 'Active owner'
        : (i === 0 ? 'Preferred host' : 'Fallback host'))
        + (entry.cpu ? ' — CPU-resident on this host (holds no GPU VRAM)' : '');
      chainEl.appendChild(pill);
    });
    line.appendChild(chainEl);
  }

  line.appendChild(startupBadge(m, p));

  // #434: no per-card size chip and no host budget bar — the cards stay
  // light; sizes/capacity live in the placement editor and the Fleet summary.

  wrap.appendChild(line);
  return wrap;
}

/* Trailing "More actions" menu of a model row — one <details> whose summary is
 * the row's single accessory. Items are plain buttons carrying data-act, routed
 * through handleAction like the row's visible controls. The edit item is only
 * offered where this hub may write (tower — the single-writer contract) and the
 * row's placement is its own (#424). */
function buildRowMenu(m, items, open) {
  const menu = document.createElement('details');
  menu.className = 'row-menu';
  menu.open = !!open;
  const toggle = document.createElement('summary');
  toggle.className = 'row-menu-toggle';
  toggle.title = 'More actions';
  toggle.setAttribute('aria-label', 'More actions');
  toggle.innerHTML = icon('chevron-down');
  menu.appendChild(toggle);
  const list = document.createElement('div');
  list.className = 'row-menu-list';
  list.setAttribute('role', 'menu');
  items.forEach(function (it) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'row-menu-item' + (it.danger ? ' danger' : '') +
      (it.active ? ' active' : '') + (it.cls ? ' ' + it.cls : '');
    btn.dataset.act = it.act;
    btn.disabled = !!it.disabled;
    btn.setAttribute('role', 'menuitem');
    btn.innerHTML = it.glyph + '<span>' + escapeHtml(it.label) + '</span>';
    btn.addEventListener('click', function () {
      menu.open = false;
      handleAction(m, it.act);
    });
    list.appendChild(btn);
  });
  menu.appendChild(list);
  return menu;
}

/* Startup policy badge: `eager` is the quiet default; an on-demand row reads
 * its live state honestly — "loaded" while the backend answers, else
 * "idle-unloaded" (stopped by the idle watchdog or never yet demanded). */
function startupBadge(m, p) {
  const el = document.createElement('span');
  if (p.startup === 'on_demand') {
    const idleNote = p.idle_unload_minutes
      ? 'Loads on first request; unloads after ' + p.idle_unload_minutes + ' min idle'
      : 'Loads on first request; stays up until stopped';
    if (m.reachable) {
      el.className = 'badge good';
      el.textContent = 'on-demand · loaded';
    } else {
      el.className = 'badge';
      el.textContent = 'on-demand · idle-unloaded';
    }
    el.title = idleNote;
  } else {
    el.className = 'badge';
    el.textContent = 'eager';
    el.title = 'Always-on: autostarted and kept resident';
  }
  return el;
}

/* ---------- placement editor (#424) ----------
 * Inline panel under a model row that edits its declared placement — hosts
 * chain (reorder / add / remove / cpu tier), startup policy, idle-unload
 * window — and PUTs it to /admin/api/models/<id>/placement, which validates
 * hard (schema + the #375 VRAM budget) and then writes through to git
 * (comment-preserving models.yaml edit, config-bot commit, push).
 *
 * Same sibling-panel recipe as the dictionary editor: the panel lives NEXT
 * to .app-main inside the <li>, so the 5 s poll's .app-main rebuild leaves
 * an open editor (and its unsaved draft) intact. Only rendered where the
 * server says this hub may write (config.write_enabled — tower only). */
function canEditPlacement(m) {
  return !!(state.modelsConfig && state.modelsConfig.write_enabled &&
    m.placement && m.placement.editable !== false);
}

function togglePlacementEditor(m) {
  const root = els.modelsList;
  if (!root) return;
  const li = root.querySelector('.app-item[data-id="' + cssEscape(m.id) + '"]');
  if (!li) return;
  const alreadyOpen = !!li.querySelector(':scope > .placement-editor');
  closeAllPlacementEditors(root);
  if (alreadyOpen) return;
  const panel = document.createElement('div');
  panel.className = 'placement-editor';
  li.appendChild(panel);
  mountPlacementEditor(panel, m);
  const btn = li.querySelector('.row-menu-item[data-act="edit-placement"]');
  if (btn) btn.classList.add('active');
}

function closeAllPlacementEditors(root) {
  root.querySelectorAll('.placement-editor').forEach(function (p) { p.remove(); });
  root.querySelectorAll('.row-menu-item[data-act="edit-placement"].active')
    .forEach(function (b) { b.classList.remove('active'); });
}

function mountPlacementEditor(panel, m) {
  const p = m.placement || {};
  const draft = {
    chain: (p.chain || []).map(function (e) { return { id: e.id, cpu: !!e.cpu }; }),
    startup: p.startup === 'on_demand' ? 'on_demand' : 'eager',
    idle: p.idle_unload_minutes || null,
  };
  let errorMsg = '';
  let saving = false;

  function fleetHosts() {
    const cfg = state.modelsConfig;
    return (cfg && cfg.fleet_hosts) || [];
  }

  function move(i, delta) {
    const j = i + delta;
    if (j < 0 || j >= draft.chain.length) return;
    const tmp = draft.chain[i];
    draft.chain[i] = draft.chain[j];
    draft.chain[j] = tmp;
    render();
  }

  async function save() {
    errorMsg = '';
    saving = true;
    render();
    try {
      const body = await putJson(
        '/admin/api/models/' + encodeURIComponent(m.id) + '/placement',
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
      closeAllPlacementEditors(els.modelsList);
      fetchModels();
    } catch (exc) {
      // Inline, not just a toast: the validation detail (e.g. the VRAM
      // overcommit arithmetic) is the whole point of the rejection.
      errorMsg = String(exc.message || exc);
      saving = false;
      render();
    }
  }

  function render() {
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
    draft.chain.forEach(function (entry, i) {
      const row = document.createElement('div');
      row.className = 'pe-host-row';
      row.dataset.host = entry.id;

      const name = document.createElement('span');
      name.className = 'pe-host-name';
      name.textContent = entry.id;
      row.appendChild(name);

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
      row.appendChild(cpuLabel);

      const controls = [
        { cls: 'pe-up', glyph: 'chevron-up', label: 'Move up', disabled: i === 0, fn: function () { move(i, -1); } },
        { cls: 'pe-down', glyph: 'chevron-down', label: 'Move down', disabled: i === draft.chain.length - 1, fn: function () { move(i, 1); } },
        { cls: 'pe-remove', glyph: 'x', label: 'Remove host', danger: true, fn: function () { draft.chain.splice(i, 1); render(); } },
      ];
      controls.forEach(function (c) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'icon-btn ' + c.cls + (c.danger ? ' danger' : '');
        b.title = c.label;
        b.setAttribute('aria-label', c.label);
        b.disabled = !!c.disabled;
        b.innerHTML = icon(c.glyph);
        b.addEventListener('click', c.fn);
        row.appendChild(b);
      });
      list.appendChild(row);
    });
    panel.appendChild(list);

    const inChain = {};
    draft.chain.forEach(function (e) { inChain[e.id] = true; });
    const addable = fleetHosts().filter(function (h) { return !inChain[h.id]; });
    if (addable.length) {
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
        draft.chain.push({ id: sel.value, cpu: false });
        render();
      });
      addRow.appendChild(sel);
      addRow.appendChild(addBtn);
      panel.appendChild(addRow);
    }

    const policyTitle = document.createElement('div');
    policyTitle.className = 'opt-group-title';
    policyTitle.textContent = 'Lifecycle';
    panel.appendChild(policyTitle);

    const policy = document.createElement('div');
    policy.className = 'pe-policy';

    const startupLabel = document.createElement('label');
    startupLabel.className = 'pe-field';
    startupLabel.appendChild(document.createTextNode('Startup'));
    const startupSel = document.createElement('select');
    startupSel.className = 'pe-startup';
    [
      { v: 'eager', t: 'eager — always on' },
      { v: 'on_demand', t: 'on-demand — load on first request' },
    ].forEach(function (o) {
      const opt = document.createElement('option');
      opt.value = o.v;
      opt.textContent = o.t;
      if (draft.startup === o.v) opt.selected = true;
      startupSel.appendChild(opt);
    });
    startupSel.addEventListener('change', function () {
      draft.startup = startupSel.value;
      render();
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
    panel.appendChild(policy);

    const err = document.createElement('div');
    err.className = 'pe-error';
    err.setAttribute('role', 'alert');
    err.hidden = !errorMsg;
    err.textContent = errorMsg;
    panel.appendChild(err);

    const actions = document.createElement('div');
    actions.className = 'pe-actions';
    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'ghost-btn primary pe-save';
    saveBtn.innerHTML = icon('save') + (saving ? 'Saving…' : 'Save & commit');
    saveBtn.disabled = saving;
    saveBtn.addEventListener('click', save);
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'ghost-btn pe-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () {
      closeAllPlacementEditors(els.modelsList);
    });
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    panel.appendChild(actions);
  }

  render();
}

function badge(m) {
  if (m.backend === 'typesafe' && !m.key_configured) {
    return ' <span class="badge warn" title="Set TYPESAFE_API_KEY in the hub’s .env">no key</span>';
  }
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
  } else if (act === 'edit-placement') {
    togglePlacementEditor(m);
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
  const btn = li.querySelector('.row-menu-item[data-act="dictionary"]');
  if (btn) btn.classList.add('active');
}

function closeAllDictionaryPanels(root) {
  root.querySelectorAll('.glossary-panel').forEach(function (p) { p.remove(); });
  root.querySelectorAll('.row-menu-item[data-act="dictionary"].active')
    .forEach(function (b) { b.classList.remove('active'); });
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

/* escapeHtml lives in api.js (sibling dedup, #211). */

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

// "Active models only" switch — the first row of the card body (#266), a
// vendored switch rather than a control in the collapse-summary, where a nested
// control makes every tap ambiguous between toggling the filter and the card.
// Persisted like home-automation Plugs' show-hidden localStorage flag.
function renderActiveToggle() {
  const btn = els.modelsActiveToggle;
  if (!btn) return;
  setSwitch(btn, state.modelsActiveOnly);
}

export function wireModels() {
  try {
    const stored = localStorage.getItem(MODELS_ACTIVE_ONLY_KEY);
    if (stored !== null) state.modelsActiveOnly = stored === 'true';
  } catch (_) { /* private mode */ }
  renderActiveToggle();

  // A row's "More actions" menu closes on any tap outside it.
  document.addEventListener('click', function (ev) {
    if (!els.modelsList || ev.target.closest('.row-menu')) return;
    els.modelsList.querySelectorAll('.row-menu[open]').forEach(function (d) { d.open = false; });
  });

  if (els.modelsActiveToggle) {
    els.modelsActiveToggle.addEventListener('click', function () {
      state.modelsActiveOnly = !state.modelsActiveOnly;
      try {
        localStorage.setItem(MODELS_ACTIVE_ONLY_KEY, String(state.modelsActiveOnly));
      } catch (_) { /* private mode */ }
      renderActiveToggle();
      renderModels();
    });
  }
}
