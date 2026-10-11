/* Shared parts for every pane (#736, umbrella #735) — one row, one chip, one
 * header line, one info sheet and the wide-window switch, so the admin draws
 * "a thing that is up or down, with one main action" one way everywhere.
 *
 *   row(opts)            One list row on the vendored action-row
 *                        (_vendored/action-row): a leading avatar whose corner
 *                        badge means alive ('up') or down ('down') and nothing
 *                        else, a one-line title, a budgeted meta line (a lead
 *                        that never shrinks, exception chips, then one context
 *                        string that ellipsizes), at most one visible verb and
 *                        one optional trailing item. Tapping the row is its
 *                        primary action (open the sheet / detail pane).
 *                        Destructive actions never go on a row: they live in
 *                        the item's sheet or menu, last, behind a confirm.
 *   chip(text, tone)     The one status chip, for exceptions only. `tone` is
 *                        the tone map in styles.css: none (a plain fact),
 *                        'accent' (in progress), 'attention' (needs you soon),
 *                        'danger' (broken). A healthy state is never a chip.
 *   setHeadLine(el, parts, fact)
 *                        The pane header's one context line: the exceptions
 *                        (at most two, each in its tone) or, when nothing is
 *                        wrong, one plain fact in muted. One line, ellipsized,
 *                        never wraps, so it never moves the layout.
 *   infoButton(key, label) / defineInfo(key, title, html) / wireInfoSheet()
 *                        An ⓘ icon-button that opens the shared #infoSheet
 *                        (vendored modal) with an explanation that used to be
 *                        a permanent footnote paragraph.
 *   isWide() / onWideChange(fn)
 *                        The wide-window layout's breakpoint (design.md
 *                        layout.wide). A pane opts in with `data-wide`:
 *                        "split" — list plus a detail pane (`.wide-detail`, the
 *                        element right after the pane) replacing the phone's
 *                        sheet; and any container may use `.wide-cols` for two
 *                        columns. The phone layout never changes. CSS: rows.css.
 */

import { escapeHtml } from './api.js';
import { icon } from './_vendored/icons/icons.js';

// ----------------------------------------------------------------- wide window
export const WIDE_QUERY = '(min-width: 1100px) and (pointer: fine)';
const wideMq = window.matchMedia(WIDE_QUERY);

export function isWide() { return wideMq.matches; }

export function onWideChange(fn) {
  wideMq.addEventListener('change', function () { fn(wideMq.matches); });
}

// ----------------------------------------------------------------------- chip
const TONES = { accent: true, attention: true, danger: true };

export function chip(text, tone) {
  const t = TONES[tone] ? ' data-tone="' + tone + '"' : '';
  return '<span class="chip"' + t + '>' + escapeHtml(text) + '</span>';
}

// ------------------------------------------------------------------------ row
/* opts:
 *   attrs      extra attributes on the <li> (already escaped), e.g. data-machine-id
 *   cls        extra classes on the <li>
 *   glyph      avatar glyph (sprite name); omit for no avatar
 *   badge      'up' | 'down' | null — the avatar's alive badge
 *   title      one-line title
 *   lead       meta text that never shrinks (a state, an uptime)
 *   chips      [{ text, tone }] exception chips, after the lead
 *   meta       the context string, last, the only part that ellipsizes
 *   selected   the row whose detail is open in the wide detail pane
 *   mainAttrs  extra attributes on the row's main button (already escaped)
 *   mainCls    extra classes on the row's main button
 *   metaAttrs  extra attributes on the meta line (a live region a caller repaints)
 *   verb       { attrs, label, glyph, disabled, title } — the one visible verb
 *   trail      raw HTML for the one trailing item (an ⓘ, a value)
 */
