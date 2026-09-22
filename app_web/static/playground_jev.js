/* Playground — Decision (Jev) card (#611).
 *
 * TypeSafe's Jev answers typed questions (noul / choice / score) about a
 * `state` with per-option probabilities and a confidence, so the card takes
 * the questions as raw JSON (the whole vendor API, no form to outgrow) and
 * renders each answer as probability bars. Evaluations go through
 * /admin/api/playground/systemone → the hub's own /v1/systemone, so they land
 * in the request ring like any other caller's.
 */

import { els } from './state.js';
import { jsonApi, postJson, toast } from './api.js';

// The vendor quickstart (docs.typesafe.ai/api.md) — one question of each type.
const SAMPLE_STATE = 'Help! My payouts have been failing for 3 days.';
const SAMPLE_QUESTIONS = {
  is_urgent: {
    type: 'noul',
    instructions: 'Does this convey urgency?',
    criteria: { true: 'Explicitly time-sensitive', false: 'No urgency expressed' },
  },
  department: {
    type: 'choice',
    instructions: 'Which team should handle this?',
    criteria: {
      billing: 'Payments, invoicing, refunds',
      technical: 'Bugs, outages, integrations',
      sales: 'Pricing, upgrades, new accounts',
    },
  },
  frustration: {
    type: 'score',
    instructions: 'How frustrated is the customer?',
    criteria: ['Calm', 'Frustrated', 'Very angry'],
  },
};

export async function fetchJevInfo() {
  if (!els.jevModel) return;
  try {
    const body = await jsonApi('/admin/api/playground/systemone_info');
    els.jevModel.innerHTML = '';
    (body.models || []).forEach(function (id) {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = id;
      els.jevModel.appendChild(opt);
    });
    if (els.jevKeyNote) {
      els.jevKeyNote.hidden = !!body.key_configured;
      els.jevKeyNote.textContent = body.key_configured ? ''
        : 'TYPESAFE_API_KEY is not set in the hub’s .env — evaluations will answer 503.';
    }
  } catch (_) { /* ignore — the card still works once the hub answers */ }
}

export function wireJev() {
  if (!els.jevEvalBtn) return;
  resetSample();
  els.jevEvalBtn.addEventListener('click', evaluate);
  if (els.jevResetBtn) els.jevResetBtn.addEventListener('click', resetSample);
  if (els.jevQuestions) {
    els.jevQuestions.addEventListener('input', function () { showJsonError(''); });
  }
}

function resetSample() {
  if (els.jevState) els.jevState.value = SAMPLE_STATE;
  if (els.jevQuestions) els.jevQuestions.value = JSON.stringify(SAMPLE_QUESTIONS, null, 2);
  showJsonError('');
  if (els.jevResults) els.jevResults.hidden = true;
  if (els.jevLatency) els.jevLatency.textContent = '';
}

function showJsonError(msg) {
  if (!els.jevJsonError) return;
  els.jevJsonError.textContent = msg;
  els.jevJsonError.hidden = !msg;
}

function parseQuestions() {
  let parsed;
  try {
    parsed = JSON.parse(els.jevQuestions.value || '');
  } catch (exc) {
    showJsonError('Invalid JSON — ' + exc.message);
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || !Object.keys(parsed).length) {
    showJsonError('Questions must be a JSON object: { "name": { "type": …, "instructions": …, "criteria": … } }');
    return null;
  }
  return parsed;
}

async function evaluate() {
  const state = (els.jevState.value || '').trim();
  if (!state) {
    toast('State is empty.', 'error');
    return;
  }
  const questions = parseQuestions();
  if (!questions) return;

  els.jevEvalBtn.disabled = true;
  els.jevLatency.textContent = 'evaluating…';
  const t0 = performance.now();
  try {
    const body = await postJson('/admin/api/playground/systemone', {
      model: els.jevModel.value, state: state, questions: questions,
    });
    els.jevLatency.textContent = (performance.now() - t0).toFixed(0) + ' ms';
    renderAnswers(body);
  } catch (exc) {
    els.jevLatency.textContent = '';
    toast(String(exc.message || exc), 'error');
  } finally {
    els.jevEvalBtn.disabled = false;
  }
}

