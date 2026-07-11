/* Models tab — "Startup" card: what autostarts with the hub (issue #265).
 *
 * Thin CRUD view over GET/PATCH /admin/api/startup-profile. Each row is the
 * vendored fleet switch (_vendored/switch) — optimistic-free: the toggle
 * only flips once the PATCH confirms, and reverts on failure.
 */

import { els, state } from './state.js';
import { jsonApi, patchJson, toast } from './api.js';
import { switchEl, setSwitch } from './_vendored/switch/switch.js';

export async function fetchStartupProfile() {
  try {
    const body = await jsonApi('/admin/api/startup-profile');
    state.startupProfile = body;
    renderStartup();
  } catch (_) { /* ignore */ }
}

function renderStartup() {
  const root = els.startupList;
  const data = state.startupProfile;
  if (!root || !data) return;

  const profile = data.profile || {};
  const frag = document.createDocumentFragment();

  frag.appendChild(sectionTitle('Services'));
  (data.services || []).forEach(function (svc) {
    frag.appendChild(buildRow(svc.label, !!profile[svc.id], function (next) {
      return patchJson('/admin/api/startup-profile', { [svc.id]: next });
    }));
  });

  frag.appendChild(sectionTitle('Models'));
  const activeModels = new Set(profile.models || []);
  (data.models || []).forEach(function (m) {
    frag.appendChild(buildRow(m.display_name, activeModels.has(m.id), function (next) {
      const models = new Set(profile.models || []);
      if (next) models.add(m.id); else models.delete(m.id);
      return patchJson('/admin/api/startup-profile', { models: Array.from(models) });
    }));
  });

  root.replaceChildren(frag);
}

function sectionTitle(text) {
  const li = document.createElement('li');
  li.className = 'opt-group-title';
  li.textContent = text;
  return li;
}

function buildRow(label, on, apply) {
  const li = document.createElement('li');
  li.className = 'startup-row';

  const labelSpan = document.createElement('span');
  labelSpan.className = 'startup-row-label';
  labelSpan.textContent = label;
  li.appendChild(labelSpan);

  const sw = switchEl(on, {
    label: label,
    onToggle: function (next, btn) {
      if (btn.disabled) return;
      btn.disabled = true;
      apply(next)
        .then(function (body) {
          setSwitch(btn, next);
          if (body && body.profile) state.startupProfile.profile = body.profile;
        })
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

export function wireStartupProfile() { /* nothing to wire — rows re-render on fetch */ }
