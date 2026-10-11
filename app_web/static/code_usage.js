/* Code tab: coding-agent usage (issues #20, #50; redesigned by #739).
 *
 * Totals and cost first: the period switch over the four counters (requests,
 * input, output, cache reads) with their equivalent cost and deltas, and the
 * total cost above them. The agent filter opens a sheet: All, every built-in
 * vendor, the AgentsView extras with their freshness, and the separate
 * Sub-agents (OTel) source, which shows Claude Code's own OTel export for the
 * same period and is never summed into All. One trend chart takes its metric
 * from the counter last tapped. The breakdowns are rows (By project) and
 * sheets (every project, By model, recent sessions); the per-agent table shows
 * on a wide window.
 *
 * Polls /admin/api/code/usage/summary?period=&vendor= every 30 s while the
 * tab is visible (or the OTel usage route while Sub-agents is chosen).
 *
 * Charts (issue #50): requires Chart.js 4.x loaded globally via CDN before
 * this module executes (see the <script> tag just before main.js in index.html).
 */

import { els, state } from './state.js';
import { jsonApi, fmtTok, fmtCost, tokPair, escapeHtml, renderTable, toastPending, isAuthError } from './api.js';
import { row, setHeadLine, defineInfo } from './rows.js';

defineInfo('code-usage', 'About these figures',
  '<p><strong>Input tokens</strong> are net new plus cache creation (both billed). <strong>Cache reads</strong> '
  + 'are served from the 5-minute or 1-hour prompt cache. The ≈ $ figures are the equivalent metered-API cost '
  + 'these tokens would have billed.</p>'
  + '<p><strong>Claude figures undercount two ways.</strong> Sessions bridged through claude.ai/code write no usage '
  + 'to the local transcripts (the <strong>Sub-agents (OTel)</strong> source and the OTel tab capture those when '
  + 'telemetry is enabled), and Claude Code prunes transcripts after about 30 days: the hub snapshots daily rollups, '
  + 'so All keeps growing from 2026-07-12 forward, but earlier pruned days are gone. Sub-agent (Task tool) usage '
  + '<em>is</em> counted where the newer per-session transcript folders exist.</p>'
  + '<p><strong>Sub-agents (OTel)</strong> is Claude Code\'s own OTel export (<code>POST /v1/metrics</code>), the only '
  + 'channel that sees sub-agent usage. It is stored separately and never added into All, so main-agent activity is '
  + 'not counted twice. Its project is tagged for sessions launched through the <code>claude</code> shell wrapper.</p>'
  + '<p>Projects under the <code>automation</code> workspace show just the folder name (the <code>automation-</code> '
  + 'prefix is stripped). Long names are cut with “…”.</p>');

// ---------------------------------------------------------------------------
// Chart constants (issue #50)
// ---------------------------------------------------------------------------

// Fixed colours for the Claude model families. Hardcoded (not from CSS vars)
// because the Chart.js canvas cannot read CSS custom properties. Fills are the
// energy-tab's translucent 0.18 alpha over a solid 2px line (#215).
const CLAUDE_COLORS = {
  Haiku:  { bg: 'rgba(74,138,243,0.18)',  border: 'rgba(74,138,243,0.85)'  },
  Sonnet: { bg: 'rgba(76,175,80,0.18)',   border: 'rgba(76,175,80,0.85)'   },
  Opus:   { bg: 'rgba(240,161,0,0.18)',   border: 'rgba(240,161,0,0.85)'   },
  Fable:  { bg: 'rgba(63,81,181,0.18)',   border: 'rgba(63,81,181,0.85)'   },
};
const CLAUDE_ORDER = ['Haiku', 'Sonnet', 'Opus', 'Fable'];
// Extra colours assigned in first-seen order to non-Claude families (Codex
// GPT models, etc.) so each gets its own series instead of collapsing to grey.
const EXTRA_COLORS = [
  { bg: 'rgba(186,104,200,0.18)', border: 'rgba(186,104,200,0.85)' }, // purple
  { bg: 'rgba(0,188,212,0.18)',   border: 'rgba(0,188,212,0.85)'   }, // cyan
  { bg: 'rgba(233,30,99,0.18)',   border: 'rgba(233,30,99,0.85)'   }, // pink
  { bg: 'rgba(121,85,72,0.18)',   border: 'rgba(121,85,72,0.85)'   }, // brown
  { bg: 'rgba(255,112,67,0.18)',  border: 'rgba(255,112,67,0.85)'  }, // deep orange
];
// Final fallback for an absent/unattributable model ("unknown").
const OTHER_COLOR = { bg: 'rgba(154,154,154,0.14)', border: 'rgba(154,154,154,0.65)' };

// The trend chart's four metrics, one per counter (#739).
const METRICS = {
  requests:          { label: 'Requests',      isTok: false },
  input_tokens:      { label: 'Input tokens',  isTok: true },
  output_tokens:     { label: 'Output tokens', isTok: true },
  cache_read_tokens: { label: 'Cache reads',   isTok: true },
};

