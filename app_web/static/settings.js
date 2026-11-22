/* Settings: the gear on every pane opens the vendored full Settings page
 * (settings-group): Display (text size, theme), Startup (service startup
 * switches), Machines (dormant switches), Health (Health & install) and About
 * (build, config version, process id, Langfuse UI, frontier report).
 *
 * A row with a body (Service startup, Dormant machines, Health & install)
 * opens it as a fitted sheet over the page, on every width. Text size keeps
 * the vendored control and its `llmhub.textsize` key; the pre-paint boot
 * script in index.html stamps it before first paint.
 */

import { els, state } from './state.js';
import { jsonApi, postJson, toast, escapeHtml } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { bindTextSize } from './_vendored/text-size/text-size.js';
import { langfuseUiBase } from './langfuse.js';
import { fetchDormant } from './dormant.js';
import { renderHeadLine as renderHubHeadLine } from './hub.js';

const APP_PREFIX = 'llmhub';

// --------------------------------------------------------- sheets
/* Open one group's body as its fitted sheet over the page. The health battery
 * runs the first time its sheet opens (it takes seconds). */
function openSheet(key) {
  const sheet = document.getElementById('settingsPage-' + key);
  if (!sheet || sheet.open) return;
  if (key === 'machines') fetchDormant();
  if (key === 'health' && !state.installChecked) {
    state.installChecked = true;
    fetchInstallStatus();
  }
  sheet.showModal();
}

function syncAbout() {
  const v = state.version || {};
  const sha = v.git_sha || '—';
  const ts = (v.built_at || '').replace('T', ' ').slice(0, 16);
  if (els.aboutBuild) els.aboutBuild.title = els.aboutBuild.textContent = ts ? sha + ' · ' + ts : sha;
  if (els.aboutLangfuse) els.aboutLangfuse.dataset.href = langfuseUiBase();
}

// --------------------------------------------------------- Health & install
async function fetchInstallStatus() {
  try {
    const body = await jsonApi('/admin/api/install/status');
    state.installChecks = body.checks || [];
    renderInstall(body);
  } catch (_) {
    if (els.installSummary) els.installSummary.textContent = 'not confirmed';
  }
  renderHubHeadLine();
}

function renderInstall(body) {
  if (!els.installRows || !els.installSummary) return;
  const checks = body.checks || [];
  const overall = body.worst_status || 'ok';
  const failing = checks.filter(function (c) { return c.status === 'missing' || c.status === 'error'; }).length;
  // The row's value: a failing check is the exception and takes its tone.
  els.installSummary.textContent = failing
    ? failing + (failing === 1 ? ' check failing' : ' checks failing') + ' of ' + checks.length
    : checks.length + (checks.length === 1 ? ' check · ' : ' checks · ') + (overall === 'warn' ? 'with warnings' : 'all pass');
  if (failing) els.installSummary.dataset.tone = 'attention';
  else delete els.installSummary.dataset.tone;
  els.installRows.innerHTML = '';
  // The one filled button is a repair: offer it only once a check has something to fix (J-02).
  if (els.installFixAllBtn) {
    els.installFixAllBtn.hidden = !checks.some(function (c) {
      return c.fix_id && (c.status === 'missing' || c.status === 'error');
    });
  }
  checks.forEach(function (c) {
    const r = document.createElement('div');
    r.className = 'install-row install-' + c.status;
    const glyph = c.status === 'ok' ? icon('circle-check')
      : c.status === 'warn' ? icon('triangle-alert')
      : c.status === 'missing' ? icon('circle-help')
      : icon('circle-x');
    r.innerHTML =
      '<span class="install-glyph">' + glyph + '</span>' +
      '<span class="install-label">' + escapeHtml(c.label) + '</span>' +
      '<span class="install-detail muted small">' + escapeHtml(c.detail || '') + '</span>';
    if (c.fix_id && (c.status === 'missing' || c.status === 'error')) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.track = 'install-fix';
      btn.className = 'button-ghost';
      btn.innerHTML = icon('wrench') + escapeHtml(c.fix_label || 'Fix');
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        btn.textContent = 'Running…';
        try {
          await postJson('/admin/api/install/fix', { fix_id: c.fix_id });
          toast('Fixed ' + c.id, 'good');
          await fetchInstallStatus();
        } catch (exc) {
          toast(String(exc.message || exc), 'error');
          btn.disabled = false;
          btn.innerHTML = icon('wrench') + 'Retry';
        }
      });
      r.appendChild(btn);
    }
    els.installRows.appendChild(r);
  });
}

function wireInstall() {
  if (els.installFixAllBtn) {
    els.installFixAllBtn.addEventListener('click', async function () {
      els.installFixAllBtn.disabled = true;
      const original = els.installFixAllBtn.innerHTML;
      els.installFixAllBtn.textContent = 'Running…';
      try {
        const body = await postJson('/admin/api/install/fix-all', {});
        const ran = (body && body.ran) || [];
        const failed = ran.filter(function (r) { return !r.ok; });
        if (failed.length) {
          toast('Fix-all: ' + failed.map(function (r) { return r.fix_id + ' (' + r.error + ')'; }).join(', '), 'error');
        } else {
          toast('Fix-all complete.', 'good');
        }
        await fetchInstallStatus();
      } catch (exc) {
        toast(String(exc.message || exc), 'error');
      } finally {
        els.installFixAllBtn.disabled = false;
        els.installFixAllBtn.innerHTML = original;
      }
    });
  }
  if (els.installRefreshBtn) {
    els.installRefreshBtn.addEventListener('click', function () { fetchInstallStatus(); });
  }
}

// --------------------------------------------------------- wiring
export function wireSettings() {
  const dialog = els.settingsDialog;
  if (!dialog || !dialog.showModal) return;
  if (els.textSizeControl) bindTextSize(els.textSizeControl, APP_PREFIX);
  document.querySelectorAll('.home-settings').forEach(function (btn) {
    btn.addEventListener('click', function () {
      syncAbout();
      dialog.showModal();
    });
  });
  if (els.settingsCloseBtn) els.settingsCloseBtn.addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('click', function (ev) {
    const link = ev.target.closest('.settings-link');
    if (link) { openSheet(link.dataset.settings); return; }
    // A row that leaves the app is a button row; the address
    // rides in data-href and opens in its own tab, as a link row would.
    const out = ev.target.closest('.settings-row[data-href]');
    if (out && out.dataset.href) {
      window.open(out.dataset.href, '_blank', 'noopener');
      return;
    }
  });
  document.querySelectorAll('.settings-sheet [data-sheet-close]').forEach(function (btn) {
    btn.addEventListener('click', function () { btn.closest('dialog').close(); });
  });
  wireInstall();
}
