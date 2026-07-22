/* Models tab — "Fleet placement" card (issue #354).
 *
 * The fleet-wide sibling of the local Startup card: a per-machine grid over
 * GET/PATCH /admin/api/fleet-placement — the always-on control plane's desired
 * state (#353). Each host is a group; each model that host can launch is a row
 * with the vendored fleet switch. Toggling a switch writes the fleet's *desired*
 * placement; the tower's reconcile loop converges the fleet (starting a newly
 * placed model, waking an offline satellite, stopping an un-placed one).
 *
 * A powered-off machine still renders its models with working toggles — desired
 * placement is editable while a host is offline and applies when it powers up,
 * so an unreachable host is a deferred-apply state, never an error. Same five
 * async lifecycle states as the Machines tab (design.md): loading / ready /
 * empty / stale / error.
 */

import { els, state } from './state.js';
import { jsonApi, patchJson, toast, escapeHtml } from './api.js';
import { switchEl, setSwitch } from './_vendored/switch/switch.js';
import { icon } from './_vendored/icons/icons.js';
import { emptyStateEl } from './_vendored/empty-state/empty-state.js';

// A newly-placed model needs the reconcile pass a beat to start it before the
// running badge can flip — refresh once shortly after a successful toggle.
const RECONCILE_SETTLE_MS = 1000;

export async function fetchFleetPlacement() {
  try {
    const body = await jsonApi('/admin/api/fleet-placement');
    state.fleetPlacement = body;
    state.fleetPlacementState = (body.hosts || []).length ? 'ready' : 'empty';
    state.fleetPlacementUpdated = Date.now();
    renderFleetPlacement();
  } catch (exc) {
    if (String(exc.message) === 'auth required') return;
    // Good data from an earlier fetch → stale (keep + label it), else error.
    state.fleetPlacementState = state.fleetPlacement ? 'stale' : 'error';
    renderFleetPlacement();
  }
}

// -------------------------------------------------------------------- render
function placedList(hostId) {
  const p = (state.fleetPlacement && state.fleetPlacement.placement) || {};
  return (p[hostId] || []).slice();
}

/* The live running badge for one model on one host. Only meaningful once the
 * model is placed — an un-placed row carries no badge (its switch is the whole
 * signal). Reachable-but-not-running is "pending" (reconcile will start it);
 * placed on an offline host is "deferred" until it powers up. */
function modelBadge(host, modelId) {
  const placed = placedList(host.id).includes(modelId);
  if (!placed) return '';
  if ((host.running || []).includes(modelId)) return ' <span class="badge good">running</span>';
  if (host.reachable) return ' <span class="badge warn">pending</span>';
  return ' <span class="badge">deferred</span>';
}

function hostChip(host) {
  if (host.local) return { cls: 'good', label: 'This machine' };
  if (host.reachable) return { cls: 'good', label: 'Online' };
  return { cls: '', label: 'Offline' };
}

function hostGlyph(host) {
  return host.icon || (host.local ? 'monitor' : 'server');
}

function buildModelRow(host, model) {
  const li = document.createElement('li');
  li.className = 'startup-row';

  const label = document.createElement('span');
  label.className = 'startup-row-label';
  label.innerHTML = '<span class="fleet-model-name">' + escapeHtml(model.display_name) + '</span>'
    + modelBadge(host, model.id);
  li.appendChild(label);

  const on = placedList(host.id).includes(model.id);
  const sw = switchEl(on, {
    label: 'Place ' + model.display_name + ' on ' + (host.display_name || host.id),
    onToggle: function (next, btn) {
      if (btn.disabled) return;
      btn.disabled = true;
      applyPlacement(host, model.id, next)
        .then(function () { setSwitch(btn, next); })
        .catch(function (exc) {
          setSwitch(btn, on);
          toast(String(exc.message || exc), 'error');
        })
        .finally(function () { btn.disabled = false; });
    },
  });
  li.appendChild(sw);
  return li;
}

/* PATCH the fleet's desired placement for one host, then reflect it locally so
 * the badge/switch stay in sync, and refresh once so the reconcile result (a
 * model starting) shows up. Placement is desired-state — not freshness-
 * sensitive like a reboot — so editing it is allowed even while status is
 * stale. */
