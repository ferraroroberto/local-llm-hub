/* Six-tab switcher: Hub | Models | Playground | Telemetry | Claude Code | Machines.
 *
 * Thin adapter over the vendored fleet nav (_vendored/nav/nav-tabs.js —
 * local-llm-hub#211). The vendored component owns tab discovery, ARIA +
 * roving tabindex, localStorage persistence, scroll reset, and the iOS
 * pin behaviour; this file only bridges it to the app's existing
 * onTabChange/setTab API and mirrors the active tab into state.tab.
 */

import { state, TAB_KEY } from './state.js';
import { initNavTabs } from './_vendored/nav/nav-tabs.js';

const onTabChangeCallbacks = [];
let nav = null;

export function onTabChange(fn) { onTabChangeCallbacks.push(fn); }

export function setTab(tab) { if (nav) nav.setTab(tab); }

export function wireTabs() {
  nav = initNavTabs({
    storageKey: TAB_KEY,
    onChange: function (tab) {
      state.tab = tab;
      onTabChangeCallbacks.forEach(function (fn) { try { fn(tab); } catch (_) {} });
    },
  });
}