export function row(opts) {
  const metaParts = [];
  if (opts.lead) metaParts.push('<span class="row-meta-lead">' + escapeHtml(opts.lead) + '</span>');
  (opts.chips || []).forEach(function (c) { metaParts.push(chip(c.text, c.tone)); });
  if (opts.meta) metaParts.push('<span class="row-meta-text">' + escapeHtml(opts.meta) + '</span>');
  const meta = metaParts.length || opts.metaAttrs
    ? '<span class="action-row-meta"' + (opts.metaAttrs || '') + '>' + metaParts.join('') + '</span>'
    : '';
  const avatar = opts.glyph
    ? '<span class="action-row-avatar"' + (opts.badge ? ' data-badge="' + opts.badge + '"' : '')
      + ' aria-hidden="true">' + icon(opts.glyph) + '</span>'
    : '';
  const titleMeta = '<span class="action-row-title">' + escapeHtml(opts.title) + '</span>' + meta;
  // The vendored row wraps title + meta beside an avatar; without one they
  // sit straight in the main button's column.
  const inner = avatar ? avatar + '<span class="action-row-text">' + titleMeta + '</span>' : titleMeta;
  let verb = '';
  if (opts.verb) {
    const v = opts.verb;
    verb = '<button type="button" class="icon-button action-row-verb row-verb"' + (v.attrs || '')
      + (v.title ? ' title="' + escapeHtml(v.title) + '"' : '')
      + (v.disabled ? ' disabled' : '') + '>'
      + (v.glyph ? icon(v.glyph) : '') + '<span>' + escapeHtml(v.label) + '</span></button>';
  }
  return '<li class="action-row' + (opts.cls ? ' ' + opts.cls : '') + (opts.selected ? ' is-selected' : '') + '"'
    + (opts.attrs || '') + '>'
    + '<button type="button" class="action-row-main' + (opts.mainCls ? ' ' + opts.mainCls : '') + '"'
    + (opts.mainAttrs || '')
    + (opts.selected ? ' aria-current="true"' : '') + '>'
    + inner + '</button>'
    + verb + (opts.trail || '')
    + '</li>';
}

// ---------------------------------------------------------------- header line
/* parts: [{ text, tone }] — the exceptions, most important first; only the
 * first two show. fact: the plain line when nothing is wrong. title: the full
 * text for the tooltip / long form (e.g. the stale timestamp). */
export function setHeadLine(el, parts, fact, title) {
  if (!el) return;
  el.classList.add('head-line');
  const shown = (parts || []).filter(Boolean).slice(0, 2);
  if (shown.length) {
    el.innerHTML = shown.map(function (p) {
      return '<span class="head-part"' + (TONES[p.tone] ? ' data-tone="' + p.tone + '"' : '') + '>'
        + escapeHtml(p.text) + '</span>';
    }).join('<span class="head-sep"> · </span>');
  } else {
    el.textContent = fact || '';
  }
  el.title = title || el.textContent;
}

// ----------------------------------------------------------------- info sheet
const infoRegistry = {};

export function defineInfo(key, title, html) {
  infoRegistry[key] = { title: title, html: html };
}

export function infoButton(key, label) {
  return '<button type="button" class="icon-button info-button" data-info="' + escapeHtml(key) + '"'
    + ' aria-haspopup="dialog" aria-label="' + escapeHtml(label) + '" title="' + escapeHtml(label) + '">'
    + icon('info') + '</button>';
}

function openInfo(key) {
  const entry = infoRegistry[key];
  const dlg = document.getElementById('infoSheet');
  if (!entry || !dlg) return;
  document.getElementById('infoSheetTitle').textContent = entry.title;
  document.getElementById('infoSheetBody').innerHTML = entry.html;
  if (!dlg.open) dlg.showModal();
}

export function wireInfoSheet() {
  document.addEventListener('click', function (ev) {
    const btn = ev.target.closest('[data-info]');
    if (btn) openInfo(btn.dataset.info);
  });
  const close = document.getElementById('infoSheetCloseBtn');
  const dlg = document.getElementById('infoSheet');
  if (close && dlg) close.addEventListener('click', function () { dlg.close(); });
}
