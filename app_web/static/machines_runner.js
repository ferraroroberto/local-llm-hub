/* Machines tab — the GitHub Actions runner section of a machine's sheet.
 *
 * A machine whose card carries `runner: true` hosts a self-hosted runner. Its
 * state is read on demand from /admin/api/machines/{id}/runner (an SSH read
 * plus GitHub reads, too heavy for the tab's 10 s poll), refreshed while the
 * machine's sheet or detail pane is open, and re-read by the server after
 * every Start / Stop / Install so the toast reports what the re-read saw.
 *
 * The service and GitHub's view are shown as separate rows because they fail
 * separately; a source that could not be read says "Not confirmed", never a
 * fine state (design.md "stale"/unknown rule).
 */

import { state } from './state.js';
import { jsonApi, postJson, toast, escapeHtml, fmtDuration, isAuthError } from './api.js';
import { icon } from './_vendored/icons/icons.js';
import { infoButton, defineInfo } from './rows.js';

// The server caches GitHub for 15 s; asking more often only repaints.
const REFRESH_MS = 20000;

let rerender = function () {};
const inflight = {};

/* machines.js hands in its render, so a landed read repaints the open sheet. */
export function initRunner(onChange) { rerender = onChange; }

defineInfo('actions-runner', 'GitHub Actions runner',
  '<p>This machine runs a self-hosted GitHub Actions runner. Service is the runner’s launch service on the machine; '
  + 'GitHub is what GitHub itself reports (online or offline, idle or running a job). They are read separately, '
  + 'so a disagreement shows up instead of hiding.</p>'
  + '<p>Stop is refused while a job runs, and while GitHub’s view cannot be read. Start, Stop and Install run over '
  + 'SSH, then both sources are read again and the toast says what they showed.</p>'
  + '<p>The runner starts at boot only through automatic login for its user, because the iOS Simulator needs that '
  + 'login session. Turning automatic login on needs the user’s password, so it is a step at the Mac; the Boot row '
  + 'says when it is still outstanding.</p>');

// --------------------------------------------------------- fetch
function slot(id) {
  if (!state.machinesRunner[id]) state.machinesRunner[id] = { data: null, at: 0, error: false };
  return state.machinesRunner[id];
}

export async function fetchRunner(id, fresh) {
  if (inflight[id]) return;
  inflight[id] = true;
  const s = slot(id);
  s.at = Date.now();
  try {
    s.data = await jsonApi('/admin/api/machines/' + encodeURIComponent(id) + '/runner' + (fresh ? '?fresh=true' : ''));
    s.error = false;
  } catch (exc) {
    if (isAuthError(exc)) { inflight[id] = false; return; }
    // Keep the last good reading on screen, marked as not current.
    s.error = true;
  } finally {
    inflight[id] = false;
  }
  rerender();
}

/* Called after every machines render: read the open machine's runner when it
 * has none yet or the reading is old. The timestamp is set on request, so the
 * repaint a read triggers does not start another. */
export function refreshRunnerIfDue(machine) {
  if (!machine || !machine.runner || inflight[machine.id] || state.machinesRunnerBusy[machine.id]) return;
  const s = slot(machine.id);
  if (Date.now() - s.at >= REFRESH_MS) fetchRunner(machine.id, false);
}

// --------------------------------------------------------- words
const HEADLINE = {
  online: ['Online · idle', ''],
  busy: ['Online · running a job', ''],
  stopped: ['Stopped', 'attention'],
  not_installed: ['Not installed', 'attention'],
  mismatch: ['Service and GitHub disagree', 'attention'],
  unknown: ['Not confirmed', 'attention'],
};
// Third entry: the server's reason explains this state, so the card shows it.
const SERVICE = {
  running: ['Running', ''],
  stopped: ['Stopped', 'attention'],
  loaded: ['Loaded, not running', 'attention', true],
  not_installed: ['Not installed', 'attention'],
  unknown: ['Not confirmed', 'attention', true],
};
const RUN = {
  passed: ['Passed', ''],
  failed: ['Failed', 'attention'],
  cancelled: ['Cancelled', ''],
  running: ['Running', ''],
  queued: ['Queued', ''],
  none: ['Not run yet', ''],
  unknown: ['Not confirmed', 'attention'],
};

// Third entry: the words when the runner is also running a job.
const GITHUB = {
  online: ['Online · idle', '', 'Online · busy'],
  offline: ['Offline', 'attention'],
  not_registered: ['Not registered', 'attention'],
  unknown: ['Not confirmed', 'attention'],
};

function githubWords(g) {
  const words = GITHUB[g.state] || GITHUB.unknown;
  return g.busy && words[2] ? [words[2], words[1]] : words;
}

function ago(iso) {
  const t = Date.parse(iso || '');
  if (!Number.isFinite(t)) return '';
  const word = fmtDuration((Date.now() - t) / 1000, { days: true, empty: '' });
  return word ? ' · ' + word + ' ago' : '';
}

function bootWords(b) {
  if (!b) return ['Not confirmed', 'attention'];
  if (b.state === 'ok') return ['Auto-login on · sleep off', ''];
  if (b.state === 'owner_step') return ['Needs a step at the Mac', 'attention'];
  return ['Not confirmed', 'attention'];
}

