/* Hub tab (glance + services as exceptions + one Activity feed since #738).
 *
 * The tab answers "is the hub serving, and is anything failing" first:
 *   - the glance card: requests, errors and median latency since hub start
 *     (the hub keeps no hourly history, so it names its window), one load
 *     sparkline whose series a segmented control swaps, and the process line;
 *   - services as exceptions: healthy Docker / Langfuse / AgentsView fold
 *     into one row that opens the services sheet; a stopped or failing one is
 *     its own row with Start. The Langfuse sheet carries the OTel health the
 *     OTel pane shows (state, PII mode, endpoint, health, Open Langfuse UI,
 *     offline hint);
 *   - one Activity feed of shared rows (rows.js) over the hub request stream,
 *     Requests or Errors; a row with a trace opens its sheet: the prompt and
 *     completion preview, a thumbs score and the Langfuse link (the OTel
 *     pane's trace functions, same helpers). By model (the per-backend
 *     counters + summary) and the server log open from the feed's foot.
 * Health & install and the process id live in Settings since #741; a failing
 * install check still reaches this tab's header line.
 */

import { els, state } from './state.js';
import { jsonApi, postJson, eventStream, toast, escapeHtml, fmtClock, renderCounterTable, shortGpu, modelLabelText, applyListFilter, isAuthError, tokPair } from './api.js';
import { langfuseTraceUrl, langfuseUiBase, fetchOtelHealth, hydrateTraceDetail, postFeedback } from './langfuse.js';
import { icon } from './_vendored/icons/icons.js';
import { row, chip, setHeadLine, defineInfo } from './rows.js';

defineInfo('hub-activity', 'About activity',
  '<p>Every request that passes through this hub: the most recent 50 here, the errors among them '
  + 'under Errors. By model counts every request since the hub started, per backend; the counts '
  + 'reset when the hub restarts.</p>'
  + '<p>Audio posted straight to the speech servers on <code>:8090</code> / <code>:8091</code> '
  + 'bypasses the hub, so it is neither listed nor counted.</p>');

// --------------------------------------------------------- status / uptime
export async function fetchHubStatus() {
  try {
    const body = await jsonApi('/admin/api/hub/status');
    state.status = body;
    state.hubStatusOk = true;
    if (els.aboutPid) els.aboutPid.textContent = body.pid || '—';
    if (els.hubUptime) els.hubUptime.textContent = fmtUptime(body.uptime_s);
  } catch (exc) {
    if (isAuthError(exc)) return;
    state.hubStatusOk = false;
  }
  renderHeadLine();
}

function fmtUptime(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  if (seconds < 60) return Math.floor(seconds) + 's';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.floor(seconds % 60) + 's';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h < 48) return h + 'h ' + m + 'm';
  return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
}

function fmtMs(ms) {
  if (ms == null || !Number.isFinite(ms)) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : Math.round(ms) + ' ms';
}

/* The header line: what is wrong now (an unreachable hub, a down service, the
 * errors in the feed — the same list the Errors view draws — or a failing
 * install check once Settings has run them), else the uptime. */
export function renderHeadLine() {
  const el = els.hubHeadLine;
  if (!el) return;
  if (state.hubStatusOk === null) { setHeadLine(el, [], 'Reading hub…'); return; }
  const parts = [];
  if (state.hubStatusOk === false) parts.push({ text: 'Hub unreachable', tone: 'danger' });
  serviceRows().filter(function (s) { return s.failing; }).forEach(function (s) {
    parts.push({ text: s.name + ' down', tone: s.key === 'agentsview' ? 'attention' : 'danger' });
  });
  const errs = (state.recentErrors || []).length;
  if (errs) parts.push({ text: errs + (errs === 1 ? ' error' : ' errors'), tone: 'attention' });
  const failing = (state.installChecks || []).filter(function (c) {
    return c.status === 'missing' || c.status === 'error';
  }).length;
  if (failing) parts.push({ text: failing + (failing === 1 ? ' install check failing' : ' install checks failing'), tone: 'attention' });
  const up = state.status && Number.isFinite(state.status.uptime_s) ? 'Up ' + fmtUptime(state.status.uptime_s) : 'Serving';
  setHeadLine(el, parts, up);
}

// --------------------------------------------------------- glance + By model
/* One read feeds both the glance and the By model sheet: the per-backend
 * counters and their summary since hub start (the OTel leaderboard's source
 * too — one table, not two). A failed read never shows zeros. */