function applyPlacement(host, modelId, next) {
  const cur = placedList(host.id);
  const desired = next
    ? (cur.includes(modelId) ? cur : cur.concat([modelId]))
    : cur.filter(function (x) { return x !== modelId; });

  return patchJson('/admin/api/fleet-placement', { [host.id]: desired }).then(function (body) {
    if (body && body.placement) state.fleetPlacement.placement = body.placement;
    else if (state.fleetPlacement) state.fleetPlacement.placement[host.id] = desired;
    // Let the reconcile pass act, then repaint the running badges.
    setTimeout(function () { fetchFleetPlacement(); }, RECONCILE_SETTLE_MS);
  });
}

function buildHostGroup(host) {
  const group = document.createElement('div');
  group.className = 'fleet-host';

  const head = document.createElement('div');
  head.className = 'fleet-host-head';
  const chip = hostChip(host);
  head.innerHTML =
    '<span class="fleet-host-name">' + icon(hostGlyph(host))
    + '<span>' + escapeHtml(host.display_name || host.id) + '</span></span>'
    + '<span class="hub-live-status ' + chip.cls + '"><span class="dot"></span><span>'
    + escapeHtml(chip.label) + '</span></span>';
  group.appendChild(head);

  const eligible = host.eligible || [];

  // A host the control plane can't place onto — no toggles, an honest reason
  // instead of a control that can't do what it says. A managed-only satellite
  // runs no hub (it's driven directly over SSH); a hub host may simply have no
  // models configured yet.
  if (!eligible.length) {
    const note = document.createElement('p');
    note.className = 'fleet-host-note muted small';
    note.textContent = host.runs_hub
      ? 'No models configured for this machine yet.'
      : 'Runs model servers directly (no hub on this machine) — not placeable from here yet.';
    group.appendChild(note);
    return group;
  }

  // A manageable host that's powered off: its placement is remembered and
  // applies on power-up — a deferred state, spelled out so it never reads as a
  // failure. (Editing desired state while a machine is off is intentional.)
  if (!host.local && !host.reachable) {
    const note = document.createElement('p');
    note.className = 'fleet-host-note muted small';
    note.textContent = 'Offline — saved placement applies when this machine powers up.';
    group.appendChild(note);
  }

  const list = document.createElement('ul');
  list.className = 'startup-list';
  eligible.forEach(function (m) { list.appendChild(buildModelRow(host, m)); });
  group.appendChild(list);
  return group;
}

function renderFleetPlacement() {
  const root = els.fleetPlacementBody;
  if (!root) return;
  const ds = state.fleetPlacementState;
  const note = els.fleetPlacementStaleNote;

  if (ds === 'loading') {
    root.replaceChildren(emptyStateEl('server', 'Reading fleet placement…'));
    if (note) note.hidden = true;
    return;
  }
  if (ds === 'error') {
    root.replaceChildren(emptyStateEl('triangle-alert', 'Could not read fleet placement.', {
      actionLabel: 'Retry',
      onAction: function () {
        state.fleetPlacementState = 'loading';
        renderFleetPlacement();
        fetchFleetPlacement();
      },
    }));
    if (note) note.hidden = true;
    return;
  }

  const hosts = (state.fleetPlacement && state.fleetPlacement.hosts) || [];
  if (!hosts.length) {
    root.replaceChildren(emptyStateEl('server', 'No machines available to place models on.'));
    if (note) note.hidden = true;
    return;
  }

  const frag = document.createDocumentFragment();
  hosts.forEach(function (h) { frag.appendChild(buildHostGroup(h)); });
  root.replaceChildren(frag);

  // Stale: keep the last-known grid, label it, per the design.md async contract.
  if (note) {
    if (ds === 'stale' && state.fleetPlacementUpdated) {
      const t = new Date(state.fleetPlacementUpdated);
      const hh = String(t.getHours()).padStart(2, '0');
      const mm = String(t.getMinutes()).padStart(2, '0');
      note.textContent = 'Last updated ' + hh + ':' + mm + ' · live data unavailable';
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }
}

export function wireFleetPlacement() {
  if (els.fleetPlacementRefreshBtn) {
    els.fleetPlacementRefreshBtn.addEventListener('click', function (ev) {
      // The button lives inside <summary>; stop the native details toggle.
      ev.preventDefault();
      ev.stopPropagation();
      fetchFleetPlacement();
    });
  }
}