const PERIOD_LABEL = { today: 'Day', week: 'Week', month: 'Month', all: 'All' };
const BUILTIN_VENDORS = ['claude', 'codex', 'copilot'];
const SUBAGENTS = 'subagents';
// By project on the face: the busiest few; every project opens as a table.
const PROJECT_ROWS = 6;

// The one retained Chart.js instance — destroyed before each recreation.
let _chart = null;
let _lastTs = null;

const POLL_MS = 30_000;
let _pollHandle = null;

// A vendor/period switch (#693): the loading toast goes up at once, the fetch
// starts once the clicks settle, and any earlier load is aborted, so a burst of
// clicks sends one request for the final choice. `_choice` is the live switch;
// `_pollCtl` the latest background poll, which a switch aborts and which never
// raises the toast. `_gen` bumps on every switch so a poll that started before
// it can never paint over the newer choice.
const CHOICE_SETTLE_MS = 200;
let _choice = null;
let _pollCtl = null;
let _gen = 0;

// Empty-state blocks whose copy swaps to a "could not load" line while the
// summary is failing (#580), and the pristine markup to restore on recovery.
const EMPTY_STATE_KEYS = [
  'cldVendorEmpty', 'cldModelEmpty', 'cldProjectEmpty', 'cldSessionsEmpty', 'cldSubEmpty',
];
const _pristineEmptyMsg = {};
let _loadFailed = false;
let _answered = false;      // the first read (success or failure) has landed
let _agentsview = null;     // the last AgentsView status block
let _lastCost = null;       // the shown period's total cost, for the header line

// ---------------------------------------------------------------------------
// Public lifecycle — called from main.js
// ---------------------------------------------------------------------------

export function wireCodeUsage() {
  // Period (Day / Week / Month / All): the one true peer set, a segmented row.
  if (els.cldPeriodSeg) {
    els.cldPeriodSeg.addEventListener('click', function (e) {
      const btn = e.target.closest('button[data-period]');
      if (!btn) return;
      const next = btn.dataset.period;
      if (next === state.cldPeriod) return;     // nothing to do
      state.cldPeriod = next;
      syncPeriodSeg();
      startChoiceLoad();
    });
  }

  // Agent filter: a button opening the sheet of every agent and source.
  if (els.cldAgentBtn) {
    els.cldAgentBtn.addEventListener('click', function () {
      renderAgentSheet();
      if (els.cldAgentSheet && !els.cldAgentSheet.open) els.cldAgentSheet.showModal();
    });
  }
  if (els.cldAgentList) {
    els.cldAgentList.addEventListener('click', function (e) {
      const item = e.target.closest('[data-vendor]');
      if (!item) return;
      if (els.cldAgentSheet && els.cldAgentSheet.open) els.cldAgentSheet.close();
      const next = item.dataset.vendor;
      if (next === state.cldVendor) return;
      state.cldVendor = next;
      syncAgentLabel();
      applyView();
      startChoiceLoad();
    });
  }

  // A counter tap picks the trend chart's metric.
  if (els.cldCounterGrid) {
    els.cldCounterGrid.addEventListener('click', function (e) {
      const btn = e.target.closest('button[data-metric]');
      if (!btn || !METRICS[btn.dataset.metric]) return;
      state.cldMetric = btn.dataset.metric;
      syncMetric();
      drawChart();
    });
  }

  [[els.cldProjectsAllBtn, els.cldProjectSheet], [els.cldModelOpenBtn, els.cldModelSheet],
   [els.cldSessionsOpenBtn, els.cldSessionsSheet]].forEach(function (pair) {
    if (!pair[0] || !pair[1]) return;
    pair[0].addEventListener('click', function () { if (!pair[1].open) pair[1].showModal(); });
  });

  syncPeriodSeg();
  syncAgentLabel();
  syncMetric();
  renderHeadLine();
}

export function startCodeUsagePolls() {
  pollSummary();
  _pollHandle = setInterval(pollSummary, POLL_MS);
}

export function stopCodeUsagePolls() {
  if (_pollHandle !== null) {
    clearInterval(_pollHandle);
    _pollHandle = null;
  }
}

// ---------------------------------------------------------------------------
// Data fetch
// ---------------------------------------------------------------------------

/* Background refresh: silent (no toast), and it yields to a switch in flight. */
function pollSummary() {
  if (_choice) return;
  const ctl = new AbortController();
  _pollCtl = ctl;
  fetchSummary(ctl, _gen).catch(function () {});
}

