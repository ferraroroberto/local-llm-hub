/* Transcription dictionary editor — inline panel opened from a whisper row.
 *
 * One shared dictionary (config/transcription_glossary.json) feeds every
 * whisper backend, so this editor is the same regardless of which whisper
 * row opens it. Sections:
 *   • Replacements — ordered {from → to} literal fixes (add/edit/delete/reorder)
 *   • Boost terms  — vocabulary chips fed to whisper at launch (add/delete)
 *   • ✨ Suggest    — mine recent transcripts for candidate additions
 *
 * mountGlossaryEditor(container) renders into `container` with its own
 * closure state, fetching fresh on mount and saving the whole file.
 */

import { jsonApi, postJson, putJson, toast } from './api.js';
import { icon } from './_vendored/icons/icons.js';

export function mountGlossaryEditor(container) {
  // Per-mount editor: the working copy of the dictionary plus the nodes the
  // render/load/save helpers below repaint.
  const ed = { model: { replacements: [], boost_terms: [] } };

  container.replaceChildren();
  container.classList.add('glossary-editor');

  // Make the shared nature unmistakable: this is one list, reachable from
  // every whisper row, not a per-model dictionary.
  const sharedNote = document.createElement('p');
  sharedNote.className = 'glossary-shared-note';
  sharedNote.textContent =
    '🔗 Shared dictionary — one list used by every whisper model. ' +
    'Editing it here (Turbo or Translate) changes it everywhere.';

  ed.status = document.createElement('p');
  ed.status.className = 'muted small glossary-status';
  ed.status.textContent = 'Loading…';

  const replSection = section('Replacements',
    'Literal fixes applied after whisper returns text — e.g. “cloud code” → “Claude Code”. Order matters; longest match wins.');
  ed.replList = document.createElement('div');
  ed.replList.className = 'glossary-repl-list';
  const addReplBtn = ghostBtn('+ add rule', function () {
    ed.model.replacements.push({ from: '', to: '' });
    renderRepl(ed);
  });
  replSection.body.append(ed.replList, addReplBtn);

  const boostSection = section('Boost terms',
    'Vocabulary biased into whisper at launch. Edits here bind on the next whisper start, not immediately.');
  ed.boostChips = document.createElement('div');
  ed.boostChips.className = 'glossary-chips';
  const boostAddRow = document.createElement('div');
  boostAddRow.className = 'glossary-add-row';
  const boostInput = document.createElement('input');
  boostInput.type = 'text';
  boostInput.placeholder = 'add a term, Enter';
  boostInput.setAttribute('aria-label', 'Add boost term');
  boostInput.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') { ev.preventDefault(); addBoost(ed, boostInput.value); boostInput.value = ''; }
  });
  boostAddRow.append(boostInput, ghostBtn('+ add', function () { addBoost(ed, boostInput.value); boostInput.value = ''; }));
  ed.boostBudget = document.createElement('p');
  ed.boostBudget.className = 'glossary-budget muted small';
  boostSection.body.append(ed.boostChips, boostAddRow, ed.boostBudget);

  // Suggestions area (filled by the miner).
  ed.suggestWrap = document.createElement('div');
  ed.suggestWrap.className = 'glossary-suggest';
  ed.suggestWrap.hidden = true;

  const actions = document.createElement('div');
  actions.className = 'glossary-actions';
  ed.mineBtn = document.createElement('button');
  ed.mineBtn.type = 'button';
  ed.mineBtn.className = 'ghost-btn';
  ed.mineBtn.innerHTML = icon('sparkles') + 'Suggest from transcripts';
  ed.mineBtn.addEventListener('click', function () { onMine(ed); });
  ed.saveBtn = document.createElement('button');
  ed.saveBtn.type = 'button';
  ed.saveBtn.className = 'ghost-btn primary';
  ed.saveBtn.innerHTML = icon('save') + 'Save';
  ed.saveBtn.addEventListener('click', function () { onSave(ed); });
  actions.append(ed.mineBtn, ed.saveBtn);

  container.append(sharedNote, ed.status, replSection.root, boostSection.root, ed.suggestWrap, actions);

  load(ed);
}