export async function fetchCounters() {
  try {
    const body = await jsonApi('/admin/api/telemetry/metrics');
    state.hubMetrics = body;
    state.counters = body.counters || [];
    state.hubMetricsOk = true;
  } catch (exc) {
    if (isAuthError(exc)) return;
    state.hubMetricsOk = false;
  }
  renderGlance();
  renderByModel();
}

function renderGlance() {
  const s = (state.hubMetrics && state.hubMetrics.summary) || null;
  const ok = state.hubMetricsOk === true && s;
  if (els.hubReqCount) els.hubReqCount.textContent = ok ? String(s.requests) : '—';
  if (els.hubErrCount) {
    els.hubErrCount.textContent = ok ? String(s.errors) : '—';
    els.hubErrCount.dataset.tone = ok && s.errors ? 'attention' : '';
  }
  if (els.hubP50) els.hubP50.textContent = ok ? fmtMs(s.p50_ms) : '—';
  if (els.hubWindow) {
    els.hubWindow.textContent = ok
      ? 'since start · ' + fmtUptime(s.since_uptime_s)
      : (state.hubMetricsOk === false ? 'since start · not confirmed' : 'since start · reading…');
  }
}

function renderByModel() {
  renderCounterTable(els.countersTable, state.counters);
  const s = (state.hubMetrics && state.hubMetrics.summary) || null;
  const text = state.hubMetricsOk === true && s
    ? s.requests + ' req · ' + (s.requests ? ((s.errors / s.requests) * 100).toFixed(1) : '0.0') + '% err · since '
      + fmtUptime(s.since_uptime_s)
    : (state.hubMetricsOk === false ? 'Not confirmed — the counters could not be read.' : 'Reading…');
  if (els.hubByModelSummary) els.hubByModelSummary.textContent = text;
  if (els.byModelMeta) els.byModelMeta.textContent = text;
}

// --------------------------------------------------------- activity feed
function prependRequest(rec) {
  // Dedup by ts. The SSE seed (20 most-recent records) is re-sent every
  // time the EventSource (re)connects — and the browser auto-reconnects
  // after network blips, a tab-switch round-trip, or any uvicorn keepalive
  // drop. Without dedup each reconnect duplicates whatever's still in the
  // server-side ring. `ts` is the middleware's wall-clock float seconds,
  // unique per request because the recording `finally` only fires once.
  const isDup = function (arr) {
    return rec && rec.ts != null && arr.some(function (r) { return r.ts === rec.ts; });
  };
  if (!isDup(state.liveRequests)) {
    state.liveRequests = [rec].concat(state.liveRequests).slice(0, 50);
    renderRequests();
  }
  if (rec.status >= 400 && !isDup(state.recentErrors)) {
    state.recentErrors = [rec].concat(state.recentErrors).slice(0, 50);
    renderErrors();
    renderHeadLine();
  }
}

/* Route-specific one-liner (e.g. "3 questions" on /v1/systemone, #611). */
function activityMeta(r, withError) {
  const bits = [];
  if (withError && r.error_detail) bits.push(String(r.error_detail).slice(0, 80));
  if (r.backend) bits.push(r.backend);
  if (r.in_tok || r.out_tok) bits.push(tokPair(r.in_tok, r.out_tok) + ' tok');
  if (r.detail) bits.push(r.detail);
  return bits.join(' · ');
}

/* One request on the shared row: the model first, status and latency, the
 * meta, the clock last (J-10, #647). A failure's status is its chip; a row
 * with a trace opens the trace sheet, one without does nothing on tap. */
function activityRow(r, withError) {
  const failed = r.status >= 400;
  const hasTrace = !!r.trace_id;
  return row({
    cls: 'activity-row' + (hasTrace ? '' : ' is-static'),
    attrs: ' data-ts="' + escapeHtml(String(r.ts)) + '"',
    title: modelLabelText(r, '(no model)'),
    lead: fmtMs(r.latency_ms),
    chips: failed ? [{ text: String(r.status), tone: r.status >= 500 ? 'danger' : 'attention' }] : [],
    meta: activityMeta(r, withError),
    mainAttrs: hasTrace ? ' data-act="trace" aria-haspopup="dialog"' : ' tabindex="-1"',
    trail: '<span class="action-row-trail activity-clock">' + escapeHtml(fmtClock(r.ts) || '—') + '</span>',
  });
}