/* A user-picked vendor/period: toast now, fetch once the clicks settle. */
function startChoiceLoad() {
  _gen += 1;
  if (_choice) {
    clearTimeout(_choice.timer);
    _choice.ctl.abort();
  }
  if (_pollCtl) _pollCtl.abort();
  const ctl = new AbortController();
  const mine = { ctl, gen: _gen, done: toastPending('Loading usage…'), timer: null };
  _choice = mine;
  mine.timer = setTimeout(function () {
    fetchSummary(ctl, mine.gen).then(function (outcome) {
      // Only the newest switch owns the toast; an aborted one is replaced.
      if (_choice !== mine || outcome === 'aborted') return;
      _choice = null;
      // The sign-in overlay already speaks for an auth failure; no second message.
      mine.done(outcome === 'ok' || outcome === 'auth' ? undefined : 'Could not load usage — ' + outcome);
    });
  }, CHOICE_SETTLE_MS);
}

/* Fetch + render the current choice: the summary, or the Sub-agents (OTel)
 * rollup while that source is chosen. Resolves 'ok', 'aborted' (superseded —
 * nothing rendered), 'auth' (the sign-in overlay owns it) or the failure
 * reason (already shown through the #580 banner). */
async function fetchSummary(ctl, gen) {
  const sub = state.cldVendor === SUBAGENTS;
  try {
    const body = await jsonApi(sub
      ? '/admin/api/telemetry/claude-code/usage?period=' + state.cldPeriod
      : '/admin/api/code/usage/summary?period=' + state.cldPeriod + '&vendor=' + state.cldVendor,
      { signal: ctl.signal }
    );
    if (ctl.signal.aborted || gen !== _gen) return 'aborted';
    // Belt and braces (#580): the route now answers 503 on a failed build, so
    // jsonApi throws and the catch below owns the error state. A 200 that
    // still carries `error` (an older hub on a peer machine) is a failure too
    // — read the field rather than trusting the status alone.
    if (body && body.error) throw new Error(String(body.error));
    setLoadError(null);
    if (sub) renderSubagents(body);
    else renderSummary(body);
    return 'ok';
  } catch (exc) {
    if (ctl.signal.aborted || (exc && exc.name === 'AbortError') || gen !== _gen) return 'aborted';
    if (isAuthError(exc)) return 'auth';
    const reason = exc && exc.message ? String(exc.message) : 'unknown error';
    setLoadError(reason);
    return reason;
  }
}

function renderSummary(body) {
  state.cldSummary = body;
  _agentsview = body.agentsview || _agentsview;
  // If the server coerced an unknown vendor back to "all" (e.g. the hub
  // restarted and hasn't re-discovered an AgentsView vendor yet), resync
  // the selector so the UI and the data agree.
  if (body.vendor && body.vendor !== state.cldVendor) {
    state.cldVendor = body.vendor;
    syncAgentLabel();
  }
  if (state.cldVendor === 'all') {
    state.cldVendorTotals = { period: state.cldPeriod, rows: body.by_vendor || [], totals: body.totals || {} };
  }
  render(body);
  // Copilot billing is a separate authoritative source (issue #231, part B):
  // only fetched while that vendor is chosen, so the GitHub API isn't hit on
  // every 30 s poll regardless.
  if (state.cldVendor === 'copilot') fetchCopilotBilling().catch(function () {});
}

/* Render "the summary could not be built" as its own state (#580).
 *
 * The whole point is that this is NOT a zero-usage day: every counter goes to
 * the unknown dash rather than 0, the trend chart is torn down instead of
 * drawing a flat zero line, and each table's empty-state says the load failed
 * rather than "no data for this period". Driven purely by the server
 * reporting failure — never inferred from totals being zero, so a genuinely
 * idle day still renders its honest zeroes.
 *
 * Passing null clears the state and restores the normal empty-state copy. */
function setLoadError(reason) {
  const failed = reason !== null && reason !== undefined;
  // Clearing is only work on the transition back — the success path calls this
  // on every 30s poll and must not rewrite the DOM each time.
  if (!failed && !_loadFailed) return;
  _loadFailed = failed;

  if (els.cldError) els.cldError.hidden = !failed;
  if (els.cldErrorMsg && failed) {
    els.cldErrorMsg.textContent = 'Usage data could not be loaded — ' + reason +
      '. Retrying every 30s. This is not a zero-usage period.';
  }
  EMPTY_STATE_KEYS.forEach(function (key) {
    const el = els[key];
    if (!el) return;
    const msg = el.querySelector('.empty-state-message');
    if (!msg) return;
    // Capture the pristine copy once — some carry markup (a <code> path), so
    // restoring them needs the original HTML, not a text snapshot.
    if (_pristineEmptyMsg[key] === undefined) _pristineEmptyMsg[key] = msg.innerHTML;
    if (failed) msg.textContent = 'Could not load — see the error above.';
    else msg.innerHTML = _pristineEmptyMsg[key];
  });
  if (!failed) { applyView(); return; }

  // Unknown, not zero: blank every counter and cost line, drop the deltas
  // (there is nothing to compare), tear the chart down and empty the tables.
  [els.cldRequests, els.cldInputTok, els.cldOutputTok, els.cldCacheRead, els.cldTotalCost]
    .forEach(function (el) { set(el, '—'); });
  [els.cldInputCost, els.cldOutputCost, els.cldCacheCost]
    .forEach(function (el) { set(el, ''); });
  if (els.cldOutputTok) els.cldOutputTok.removeAttribute('title');
  [els.cldDeltaRequests, els.cldDeltaInputTok, els.cldDeltaOutputTok,
   els.cldDeltaCacheRead].forEach(function (el) { if (el) el.hidden = true; });
  _lastTs = null;
  _lastCost = null;
  _destroyChart();

  const noRows = function () { return ''; };
  renderTable(els.cldVendorTable, els.cldVendorEmpty, [], noRows);
  renderTable(els.cldModelTable, els.cldModelEmpty, [], noRows);
  renderTable(els.cldProjectTable, null, [], noRows);
  renderTable(els.cldSubTable, els.cldSubEmpty, [], noRows);
  if (els.cldProjectList) els.cldProjectList.innerHTML = '';
  if (els.cldProjectEmpty) els.cldProjectEmpty.hidden = false;
  set(els.cldSubSummary, '—');
  if (els.cldSessionsList) els.cldSessionsList.innerHTML = '';
  if (els.cldSessionsEmpty) els.cldSessionsEmpty.hidden = false;
  // "—", not "0": the counts are unknown, not known to be none.
  set(els.cldSessionsBadge, '—');
  set(els.cldModelMeta, 'Could not load');
  set(els.cldProjectsMeta, 'Could not load');
  _answered = true;
  setFreshness('load failed ' + new Date().toLocaleTimeString());
  applyView();
}

