/* Settings › Performance — "Worker pools": the size of each of the six thread
 * pools blocking work runs on.
 *
 * Thin view over GET/PUT /admin/api/worker-pools. The hub builds the pools when
 * it starts, so a saved size applies at the next restart; the sheet says so,
 * and says it again while a saved size differs from the running one. The
 * server refuses an out-of-range value and its reason is shown as-is.
 */

import { els, state } from './state.js';
import { jsonApi, putJson, toast } from './api.js';

export async function fetchWorkerPools() {
  try {
    state.workerPools = await jsonApi('/admin/api/worker-pools');
    renderPools();
  } catch (_) {
    if (els.poolsSummary) els.poolsSummary.textContent = 'not confirmed';
  }
}

function renderPools() {
  const data = state.workerPools;
  if (!els.poolsList || !data) return;
  els.poolsList.replaceChildren(...data.pools.map(function (p) { return buildRow(p, data); }));
  renderStatus();
}

function renderStatus() {
  const data = state.workerPools;
  if (!data) return;
  const custom = data.pools.filter(function (p) { return p.size !== p.default; }).length;
  if (els.poolsSummary) els.poolsSummary.textContent = custom ? custom + ' changed' : 'Defaults';
  if (els.poolsPending) els.poolsPending.hidden = !data.restart_pending;
}

function buildRow(p, data) {
  const li = document.createElement('li');
  li.className = 'startup-row pool-row';
  li.dataset.poolId = p.id;

  const label = document.createElement('label');
  label.className = 'startup-row-label pool-row-label';
  const name = document.createElement('span');
  name.textContent = p.label;
  const hint = document.createElement('span');
  hint.className = 'muted small';
  hint.textContent = p.description + ' Default ' + p.default + '.';
  label.append(name, hint);

  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'pool-input';
  input.min = data.min;
  input.max = data.max;
  input.step = 1;
  input.value = p.size;
  input.dataset.poolId = p.id;
  input.dataset.track = 'pool-size';
  label.htmlFor = input.id = 'poolSize-' + p.id;

  li.append(label, input);
  return li;
}

async function save() {
  const sizes = {};
  els.poolsList.querySelectorAll('input[data-pool-id]').forEach(function (input) {
    // An emptied field is sent as null so the server refuses it by name.
    sizes[input.dataset.poolId] = input.value === '' ? null : Number(input.value);
  });
  els.poolsSaveBtn.disabled = true;
  try {
    state.workerPools = await putJson('/admin/api/worker-pools', { sizes: sizes });
    renderPools();
    toast('Saved. Pool sizes apply at the next hub restart.', 'success');
  } catch (exc) {
    toast(String(exc.message || exc), 'error');
  } finally {
    els.poolsSaveBtn.disabled = false;
  }
}

export function wireWorkerPools() {
  if (els.poolsSaveBtn) els.poolsSaveBtn.addEventListener('click', save);
}
