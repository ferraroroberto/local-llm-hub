/* Settings (#741): the gear on every pane opens the design system's settings
 * groups — Display (text size, theme), Startup (service startup switches),
 * Machines (dormant switches, #765), Health (Health & install) and About
 * (build, config version, process id, Langfuse UI, frontier report).
 *
 * A row with a body (Service startup, Health & install) opens it in place of
 * the list on a phone, with a back row, and beside the list on a wide window
 * (list : detail, the geometry proposed on #741), where the first one opens
 * with the dialog. Text size keeps the vendored control and its
 * `llmhub.textsize` key; the pre-paint boot script in index.html stamps it
 * before first paint.
 */

import { els, state } from './state.js';
import { jsonApi, postJson, toast, escapeHtml } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { bindTextSize } from './_vendored/text-size/text-size.js';
import { isWide, onWideChange } from './rows.js';
import { langfuseUiBase } from './langfuse.js';
import { fetchDormant } from './dormant.js';
import { renderHeadLine as renderHubHeadLine } from './hub.js';

const APP_PREFIX = 'llmhub';

// --------------------------------------------------------- pages
/* Show one group's body (or none: the list alone, on a phone). The row that
 * opened it is the selected one; the health battery runs the first time its
 * page opens (it takes seconds, #646). */
function openPage(key) {
  if (els.settingsLayout) els.settingsLayout.dataset.open = key || '';
  document.querySelectorAll('#settingsDialog .settings-page').forEach(function (page) {
    page.hidden = page.dataset.page !== key;
  });
  document.querySelectorAll('#settingsDialog .settings-link').forEach(function (row) {
    const on = row.dataset.settings === key;
    row.classList.toggle('is-selected', on);
    const main = row.querySelector('.action-row-main');
    if (main) {
      if (on) main.setAttribute('aria-current', 'true');
      else main.removeAttribute('aria-current');
    }
  });
  if (key === 'machines') fetchDormant();
  if (key === 'health' && !state.installChecked) {
    state.installChecked = true;
    fetchInstallStatus();
  }
}

/* A wide window always shows a body beside the list; a phone shows the list. */
function defaultPage() {
  return isWide() ? 'startup' : '';
}

function syncAbout() {
  const v = state.version || {};
  const sha = v.git_sha || '—';
  const ts = (v.built_at || '').replace('T', ' ').slice(0, 16);
  if (els.aboutBuild) els.aboutBuild.textContent = ts ? sha + ' · ' + ts : sha;
  if (els.aboutLangfuse) els.aboutLangfuse.href = langfuseUiBase();
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
  // The one filled button is a repair: offer it only once a check has something to fix (J-02, #647).
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
      btn.className = 'ghost-btn';
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
      const original = els.installFixAllBtn.textContent;
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
        els.installFixAllBtn.textContent = original;
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
      openPage(defaultPage());
      dialog.showModal();
    });
  });
  if (els.settingsCloseBtn) els.settingsCloseBtn.addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('click', function (ev) {
    const link = ev.target.closest('.settings-link');
    if (link) { openPage(link.dataset.settings); return; }
    if (ev.target.closest('[data-settings-back]')) openPage('');
  });
  onWideChange(function () {
    if (!dialog.open) return;
    const open = els.settingsLayout ? els.settingsLayout.dataset.open : '';
    if (!open && isWide()) openPage(defaultPage());
  });
  wireInstall();
}