// ---------------------------------------------------------------------------
// Which blocks show (#739): the figures or the Sub-agents table, the chart,
// the breakdowns, Copilot's credits, the note that applies now.
// ---------------------------------------------------------------------------

function applyView() {
  const sub = state.cldVendor === SUBAGENTS;
  const all = state.cldVendor === 'all';
  if (els.cldFigures) els.cldFigures.hidden = sub;
  if (els.cldSubagents) els.cldSubagents.hidden = !sub;
  if (els.cldUndercountNote) {
    els.cldUndercountNote.hidden = sub || _loadFailed || !(all || state.cldVendor === 'claude');
  }
  // The breakdowns stay hidden until the first summary has been answered,
  // success or failure (#647): a header-only table or a "none found" line
  // before that would state an emptiness nothing has measured.
  if (els.cldPending) els.cldPending.hidden = _answered || sub;
  if (els.cldProjectCard) els.cldProjectCard.hidden = !_answered || sub;
  if (els.cldVendorCard) els.cldVendorCard.hidden = !_answered || !all;
  if (els.cldCopilotBillingCard && state.cldVendor !== 'copilot') els.cldCopilotBillingCard.hidden = true;
  if (els.cldChartsCard) els.cldChartsCard.hidden = !_answered || sub || _loadFailed;
  renderHeadLine();
}

function agentLabel(v) {
  if (v === 'all') return 'All agents';
  if (v === SUBAGENTS) return 'Sub-agents (OTel)';
  return vendorLabel(v);
}

/* The header line: what is wrong now (a failed load, an offline AgentsView
 * behind the extras), else the period, the agent and the total cost. */
function renderHeadLine() {
  const el = els.codeHeadLine;
  if (!el) return;
  if (!_answered) { setHeadLine(el, [], 'Reading usage…'); return; }
  const parts = [];
  if (_loadFailed) parts.push({ text: 'Usage not loaded', tone: 'danger' });
  if (agentsviewOffline()) parts.push({ text: 'AgentsView offline', tone: 'attention' });
  const bits = [PERIOD_LABEL[state.cldPeriod] || state.cldPeriod];
  if (state.cldVendor !== 'all') bits.push(agentLabel(state.cldVendor));
  if (_lastCost !== null) bits.push(fmtCost(_lastCost) || '≈ $0.00');
  setHeadLine(el, parts, bits.join(' · '));
}

function agentsviewOffline() {
  const av = _agentsview;
  return !!(av && av.vendors && av.vendors.length && !av.reachable);
}

// ---------------------------------------------------------------------------
// The agent sheet (#739)
// ---------------------------------------------------------------------------

function syncPeriodSeg() {
  if (!els.cldPeriodSeg) return;
  els.cldPeriodSeg.querySelectorAll('button[data-period]').forEach(function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.period === state.cldPeriod));
  });
}

function syncAgentLabel() {
  set(els.cldAgentLabel, agentLabel(state.cldVendor));
}

function syncMetric() {
  if (!els.cldCounterGrid) return;
  els.cldCounterGrid.querySelectorAll('button[data-metric]').forEach(function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.metric === state.cldMetric));
  });
  set(els.cldChartLabel, METRICS[state.cldMetric].label);
}

/* One agent's figures for the shown period, from the last All read (its
 * per-vendor breakdown); empty when that read is for another period. */
