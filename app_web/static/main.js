/* Entry point: boots the SPA. Each module exposes named functions; this
 * file sequences boot, polling, and tab-change driven start/stop of
 * background event streams.
 */

import { state, els, STATUS_POLL_MS, COUNTERS_POLL_MS, MODELS_POLL_MS } from './state.js';
import { jsonApi, tokenFromUrl, writeToken, wireLoginForm, toast } from './api.js';
import { wireTabs, setTab, onTabChange } from './tabs.js';
import { wireHub, fetchHubStatus, fetchCounters, startHubStreams, stopHubStreams, fetchInstallStatus, fetchServicesStatus } from './hub.js';
import { wireModels, fetchModels } from './models.js';
import { wirePlayground, fetchPlaygroundModels } from './playground.js';
import { wireTelemetry, startTelemetryPolls, stopTelemetryPolls } from './telemetry.js';
import { wireCodeUsage, startCodeUsagePolls, stopCodeUsagePolls } from './code_usage.js';

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
    fetchPlaygroundModels(),
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

async function resumeAfterLogin() {
  toast('Signed in.', 'good');
  await Promise.allSettled([
    fetchHubStatus(),
    fetchCounters(),
    fetchModels(),
    fetchInstallStatus(),
    fetchServicesStatus(),
    fetchPlaygroundModels(),
  ]);
  startHubStreams();
}

window.addEventListener('DOMContentLoaded', function () {
  boot().catch(function (err) {
    console.error('boot failed', err);
  });
});