// ----------------------------------------------------------- rendering
function renderRepl(ed) {
  const rules = ed.model.replacements;
  ed.replList.replaceChildren();
  rules.forEach(function (rule, idx) {
    const row = document.createElement('div');
    row.className = 'glossary-repl-row';

    const from = document.createElement('input');
    from.type = 'text'; from.value = rule.from; from.placeholder = 'heard as…';
    from.setAttribute('aria-label', 'Replace from');
    from.addEventListener('input', function () { rule.from = from.value; });

    const arrow = document.createElement('span');
    arrow.className = 'glossary-arrow'; arrow.innerHTML = icon('chevron-right');
    arrow.setAttribute('aria-label', 'becomes');

    const to = document.createElement('input');
    to.type = 'text'; to.value = rule.to; to.placeholder = 'correct to…';
    to.setAttribute('aria-label', 'Replace to');
    to.addEventListener('input', function () { rule.to = to.value; });

    const up = iconBtn(icon('chevron-up'), 'Move up', function () { move(ed, idx, -1); });
    const down = iconBtn(icon('chevron-down'), 'Move down', function () { move(ed, idx, 1); });
    up.disabled = idx === 0;
    down.disabled = idx === rules.length - 1;
    const del = iconBtn(icon('x'), 'Delete rule', function () {
      rules.splice(idx, 1); renderRepl(ed);
    });
    del.classList.add('danger');

    row.append(from, arrow, to, up, down, del);
    ed.replList.appendChild(row);
  });
}

function move(ed, idx, delta) {
  const rules = ed.model.replacements;
  const j = idx + delta;
  if (j < 0 || j >= rules.length) return;
  const tmp = rules[idx];
  rules[idx] = rules[j];
  rules[j] = tmp;
  renderRepl(ed);
}

function renderBoost(ed) {
  const terms = ed.model.boost_terms;
  ed.boostChips.replaceChildren();
  if (!terms.length) {
    const empty = document.createElement('span');
    empty.className = 'muted small';
    empty.textContent = 'No boost terms yet.';
    ed.boostChips.appendChild(empty);
    return;
  }
  terms.forEach(function (term, idx) {
    const chip = document.createElement('span');
    chip.className = 'glossary-chip';
    chip.append(document.createTextNode(term));
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'icon-button glossary-chip-x'; x.innerHTML = icon('x');
    x.setAttribute('aria-label', 'Remove ' + term);
    x.addEventListener('click', function () { terms.splice(idx, 1); renderBoost(ed); });
    chip.appendChild(x);
    ed.boostChips.appendChild(chip);
  });
}

// Saved-state token budget of the launch prompt (the server ranks and caps it);
// warns when launch would drop terms, since whisper honours only the prompt tail.
const DROPPED_NAMES_SHOWN = 5;

function renderBudget(ed, b) {
  ed.boostBudget.replaceChildren();
  ed.boostBudget.classList.remove('warn');
  if (!b) return;
  const dropped = b.dropped || [];
  if (!dropped.length) {
    ed.boostBudget.textContent = 'Launch prompt (saved): ~' + b.total_tokens + ' / ' + b.budget + ' tokens.';
    return;
  }
  ed.boostBudget.classList.add('warn');
  const names = dropped.slice(0, DROPPED_NAMES_SHOWN).join(', ')
    + (dropped.length > DROPPED_NAMES_SHOWN ? ', …' : '');
  ed.boostBudget.innerHTML = icon('triangle-alert');
  ed.boostBudget.append(
    'Over budget (saved): ~' + b.total_tokens + ' / ' + b.budget + ' tokens — '
    + dropped.length + ' term(s) will be dropped at launch, e.g. ' + names + '.');
}

function addBoost(ed, value) {
  const term = String(value || '').trim();
  if (!term) return;
  if (ed.model.boost_terms.some(function (t) { return t.toLowerCase() === term.toLowerCase(); })) {
    toast('“' + term + '” is already a boost term', 'error');
    return;
  }
  ed.model.boost_terms.push(term);
  renderBoost(ed);
}

// ----------------------------------------------------------- load / save
// Replace the working copy with a server-side dictionary and repaint it.
function adopt(ed, glossary) {
  ed.model.replacements = (glossary.replacements || []).map(function (r) {
    return { from: String(r.from || ''), to: String(r.to || '') };
  });
  ed.model.boost_terms = (glossary.boost_terms || []).map(String);
  renderRepl(ed);
  renderBoost(ed);
}

async function load(ed) {
  try {
    const body = await jsonApi('/admin/api/glossary');
    adopt(ed, body);
    renderBudget(ed, body.boost_budget);
    ed.status.textContent = ed.model.replacements.length + ' replacement(s) · ' + ed.model.boost_terms.length + ' boost term(s)';
  } catch (exc) {
    ed.status.textContent = 'Failed to load dictionary: ' + String(exc.message || exc);
  }
}