function agentFigures(v) {
  const cache = state.cldVendorTotals;
  if (!cache || cache.period !== state.cldPeriod) return '';
  const r = v === 'all' ? cache.totals : (cache.rows || []).find(function (x) { return x.vendor === v; });
  if (!r) return v === 'all' ? '' : 'no usage';
  const cost = (r.input_cost || 0) + (r.output_cost || 0) + (r.cache_read_cost || 0);
  return fmtNum(r.requests) + ' req · ' + (fmtCost(cost) || '≈ $0.00');
}

function agentRow(v, meta, chips) {
  return row({
    cls: 'cld-agent-row',
    attrs: ' data-vendor="' + escapeHtml(v) + '"',
    title: agentLabel(v),
    meta: meta,
    chips: chips || [],
    selected: state.cldVendor === v,
  });
}

function renderAgentSheet() {
  const list = els.cldAgentList;
  if (!list) return;
  const av = _agentsview || {};
  const extras = (av.vendors || []).filter(function (v) { return BUILTIN_VENDORS.indexOf(v) === -1; });
  const fresh = av.last_refresh ? fmtTs(av.last_refresh) : '';
  const html = ['<li class="group-overline">Agents</li>', agentRow('all', agentFigures('all'))];
  BUILTIN_VENDORS.forEach(function (v) { html.push(agentRow(v, agentFigures(v))); });
  extras.forEach(function (v) {
    const figures = agentFigures(v);
    const freshness = 'AgentsView' + (fresh ? (av.reachable ? ' · updated ' : ' · last fetched ') + fresh : '');
    html.push(agentRow(v, [freshness, figures].filter(Boolean).join(' · '),
      av.reachable ? [] : [{ text: 'offline', tone: 'attention' }]));
  });
  html.push('<li class="group-overline">Separate source</li>');
  html.push(agentRow(SUBAGENTS, 'Separate store · never added into All'));
  list.innerHTML = html.join('');
}

// ---------------------------------------------------------------------------
// Copilot official billing card (issue #231, part B)
// ---------------------------------------------------------------------------

async function fetchCopilotBilling() {
  if (!els.cldCopilotBillingCard) return;
  try {
    const body = await jsonApi('/admin/api/code/copilot/billing');
    renderCopilotBilling(body);
  } catch (exc) {
    if (isAuthError(exc)) return;
    renderCopilotBilling({ available: false, reason: 'error fetching data', daily: [] });
  }
}

