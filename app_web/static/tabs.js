/* Three-tab switcher: Hub | Models | Playground. */

import { els, state } from './state.js';

const onTabChangeCallbacks = [];

export function onTabChange(fn) { onTabChangeCallbacks.push(fn); }

export function setTab(tab) {
  state.tab = tab;
  if (els.tabHub) els.tabHub.classList.toggle('active', tab === 'hub');
  if (els.tabModels) els.tabModels.classList.toggle('active', tab === 'models');
  if (els.tabPlayground) els.tabPlayground.classList.toggle('active', tab === 'playground');
  if (els.paneHub) els.paneHub.hidden = tab !== 'hub';
  if (els.paneModels) els.paneModels.hidden = tab !== 'models';
  if (els.panePlayground) els.panePlayground.hidden = tab !== 'playground';
  onTabChangeCallbacks.forEach(function (fn) { try { fn(tab); } catch (_) {} });
}

export function wireTabs() {
  if (els.tabHub) els.tabHub.addEventListener('click', function () { setTab('hub'); });
  if (els.tabModels) els.tabModels.addEventListener('click', function () { setTab('models'); });
  if (els.tabPlayground) els.tabPlayground.addEventListener('click', function () { setTab('playground'); });
}