function renderRequests() {
  const items = state.liveRequests || [];
  const list = els.liveRequestsList;
  if (!list) return;
  list.innerHTML = items.map(function (r) { return activityRow(r, false); }).join('');
  renderActivityChrome();
  applyListFilter(els.liveRequestsFilter, list);
}

function renderErrors() {
  const items = state.recentErrors || [];
  const list = els.recentErrorsList;
  if (!list) return;
  list.innerHTML = items.map(function (r) { return activityRow(r, true); }).join('');
  renderActivityChrome();
  applyListFilter(els.liveRequestsFilter, list);
}

/* The scope control, which list shows, its empty state and the filter's
 * placeholder — the count's one home (design.md action-row). */
function renderActivityChrome() {
  const errorsView = state.activityScope === 'errors';
  const reqs = (state.liveRequests || []).length;
  const errs = (state.recentErrors || []).length;
  if (els.liveRequestsList) els.liveRequestsList.hidden = errorsView;
  if (els.recentErrorsList) els.recentErrorsList.hidden = !errorsView;
  if (els.liveRequestsEmpty) els.liveRequestsEmpty.hidden = errorsView || reqs > 0;
  if (els.recentErrorsEmpty) els.recentErrorsEmpty.hidden = !errorsView || errs > 0;
  if (els.activityScope) {
    els.activityScope.querySelectorAll('.segmented-item').forEach(function (b) {
      b.setAttribute('aria-pressed', String((b.dataset.scope === 'errors') === errorsView));
    });
  }
  if (els.liveRequestsFilter) {
    els.liveRequestsFilter.placeholder = errorsView
      ? 'Filter ' + errs + (errs === 1 ? ' error' : ' errors') + '…'
      : 'Filter ' + reqs + (reqs === 1 ? ' request' : ' requests') + '…';
  }
}

function findRecord(ts) {
  const all = (state.liveRequests || []).concat(state.recentErrors || []);
  return all.find(function (r) { return String(r.ts) === String(ts); });
}

/* A request's sheet: its facts, the prompt and completion preview (with the
 * fallback message while Langfuse is offline), a thumbs score and the
 * Langfuse link — the OTel pane's trace functions, its own helpers. */
function openTrace(r) {
  if (!r || !r.trace_id || !els.traceSheet) return;
  els.traceSheetTitle.textContent = modelLabelText(r, '(no model)');
  const pairs = [
    ['Status', String(r.status)], ['Latency', fmtMs(r.latency_ms)],
    ['Tokens', tokPair(r.in_tok, r.out_tok)], ['Backend', r.backend || '—'],
    ['Time', fmtClock(r.ts) || '—'],
  ];
  els.traceSheetBody.innerHTML = '<div class="sheet-section" data-trace-id="' + escapeHtml(r.trace_id) + '">'
    + '<dl class="sheet-pairs">' + pairs.map(function (p) {
      return '<dt>' + escapeHtml(p[0]) + '</dt><dd>' + escapeHtml(p[1]) + '</dd>';
    }).join('') + '</dl>'
    + (r.error_detail ? '<p class="sheet-meta trace-error">' + escapeHtml(String(r.error_detail).slice(0, 280)) + '</p>' : '')
    + '</div>'
    + '<div class="sheet-section"><div class="trace-detail"><span class="muted">Loading…</span></div></div>'
    + '<div class="sheet-section trace-actions"><span class="icon-button-group">'
    + '<button type="button" class="icon-button trace-thumb" data-thumbs="1" aria-label="Thumbs up" title="Thumbs up">' + icon('thumbs-up') + '</button>'
    + '<button type="button" class="icon-button trace-thumb" data-thumbs="-1" aria-label="Thumbs down" title="Thumbs down">' + icon('thumbs-down') + '</button>'
    + '</span>'
    + '<a class="button-tint trace-link" target="_blank" rel="noopener" href="' + escapeHtml(langfuseTraceUrl(r.trace_id)) + '">'
    + icon('external-link') + '<span>Open in Langfuse</span></a>'
    + '</div>';
  if (!els.traceSheet.open) els.traceSheet.showModal();
  hydrateTraceDetail(els.traceSheetBody.querySelector('.trace-detail'), r.trace_id);
}

// --------------------------------------------------------- log tail
let logBuf = [];

function appendLogLine(line) {
  if (state.logPaused) return;
  logBuf.push(line);
  if (logBuf.length > 800) logBuf = logBuf.slice(-800);
  const pre = els.hubLog;
  if (!pre) return;
  pre.textContent = logBuf.join('\n');
  pre.scrollTop = pre.scrollHeight;
}

