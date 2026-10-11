/* Shared singletons: state, DOM-element references, polling intervals.
 *
 * Auth: a bearer token is stored in localStorage. The page extracts it
 * from ?token=… on first load and strips it from the URL. On 401, the
 * login overlay shows; password → /admin/api/login → bearer token.
 */

export const TOKEN_KEY = 'llmhub.token';
/* Theme override — same key the pre-paint boot script in index.html reads;
 * absent = follow the OS prefers-color-scheme. */
export const THEME_KEY = 'llmhub.theme';
/* Active tab — persisted by the vendored nav so the installed PWA reopens
 * where you left it (fleet nav contract). */
export const TAB_KEY = 'llmhub.tab';
/* Models tab "Active only" filter — persisted like Plugs' show-hidden
 * toggle in home-automation (#266). */
export const MODELS_ACTIVE_ONLY_KEY = 'llmhub.models.activeOnly';
// The Playground's last mode on this device (#740): chat | image | speech | decide.
export const PLAYGROUND_MODE_KEY = 'llmhub.playground.mode';

export const STATUS_POLL_MS = 4000;
export const COUNTERS_POLL_MS = 4000;
export const MODELS_POLL_MS = 5000;
/* Machines tab (issue #309) — only polled while that tab is active. */
export const MACHINES_POLL_MS = 10000;

export const state = {
  tab: 'hub',
  status: null,           // /admin/api/hub/status payload
  models: [],             // /admin/api/models payload
  counters: [],           // per-backend rows from /admin/api/telemetry/metrics
  liveRequests: [],       // ring synced from SSE stream
  recentErrors: [],
  logPaused: false,
  installChecks: [],      // the last install battery (Settings › Health); a failing one reaches Hub's header
  installChecked: false,   // the install battery has been requested once (it loads when Settings › Health opens)
  version: null,
  hubStreamCtl: null,     // EventSource abort handles
  hubLogStreamCtl: null,

  // Telemetry tab — its own state slice (issue #4).
  otelHealth: null,         // GET /admin/api/telemetry/health (langfuse.js): the Hub Langfuse sheet, trace links
  otelHealthFailed: false,  // the last health read failed: the Langfuse sheet says "unreachable" (#738)
  // Claude Code (host CLI) OTel-sourced usage panel (issue #68).

  // Code-usage tab — host Claude Code session data (issue #20).
  cldSummary: null,
  cldPeriod: 'today',   // 'today' | 'week' | 'month' | 'all'
  cldMetric: 'input_tokens', // the trend chart's metric: the counter last tapped (#739)
  cldVendorTotals: null,     // {period, rows, totals} from the last All read: the agent sheet's figures (#739)
  cldVendor: 'all',     // 'all' | 'claude' | 'codex' | 'copilot' | <agentsview agent> | 'subagents' (Claude Code OTel, #739) (issues #71, #231, #280)

  // Services card — Docker + Langfuse status (issue #27).
  services: null,
  servicesLaunching: false,
  hubSeries: 'cpu',       // which load history the Hub glance sparkline shows (#738)
  activityScope: 'requests', // Hub Activity: 'requests' | 'errors' (#738)
  hubMetrics: null,       // last /admin/api/telemetry/metrics payload (glance + By model, #738)
  hubMetricsOk: null,     // null before the first read; false after a failed one (glance says "not confirmed")
  hubStatusOk: null,      // null until the first status read; false when the hub did not answer
  agentsviewBusy: false,   // AgentsView start/stop in flight (#280, #284)
  dockerBusy: false,   // Docker Desktop start/stop in flight (#284)
  langfuseBusy: false,   // Langfuse stack start/stop in flight (#284)

  // Startup card — what autostarts with the hub (issue #265).
  startupProfile: null,   // /admin/api/startup-profile payload

  // Fleet placement card — fleet-wide desired state (issue #354).
  fleetPlacement: null,          // /admin/api/fleet-placement payload
  fleetPlacementState: 'loading',// loading | ready | empty | stale | error
  fleetPlacementUpdated: 0,      // Date.now() of the last good fetch

  // Models tab — "Active only" filter, default on (issue #266).
  modelsActiveOnly: true,
  modelsSelected: null,          // { kind: 'model' | 'host', id } — sheet / wide detail (#737)
  roles: null,                   // /admin/api/roles payload, read on Models tab open (#737)
  // Models tab — config-as-code context (#424): models.yaml HEAD sha,
  // whether this hub may write (tower only), and the fleet host list the
  // chain editor offers. Null until the first /admin/api/models fetch.
  modelsConfig: null,

  // Machines tab — fleet status/actions/terminal (issue #309).
  machinesStatus: null,     // last good /admin/api/machines/status payload
  machinesDataState: 'loading',  // loading | ready | empty | stale | error
  machinesLastUpdated: 0,   // Date.now() of the last good fetch
  machinesErrorMsg: '',     // sanitized message for the error empty-state
  machinesBusyIds: {},      // machine id -> true while a reboot/shutdown POST is in flight
  machinesRecheckIds: {},   // machine id -> true while a fresh status re-check is pending
  machinesHubBusyIds: {},   // machine id -> true while a peer-hub bootstrap/sync POST is in flight (#736)
  machinesSelectedId: null, // machine whose sheet / wide detail pane is open (#736)

  // Diagnostics drill-in (issue #315)
  diagStatus: null,         // last /admin/api/diagnostics/status payload
  diagDataState: 'loading', // loading | ready | empty | stale | error
  diagRuns: [],             // recent capture runs (newest first)
  diagSelectedRun: null,    // run_id whose summary is open
  diagSummary: null,        // summary digest for the selected run
  diagDrift: null,          // drift-vs-baseline for the selected run
  diagSummaryState: 'ready',// lifecycle for the selected-run panel
};

