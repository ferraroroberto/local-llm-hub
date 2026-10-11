/* Theme: System, Light or Dark (#741).
 *
 * The pre-paint boot script in index.html already stamped html[data-theme]
 * (a stored light/dark, else prefers-color-scheme). This module owns the
 * sun/moon toggle in every pane header (an explicit light or dark) and the
 * Settings › Display › Theme row (System removes the stored choice, so the
 * page follows the device again). Same mechanism as home-automation and
 * app-launcher.
 */

import { els, THEME_KEY } from './state.js';
import { icon } from './_vendored/icons/icons.js';
import { restyleCodeUsageCharts } from './code_usage.js';

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function themePref() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === 'dark' || stored === 'light' ? stored : 'system';
  } catch (_) { return 'system'; }   // private mode / blocked storage
}

function paint() {
  const pref = themePref();
  const dark = pref === 'system' ? darkQuery.matches : pref === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  // Show the glyph for the action: sun to switch to light, moon to switch to dark.
  els.themeToggleBtns.forEach(function (btn) { btn.innerHTML = icon(dark ? 'sun' : 'moon'); });
  if (els.themeSeg) {
    els.themeSeg.querySelectorAll('[data-theme-pref]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.themePref === pref));
    });
  }
  // Chart.js canvases can't follow CSS vars on their own — re-resolve them.
  restyleCodeUsageCharts();
}

function setThemePref(pref) {
  try {
    if (pref === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch (_) { /* private mode: the choice lasts this page only */ }
  paint();
}

export function wireTheme() {
  els.themeToggleBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setThemePref(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    });
  });
  if (els.themeSeg) {
    els.themeSeg.addEventListener('click', function (ev) {
      const seg = ev.target.closest('[data-theme-pref]');
      if (seg) setThemePref(seg.dataset.themePref);
    });
  }
  // Following the device: repaint when it switches.
  darkQuery.addEventListener('change', function () { if (themePref() === 'system') paint(); });
  paint();
}