// --------------------------------------------------------- streams
export function startHubStreams() {
  stopHubStreams();
  state.hubStreamCtl = eventStream('/admin/api/hub/requests/stream', {
    message: function (data) {
      if (!data || typeof data !== 'object') return;
      prependRequest(data);
    },
  });
  state.hubLogStreamCtl = eventStream('/admin/api/hub/log/tail', {
    message: function (data) {
      if (typeof data === 'string') appendLogLine(data);
    },
  });
}

export function stopHubStreams() {
  if (state.hubStreamCtl) { try { state.hubStreamCtl.close(); } catch (_) {} state.hubStreamCtl = null; }
  if (state.hubLogStreamCtl) { try { state.hubLogStreamCtl.close(); } catch (_) {} state.hubLogStreamCtl = null; }
}

// --------------------------------------------------------- services (issue #27)
export async function fetchServicesStatus() {
  try {
    const body = await jsonApi('/admin/api/services/status');
    state.services = body;
  } catch (exc) {
    if (isAuthError(exc)) return;
    // Probe error itself — render an "unreachable" state. `probeFailed`
    // tells renderServices() this isn't a real launchable=false verdict
    // (we never got far enough to check the install path), so it must
    // not show the "Docker Desktop install not found" hint.
    state.services = { docker: { running: false, error: String(exc.message || exc) },
                       langfuse: { reachable: false, error: '' },
                       launchable: false, platform: '', probeFailed: true };
  }
  renderServices();
}

/* The three services as facts: whether each counts as failing (down when it
 * should be up; AgentsView only when it is enabled and installed) and whether
 * it can be started from here. */
function serviceRows() {
  const body = state.services;
  if (!body) return [];
  const docker = body.docker || {};
  const lf = body.langfuse || {};
  const av = body.agentsview || {};
  const avEnabled = !!av.host;
  return [
    { key: 'docker', name: 'Docker', glyph: 'container', failing: !docker.running,
      canStart: !!(body.launchable && !docker.running) },
    { key: 'langfuse', name: 'Langfuse', glyph: 'radio', failing: !lf.reachable, canStart: !lf.reachable },
    { key: 'agentsview', name: 'AgentsView', glyph: 'code',
      failing: avEnabled && av.installed !== false && !av.reachable,
      canStart: !!(avEnabled && !av.reachable && av.installed) },
  ];
}

const SERVICE_BUSY = { docker: 'dockerBusy', langfuse: 'langfuseBusy', agentsview: 'agentsviewBusy' };

function renderServiceRows() {
  const list = els.servicesList;
  if (!list) return;
  const rows = serviceRows();
  if (!rows.length) {
    list.innerHTML = row({ cls: 'services-summary', glyph: 'layers', title: 'Services', lead: 'Checking…',
      mainAttrs: ' data-act="services" aria-haspopup="dialog"' });
    return;
  }
  const failing = rows.filter(function (s) { return s.failing; });
  const html = failing.map(function (s) {
    const busy = !!state[SERVICE_BUSY[s.key]];
    return row({
      cls: 'service-row',
      attrs: ' data-svc="' + s.key + '"',
      glyph: s.glyph,
      badge: 'down',
      title: s.name,
      lead: 'Down',
      meta: s.key === 'langfuse' ? 'traces and previews unavailable' : '',
      mainAttrs: ' data-act="' + (s.key === 'langfuse' ? 'langfuse' : 'services') + '" aria-haspopup="dialog"',
      verb: s.canStart ? {
        attrs: ' data-act="svc-start" aria-label="Start ' + s.name + '"',
        label: busy ? 'Starting…' : 'Start', glyph: 'play', disabled: busy,
      } : null,
    });
  });
  const up = rows.length - failing.length;
  html.push(row({
    cls: 'services-summary',
    glyph: 'layers',
    title: 'Services',
    lead: failing.length ? up + ' of ' + rows.length + ' up' : 'All up',
    meta: rows.map(function (s) { return s.name; }).join(' · '),
    mainAttrs: ' data-act="services" aria-haspopup="dialog"',
  }));
  list.innerHTML = html.join('');
}

/* A Services sheet row's detail is a raw error or version line (#647, J-10).
 * A short one stays inline; a long one is a one-line, ellipsised disclosure
 * whose full text opens in place. Nothing is dropped. */
const DETAIL_INLINE_MAX = 40;