function pair(label, words, extra, attr) {
  return '<dt>' + escapeHtml(label) + '</dt><dd' + (words[1] ? ' data-tone="' + words[1] + '"' : '')
    + (attr ? ' ' + attr : '') + '>' + escapeHtml(words[0] + (extra || '')) + '</dd>';
}

function verbButton(verb, glyph, label, busyLabel, allowed, busy) {
  const isBusy = busy === verb;
  return '<button type="button" class="button-tint" data-machine-action="runner-' + verb + '"'
    + (!allowed || busy ? ' disabled' : '') + '>' + icon(glyph)
    + '<span>' + escapeHtml(isBusy ? busyLabel : label) + '</span></button>';
}

// --------------------------------------------------------- section
export function runnerSection(m) {
  if (!m.runner) return '';
  const s = slot(m.id);
  const d = s.data;
  const busy = state.machinesRunnerBusy[m.id] || '';
  const head = '<div class="sheet-row">' + icon('cpu') + '<span class="sheet-row-label">Runner</span>';
  const info = infoButton('actions-runner', 'About the Actions runner') + '</div>';
  if (!d) {
    const words = s.error ? ['Not confirmed', 'attention'] : ['Reading…', ''];
    return '<div class="sheet-section machine-runner">' + head
      + '<span class="sheet-row-value" data-machine-runner-state' + (words[1] ? ' data-tone="' + words[1] + '"' : '') + '>'
      + escapeHtml(words[0]) + '</span>' + info
      + (s.error ? '<p class="sheet-meta">Could not read the runner.</p>' : '')
      + '</div>';
  }
  const headline = HEADLINE[d.headline] || HEADLINE.unknown;
  const service = SERVICE[d.service.state] || SERVICE.unknown;
  const run = d.last_run ? (RUN[d.last_run.state] || RUN.unknown) : null;
  const notes = [];
  if (d.problem) notes.push(d.problem + '.');
  if (service[2] && d.service.reason) notes.push(d.service.reason);
  if (d.github.state === 'unknown' && d.github.reason) notes.push(d.github.reason);
  if (d.last_run && d.last_run.state === 'unknown' && d.last_run.reason) notes.push(d.last_run.reason);
  const steps = (d.boot && d.boot.owner_steps) || [];
  const blocked = (d.blocked && d.blocked.stop) || '';
  const buttons = (d.actions.install
    ? verbButton('install', 'plus', 'Install service', 'Installing…', true, busy)
    : verbButton('start', 'play', 'Start', 'Starting…', d.actions.start, busy))
    + verbButton('stop', 'square', 'Stop', 'Stopping…', d.actions.stop, busy);
  return '<div class="sheet-section machine-runner" data-runner-headline="' + escapeHtml(d.headline) + '">' + head
    + '<span class="sheet-row-value" data-machine-runner-state' + (headline[1] ? ' data-tone="' + headline[1] + '"' : '') + '>'
    + escapeHtml(headline[0]) + '</span>' + info
    + '<dl class="sheet-pairs">'
    + pair('Service', service, '', 'data-runner-service')
    + pair('GitHub', githubWords(d.github), '', 'data-runner-github')
    + (run ? pair('Last run', run, ago(d.last_run.at), 'data-runner-last-run') : '')
    + pair('Boot', bootWords(d.boot), '', 'data-runner-boot')
    + '</dl>'
    + (s.error ? '<p class="sheet-meta" data-tone="attention">Not current: the last read failed.</p>' : '')
    + notes.map(function (n) { return '<p class="sheet-meta" data-runner-note>' + escapeHtml(n) + '</p>'; }).join('')
    + steps.map(function (n) { return '<p class="sheet-meta" data-tone="attention" data-runner-step>' + escapeHtml(n) + '</p>'; }).join('')
    + (blocked && d.actions.stop === false ? '<p class="sheet-meta" data-runner-blocked>' + escapeHtml(blocked) + '</p>' : '')
    + '<div class="sheet-buttons">' + buttons + '</div>'
    + '</div>';
}

// --------------------------------------------------------- verbs
/* The server answers after a re-read, so the toast is the outcome, not the
 * request: only a full match (service and GitHub) is the good tone. */
export async function runnerVerb(id, displayName, verb) {
  if (state.machinesRunnerBusy[id]) return;
  state.machinesRunnerBusy = Object.assign({}, state.machinesRunnerBusy);
  state.machinesRunnerBusy[id] = verb;
  rerender();
  try {
    const body = await postJson('/admin/api/machines/' + encodeURIComponent(id) + '/runner/' + encodeURIComponent(verb), {});
    slot(id).data = body.runner;
    slot(id).error = false;
    slot(id).at = Date.now();
    toast(body.message, body.ok ? 'good' : 'error');
  } catch (exc) {
    if (isAuthError(exc)) return;
    // A 409 carries the reason the verb is not allowed (a job is running, say);
    // it is written for this toast. Anything else is a sanitized line.
    toast(exc.status === 409 && exc.message ? exc.message : 'Runner action failed on ' + displayName + '.', 'error');
    slot(id).at = 0;
  } finally {
    const next = Object.assign({}, state.machinesRunnerBusy);
    delete next[id];
    state.machinesRunnerBusy = next;
    rerender();
  }
}
