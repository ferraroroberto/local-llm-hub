/* Models tab — the memory glance and each machine's summary (issue #354,
 * read-only since #431; meters since #737).
 *
 * Over GET /admin/api/fleet-placement. The glance card draws one row per
 * fleet host with a memory meter: live GPU memory where the machine reports
 * it, else the estimated footprint of the models placed there against the
 * declared ceiling (marked ~), and RAM on a machine with no discrete GPU —
 * the same decision capacityHtml makes. A probe that has not answered yet,
 * or failed, reads "not confirmed", never an empty meter that would look like
 * free memory. The rows are drawn at once from the models payload's fleet
 * hosts, so the card never changes size when the probe lands.
 *
 * Tapping a row opens that machine's summary (hostBodyEl): the models running
 * there now (live state), the models it could load (chain members, incl.
 * on-demand rows), the capacity line, the advisory over-capacity warning and
 * the deferred-apply note of a machine that is off.
 *
 * The probe runs live peer checks (seconds), so it is never polled: it is read
 * on tab open and once after a Start / Stop settles (models.js). Same five
 * async lifecycle states as the Machines tab (design.md).
 */

import { els, state } from './state.js';
import { jsonApi, escapeHtml, fmtGb, fmtGbValue, isAuthError } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { row } from './rows.js';

const listeners = [];

/* models.js repaints the tab (rows, header line, detail) when a read lands. */
export function onFleetPlacement(fn) { listeners.push(fn); }

export async function fetchFleetPlacement() {
  try {
    const body = await jsonApi('/admin/api/fleet-placement');
    state.fleetPlacement = body;
    state.fleetPlacementState = (body.hosts || []).length ? 'ready' : 'empty';
    state.fleetPlacementUpdated = Date.now();
  } catch (exc) {
    if (isAuthError(exc)) return;
    // Good data from an earlier fetch → stale (keep + label it), else error.
    state.fleetPlacementState = state.fleetPlacement ? 'stale' : 'error';
  }
  if (listeners.length) listeners.forEach(function (fn) { fn(); });
  else renderMemory();
}

function hosts() {
  return (state.fleetPlacement && state.fleetPlacement.hosts) || [];
}

function findHost(id) {
  return hosts().find(function (h) { return h.id === id; });
}

export function hostLabel(id) {
  const h = findHost(id);
  return (h && (h.display_name || h.id)) || id || '';
}

export function overCapacityHosts() {
  return hosts().filter(function (h) { return h.capacity_warning; }).length;
}

function hostGlyph(host) {
  return host.icon || (host.local ? 'monitor' : 'server');
}

// MB → a whole-GB label for RAM totals (128 GB, not 128.0 GB).
function fmtGbWhole(mb) {
  return Math.round(Number(mb || 0) / 1024) + ' GB';
}

// -------------------------------------------------------------- the meter
/* The figure a host's meter shows, or null when nothing is confirmed:
 * live GPU used/total; else the estimate against the declared GPU ceiling;
 * else (no discrete GPU) live RAM; else the estimate against the RAM total. */
function meterFigure(host) {
  const gpu = host.gpu || null;
  if (gpu && gpu.used_mb != null && gpu.total_mb) {
    return { label: 'GPU', used: gpu.used_mb / 1024, total: gpu.total_mb / 1024, est: false };
  }
  if (host.vram_mb) {
    return { label: 'GPU', used: (host.est_vram_mb || 0) / 1024, total: host.vram_mb / 1024, est: true };
  }
  const ram = host.ram || null;
  if (ram && ram.used_gb != null && ram.total_gb) {
    return { label: 'RAM', used: Number(ram.used_gb), total: Number(ram.total_gb), est: false };
  }
  if (host.ram_mb && host.est_vram_mb) {
    return { label: 'RAM', used: host.est_vram_mb / 1024, total: host.ram_mb / 1024, est: true };
  }
  return null;
}

function meterLevel(pct) {
  if (pct >= 90) return 'high';
  if (pct >= 75) return 'warn';
  return 'ok';
}

function meterHtml(fig) {
  if (!fig) return '<span class="action-row-trail memory-meter is-unknown">not confirmed</span>';
  const pct = Math.max(0, Math.min(100, fig.total ? (fig.used / fig.total) * 100 : 0));
  return '<span class="action-row-trail memory-meter" data-level="' + meterLevel(pct) + '">'
    + '<span class="meter-track" aria-hidden="true"><span class="meter-fill" style="width:' + pct.toFixed(0) + '%"></span></span>'
    + '<span class="meter-pct">' + (fig.est ? '~' : '') + Math.round(pct) + '%</span></span>';
}

function figureText(fig) {
  if (!fig) return 'Not confirmed';
  return fig.label + ' ' + (fig.est ? '~' : '') + fmtGbValue(fig.used) + ' / ' + fmtGbValue(fig.total) + ' GB';
}