function renderAnswers(body) {
  const answers = (body && body.answers) || {};
  els.jevServedModel.textContent = body && body.model ? '· ' + body.model : '';
  const frag = document.createDocumentFragment();
  Object.keys(answers).forEach(function (name) {
    frag.appendChild(buildAnswer(name, answers[name] || {}));
  });
  els.jevAnswers.replaceChildren(frag);

  const usage = (body && body.usage) || {};
  els.jevUsage.replaceChildren();
  [['Input', usage.input_tokens || 0], ['Output', usage.output_tokens || 0]].forEach(function (r) {
    const div = document.createElement('div');
    const k = document.createElement('span');
    k.className = 'muted';
    k.textContent = r[0];
    const v = document.createElement('span');
    v.textContent = String(r[1]);
    div.append(k, v);
    els.jevUsage.appendChild(div);
  });
  els.jevResults.hidden = false;
}

function buildAnswer(name, a) {
  const wrap = document.createElement('div');
  wrap.className = 'jev-answer';

  const head = document.createElement('div');
  head.className = 'jev-answer-head';
  const title = document.createElement('code');
  title.textContent = name;
  const type = document.createElement('span');
  type.className = 'badge';
  type.textContent = a.type || '?';
  const verdict = document.createElement('span');
  verdict.className = 'jev-verdict';
  head.append(title, type, verdict);
  if (typeof a.confidence === 'number') {
    const conf = document.createElement('span');
    conf.className = 'muted small';
    conf.textContent = 'confidence ' + a.confidence.toFixed(2);
    head.appendChild(conf);
  }
  wrap.appendChild(head);

  if (a.type === 'noul' && typeof a.noul === 'number') {
    verdict.textContent = a.noul >= 0.5 ? 'yes' : 'no';
    wrap.appendChild(bar('P(yes)', a.noul, a.noul >= 0.5));
  } else if (a.type === 'choice') {
    verdict.textContent = a.choice != null ? String(a.choice) : '';
    const probs = a.probabilities || {};
    Object.keys(probs)
      .sort(function (x, y) { return probs[y] - probs[x]; })
      .forEach(function (opt) { wrap.appendChild(bar(opt, probs[opt], opt === a.choice)); });
  } else if (a.type === 'score') {
    const legend = a.legend || {};
    const levels = Object.keys(a.probabilities || legend)
      .sort(function (x, y) { return Number(x) - Number(y); });
    if (typeof a.score === 'number') {
      const nearest = String(Math.round(a.score));
      verdict.textContent = a.score.toFixed(2) + (legend[nearest] ? ' · ' + legend[nearest] : '');
    }
    const probs = a.probabilities || {};
    levels.forEach(function (lvl) {
      const label = lvl + (legend[lvl] ? ' · ' + legend[lvl] : '');
      wrap.appendChild(bar(label, probs[lvl] || 0, typeof a.score === 'number' && String(Math.round(a.score)) === lvl));
    });
  } else {
    // A type this card doesn't know yet — show the raw answer, never drop it.
    const pre = document.createElement('pre');
    pre.className = 'logpane';
    pre.textContent = JSON.stringify(a, null, 2);
    wrap.appendChild(pre);
  }
  return wrap;
}

function bar(label, p, chosen) {
  const value = Math.max(0, Math.min(1, Number(p) || 0));
  const row = document.createElement('div');
  row.className = 'jev-bar' + (chosen ? ' chosen' : '');
  const l = document.createElement('span');
  l.className = 'jev-bar-label';
  l.textContent = label;
  l.title = label;
  const track = document.createElement('span');
  track.className = 'jev-bar-track';
  const fill = document.createElement('span');
  fill.className = 'jev-bar-fill';
  fill.style.width = (value * 100).toFixed(1) + '%';
  track.appendChild(fill);
  const v = document.createElement('span');
  v.className = 'jev-bar-val';
  v.textContent = value.toFixed(2);
  row.append(l, track, v);
  return row;
}