function longDetailHtml(text) {
  return '<details class="services-detail"><summary title="Show the full detail">'
    + '<span class="services-detail-text">' + escapeHtml(text) + '</span>'
    + icon('chevron-down') + '</summary></details>';
}

// Re-rendering on every poll must not snap an open disclosure shut, so a row
// only rewrites its detail when the text changed.
function setDetail(el, text) {
  if (!el || el.dataset.detail === text) return;
  el.dataset.detail = text;
  if (text.length > DETAIL_INLINE_MAX) el.innerHTML = longDetailHtml(text);
  else el.textContent = text;
}

/* A sheet row's state in words: a healthy service is plain text, never a
 * coloured dot (design.md: success is never a status); only a failure tones. */
function setStatusPill(rootEl, textEl, kind, text) {
  if (!rootEl) return;
  rootEl.classList.remove('good', 'warn', 'danger');
  if (kind) rootEl.classList.add(kind);
  if (textEl) textEl.textContent = text;
}

function renderServices() {
  const body = state.services;
  if (!body) return;
  const docker = body.docker || {};
  const lf = body.langfuse || {};

  setStatusPill(els.dockerStatus, els.dockerStatusText, docker.running ? '' : 'danger', docker.running ? 'up' : 'down');
  setDetail(els.dockerDetail, docker.running
    ? (docker.server_version ? 'engine ' + docker.server_version : '')
    : (docker.error || ''));
  // Start/Stop (#284) — only offered where launch_docker_desktop() actually
  // knows how (win32 + a found Docker Desktop install).
  if (els.dockerStartBtn) {
    els.dockerStartBtn.hidden = !(body.launchable && !docker.running);
    els.dockerStartBtn.disabled = state.dockerBusy;
  }
  if (els.dockerStopBtn) {
    els.dockerStopBtn.hidden = !(body.launchable && docker.running);
    els.dockerStopBtn.disabled = state.dockerBusy;
  }

  setStatusPill(els.langfuseStatus, els.langfuseStatusText,
    lf.reachable ? '' : (docker.running ? 'warn' : 'danger'), lf.reachable ? 'up' : 'down');
  setDetail(els.langfuseDetail, lf.reachable ? '' : (lf.error || ''));
  if (els.langfuseStartBtn) {
    els.langfuseStartBtn.hidden = lf.reachable;
    els.langfuseStartBtn.disabled = state.langfuseBusy;
  }
  if (els.langfuseStopBtn) {
    els.langfuseStopBtn.hidden = !lf.reachable;
    els.langfuseStopBtn.disabled = state.langfuseBusy;
  }

  // AgentsView (#280): optional external indexer feeding the Code tab's AGY
  // vendor; disabled or not installed is a plain fact, not a failure.
  const av = body.agentsview || {};
  const avEnabled = !!av.host;
  let avKind = 'danger';
  let avLabel = 'down';
  if (av.reachable) { avKind = ''; avLabel = 'up'; }
  else if (!avEnabled) { avKind = ''; avLabel = 'disabled'; }
  else if (av.installed === false) { avKind = ''; avLabel = 'not installed'; }
  setStatusPill(els.agentsviewStatus, els.agentsviewStatusText, avKind, avLabel);
  setDetail(els.agentsviewDetail, av.reachable
    ? (av.version ? av.version : '')
    : (av.installed === false ? 'see docs/code-usage-agentsview.md' : (av.error || '')));
  if (els.agentsviewStartBtn) {
    els.agentsviewStartBtn.hidden = !(avEnabled && !av.reachable && av.installed);
    els.agentsviewStartBtn.disabled = state.agentsviewBusy;
  }
  if (els.agentsviewStopBtn) {
    els.agentsviewStopBtn.hidden = !(avEnabled && av.reachable);
    els.agentsviewStopBtn.disabled = state.agentsviewBusy;
  }

  // Launch button + hint visibility.
  const anyDown = !docker.running || !lf.reachable;
  const showActions = anyDown && body.launchable && !state.servicesLaunching;
  if (els.servicesActions) els.servicesActions.hidden = !(anyDown && body.launchable);
  if (els.servicesHint) {
    if (body.probeFailed) {
      // Status probe itself failed — we don't know the install state.
      els.servicesHint.textContent = 'Status check failed: ' + (docker.error || 'unknown error') + '.';
      els.servicesHint.hidden = false;
    } else if (anyDown && !body.launchable) {
      const hint = body.platform === 'darwin'
        ? 'Start Docker manually: `open -a Docker`, then `./start_langfuse.sh`.'
        : body.platform === 'linux'
          ? 'Start Docker manually: `sudo systemctl start docker`, then `./start_langfuse.sh`.'
          : 'Docker Desktop install not found — install from docker.com.';
      els.servicesHint.textContent = hint;
      els.servicesHint.hidden = false;
    } else {
      els.servicesHint.hidden = true;
    }
  }
  if (els.servicesLaunchBtn) {
    els.servicesLaunchBtn.disabled = !showActions;
    els.servicesLaunchBtn.innerHTML = state.servicesLaunching
      ? 'Launching… (up to ~90s)'
      : icon('rocket') + 'Launch Docker + Langfuse';
  }
  renderServiceRows();
  renderLangfuseSheet();
  renderHeadLine();
  // Request rows and the trace link both depend on whether Langfuse answers.
  renderRequests();
  renderErrors();
}