function renderCopilotBilling(body) {
  const card = els.cldCopilotBillingCard;
  if (!card) return;
  card.hidden = state.cldVendor !== 'copilot';
  if (state.cldVendor !== 'copilot' || !body) return;

  if (els.cldCopilotBillingAsOf) {
    els.cldCopilotBillingAsOf.textContent = body.as_of
      ? 'as of ' + new Date(body.as_of).toLocaleTimeString() : '';
  }

  // `available: false` forces the empty treatment regardless of `daily` —
  // there's no billing data worth showing, just a reason.
  const rows = (body.available && body.daily) || [];
  // Days the server could not fetch: unknown spend, shown as such — never as
  // an absent day (which reads as $0) and never summed into the total.
  const missing = (body.available && body.missing_days) || [];
  if (els.cldCopilotBillingEmptyMsg) {
    els.cldCopilotBillingEmptyMsg.textContent = body.available === false
      ? (body.reason || 'Not available.')
      : 'No billing data for this window.';
  }
  const totalCredits = rows.reduce(function (sum, r) { return sum + (Number(r.credits) || 0); }, 0);
  set(els.cldCopilotBillingTotal, rows.length
    ? totalCredits.toFixed(2) + (missing.length ? ' (partial)' : '')
    : '—');

  const tableRows = rows.concat(missing.map(function (d) { return { date: d, unknown: true }; }))
    .sort(function (a, b) { return String(a.date).localeCompare(String(b.date)); });
  renderTable(els.cldCopilotBillingTable, els.cldCopilotBillingEmpty, tableRows, function (r) {
    if (r.unknown) {
      return '<tr>' +
        '<td>' + escapeHtml(r.date) + '</td>' +
        '<td class="muted" colspan="3">unknown — could not be fetched</td>' +
        '</tr>';
    }
    return '<tr>' +
      '<td>' + escapeHtml(r.date) + '</td>' +
      '<td>' + escapeHtml(r.model) + '</td>' +
      '<td>' + (Number(r.credits) || 0).toFixed(2) + '</td>' +
      '<td>' + (fmtCost(r.usd) || '—') + '</td>' +
      '</tr>';
  });
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render(body) {
  if (!body) return;
  _answered = true;
  renderCldCounters(body);
  renderDeltas(body);
  _lastTs = body.time_series || [];
  renderVendorTable(body.by_vendor || []);
  renderModelTable(body.by_model || []);
  renderProjects(body.by_project || []);
  renderSessions(body.recent_sessions || []);
  setFreshness('updated ' + new Date().toLocaleTimeString());
  applyView();
  drawChart();
}

/* Sub-agents (OTel): the OTel pane's table and summary line for the shown
 * period. Never summed into All — the figures stay in this block. */
function renderSubagents(body) {
  _answered = true;
  const rows = (body && body.rows) || [];
  renderTable(els.cldSubTable, els.cldSubEmpty, rows, function (r) {
    return '<tr>' +
      '<td>' + escapeHtml(r.date || '—') + '</td>' +
      '<td>' + escapeHtml(r.project || '—') + '</td>' +
      '<td>' + escapeHtml(r.model) + '</td>' +
      '<td>' + escapeHtml(r.query_source) + '</td>' +
      '<td>' + fmtTok(r.input) + '</td>' +
      '<td>' + fmtTok(r.output) + '</td>' +
      '<td>' + fmtTok(r.cache_read) + '</td>' +
      '<td>' + fmtTok(r.cache_creation) + '</td>' +
      '<td>' + (fmtCost(r.cost_usd) || '—') + '</td>' +
      '</tr>';
  });
  const totals = (body && body.totals) || {};
  const reqTok = (totals.input || 0) + (totals.output || 0);
  set(els.cldSubSummary, reqTok
    ? tokPair(totals.input, totals.output) + ' tok · ' + (fmtCost(totals.cost_usd) || '≈ $0.00')
    : 'no data yet');
  _lastCost = totals.cost_usd || 0;
  setFreshness('updated ' + new Date().toLocaleTimeString());
  applyView();
}

function renderCldCounters(body) {
  if (!body) return;
  const bucket = body.totals || {};
  set(els.cldRequests, fmtNum(bucket.requests));
  // Grand-total equivalent metered cost, the figure the owner reads first (#71, #739).
  const totalCost = (bucket.input_cost || 0) + (bucket.output_cost || 0) + (bucket.cache_read_cost || 0);
  _lastCost = totalCost;
  set(els.cldTotalCost, fmtCost(totalCost) || '≈ $0.00');
  set(els.cldInputTok, fmtTok(
    (bucket.input_tokens || 0) + (bucket.cache_creation_tokens || 0)
  ));
  set(els.cldOutputTok, fmtTok(bucket.output_tokens));
  set(els.cldCacheRead, fmtTok(bucket.cache_read_tokens));
  // Equivalent metered-API cost under the three token tiles (issue #52, #71).
  set(els.cldInputCost, fmtCost(bucket.input_cost));
  set(els.cldOutputCost, fmtCost(bucket.output_cost));
  set(els.cldCacheCost, fmtCost(bucket.cache_read_cost));
  // Codex reasoning tokens — a subset of output (#71). Off the card face so
  // the four cards stay one height (#692); the figure rides the output
  // tile's tooltip.
  const reasoning = bucket.reasoning_output_tokens || 0;
  if (els.cldOutputTok) {
    if (reasoning) els.cldOutputTok.title = 'incl. ' + fmtTok(reasoning) + ' reasoning';
    else els.cldOutputTok.removeAttribute('title');
  }
}

function renderVendorTable(rows) {
  renderTable(els.cldVendorTable, els.cldVendorEmpty, rows, function (r) {
    const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
    const cost = (r.input_cost || 0) + (r.output_cost || 0) + (r.cache_read_cost || 0);
    return '<tr>' +
      '<td>' + escapeHtml(vendorLabel(r.vendor)) + '</td>' +
      '<td>' + fmtNum(r.requests) + '</td>' +
      '<td>' + tokPair(totalIn, r.output_tokens) + '</td>' +
      '<td class="muted">' + fmtTok(r.cache_read_tokens) + '</td>' +
      '<td>' + (fmtCost(cost) || '—') + '</td>' +
      '</tr>';
  });
}

function vendorLabel(v) {
  if (v === 'claude') return 'Claude';
  if (v === 'codex') return 'Codex';
  if (v === 'copilot') return 'Copilot';
  if (v === 'agy') return 'AGY';
  // Any other AgentsView-sourced vendor (issue #280): auto-label from the slug.
  return v ? v.charAt(0).toUpperCase() + v.slice(1) : '—';
}

function renderModelTable(rows) {
  renderTable(els.cldModelTable, els.cldModelEmpty, rows, function (r) {
    const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
    return '<tr>' +
      '<td class="td-trunc" title="' + escapeHtml(r.model) + '">' + escapeHtml(r.model) + '</td>' +
      '<td>' + fmtNum(r.requests) + '</td>' +
      '<td>' + tokPair(totalIn, r.output_tokens) + '</td>' +
      '<td class="muted">' + fmtTok(r.cache_read_tokens) + '</td>' +
      '</tr>';
  });
  set(els.cldModelMeta, rows.length
    ? rows.length + (rows.length === 1 ? ' model' : ' models') + ' · requests and tokens'
    : 'None this period');
}

/* By project on the face: the busiest few as rows; every project opens as
 * the full table. */
function renderProjects(rows) {
  if (els.cldProjectList) {
    els.cldProjectList.innerHTML = rows.slice(0, PROJECT_ROWS).map(function (r) {
      const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
      return row({
        cls: 'cld-project-row is-static',
        title: r.project || r.project_key || '—',
        lead: fmtNum(r.requests) + ' req',
        meta: tokPair(totalIn, r.output_tokens) + ' tok',
        mainAttrs: ' tabindex="-1"',
      });
    }).join('');
  }
  if (els.cldProjectEmpty) els.cldProjectEmpty.hidden = rows.length > 0;
  renderTable(els.cldProjectTable, null, rows, function (r) {
    const totalIn = (r.input_tokens || 0) + (r.cache_creation_tokens || 0);
    const name = r.project || r.project_key;
    return '<tr>' +
      '<td class="td-trunc" title="' + escapeHtml(name) + '">' + escapeHtml(name) + '</td>' +
      '<td>' + fmtNum(r.requests) + '</td>' +
      '<td>' + tokPair(totalIn, r.output_tokens) + '</td>' +
      '</tr>';
  });
  set(els.cldProjectsMeta, rows.length
    ? rows.length + (rows.length === 1 ? ' project' : ' projects') + ' · the full table'
    : 'None this period');
}

function renderSessions(sessions) {
  if (!els.cldSessionsList) return;
  const empty = els.cldSessionsEmpty;

  set(els.cldSessionsBadge, sessions.length + (sessions.length === 1 ? ' session' : ' sessions'));
  if (!sessions.length) {
    els.cldSessionsList.innerHTML = '';
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;

  els.cldSessionsList.innerHTML = sessions.map(function (s) {
    const totalIn = (s.input_tokens || 0) + (s.cache_creation_tokens || 0);
    const firstTs = fmtTs(s.first_ts);
    const lastTs = fmtTs(s.last_ts);
    const proj = escapeHtml(s.project || s.project_key || '');
    const model = escapeHtml(modelShort(s.model || ''));
    const sid = escapeHtml((s.session_id || '').slice(0, 8));
    // Project leads; the times live on the meta line (J-10, #647).
    return '<li>' +
      '<span class="cld-sess-project">' + proj + '</span>' +
      '<span class="cld-sess-model">' + model + '</span>' +
      '<div class="cld-sess-meta">' +
        '<code>' + sid + '</code>' +
        ' · ' + firstTs + ' to ' + lastTs +
        ' · ' + fmtNum(s.requests) + ' req' +
        ' · ' + fmtTok(totalIn) + ' in / ' + fmtTok(s.output_tokens) + ' out' +
      '</div>' +
      '</li>';
  }).join('');
}

// ---------------------------------------------------------------------------
// Delta badges (issue #50)
// ---------------------------------------------------------------------------

function renderDeltas(body) {
  const prev = body.prev_totals;        // absent when period === 'all'
  const curr = body.totals || {};
  const hide = state.cldPeriod === 'all' || !prev;

  function apply(el, c, p) {
    if (!el) return;
    if (hide) { el.hidden = true; return; }
    // Zero base: percentage is undefined. Show "new" when this metric appeared
    // this period (e.g. Codex with no prior-week data) instead of hiding the
    // badge — the comparison window itself is valid (issue #71).
    if (p === 0) {
      if (c > 0) { el.hidden = false; el.className = 'cld-delta up'; el.textContent = 'new'; }
      else { el.hidden = true; }
      return;
    }
    el.hidden = false;
    const pct = Math.round((c - p) / p * 100);
    el.className = pct > 0 ? 'cld-delta up' : pct < 0 ? 'cld-delta down' : 'cld-delta';
    el.textContent = pct > 0 ? '+' + pct + '%' : pct < 0 ? pct + '%' : '±0%';
  }

  const prevIn  = prev ? (prev.input_tokens || 0) + (prev.cache_creation_tokens || 0) : 0;
  const currIn  = (curr.input_tokens || 0) + (curr.cache_creation_tokens || 0);
  const prevOut = prev ? (prev.output_tokens || 0) : 0;
  const prevReq = prev ? (prev.requests || 0) : 0;

  const prevCache = prev ? (prev.cache_read_tokens || 0) : 0;

  apply(els.cldDeltaRequests,  curr.requests || 0,           prevReq);
  apply(els.cldDeltaInputTok,  currIn,                        prevIn);
  apply(els.cldDeltaOutputTok, curr.output_tokens || 0,       prevOut);
  apply(els.cldDeltaCacheRead, curr.cache_read_tokens || 0,   prevCache);
}

// ---------------------------------------------------------------------------
// The trend chart (issue #50; one chart since #739)
// ---------------------------------------------------------------------------

/* Draw the chosen metric over the last summary's time series. The server
 * sends no series for All (an unbounded axis), so the card says which periods
 * the trend covers instead of drawing nothing. */
function drawChart() {
  const card = els.cldChartsCard;
  if (!card || card.hidden) return;
  const ts = _lastTs;
  const has = !!(ts && ts.length);
  if (els.cldChartWrap) els.cldChartWrap.hidden = !has;
  if (els.cldChartEmpty) els.cldChartEmpty.hidden = has;
  if (!has) { _destroyChart(); return; }
  const metric = METRICS[state.cldMetric];
  // Stable family list + colours, so a model keeps the same colour on every
  // metric (issue #71: GPT models get own series).
  _chart = _makeChart(els.cldChart, _chart, ts.map(function (b) { return b.label; }), ts,
    _orderedFamilies(ts), state.cldMetric, metric.isTok);
}

// Build the ordered list of chart series from the time-series buckets: Claude
// families first (fixed colours), then every other model (e.g. Codex GPT-5.5)
// each with its own colour, and finally a grey "Other" for unattributable ids.
function _orderedFamilies(ts) {
  const seen = new Set();
  ts.forEach(function (b) {
    Object.keys(b.models || {}).forEach(function (k) { seen.add(k); });
  });

  const series = [];
  CLAUDE_ORDER.forEach(function (k) {
    if (seen.has(k)) series.push({ key: k, label: k, bg: CLAUDE_COLORS[k].bg, border: CLAUDE_COLORS[k].border });
  });

  const rest = Array.from(seen).filter(function (k) {
    return CLAUDE_ORDER.indexOf(k) === -1 && k !== 'unknown';
  }).sort();
  rest.forEach(function (k, i) {
    const c = EXTRA_COLORS[i % EXTRA_COLORS.length];
    series.push({ key: k, label: k, bg: c.bg, border: c.border });
  });

  if (seen.has('unknown')) {
    series.push({ key: 'unknown', label: 'Other', bg: OTHER_COLOR.bg, border: OTHER_COLOR.border });
  }
  return series;
}

function _destroyChart() {
  if (_chart) { _chart.destroy(); _chart = null; }
}

function _makeChart(canvas, existing, labels, ts, families, field, isTok) {
  if (!canvas) return existing;

  var datasets = [];
  families.forEach(function (fam) {
    var values = ts.map(function (b) { return ((b.models || {})[fam.key] || {})[field] || 0; });
    if (values.every(function (v) { return v === 0; })) return;
    datasets.push({
      label: fam.label,
      data: values,
      fill: true,
      backgroundColor: fam.bg,
      borderColor: fam.border,
      borderWidth: 2,
      tension: 0.3,
      pointRadius: ts.length > 10 ? 0 : 3,
      pointHoverRadius: 4,
    });
  });

  if (existing) { existing.destroy(); }

  /* global Chart */
  return new Chart(canvas, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: _chartOptions(isTok),
  });
}

/* Chart.js cannot read CSS custom properties from the canvas itself, so
 * resolve the theme tokens at (re)creation time — and again on theme flip
 * via restyleCodeUsageCharts(). */
function _cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function _chartOptions(isTok) {
  var gridColor  = _cssVar('--line');
  var tickColor  = _cssVar('--muted');
  var fgColor    = _cssVar('--ink');
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: {
        stacked: true,
        grid: { display: false },
        ticks: { color: tickColor, font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
      },
      y: {
        stacked: true,
        beginAtZero: true,
        grid: { color: gridColor },
        ticks: {
          color: tickColor,
          font: { size: 11 },
          callback: isTok ? function (v) { return fmtTok(v); } : undefined,
        },
      },
    },
    plugins: {
      legend: {
        position: 'bottom',
        labels: { color: fgColor, boxWidth: 12, padding: 12, font: { size: 11 }, usePointStyle: true },
      },
      tooltip: {
        callbacks: isTok ? {
          label: function (ctx) {
            return ' ' + ctx.dataset.label + ': ' + fmtTok(ctx.parsed.y);
          },
        } : {},
      },
    },
  };
}