// ES modules are deferred; document.getElementById is safe at top level.
export const els = {
  // Tab buttons + panes are owned by the vendored nav (_vendored/nav) —
  // it discovers them from the markup; no element handles needed here.

  // Telemetry tab — health strip + leaderboard + live trace feed (issue #4)

  // Hub card — live status indicator lives inside the card header
  // (replaces the old always-on status strip).
  // One theme toggle per pane header (the vendored home-head).
  themeToggleBtns: Array.from(document.querySelectorAll('.theme-toggle')),
  // Settings (#741): the dialog, its layout, the theme row and the About values
  settingsDialog: document.getElementById('settingsDialog'),
  settingsCloseBtn: document.getElementById('settingsCloseBtn'),
  settingsLayout: document.getElementById('settingsLayout'),
  textSizeControl: document.getElementById('textSizeControl'),
  themeSeg: document.getElementById('themeSeg'),
  startupSummary: document.getElementById('startupSummary'),
  aboutBuild: document.getElementById('aboutBuild'),
  aboutPid: document.getElementById('aboutPid'),
  aboutLangfuse: document.getElementById('aboutLangfuse'),
  hubUptime: document.getElementById('hubUptime'),
  hubSparklines: document.getElementById('hubSparklines'),

  // Services card — Docker + Langfuse (issue #27)
  dockerStatus: document.getElementById('dockerStatus'),
  dockerStatusText: document.getElementById('dockerStatusText'),
  dockerDetail: document.getElementById('dockerDetail'),
  langfuseStatus: document.getElementById('langfuseStatus'),
  langfuseStatusText: document.getElementById('langfuseStatusText'),
  langfuseDetail: document.getElementById('langfuseDetail'),
  dockerStartBtn: document.getElementById('dockerStartBtn'),
  dockerStopBtn: document.getElementById('dockerStopBtn'),
  langfuseStartBtn: document.getElementById('langfuseStartBtn'),
  langfuseStopBtn: document.getElementById('langfuseStopBtn'),
  agentsviewStatus: document.getElementById('agentsviewStatus'),
  agentsviewStatusText: document.getElementById('agentsviewStatusText'),
  agentsviewDetail: document.getElementById('agentsviewDetail'),
  agentsviewStartBtn: document.getElementById('agentsviewStartBtn'),
  agentsviewStopBtn: document.getElementById('agentsviewStopBtn'),
  servicesActions: document.getElementById('servicesActions'),
  // Hub (#738): header line, glance, services rows, activity, sheets
  hubHeadLine: document.getElementById('hubHeadLine'),
  hubWindow: document.getElementById('hubWindow'),
  hubReqCount: document.getElementById('hubReqCount'),
  hubErrCount: document.getElementById('hubErrCount'),
  hubP50: document.getElementById('hubP50'),
  hubSeries: document.getElementById('hubSeries'),
  servicesList: document.getElementById('servicesList'),
  servicesSheet: document.getElementById('servicesSheet'),
  langfuseSheet: document.getElementById('langfuseSheet'),
  langfuseSheetOpenBtn: document.getElementById('langfuseSheetOpenBtn'),
  langfuseHealthValue: document.getElementById('langfuseHealthValue'),
  hubOtelHealth: document.getElementById('hubOtelHealth'),
  hubOtelState: document.getElementById('hubOtelState'),
  hubOtelHash: document.getElementById('hubOtelHash'),
  hubOtelEndpoint: document.getElementById('hubOtelEndpoint'),
  hubOtelOfflineHint: document.getElementById('hubOtelOfflineHint'),
  hubOpenLangfuse: document.getElementById('hubOpenLangfuse'),
  activityScope: document.getElementById('activityScope'),
  traceSheet: document.getElementById('traceSheet'),
  traceSheetTitle: document.getElementById('traceSheetTitle'),
  traceSheetBody: document.getElementById('traceSheetBody'),
  byModelSheet: document.getElementById('byModelSheet'),
  byModelOpenBtn: document.getElementById('byModelOpenBtn'),
  byModelMeta: document.getElementById('byModelMeta'),
  hubByModelSummary: document.getElementById('hubByModelSummary'),
  logSheet: document.getElementById('logSheet'),
  logOpenBtn: document.getElementById('logOpenBtn'),
  servicesLaunchBtn: document.getElementById('servicesLaunchBtn'),
  servicesHint: document.getElementById('servicesHint'),

  // Health & install
  installSummary: document.getElementById('installSummary'),
  installRows: document.getElementById('installRows'),
  installFixAllBtn: document.getElementById('installFixAllBtn'),
  installRefreshBtn: document.getElementById('installRefreshBtn'),

  // Activity feed (#738): requests, errors, the By model counters table
  liveRequestsList: document.getElementById('liveRequestsList'),
  liveRequestsFilter: document.getElementById('liveRequestsFilter'),
  liveRequestsEmpty: document.getElementById('liveRequestsEmpty'),
  countersTable: document.getElementById('countersTable'),
  recentErrorsList: document.getElementById('recentErrorsList'),
  recentErrorsEmpty: document.getElementById('recentErrorsEmpty'),
  hubLog: document.getElementById('hubLog'),
  hubLogPauseBtn: document.getElementById('hubLogPauseBtn'),

  // Models tab
  modelsList: document.getElementById('modelsList'),
  modelsEmpty: document.getElementById('modelsEmpty'),
  modelsScope: document.getElementById('modelsScope'),
  modelsConfigSha: document.getElementById('modelsConfigSha'),
  modelsHeadLine: document.getElementById('modelsHeadLine'),
  modelsMemoryList: document.getElementById('modelsMemoryList'),
  modelsDetail: document.getElementById('modelsDetail'),
  modelSheet: document.getElementById('modelSheet'),
  modelSheetTitle: document.getElementById('modelSheetTitle'),
  modelSheetBody: document.getElementById('modelSheetBody'),
  modelSheetCloseBtn: document.getElementById('modelSheetCloseBtn'),

  // Startup card (issue #265)
  startupList: document.getElementById('startupList'),

  // Fleet placement (issue #354): the memory glance's stale note (#737)
  fleetPlacementStaleNote: document.getElementById('fleetPlacementStaleNote'),

  // Playground
  playgroundModel: document.getElementById('playgroundModel'),
  playgroundSystem: document.getElementById('playgroundSystem'),
  playgroundPrompt: document.getElementById('playgroundPrompt'),
  playgroundAttachment: document.getElementById('playgroundAttachment'),
  playgroundAttachmentBtn: document.getElementById('playgroundAttachmentBtn'),
  playgroundAttachmentName: document.getElementById('playgroundAttachmentName'),
  playgroundMaxTokens: document.getElementById('playgroundMaxTokens'),
  playgroundMaxTokensSeg: document.getElementById('playgroundMaxTokensSeg'),
  playgroundSendBtn: document.getElementById('playgroundSendBtn'),
  playgroundClearBtn: document.getElementById('playgroundClearBtn'),
  playgroundLatency: document.getElementById('playgroundLatency'),
  playgroundReply: document.getElementById('playgroundReply'),
  playgroundUsage: document.getElementById('playgroundUsage'),
  // Playground — image generation / editing tester (issue #114)
  imageCard: document.getElementById('imageCard'),
  imageNone: document.getElementById('imageNone'),
  // Playground composer (#740): the mode switch and the header line
  playMode: document.getElementById('playMode'),
  playHeadLine: document.getElementById('playHeadLine'),
  imageModel: document.getElementById('imageModel'),
  imageModelNote: document.getElementById('imageModelNote'),
  imagePrompt: document.getElementById('imagePrompt'),
  // Size presets + upscale refine (issue #497)
  imageSize: document.getElementById('imageSize'),
  imageSizeNote: document.getElementById('imageSizeNote'),
  imageRefine: document.getElementById('imageRefine'),
  imageRefineRow: document.getElementById('imageRefineRow'),
  imageEditNote: document.getElementById('imageEditNote'),
  imageAttachment: document.getElementById('imageAttachment'),
  imageAttachmentBtn: document.getElementById('imageAttachmentBtn'),
  imageAttachmentName: document.getElementById('imageAttachmentName'),
  imageGenBtn: document.getElementById('imageGenBtn'),
  imageClearBtn: document.getElementById('imageClearBtn'),
  imageLatency: document.getElementById('imageLatency'),
  imagePreview: document.getElementById('imagePreview'),
  imageDownload: document.getElementById('imageDownload'),
  imageDownloadRow: document.getElementById('imageDownloadRow'),
  // Playground — text-to-speech tester (issue #98)
  ttsModel: document.getElementById('ttsModel'),
  ttsAvailability: document.getElementById('ttsAvailability'),
  ttsInput: document.getElementById('ttsInput'),
  ttsLanguage: document.getElementById('ttsLanguage'),
  ttsVoice: document.getElementById('ttsVoice'),
  ttsFormat: document.getElementById('ttsFormat'),
  ttsStreamGroup: document.getElementById('ttsStreamGroup'),
  ttsStream: document.getElementById('ttsStream'),
  ttsSpeedGroup: document.getElementById('ttsSpeedGroup'),
  ttsSpeed: document.getElementById('ttsSpeed'),
  ttsSpeedVal: document.getElementById('ttsSpeedVal'),
  ttsExaggerationGroup: document.getElementById('ttsExaggerationGroup'),
  ttsExaggeration: document.getElementById('ttsExaggeration'),
  ttsExaggerationVal: document.getElementById('ttsExaggerationVal'),
  ttsCfgWeightGroup: document.getElementById('ttsCfgWeightGroup'),
  ttsCfgWeight: document.getElementById('ttsCfgWeight'),
  ttsCfgWeightVal: document.getElementById('ttsCfgWeightVal'),
  ttsSpeakBtn: document.getElementById('ttsSpeakBtn'),
  ttsLatency: document.getElementById('ttsLatency'),
  ttsAudio: document.getElementById('ttsAudio'),
  ttsCard: document.getElementById('ttsCard'),
  ttsNone: document.getElementById('ttsNone'),
  // Playground — TypeSafe Jev decision tester (issue #611)
  jevKeyNote: document.getElementById('jevKeyNote'),
  jevModel: document.getElementById('jevModel'),
  jevState: document.getElementById('jevState'),
  jevQuestions: document.getElementById('jevQuestions'),
  jevJsonError: document.getElementById('jevJsonError'),
  jevEvalBtn: document.getElementById('jevEvalBtn'),
  jevResetBtn: document.getElementById('jevResetBtn'),
  jevLatency: document.getElementById('jevLatency'),
  jevResults: document.getElementById('jevResults'),
  jevServedModel: document.getElementById('jevServedModel'),
  jevAnswers: document.getElementById('jevAnswers'),
  jevUsage: document.getElementById('jevUsage'),

  // Code-usage tab (issue #20)
  cldFreshness: document.getElementById('cldFreshness'),
  // Code (#739): header line, agent sheet, figures, Sub-agents (OTel), one
  // trend chart, breakdown rows and sheets
  codeHeadLine: document.getElementById('codeHeadLine'),
  cldAgentBtn: document.getElementById('cldAgentBtn'),
  cldAgentLabel: document.getElementById('cldAgentLabel'),
  cldAgentSheet: document.getElementById('cldAgentSheet'),
  cldAgentList: document.getElementById('cldAgentList'),
  cldFigures: document.getElementById('cldFigures'),
  cldCounterGrid: document.getElementById('cldCounterGrid'),
  cldUndercountNote: document.getElementById('cldUndercountNote'),
  cldSubagents: document.getElementById('cldSubagents'),
  cldSubSummary: document.getElementById('cldSubSummary'),
  cldSubTable: document.getElementById('cldSubTable'),
  cldSubEmpty: document.getElementById('cldSubEmpty'),
  cldChart: document.getElementById('cldChart'),
  cldChartLabel: document.getElementById('cldChartLabel'),
  cldChartWrap: document.getElementById('cldChartWrap'),
  cldChartEmpty: document.getElementById('cldChartEmpty'),
  cldProjectList: document.getElementById('cldProjectList'),
  cldProjectsAllBtn: document.getElementById('cldProjectsAllBtn'),
  cldProjectsMeta: document.getElementById('cldProjectsMeta'),
  cldProjectSheet: document.getElementById('cldProjectSheet'),
  cldModelOpenBtn: document.getElementById('cldModelOpenBtn'),
  cldModelMeta: document.getElementById('cldModelMeta'),
  cldModelSheet: document.getElementById('cldModelSheet'),
  cldSessionsOpenBtn: document.getElementById('cldSessionsOpenBtn'),
  cldSessionsSheet: document.getElementById('cldSessionsSheet'),
  cldError: document.getElementById('cldError'),
  cldErrorMsg: document.getElementById('cldErrorMsg'),
  cldPeriodSeg: document.getElementById('cldPeriodSeg'),
  cldRequests: document.getElementById('cldRequests'),
  cldTotalCost: document.getElementById('cldTotalCost'),
  cldInputTok: document.getElementById('cldInputTok'),
  cldOutputTok: document.getElementById('cldOutputTok'),
  cldCacheRead: document.getElementById('cldCacheRead'),
  cldInputCost: document.getElementById('cldInputCost'),
  cldOutputCost: document.getElementById('cldOutputCost'),
  cldCacheCost: document.getElementById('cldCacheCost'),
  cldDeltaRequests: document.getElementById('cldDeltaRequests'),
  cldDeltaInputTok: document.getElementById('cldDeltaInputTok'),
  cldDeltaOutputTok: document.getElementById('cldDeltaOutputTok'),
  cldDeltaCacheRead: document.getElementById('cldDeltaCacheRead'),
  cldModelTable: document.getElementById('cldModelTable'),
  cldModelEmpty: document.getElementById('cldModelEmpty'),
  cldProjectTable: document.getElementById('cldProjectTable'),
  cldProjectEmpty: document.getElementById('cldProjectEmpty'),
  cldPending: document.getElementById('cldPending'),
  cldProjectCard: document.getElementById('cldProjectCard'),
  cldVendorCard: document.getElementById('cldVendorCard'),
  cldVendorTable: document.getElementById('cldVendorTable'),
  cldVendorEmpty: document.getElementById('cldVendorEmpty'),
  // Copilot official billing card (issue #231)
  cldCopilotBillingCard: document.getElementById('cldCopilotBillingCard'),
  cldCopilotBillingAsOf: document.getElementById('cldCopilotBillingAsOf'),
  cldCopilotBillingTotal: document.getElementById('cldCopilotBillingTotal'),
  cldCopilotBillingTable: document.getElementById('cldCopilotBillingTable'),
  cldCopilotBillingEmpty: document.getElementById('cldCopilotBillingEmpty'),
  cldCopilotBillingEmptyMsg: document.getElementById('cldCopilotBillingEmptyMsg'),
  // Charts (issue #50)
  cldChartsCard: document.getElementById('cldChartsCard'),
  cldSessionsList: document.getElementById('cldSessionsList'),
  cldSessionsBadge: document.getElementById('cldSessionsBadge'),
  cldSessionsEmpty: document.getElementById('cldSessionsEmpty'),

  // Machines tab (issue #309)
  machinesLoading: document.getElementById('machinesLoading'),
  machinesError: document.getElementById('machinesError'),
  machinesErrorMsg: document.getElementById('machinesErrorMsg'),
  machinesRetryBtn: document.getElementById('machinesRetryBtn'),
  machinesHeadLine: document.getElementById('machinesHeadLine'),
  machinesList: document.getElementById('machinesList'),
  machinesDiagCard: document.getElementById('machinesDiagCard'),
  machinesDetail: document.getElementById('machinesDetail'),
  machineSheet: document.getElementById('machineSheet'),
  machineSheetTitle: document.getElementById('machineSheetTitle'),
  machineSheetBody: document.getElementById('machineSheetBody'),
  machineSheetCloseBtn: document.getElementById('machineSheetCloseBtn'),
  machinesConfirmDialog: document.getElementById('machinesConfirmDialog'),
  machinesConfirmTitle: document.getElementById('machinesConfirmTitle'),
  machinesConfirmCloseBtn: document.getElementById('machinesConfirmCloseBtn'),
  machinesConfirmBody: document.getElementById('machinesConfirmBody'),
  machinesConfirmBtn: document.getElementById('machinesConfirmBtn'),
  machinesTerminalDialog: document.getElementById('machinesTerminalDialog'),
  machinesTerminalTitle: document.getElementById('machinesTerminalTitle'),
  machinesTerminalCloseBtn: document.getElementById('machinesTerminalCloseBtn'),
  machinesTerminalUnavailable: document.getElementById('machinesTerminalUnavailable'),
  machinesTerminalUnavailableMsg: document.getElementById('machinesTerminalUnavailableMsg'),
  machinesTerminalMount: document.getElementById('machinesTerminalMount'),
  diagDialog: document.getElementById('diagDialog'),
  diagCloseBtn: document.getElementById('diagCloseBtn'),
  diagBody: document.getElementById('diagBody'),

  // Misc
  toast: document.getElementById('toast'),
  loginOverlay: document.getElementById('loginOverlay'),
  loginForm: document.getElementById('loginForm'),
  loginPassword: document.getElementById('loginPassword'),
  loginError: document.getElementById('loginError'),
  buildReadout: document.getElementById('buildReadout'),
};