/* The Langfuse sheet: the OTel pane's stack health, same values. */
function langfuseHealth() {
  const h = state.otelHealth;
  if (state.otelHealthFailed) return { text: 'unreachable', tone: 'danger' };
  if (!h) return { text: 'checking…', tone: '' };
  if (!h.otel_enabled) return { text: 'OTel disabled', tone: 'attention' };
  if (h.langfuse_reachable) return { text: 'live', tone: '' };
  return { text: 'Langfuse offline', tone: 'danger' };
}

function renderLangfuseSheet() {
  const h = state.otelHealth || {};
  const health = langfuseHealth();
  [els.hubOtelHealth, els.langfuseHealthValue].forEach(function (el) {
    if (!el) return;
    el.textContent = health.text;
    if (health.tone) el.dataset.tone = health.tone; else delete el.dataset.tone;
  });
  if (els.hubOtelState) els.hubOtelState.textContent = state.otelHealth ? (h.otel_enabled ? 'on' : 'off') : '—';
  if (els.hubOtelHash) els.hubOtelHash.textContent = state.otelHealth ? (h.hash_prompts ? 'hashed' : 'raw') : '—';
  if (els.hubOtelEndpoint) els.hubOtelEndpoint.textContent = h.otel_endpoint || '—';
  if (els.hubOpenLangfuse) els.hubOpenLangfuse.href = langfuseUiBase();
  if (els.hubOtelOfflineHint) {
    els.hubOtelOfflineHint.hidden = !state.otelHealth || !!h.langfuse_reachable || !h.otel_enabled;
  }
}

async function onServicesLaunchClick() {
  if (state.servicesLaunching) return;
  state.servicesLaunching = true;
  renderServices();
  try {
    const result = await postJson('/admin/api/services/launch', {});
    const steps = (result && result.steps) || [];
    const summary = steps.map(function (s) { return s.name + ':' + s.status; }).join(' · ');
    if (result.ok) {
      toast('Services launched · ' + summary, 'good');
    } else {
      const first = steps.find(function (s) { return s.status === 'error'; });
      const detail = first ? (first.name + ': ' + first.detail) : summary;
      toast('Launch failed — ' + detail, 'error');
    }
  } catch (exc) {
    toast('Launch failed: ' + (exc.message || exc), 'error');
  } finally {
    state.servicesLaunching = false;
    await fetchServicesStatus();
  }
}

// One start/stop action on a service row: busy flag → re-render → POST →
// {ok, steps}-driven toast → clear the flag and refresh (#284). AgentsView,
// Docker Desktop and Langfuse all ride this one helper (#470). `busyKey` is
// the `state` flag the row's buttons read to disable themselves.
async function onServiceAction(label, busyKey, path, verb, doneLabel) {
  if (state[busyKey]) return;
  state[busyKey] = true;
  renderServices();
  try {
    const result = await postJson(path, {});
    if (result.ok) {
      toast(label + ' ' + doneLabel, 'good');
    } else {
      const first = (result.steps || []).find(function (s) { return s.status === 'error'; });
      toast(label + ' ' + verb + ' failed — ' + (first ? first.detail : 'unknown'), 'error');
    }
  } catch (exc) {
    toast(label + ' ' + verb + ' failed: ' + (exc.message || exc), 'error');
  } finally {
    state[busyKey] = false;
    await fetchServicesStatus();
  }
}

