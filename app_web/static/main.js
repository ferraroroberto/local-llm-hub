/* Entry point: boots the SPA. Each module exposes named functions; this
 * file sequences boot, polling, and tab-change driven start/stop of
 * background event streams.
 */

import { state, els, THEME_KEY, STATUS_POLL_MS, COUNTERS_POLL_MS, MODELS_POLL_MS } from './state.js';
import { jsonApi, tokenFromUrl, urlWithToken, writeToken, wireLoginForm, toast } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { wireTabs, setTab, onTabChange } from './tabs.js';
import { wireHub, fetchHubStatus, fetchCounters, startHubStreams, stopHubStreams, fetchInstallStatus, fetchServicesStatus } from './hub.js';
import { wireModels, fetchModels } from './models.js';
import { wirePlayground, fetchPlaygroundModels, fetchTtsModels, fetchImageModels } from './playground.js';
import { wireTelemetry, startTelemetryPolls, stopTelemetryPolls, fetchTelemetryHealth } from './telemetry.js';
import { wireCodeUsage, startCodeUsagePolls, stopCodeUsagePolls, restyleCodeUsageCharts } from './code_usage.js';

// --------------------------------------------------------------- theme toggle
// The pre-paint boot script in index.html already stamped html[data-theme]
// (localStorage override, prefers-color-scheme fallback); this block owns the
// Hub-card sun/moon button. Same mechanism as home-automation/app-launcher.
function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  // Show the glyph for the action: sun to switch to light, moon to switch to dark.
  if (els.themeToggleBtn) els.themeToggleBtn.innerHTML = icon(dark ? 'sun' : 'moon');
  localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
  // Chart.js canvases can't follow CSS vars on their own — re-resolve them.
  restyleCodeUsageCharts();
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme !== 'dark');
}

(function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(stored ? stored === 'dark' : prefersDark);
})();

if (els.themeToggleBtn) els.themeToggleBtn.addEventListener('click', toggleTheme);

async function fetchVersion() {
  try {
    const body = await jsonApi('/admin/api/version');
    state.version = body;
    const sha = body.git_sha || 'unknown';
    const ts = (body.built_at || '').replace('T', ' ').slice(0, 16);
    if (els.buildReadout) {
      els.buildReadout.textContent = ts ? ('Build: ' + sha + ' · ' + ts) : ('Build: ' + sha);
    }
  } catch (_) { /* ignore */ }
}

async function boot() {
  const fromUrl = tokenFromUrl();
  if (fromUrl) writeToken(fromUrl);

  wireTabs();
  wireLoginForm(function () { return resumeAfterLogin(); });
  wireHub();
  wireModels();
  wirePlayground();
  wireTelemetry();
  wireCodeUsage();
  wireFrontierLink();

  onTabChange(function (tab) {
    if (tab === 'hub') {
      startHubStreams();
    } else {
      stopHubStreams();
    }
    if (tab === 'telemetry') {
      startTelemetryPolls();
    } else {
      stopTelemetryPolls();
    }
    if (tab === 'code-usage') {
      startCodeUsagePolls();
    } else {
      stopCodeUsagePolls();
    }
  });

  await Promise.allSettled([
    fetchVersion(),
    fetchHubStatus(),
    fetchCounters(),
    fetchModels(),
    fetchInstallStatus(),
    fetchServicesStatus(),
    fetchTelemetryHealth(),
    fetchPlaygroundModels(),
    fetchTtsModels(),
    fetchImageModels(),
  ]);

  startHubStreams();

  // Poll loops — light, no SSE for these
  setInterval(function () { fetchHubStatus().catch(function () {}); }, STATUS_POLL_MS);
  setInterval(function () { fetchCounters().catch(function () {}); }, COUNTERS_POLL_MS);
  setInterval(function () {
    if (state.tab === 'models') fetchModels().catch(function () {});
  }, MODELS_POLL_MS);

  setTab('hub');
}

function wireFrontierLink() {
  if (!els.frontierLink) return;
  els.frontierLink.addEventListener('click', function () {
    els.frontierLink.href = urlWithToken('/admin/frontier');
  });
}

async function resumeAfterLogin() {
  toast('Signed in.', 'good');
  await Promise.allSettled([
    fetchHubStatus(),
    fetchCounters(),
    fetchModels(),
    fetchInstallStatus(),
    fetchServicesStatus(),
    fetchTelemetryHealth(),
    fetchPlaygroundModels(),
    fetchTtsModels(),
    fetchImageModels(),
  ]);
  startHubStreams();
}

window.addEventListener('DOMContentLoaded', function () {
  boot().catch(function (err) {
    console.error('boot failed', err);
  });
});
