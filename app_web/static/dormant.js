/* Settings › Machines — "Dormant machines": mark a peer that is off on purpose
 * so it raises no offline alert.
 *
 * Thin view over GET /admin/api/machines/dormant (config only, no live probe)
 * and PUT /admin/api/machines/{id}/dormant, which writes the gitignored
 * config/machines.local.yaml. Each row is the vendored fleet switch and, like
 * Service startup, only flips once the PUT confirms and reverts on failure.
 * The machine the hub runs on is never listed: it cannot be dormant.
 */

import { els, state } from './state.js';
import { jsonApi, putJson, toast } from './api.js';
import { switchEl, setSwitch } from './_vendored/switch/switch.js';
import { fetchMachinesStatus } from './machines.js';

export async function fetchDormant() {
  try {
    const body = await jsonApi('/admin/api/machines/dormant');
    state.dormantMachines = body.machines || [];
    renderDormant();
  } catch (_) {
    if (els.dormantSummary) els.dormantSummary.textContent = 'not confirmed';
  }
}

function renderDormant() {
  const root = els.dormantList;
  const list = state.dormantMachines;
  if (!root || !list) return;
  if (!list.length) {
    root.replaceChildren();
    const li = document.createElement('li');
    li.className = 'startup-row muted';
    li.textContent = 'No other machines are configured.';
    root.appendChild(li);
  } else {
    root.replaceChildren(...list.map(buildRow));
  }
  renderSummary();
}

/* The Settings row's value: how many machines are marked intentionally off. */
function renderSummary() {
  const list = state.dormantMachines;
  if (!els.dormantSummary || !list) return;
  const n = list.filter(function (m) { return m.dormant; }).length;
  els.dormantSummary.textContent = n ? n + ' of ' + list.length + ' dormant' : 'None dormant';
}

function buildRow(m) {
  const li = document.createElement('li');
  li.className = 'startup-row';
  li.dataset.machineId = m.id;

  const label = document.createElement('span');
  label.className = 'startup-row-label dormant-row-label';
  const name = document.createElement('span');
  name.textContent = m.display_name;
  const hint = document.createElement('span');
  hint.className = 'muted small';
  hint.textContent = 'Mark as intentionally off';
  label.append(name, hint);
  li.appendChild(label);

  li.appendChild(switchEl(m.dormant, {
    label: 'Dormant: ' + m.display_name,
    onToggle: function (next, btn) {
      if (btn.disabled) return;
      btn.disabled = true;
      putJson('/admin/api/machines/' + encodeURIComponent(m.id) + '/dormant', { dormant: next })
        .then(function () {
          setSwitch(btn, next);
          m.dormant = next;
          renderSummary();
          // The Machines tab re-reads at once instead of waiting for its poll.
          if (state.machinesStatus) fetchMachinesStatus();
        })
        .catch(function (exc) {
          setSwitch(btn, m.dormant);
          toast(String(exc.message || exc), 'error');
        })
        .finally(function () { btn.disabled = false; });
    },
  }));
  return li;
}