async function onSave(ed) {
  // Drop blank replacement rows before persisting.
  const replacements = ed.model.replacements.filter(function (r) {
    return r.from.trim() && typeof r.to === 'string';
  });
  ed.saveBtn.disabled = true;
  try {
    const body = await putJson('/admin/api/glossary', {
      replacements: replacements,
      boost_terms: ed.model.boost_terms,
    });
    adopt(ed, body.glossary || {});
    renderBudget(ed, body.boost_budget);
    toast(body.boost_terms_need_restart
      ? 'Saved · replacements live now, boost terms apply on next whisper start'
      : 'Dictionary saved', 'good');
  } catch (exc) {
    toast('Save failed: ' + String(exc.message || exc), 'error');
  } finally {
    ed.saveBtn.disabled = false;
  }
}

// ----------------------------------------------------------- mining
async function onMine(ed) {
  ed.mineBtn.disabled = true;
  const orig = ed.mineBtn.innerHTML;
  ed.mineBtn.innerHTML = icon('hourglass') + 'Mining…';
  try {
    const body = await postJson('/admin/api/glossary/mine', {});
    renderSuggestions(ed, body);
  } catch (exc) {
    toast('Mining failed: ' + String(exc.message || exc), 'error');
  } finally {
    ed.mineBtn.disabled = false;
    ed.mineBtn.innerHTML = orig;
  }
}

function renderSuggestions(ed, body) {
  const wrapEl = ed.suggestWrap;
  wrapEl.replaceChildren();
  wrapEl.hidden = false;
  const meta = body.meta || {};
  const head = document.createElement('div');
  head.className = 'glossary-suggest-head';
  head.innerHTML = icon('sparkles');
  head.append(' Suggestions · ' + (meta.n_sessions || 0) + ' transcript(s), last ' + (meta.days || '?') + 'd'
    + (meta.llm_used ? ' · LLM-assisted' : (meta.llm_failed ? ' · LLM pass failed' : '')));
  wrapEl.appendChild(head);

  const boosts = body.boost_terms || [];
  const repls = body.replacements || [];
  if (!boosts.length && !repls.length) {
    const empty = document.createElement('p');
    empty.className = 'muted small';
    empty.textContent = 'No new suggestions — your dictionary already covers the recent vocabulary.';
    wrapEl.appendChild(empty);
    return;
  }

  if (boosts.length) {
    wrapEl.append(subLabel('Candidate boost terms'), suggestionChips(boosts, function (c) {
      return c.term + ' ·' + c.count;
    }, function (c) {
      addBoost(ed, c.term);
    }));
  }

  if (repls.length) {
    wrapEl.append(subLabel('Candidate replacements'), suggestionChips(repls, function (r) {
      return r.from + ' to ' + r.to;
    }, function (r) {
      ed.model.replacements.push({ from: r.from, to: r.to });
      renderRepl(ed);
    }));
  }

  const hint = document.createElement('p');
  hint.className = 'muted small';
  hint.textContent = 'Tap a suggestion to add it, then Save.';
  wrapEl.appendChild(hint);
}

// A chip row of suggestions; accepting one applies it and removes its chip.
function suggestionChips(items, labelOf, accept) {
  const wrap = document.createElement('div');
  wrap.className = 'glossary-chips';
  items.forEach(function (item) {
    const chip = suggestionChip(labelOf(item), function () {
      accept(item); chip.remove();
    });
    wrap.appendChild(chip);
  });
  return wrap;
}

// --------------------------------------------------------------- helpers
function section(title, hint) {
  const root = document.createElement('div');
  root.className = 'glossary-section';
  const h = document.createElement('h3');
  h.className = 'opt-group-title';
  h.textContent = title;
  const p = document.createElement('p');
  p.className = 'muted small';
  p.textContent = hint;
  const body = document.createElement('div');
  root.append(h, p, body);
  return { root: root, body: body };
}

function ghostBtn(label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'ghost-btn';
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function iconBtn(glyph, label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'icon-button';
  b.innerHTML = glyph;  // glyph is an icon() SVG string (no user input)
  b.title = label;
  b.setAttribute('aria-label', label);
  b.addEventListener('click', onClick);
  return b;
}

function suggestionChip(label, onAccept) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'glossary-chip glossary-chip-suggest';
  chip.innerHTML = icon('plus');     // static SVG; label appended as text below
  chip.append(' ' + label);
  chip.title = 'Add to dictionary';
  chip.addEventListener('click', onAccept);
  return chip;
}

function subLabel(text) {
  const el = document.createElement('div');
  el.className = 'glossary-sublabel muted small';
  el.textContent = text;
  return el;
}
