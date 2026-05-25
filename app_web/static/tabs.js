/* Five-tab switcher: Hub | Models | Playground | Telemetry | Claude Code. */

import { els, state } from './state.js';

const onTabChangeCallbacks = [];

export function onTabChange(fn) { onTabChangeCallbacks.push(fn); }

export function setTab(tab) {
  state.tab = tab;
  if (els.tabHub) els.tabHub.classList.toggle('active', tab === 'hub');
  if (els.tabModels) els.tabModels.classList.toggle('active', tab === 'models');
  if (els.tabPlayground) els.tabPlayground.classList.toggle('active', tab === 'playground');
  if (els.tabTelemetry) els.tabTelemetry.classList.toggle('active', tab === 'telemetry');
  if (els.tabCodeUsage) els.tabCodeUsage.classList.toggle('active', tab === 'code-usage');
  if (els.paneHub) els.paneHub.hidden = tab !== 'hub';
  if (els.paneModels) els.paneModels.hidden = tab !== 'models';
  if (els.panePlayground) els.panePlayground.hidden = tab !== 'playground';
  if (els.paneTelemetry) els.paneTelemetry.hidden = tab !== 'telemetry';
  if (els.paneCodeUsage) els.paneCodeUsage.hidden = tab !== 'code-usage';
  onTabChangeCallbacks.forEach(function (fn) { try { fn(tab); } catch (_) {} });
}

export function wireTabs() {
  if (els.tabHub) els.tabHub.addEventListener('click', function () { setTab('hub'); });
  if (els.tabModels) els.tabModels.addEventListener('click', function () { setTab('models'); });
  if (els.tabPlayground) els.tabPlayground.addEventListener('click', function () { setTab('playground'); });
  if (els.tabTelemetry) els.tabTelemetry.addEventListener('click', function () { setTab('telemetry'); });
  if (els.tabCodeUsage) els.tabCodeUsage.addEventListener('click', function () { setTab('code-usage'); });
}