function memoryRow(host, confirmed) {
  const fig = confirmed ? meterFigure(host) : null;
  const running = (host.running || []).length;
  const meta = !confirmed ? ''
    : (!host.local && !host.reachable ? 'off' : running + ' running');
  return row({
    cls: 'memory-row' + (confirmed && host.capacity_warning ? ' is-over' : ''),
    attrs: ' data-host-id="' + escapeHtml(host.id) + '"',
    glyph: hostGlyph(host),
    title: host.display_name || host.id,
    lead: figureText(fig),
    meta: meta,
    mainAttrs: ' data-host-id="' + escapeHtml(host.id) + '" aria-haspopup="dialog"',
    trail: meterHtml(fig),
  });
}

/* Over capacity is not a chip on the row (it would crowd the meter at
 * 390px): the meter's danger fill and percent carry it, the header line names
 * it, and the machine's summary shows the advisory warning. */

/* The glance rows: the probed hosts once the read has landed, else the fleet
 * hosts the models payload names, each "not confirmed" until then. */
export function renderMemory() {
  const list = els.modelsMemoryList;
  if (!list) return;
  const ds = state.fleetPlacementState;
  const confirmed = ds === 'ready' || ds === 'stale';
  let rows = hosts();
  if (!confirmed || !rows.length) {
    const cfg = state.modelsConfig;
    const declared = (cfg && cfg.fleet_hosts) || [];
    rows = rows.length ? rows : declared.map(function (h) { return { id: h.id }; });
  }
  list.innerHTML = rows.map(function (h) { return memoryRow(h, confirmed); }).join('');
  const note = els.fleetPlacementStaleNote;
  if (!note) return;
  if (ds === 'stale' && state.fleetPlacementUpdated) {
    const t = new Date(state.fleetPlacementUpdated);
    note.textContent = 'Last updated ' + String(t.getHours()).padStart(2, '0') + ':'
      + String(t.getMinutes()).padStart(2, '0') + ' · live data unavailable';
    note.hidden = false;
  } else if (ds === 'error') {
    note.textContent = 'Memory not confirmed — the fleet summary could not be read.';
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

// -------------------------------------------------------------- a machine's summary
/* A small, low-emphasis device hint for CPU-resident rows (piper, -ng
 * whisper rows, a chain's degraded cpu tier — #387/#431): a model that
 * contributes 0 to the VRAM sum reads as intentionally exempt rather than
 * an omission. */
function deviceHint(model) {
  if (!model || !model.device) return '';
  return ' <span class="muted small">· ' + escapeHtml(model.device) + '</span>';
}

/* The advisory VRAM-overcommit warning (issue #375): only when the host
 * declares a `vram_mb` ceiling and the estimated footprint of its desired +
 * running models exceeds it — a heads-up, never a hard block. */
function capacityWarnEl(host) {
  if (!host.capacity_warning) return null;
  const p = document.createElement('p');
  p.className = 'fleet-host-note fleet-capacity-warn small';
  p.title = 'Estimated GPU-VRAM footprint of this host’s desired + running models '
    + '(CPU-resident rows excluded) exceeds its ' + fmtGb(host.vram_mb)
    + ' ceiling. Advisory only.';
  p.innerHTML = icon('triangle-alert')
    + '<span>Over VRAM capacity — ~' + fmtGb(host.est_vram_mb)
    + ' est / ' + fmtGb(host.vram_mb) + ' GPU</span>';
  return p;
}

/* One model's name in a summary list, with a distinct "external" marker for a
 * live backend served by a process the hub adopted but never spawned
 * (voice-transcriber's whisper-server on the mutex-shared :8090 — #431). */
function modelNameHtml(model, id, external) {
  const name = model ? model.display_name : id;
  let html = '<span class="fleet-model-name">' + escapeHtml(name) + '</span>' + deviceHint(model);
  if (external) {
    html += ' <span class="muted small" title="Served by an external process the hub'
      + ' adopted on a mutex-shared port — not started by this hub">· external</span>';
  }
  return html;
}

function modelLine(innerHtml) {
  return '<span class="fleet-model-line muted small">' + innerHtml + '</span>';
}

function summaryRow(label, valueHtml, title) {
  const li = document.createElement('li');
  li.className = 'startup-row';
  if (title) li.title = title;
  li.innerHTML =
    '<span class="startup-row-label muted small">' + escapeHtml(label) + '</span>'
    + '<span class="roles-row-value">' + valueHtml + '</span>';
  return li;
}

/* Capacity line (#434, live-first since #436): `GPU <used> / <total> · RAM
 * <used> / <total> GB` from the same live probes the Machines tab reads; the
 * `~` estimate vs the declared ceiling only where no live GPU figure exists
 * (the system-RAM total stands in on a unified-memory host), and the declared
 * RAM total where no live snapshot rides the API. */
function capacityHtml(host) {
  const live = host.ram || null;
  const gpu = host.gpu || null;
  const ramTotalMb = live && live.total_gb ? live.total_gb * 1024 : (host.ram_mb || 0);
  const parts = [];
  if (gpu && gpu.used_mb != null && gpu.total_mb) {
    parts.push('GPU ' + fmtGbValue(gpu.used_mb / 1024) + ' / '
      + fmtGbValue(gpu.total_mb / 1024) + ' GB');
  } else {
    const gpuCeilMb = host.vram_mb || ramTotalMb;
    if (host.est_vram_mb || gpuCeilMb) {
      parts.push('GPU ~' + fmtGb(host.est_vram_mb)
        + (gpuCeilMb ? ' / ' + fmtGb(gpuCeilMb) : ''));
    }
  }
  if (live && live.used_gb != null && live.total_gb) {
    parts.push('RAM ' + fmtGbValue(Number(live.used_gb)) + ' / '
      + fmtGbValue(Number(live.total_gb)) + ' GB');
  } else if (ramTotalMb) {
    parts.push('RAM ' + fmtGbWhole(ramTotalMb));
  }
  return parts.length
    ? '<span class="muted small">' + parts.join(' · ') + '</span>'
    : '';
}

function buildHostGroup(host) {
  const group = document.createElement('div');
  group.className = 'fleet-host';

  // Whether the machine is up is the Machines tab's fact; here only which
  // one you are on, in plain words (an off machine gets its note below).
  if (host.local) {
    const where = document.createElement('p');
    where.className = 'sheet-meta fleet-host-state';
    where.textContent = 'This machine';
    group.appendChild(where);
  }

  const warn = capacityWarnEl(host);
  if (warn) group.appendChild(warn);

  const eligible = host.eligible || [];

  // A host with nothing placeable — an honest note instead of empty rows.
  if (!eligible.length) {
    const note = document.createElement('p');
    note.className = 'fleet-host-note muted small';
    // Name the control that changes it (J-09, #647).
    note.textContent = 'No models placeable on this machine. Add it to a model’s host chain with Edit placement on the Models list.';
    group.appendChild(note);
    return group;
  }

  // A manageable host that's powered off: its desired set is kept in the
  // registry and applies on power-up — a deferred state, never a failure.
  if (!host.local && !host.reachable) {
    const note = document.createElement('p');
    note.className = 'fleet-host-note muted small';
    note.textContent = 'Off — desired models apply when this machine powers up.';
    group.appendChild(note);
  }

  const byId = {};
  eligible.forEach(function (m) { byId[m.id] = m; });
  const running = host.running || [];
  const runningSet = {};
  running.forEach(function (id) { runningSet[id] = true; });
  const externalSet = {};
  (host.external || []).forEach(function (id) { externalSet[id] = true; });
  const loadable = eligible.filter(function (m) { return !runningSet[m.id]; });

  const list = document.createElement('ul');
  list.className = 'startup-list';

  const runningHtml = running.length
    ? running.map(function (id) {
        return modelLine(modelNameHtml(byId[id], id, !!externalSet[id]));
      }).join('')
    : '<span class="muted small">none</span>';
  list.appendChild(summaryRow('Running', runningHtml,
    'Models serving on this machine right now (live state)'));

  if (loadable.length) {
    const loadableHtml = loadable.map(function (m) {
      return modelLine(escapeHtml(m.display_name) + deviceHint(m));
    }).join('');
    list.appendChild(summaryRow('Loadable', loadableHtml,
      'Enabled chain members this machine could serve but is not running — '
      + 'includes on-demand rows that load on first request'));
  }

  const cap = capacityHtml(host);
  if (cap) {
    list.appendChild(summaryRow('Capacity', cap,
      'GPU: live used/total from the same probe the Machines tab reads, where the '
      + 'machine reports one; otherwise the ~static footprint estimate of desired + '
      + 'running models (CPU-resident rows excluded) vs the declared ceiling — '
      + 'system-RAM total on unified-memory hosts. '
      + 'RAM: live used/total where the machine answers stats, declared total otherwise.'));
  }

  group.appendChild(list);
  return group;
}

/* One machine's summary for the sheet / wide detail pane. */
export function hostBodyEl(id) {
  const ds = state.fleetPlacementState;
  const host = findHost(id);
  if (!host) {
    if (ds === 'error') {
      return emptyStateEl('triangle-alert', 'Could not read the fleet summary.', {
        actionLabel: 'Retry',
        onAction: function () {
          state.fleetPlacementState = 'loading';
          fetchFleetPlacement();
        },
      });
    }
    return emptyStateEl('server', 'Reading the fleet summary…');
  }
  const wrap = document.createElement('div');
  wrap.className = 'host-body';
  wrap.appendChild(buildHostGroup(host));
  return wrap;
}