// AgentsView's start endpoint is `/launch`, not `/start` — hence the full
// path per action rather than a shared prefix + verb.
const SERVICE_ACTIONS = {
  agentsview: {
    start: function () { return onServiceAction('AgentsView', 'agentsviewBusy', '/admin/api/services/agentsview/launch', 'start', 'started'); },
    stop: function () { return onServiceAction('AgentsView', 'agentsviewBusy', '/admin/api/services/agentsview/stop', 'stop', 'stopped'); },
  },
  docker: {
    start: function () { return onServiceAction('Docker', 'dockerBusy', '/admin/api/services/docker/start', 'start', 'started'); },
    stop: function () { return onServiceAction('Docker', 'dockerBusy', '/admin/api/services/docker/stop', 'stop', 'stopped'); },
  },
  langfuse: {
    start: function () { return onServiceAction('Langfuse', 'langfuseBusy', '/admin/api/services/langfuse/start', 'start', 'started'); },
    stop: function () { return onServiceAction('Langfuse', 'langfuseBusy', '/admin/api/services/langfuse/stop', 'stop', 'stopped'); },
  },
};

function openSheet(dlg) {
  if (dlg && !dlg.open) dlg.showModal();
}

// --------------------------------------------------------- wiring
export function wireHub() {
  if (els.liveRequestsFilter) {
    els.liveRequestsFilter.addEventListener('input', function () {
      applyListFilter(els.liveRequestsFilter, state.activityScope === 'errors' ? els.recentErrorsList : els.liveRequestsList);
    });
  }
  if (els.activityScope) {
    els.activityScope.addEventListener('click', function (ev) {
      const seg = ev.target.closest('.segmented-item');
      if (!seg) return;
      state.activityScope = seg.dataset.scope;
      renderActivityChrome();
      applyListFilter(els.liveRequestsFilter, state.activityScope === 'errors' ? els.recentErrorsList : els.liveRequestsList);
    });
  }
  [els.liveRequestsList, els.recentErrorsList].forEach(function (list) {
    if (!list) return;
    list.addEventListener('click', function (ev) {
      const main = ev.target.closest('[data-act="trace"]');
      if (!main) return;
      const li = main.closest('.activity-row');
      openTrace(findRecord(li && li.dataset.ts));
    });
  });
  if (els.traceSheetBody) {
    els.traceSheetBody.addEventListener('click', function (ev) {
      const thumb = ev.target.closest('.trace-thumb');
      if (!thumb) return;
      const holder = els.traceSheetBody.querySelector('[data-trace-id]');
      if (holder) postFeedback(holder.dataset.traceId, parseInt(thumb.dataset.thumbs || '0', 10));
    });
  }
  if (els.servicesList) {
    els.servicesList.addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-act]');
      if (!btn || btn.disabled) return;
      const act = btn.dataset.act;
      if (act === 'svc-start') {
        const key = btn.closest('[data-svc]').dataset.svc;
        if (SERVICE_ACTIONS[key]) SERVICE_ACTIONS[key].start();
      } else if (act === 'langfuse') {
        renderLangfuseSheet();
        openSheet(els.langfuseSheet);
      } else if (act === 'services') {
        openSheet(els.servicesSheet);
      }
    });
  }
  if (els.langfuseSheetOpenBtn) {
    els.langfuseSheetOpenBtn.addEventListener('click', function () {
      renderLangfuseSheet();
      openSheet(els.langfuseSheet);
    });
  }
  if (els.byModelOpenBtn) els.byModelOpenBtn.addEventListener('click', function () { openSheet(els.byModelSheet); });
  if (els.logOpenBtn) {
    els.logOpenBtn.addEventListener('click', function () {
      openSheet(els.logSheet);
      if (els.hubLog) els.hubLog.scrollTop = els.hubLog.scrollHeight;
    });
  }
  // Every Hub sheet closes through its × (data-close-dialog).
  document.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const dlg = btn.closest('dialog');
      if (dlg) dlg.close();
    });
  });
  if (els.hubSeries) {
    els.hubSeries.addEventListener('click', function (ev) {
      const seg = ev.target.closest('.segmented-item');
      if (!seg) return;
      state.hubSeries = seg.dataset.series;
      drawSparklines();
    });
  }

  function togglePause() {
    state.logPaused = !state.logPaused;
    if (els.hubLogPauseBtn) {
      els.hubLogPauseBtn.innerHTML = state.logPaused
        ? icon('play') + 'Resume'
        : icon('pause') + 'Pause';
    }
  }
  if (els.hubLogPauseBtn) els.hubLogPauseBtn.addEventListener('click', togglePause);

  if (els.servicesLaunchBtn) els.servicesLaunchBtn.addEventListener('click', onServicesLaunchClick);
  [['agentsview', els.agentsviewStartBtn, els.agentsviewStopBtn],
   ['docker', els.dockerStartBtn, els.dockerStopBtn],
   ['langfuse', els.langfuseStartBtn, els.langfuseStopBtn]].forEach(function (t) {
    if (t[1]) t[1].addEventListener('click', SERVICE_ACTIONS[t[0]].start);
    if (t[2]) t[2].addEventListener('click', SERVICE_ACTIONS[t[0]].stop);
  });

  renderActivityChrome();
  renderGlance();
  renderHeadLine();

  // Sparklines: lightweight inline-SVG renderer driven by /admin/api/hub/stats.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    renderSparklines();
  }, 2500);

  // Services + telemetry health every 5 s: two small probes, and the health
  // feeds the Langfuse sheet and every trace link's project id.
  setInterval(function () {
    if (state.tab !== 'hub') return;
    if (state.servicesLaunching) return;
    fetchServicesStatus().catch(function () {});
    fetchOtelHealth().then(renderLangfuseSheet).catch(function () {});
  }, 5000);
}

