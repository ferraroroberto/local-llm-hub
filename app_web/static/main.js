/* Entry point: boots the SPA. Each module exposes named functions; this
 * file sequences boot, polling, and tab-change driven start/stop of
 * background event streams.
 */

import { state, els, TAB_KEY, STATUS_POLL_MS, COUNTERS_POLL_MS, MODELS_POLL_MS } from './state.js';
import { jsonApi, tokenFromUrl, writeToken, wireLoginForm, toast } from './api.js';
import { wireTabs, onTabChange, setTab } from './tabs.js';
import { wireUsageLog } from './usage.js';
import { wireHub, fetchHubStatus, fetchCounters, startHubStreams, stopHubStreams, fetchServicesStatus, renderSparklines } from './hub.js';
import { wireModels, fetchModels, fetchRoles } from './models.js';
import { wireStartupProfile, fetchStartupProfile } from './startup.js';
import { fetchDormant } from './dormant.js';
import { fetchFleetPlacement } from './fleet_placement.js';
import { wirePlayground, wirePlayModes, setPlayMode, fetchPlaygroundModels, fetchTtsModels, fetchImageModels } from './playground.js';
import { wireJev, fetchJevInfo } from './playground_jev.js';
import { fetchOtelHealth } from './langfuse.js';
import { wireTheme } from './theme.js';
import { wireCodeUsage, startCodeUsagePolls, stopCodeUsagePolls } from './code_usage.js';
import { wireMachines, startMachinesPolls, stopMachinesPolls } from './machines.js';
import { wireSettings } from './settings.js';
import { wireInfoSheet } from './rows.js';

// --------------------------------------------------------------- #telemetry
// The retired OTel pane's ways in (#741): a #telemetry link, a stored
// `telemetry` tab and an in-app data-open-tab="telemetry" all open Hub with
// the Activity feed (which carries the pane's traces) in view.
const RETIRED_TAB = 'telemetry';

function storedTab() {
  try { return localStorage.getItem(TAB_KEY); } catch (_) { return null; }
}

function showActivity() {
  setTab('hub');
  if (location.hash === '#' + RETIRED_TAB) {
    history.replaceState(null, '', location.pathname + location.search);
  }
  // After the nav's own scroll reset (setTab scrolls to the top).
  requestAnimationFrame(function () {
    const card = document.getElementById('activityCard');
    if (card) card.scrollIntoView({ block: 'start' });
  });
}

function openTab(tab) {
  if (tab === RETIRED_TAB) { showActivity(); return; }
  // "Send a test request" lands on the Playground's Chat (#740).
  if (tab === 'playground') setPlayMode('chat');
  setTab(tab);
}

async function fetchVersion() {
  try {
    const body = await jsonApi('/admin/api/version');
    state.version = body;
    const sha = body.git_sha || 'unknown';
    const ts = (body.built_at || '').replace('T', ' ').slice(0, 16);
    if (els.buildReadout) {
      els.buildReadout.textContent = ts ? ('Build: ' + sha + ' · ' + ts) : ('Build: ' + sha);
    }
    if (els.aboutBuild) els.aboutBuild.textContent = ts ? sha + ' · ' + ts : sha;
  } catch (_) { /* ignore */ }
}

// Every authed data fetch behind the tabs — one list shared by boot() and
// resumeAfterLogin(), so a fetch added for a new card reaches both. The
// version readout isn't here: /admin/api/version is auth-exempt, so boot()
// already has it and a login doesn't change it. Two live-probe endpoints are
// left out on purpose (seconds each, neither feeds the landing view, #646):
// fleet-placement loads when the Models tab opens (onTabChange below) and
// install/status when Settings › Health opens (settings.js).
function fetchAllData() {
  return Promise.allSettled([
    fetchHubStatus(),
    fetchCounters(),
    fetchModels(),
    fetchStartupProfile(),
    fetchDormant(),
    fetchServicesStatus(),
    fetchOtelHealth(),
    fetchPlaygroundModels(),
    fetchTtsModels(),
    fetchImageModels(),
    fetchJevInfo(),
  ]);
}

async function boot() {
  const fromUrl = tokenFromUrl();
  if (fromUrl) writeToken(fromUrl);

  wireLoginForm(function () { return resumeAfterLogin(); });
  wireTheme();
  wireHub();
  wireModels();
  wireStartupProfile();
  wirePlayground();
  wirePlayModes();
  wireJev();
  wireCodeUsage();
  wireMachines();
  wireSettings();
  wireInfoSheet();

  // Start the usage log first so the restored tab counts as the first view.
  onTabChange(wireUsageLog().trackView);

  // Register the tab-change hook BEFORE wiring the nav: the vendored
  // component restores the persisted tab during wireTabs() and fires
  // onChange for it, which is what starts the right streams/polls.
  onTabChange(function (tab) {
    if (tab === 'hub') {
      startHubStreams();
      // Draw the tiles now instead of leaving them blank until the 2.5 s poll.
      renderSparklines();
    } else {
      stopHubStreams();
    }
    if (tab === 'code-usage') {
      startCodeUsagePolls();
    } else {
      stopCodeUsagePolls();
    }
    if (tab === 'machines') {
      startMachinesPolls();
    } else {
      stopMachinesPolls();
    }
    // Refresh the memory glance each time the Models tab opens so it is
    // current when you look at it. The GET runs live peer probes, so it is
    // tab-triggered (+ once after a Start / Stop), never polled. Roles ride
    // along for the model sheets (#737).
    if (tab === 'models') {
      fetchFleetPlacement().catch(function () {});
      fetchRoles().catch(function () {});
    }
  });
  // A stored `telemetry` (the retired pane) is not a tab any more: the nav
  // would fall back to Hub anyway, but the Activity feed should be in view.
  const toActivity = location.hash === '#' + RETIRED_TAB || storedTab() === RETIRED_TAB;
  wireTabs();
  if (toActivity) showActivity();
  window.addEventListener('hashchange', function () {
    if (location.hash === '#' + RETIRED_TAB) showActivity();
  });
  // Buttons that open a destination which is not in the bottom bar.
  document.querySelectorAll('[data-open-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { openTab(btn.dataset.openTab); });
  });

  await Promise.allSettled([fetchVersion(), fetchAllData()]);

  // The vendored nav already restored the persisted tab (and its streams)
  // in wireTabs(); only (re)start the hub streams if that's where we are.
  if (state.tab === 'hub') startHubStreams();

  // Poll loops — light, no SSE for these
  setInterval(function () { fetchHubStatus().catch(function () {}); }, STATUS_POLL_MS);
  setInterval(function () { fetchCounters().catch(function () {}); }, COUNTERS_POLL_MS);
  setInterval(function () {
    if (state.tab === 'models') fetchModels().catch(function () {});
  }, MODELS_POLL_MS);
}

async function resumeAfterLogin() {
  toast('Signed in.', 'good');
  await fetchAllData();
  if (state.tab === 'hub') startHubStreams();
}

window.addEventListener('DOMContentLoaded', function () {
  boot().catch(function (err) {
    console.error('boot failed', err);
  });
});
