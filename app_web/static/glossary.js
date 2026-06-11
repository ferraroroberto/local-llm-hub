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

export function mountGlossaryEditor(container) {
  // Per-mount working copy of the dictionary.
  const model = { replacements: [], boost_terms: [] };

  container.replaceChildren();
  container.classList.add('glossary-editor');

  // Make the shared nature unmistakable: this is one list, reachable from
  // every whisper row, not a per-model dictionary.
  const sharedNote = document.createElement('p');
  sharedNote.className = 'glossary-shared-note';
  sharedNote.textContent =
    '🔗 Shared dictionary — one list used by every whisper model. ' +
    'Editing it here (Turbo or Translate) changes it everywhere.';

  const status = document.createElement('p');
  status.className = 'muted small glossary-status';
  status.textContent = 'Loading…';

  const replSection = section('Replacements',
    'Literal fixes applied after whisper returns text — e.g. “cloud code” → “Claude Code”. Order matters; longest match wins.');
  const replList = document.createElement('div');
  replList.className = 'glossary-repl-list';
  const addReplBtn = ghostBtn('+ add rule', function () {
    model.replacements.push({ from: '', to: '' });
    renderRepl();
  });
  replSection.body.append(replList, addReplBtn);

  const boostSection = section('Boost terms',
    'Vocabulary biased into whisper at launch. Edits here bind on the next whisper start, not immediately.');
  const boostChips = document.createElement('div');
  boostChips.className = 'glossary-chips';
  const boostAddRow = document.createElement('div');
  boostAddRow.className = 'glossary-add-row';
  const boostInput = document.createElement('input');
  boostInput.type = 'text';
  boostInput.placeholder = 'add a term, Enter';
  boostInput.setAttribute('aria-label', 'Add boost term');
  boostInput.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') { ev.preventDefault(); addBoost(boostInput.value); boostInput.value = ''; }
  });
  boostAddRow.append(boostInput, ghostBtn('+ add', function () { addBoost(boostInput.value); boostInput.value = ''; }));
  boostSection.body.append(boostChips, boostAddRow);

  // Suggestions area (filled by the miner).
  const suggestWrap = document.createElement('div');
  suggestWrap.className = 'glossary-suggest';
  suggestWrap.hidden = true;

  const actions = document.createElement('div');
  actions.className = 'glossary-actions';
  const mineBtn = ghostBtn('✨ Suggest from transcripts', onMine);
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'ghost-btn primary';
  saveBtn.textContent = '💾 Save';
  saveBtn.addEventListener('click', onSave);
  actions.append(mineBtn, saveBtn);

  container.append(sharedNote, status, replSection.root, boostSection.root, suggestWrap, actions);

  // ----------------------------------------------------------- rendering
  function renderRepl() {
    replList.replaceChildren();
    model.replacements.forEach(function (rule, idx) {
      const row = document.createElement('div');
      row.className = 'glossary-repl-row';

      const from = document.createElement('input');
      from.type = 'text'; from.value = rule.from; from.placeholder = 'heard as…';
      from.setAttribute('aria-label', 'Replace from');
      from.addEventListener('input', function () { rule.from = from.value; });

      const arrow = document.createElement('span');
      arrow.className = 'glossary-arrow'; arrow.textContent = '→';

      const to = document.createElement('input');
      to.type = 'text'; to.value = rule.to; to.placeholder = 'correct to…';
      to.setAttribute('aria-label', 'Replace to');
      to.addEventListener('input', function () { rule.to = to.value; });

      const up = iconBtn('▲', 'Move up', function () { move(idx, -1); });
      const down = iconBtn('▼', 'Move down', function () { move(idx, 1); });
      up.disabled = idx === 0;
      down.disabled = idx === model.replacements.length - 1;
      const del = iconBtn('✕', 'Delete rule', function () {
        model.replacements.splice(idx, 1); renderRepl();
      });
      del.classList.add('danger');

      row.append(from, arrow, to, up, down, del);
      replList.appendChild(row);
    });
  }

  function move(idx, delta) {
    const j = idx + delta;
    if (j < 0 || j >= model.replacements.length) return;
    const tmp = model.replacements[idx];
    model.replacements[idx] = model.replacements[j];
    model.replacements[j] = tmp;
    renderRepl();
  }

  function renderBoost() {
    boostChips.replaceChildren();
    if (!model.boost_terms.length) {
      const empty = document.createElement('span');
      empty.className = 'muted small';
      empty.textContent = 'No boost terms yet.';
      boostChips.appendChild(empty);
      return;
    }
    model.boost_terms.forEach(function (term, idx) {
      const chip = document.createElement('span');
      chip.className = 'glossary-chip';
      chip.append(document.createTextNode(term));
      const x = document.createElement('button');
      x.type = 'button'; x.className = 'glossary-chip-x'; x.textContent = '✕';
      x.setAttribute('aria-label', 'Remove ' + term);
      x.addEventListener('click', function () { model.boost_terms.splice(idx, 1); renderBoost(); });
      chip.appendChild(x);
      boostChips.appendChild(chip);
    });
  }

  function addBoost(value) {
    const term = String(value || '').trim();
    if (!term) return;
    if (model.boost_terms.some(function (t) { return t.toLowerCase() === term.toLowerCase(); })) {
      toast('“' + term + '” is already a boost term', 'error');
      return;
    }
    model.boost_terms.push(term);
    renderBoost();
  }

  // ----------------------------------------------------------- load / save
  async function load() {
    try {
      const body = await jsonApi('/admin/api/glossary');
      model.replacements = (body.replacements || []).map(function (r) {
        return { from: String(r.from || ''), to: String(r.to || '') };
      });
      model.boost_terms = (body.boost_terms || []).map(String);
      status.textContent = model.replacements.length + ' replacement(s) · ' + model.boost_terms.length + ' boost term(s)';
      renderRepl();
      renderBoost();
    } catch (exc) {
      status.textContent = 'Failed to load dictionary: ' + String(exc.message || exc);
    }
  }

  async function onSave() {
    // Drop blank replacement rows before persisting.
    const replacements = model.replacements.filter(function (r) {
      return r.from.trim() && typeof r.to === 'string';
    });
    saveBtn.disabled = true;
    try {
      const body = await putJson('/admin/api/glossary', {
        replacements: replacements,
        boost_terms: model.boost_terms,
      });
      const g = body.glossary || {};
      model.replacements = (g.replacements || []).map(function (r) {
        return { from: String(r.from || ''), to: String(r.to || '') };
      });
      model.boost_terms = (g.boost_terms || []).map(String);
      renderRepl();
      renderBoost();
      toast(body.boost_terms_need_restart
        ? 'Saved · replacements live now, boost terms apply on next whisper start'
        : 'Dictionary saved', 'good');
    } catch (exc) {
      toast('Save failed: ' + String(exc.message || exc), 'error');
    } finally {
      saveBtn.disabled = false;
    }
  }

  // ----------------------------------------------------------- mining
  async function onMine() {
    mineBtn.disabled = true;
    const orig = mineBtn.textContent;
    mineBtn.textContent = '⏳ Mining…';
    try {
      const body = await postJson('/admin/api/glossary/mine', {});
      renderSuggestions(body);
    } catch (exc) {
      toast('Mining failed: ' + String(exc.message || exc), 'error');
    } finally {
      mineBtn.disabled = false;
      mineBtn.textContent = orig;
    }
  }

  function renderSuggestions(body) {
    suggestWrap.replaceChildren();
    suggestWrap.hidden = false;
    const meta = body.meta || {};
    const head = document.createElement('div');
    head.className = 'glossary-suggest-head';
    head.textContent = '✨ Suggestions · ' + (meta.n_sessions || 0) + ' transcript(s), last ' + (meta.days || '?') + 'd'
      + (meta.llm_used ? ' · LLM-assisted' : '');
    suggestWrap.appendChild(head);

    const boosts = body.boost_terms || [];
    const repls = body.replacements || [];
    if (!boosts.length && !repls.length) {
      const empty = document.createElement('p');
      empty.className = 'muted small';
      empty.textContent = 'No new suggestions — your dictionary already covers the recent vocabulary.';
      suggestWrap.appendChild(empty);
      return;
    }

    if (boosts.length) {
      suggestWrap.appendChild(subLabel('Candidate boost terms'));
      const wrap = document.createElement('div');
      wrap.className = 'glossary-chips';
      boosts.forEach(function (c) {
        const chip = suggestionChip(c.term + ' ·' + c.count, function () {
          addBoost(c.term); chip.remove();
        });
        wrap.appendChild(chip);
      });
      suggestWrap.appendChild(wrap);
    }

    if (repls.length) {
      suggestWrap.appendChild(subLabel('Candidate replacements'));
      const wrap = document.createElement('div');
      wrap.className = 'glossary-chips';
      repls.forEach(function (r) {
        const chip = suggestionChip(r.from + ' → ' + r.to, function () {
          model.replacements.push({ from: r.from, to: r.to });
          renderRepl(); chip.remove();
        });
        wrap.appendChild(chip);
      });
      suggestWrap.appendChild(wrap);
    }

    const hint = document.createElement('p');
    hint.className = 'muted small';
    hint.textContent = 'Tap a suggestion to add it, then Save.';
    suggestWrap.appendChild(hint);
  }

  load();
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
  b.className = 'icon-btn';
  b.textContent = glyph;
  b.title = label;
  b.setAttribute('aria-label', label);
  b.addEventListener('click', onClick);
  return b;
}

function suggestionChip(label, onAccept) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'glossary-chip glossary-chip-suggest';
  chip.textContent = '＋ ' + label;
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