// --------------------------------------------------------- the glance sparkline
let lastStats = null;

const SERIES = [
  { key: 'cpu', label: 'CPU', value: function (s) { return s.cpu && s.cpu.percent; }, hist: 'cpu_percent' },
  { key: 'gpu', label: 'GPU', gpu: true, value: function (s, g) { return g && g.util_percent; }, hist: 'gpu0_util_percent' },
  { key: 'ram', label: 'RAM', value: function (s) { return s.ram && s.ram.percent; }, hist: 'ram_percent' },
  { key: 'vram', label: 'VRAM', gpu: true, value: function (s, g) { return g && g.vram_percent; }, hist: 'gpu0_vram_percent' },
];

export async function renderSparklines() {
  try {
    lastStats = await jsonApi('/admin/api/hub/stats');
  } catch (_) { /* keep the last reading */ }
  drawSparklines();
}

/* One sparkline for the chosen series; the control above it carries all four
 * current figures, so every history stays one tap away. */
function drawSparklines() {
  const container = els.hubSparklines;
  if (!container) return;
  const stats = lastStats || {};
  const g0 = stats.gpus && stats.gpus.length ? stats.gpus[0] : null;
  const available = SERIES.filter(function (s) { return !s.gpu || g0; });
  let current = available.find(function (s) { return s.key === state.hubSeries; }) || available[0];
  state.hubSeries = current.key;
  if (els.hubSeries) {
    els.hubSeries.innerHTML = available.map(function (s) {
      const v = lastStats ? s.value(stats, g0) : null;
      return '<button type="button" class="segmented-item" data-series="' + s.key + '" aria-pressed="'
        + (s === current) + '">' + s.label + ' ' + (Number.isFinite(v) ? Math.round(v) + '%' : '—') + '</button>';
    }).join('');
  }
  const history = stats.history || [];
  const label = current.key === 'vram' && g0 ? 'VRAM ' + shortGpu(g0.name) : (current.key === 'gpu' ? 'GPU util' : current.label);
  container.replaceChildren(buildSparkline({
    label: label,
    value: lastStats ? current.value(stats, g0) : null,
    series: history.map(function (h) { return h[current.hist]; }),
  }));
}

function buildSparkline(g) {
  const root = document.createElement('div');
  root.className = 'sparkline';
  const series = (g.series || []).filter(function (v) { return v !== null && v !== undefined && !isNaN(v); });
  const max = 100;
  const w = 140, h = 64;
  let path = '';
  if (series.length >= 2) {
    const step = w / (series.length - 1);
    series.forEach(function (v, i) {
      const x = i * step;
      const y = h - (v / max) * h;
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    });
  }
  // Energy-tab chart look (#215): a 2px accent line over a translucent
  // accent area (fill closed down to the baseline).
  const area = path ? path + 'L' + w + ',' + h + ' L0,' + h + ' Z' : '';
  root.innerHTML =
    '<div class="sparkline-label"><span>' + escapeHtml(g.label) + '</span>' +
    '<span>' + (Number.isFinite(g.value) ? Math.round(g.value) + '%' : '—') + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
    (area ? '<path d="' + area + '" fill="color-mix(in srgb, var(--accent) 18%, transparent)" stroke="none"/>' : '') +
    (path ? '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>' : '') +
    '</svg>';
  return root;
}