/* Re-resolve the axis/legend token colors on the live chart after a theme
 * flip (called from main.js's applyTheme). Dataset fills are saturated and
 * theme-stable, so only grid/tick/legend colors need the update. */
export function restyleCodeUsageCharts() {
  if (!_chart) return;
  _chart.options.scales.x.ticks.color = _cssVar('--muted');
  _chart.options.scales.y.grid.color = _cssVar('--line');
  _chart.options.scales.y.ticks.color = _cssVar('--muted');
  _chart.options.plugins.legend.labels.color = _cssVar('--ink');
  _chart.update('none');
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function set(el, text) {
  if (el) el.textContent = text;
}

function setFreshness(text) {
  if (els.cldFreshness) els.cldFreshness.textContent = text;
}

function fmtNum(n) {
  if (n === undefined || n === null) return '—';
  return Number(n).toLocaleString();
}

/* fmtTok / fmtCost live in api.js (shared with the Hub/OTel tables, #215). */

function fmtTs(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return iso.slice(11, 16); }
}

function modelShort(m) {
  const lm = m.toLowerCase();
  if (lm.includes('fable')) return 'Fable';
  if (lm.includes('opus')) return 'Opus';
  if (lm.includes('sonnet')) return 'Sonnet';
  if (lm.includes('haiku')) return 'Haiku';
  return m.length > 20 ? m.slice(0, 20) + '…' : m;
}
