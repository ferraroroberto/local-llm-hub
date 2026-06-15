/* Shared singletons: state, DOM-element references, polling intervals.
 *
 * Auth: a bearer token is stored in localStorage. The page extracts it
 * from ?token=… on first load and strips it from the URL. On 401, the
 * login overlay shows; password → /admin/api/login → bearer token.
 *
 * Hub density: persisted to localStorage so the user's preference
 * survives a reload. Default is "compact".
 */

export const TOKEN_KEY = 'llmhub.token';
export const DENSITY_KEY = 'llmhub.hub.density';

export const STATUS_POLL_MS = 4000;
export const COUNTERS_POLL_MS = 4000;
export const MODELS_POLL_MS = 5000;
export const STATS_POLL_MS = 2000;

export const state = {
  tab: 'hub',
  status: null,           // /admin/api/hub/status payload
  models: [],             // /admin/api/models payload
  counters: [],           // rows from /admin/api/hub/counters
  liveRequests: [],       // ring synced from SSE stream
  recentErrors: [],
  logLines: [],
  logPaused: false,
  installRows: [],
  version: null,
  hubStreamCtl: null,     // EventSource abort handles
  hubLogStreamCtl: null,
  density: 'compact',     // 'compact' | 'expanded' — set by main.js from localStorage
  compactSection: 'live', // active sub-tab inside the compact card

  // Telemetry tab — its own state slice (issue #4).
  telHealth: null,
  telCounters: [],
  telTraces: [],
  telStreamCtl: null,
  telExpandedTraceId: '',

  // Code-usage tab — host Claude Code session data (issue #20).
  cldSummary: null,
  cldPeriod: 'today',   // 'today' | 'week' | 'month' | 'all'
  cldVendor: 'all',     // 'all' | 'claude' | 'codex' (issue #71)

  // Services card — Docker + Langfuse status (issue #27).
  services: null,
  servicesLaunching: false,
};

// ES modules are deferred; document.getElementById is safe at top level.
export const els = {
  app: document.querySelector('main.app'),
  tabHub: document.getElementById('tabHub'),
  tabModels: document.getElementById('tabModels'),
  tabPlayground: document.getElementById('tabPlayground'),
  tabTelemetry: document.getElementById('tabTelemetry'),
  tabCodeUsage: document.getElementById('tabCodeUsage'),
  paneHub: document.getElementById('paneHub'),
  paneModels: document.getElementById('paneModels'),
  panePlayground: document.getElementById('panePlayground'),
  paneTelemetry: document.getElementById('paneTelemetry'),
  paneCodeUsage: document.getElementById('paneCodeUsage'),

  // Telemetry tab — health strip + leaderboard + live trace feed (issue #4)
  telHealth: document.getElementById('telHealth'),
  telHealthText: document.getElementById('telHealthText'),
  telOtelState: document.getElementById('telOtelState'),
  telHashMode: document.getElementById('telHashMode'),
  telEndpoint: document.getElementById('telEndpoint'),
  telOfflineHint: document.getElementById('telOfflineHint'),
  telOpenLangfuse: document.getElementById('telOpenLangfuse'),
  telSummary: document.getElementById('telSummary'),
  telCountersTable: document.getElementById('telCountersTable'),
  telTracesList: document.getElementById('telTracesList'),
  telTracesBadge: document.getElementById('telTracesBadge'),
  telTracesEmpty: document.getElementById('telTracesEmpty'),

  // Hub card — live status indicator lives inside the card header
  // (replaces the old always-on status strip).
  hubLiveStatus: document.getElementById('hubLiveStatus'),
  hubLiveStatusText: document.getElementById('hubLiveStatusText'),
  hubRestartBtn: document.getElementById('hubRestartBtn'),
  hubPid: document.getElementById('hubPid'),
  hubUptime: document.getElementById('hubUptime'),
  hubSparklines: document.getElementById('hubSparklines'),

  // Services card — Docker + Langfuse (issue #27)
  servicesCard: document.getElementById('servicesCard'),
  servicesOverall: document.getElementById('servicesOverall'),
  servicesOverallText: document.getElementById('servicesOverallText'),
  dockerStatus: document.getElementById('dockerStatus'),
  dockerStatusText: document.getElementById('dockerStatusText'),
  dockerDetail: document.getElementById('dockerDetail'),
  langfuseStatus: document.getElementById('langfuseStatus'),
  langfuseStatusText: document.getElementById('langfuseStatusText'),
  langfuseDetail: document.getElementById('langfuseDetail'),
  servicesActions: document.getElementById('servicesActions'),
  servicesLaunchBtn: document.getElementById('servicesLaunchBtn'),
  servicesHint: document.getElementById('servicesHint'),

  // Health & install
  installCard: document.getElementById('installCard'),
  installSummary: document.getElementById('installSummary'),
  installRows: document.getElementById('installRows'),
  installFixAllBtn: document.getElementById('installFixAllBtn'),
  installRefreshBtn: document.getElementById('installRefreshBtn'),

  // Density toggle + compact card sub-tabs
  hubDensity: document.getElementById('hubDensity'),
  hubCompactCard: document.getElementById('hubCompactCard'),
  hubCompactTabs: document.getElementById('hubCompactTabs'),

  // Compact-mode list/badge/empty/log refs
  liveRequestsList: document.getElementById('liveRequestsList'),
  liveRequestsBadge: document.getElementById('liveRequestsBadge'),
  liveRequestsEmpty: document.getElementById('liveRequestsEmpty'),
  countersTable: document.getElementById('countersTable'),
  recentErrorsList: document.getElementById('recentErrorsList'),
  recentErrorsBadge: document.getElementById('recentErrorsBadge'),
  recentErrorsEmpty: document.getElementById('recentErrorsEmpty'),
  hubLog: document.getElementById('hubLog'),
  hubLogPauseBtn: document.getElementById('hubLogPauseBtn'),

  // Expanded-mode duplicates — render functions write to both
  liveRequestsListExp: document.getElementById('liveRequestsListExp'),
  liveRequestsBadgeExp: document.getElementById('liveRequestsBadgeExp'),
  liveRequestsEmptyExp: document.getElementById('liveRequestsEmptyExp'),
  countersTableExp: document.getElementById('countersTableExp'),
  recentErrorsListExp: document.getElementById('recentErrorsListExp'),
  recentErrorsBadgeExp: document.getElementById('recentErrorsBadgeExp'),
  recentErrorsEmptyExp: document.getElementById('recentErrorsEmptyExp'),
  hubLogExp: document.getElementById('hubLogExp'),
  hubLogPauseBtnExp: document.getElementById('hubLogPauseBtnExp'),

  // Models tab
  modelsList: document.getElementById('modelsList'),
  modelsEmpty: document.getElementById('modelsEmpty'),

  // Playground
  playgroundModel: document.getElementById('playgroundModel'),
  playgroundSystem: document.getElementById('playgroundSystem'),
  playgroundPrompt: document.getElementById('playgroundPrompt'),
  playgroundMore: document.getElementById('playgroundMore'),
  playgroundAttachment: document.getElementById('playgroundAttachment'),
  playgroundMaxTokens: document.getElementById('playgroundMaxTokens'),
  playgroundMaxTokensSeg: document.getElementById('playgroundMaxTokensSeg'),
  playgroundSendBtn: document.getElementById('playgroundSendBtn'),
  playgroundClearBtn: document.getElementById('playgroundClearBtn'),
  playgroundLatency: document.getElementById('playgroundLatency'),
  playgroundReply: document.getElementById('playgroundReply'),
  playgroundUsage: document.getElementById('playgroundUsage'),
  // Playground — image generation / editing tester (issue #114)
  imageCard: document.getElementById('imageCard'),
  imageModel: document.getElementById('imageModel'),
  imagePrompt: document.getElementById('imagePrompt'),
  imageAttachment: document.getElementById('imageAttachment'),
  imageGenBtn: document.getElementById('imageGenBtn'),
  imageClearBtn: document.getElementById('imageClearBtn'),
  imageLatency: document.getElementById('imageLatency'),
  imagePreview: document.getElementById('imagePreview'),
  imageDownload: document.getElementById('imageDownload'),
  imageDownloadRow: document.getElementById('imageDownloadRow'),
  // Playground — text-to-speech tester (issue #98)
  ttsModel: document.getElementById('ttsModel'),
  ttsInput: document.getElementById('ttsInput'),
  ttsVoice: document.getElementById('ttsVoice'),
  ttsFormat: document.getElementById('ttsFormat'),
  ttsStream: document.getElementById('ttsStream'),
  ttsExaggeration: document.getElementById('ttsExaggeration'),
  ttsExaggerationVal: document.getElementById('ttsExaggerationVal'),
  ttsCfgWeight: document.getElementById('ttsCfgWeight'),
  ttsCfgWeightVal: document.getElementById('ttsCfgWeightVal'),
  ttsSpeakBtn: document.getElementById('ttsSpeakBtn'),
  ttsLatency: document.getElementById('ttsLatency'),
  ttsAudio: document.getElementById('ttsAudio'),
  ttsCard: document.getElementById('ttsCard'),

  // Code-usage tab (issue #20)
  cldFreshness: document.getElementById('cldFreshness'),
  cldVendorSeg: document.getElementById('cldVendorSeg'),
  cldPeriodSeg: document.getElementById('cldPeriodSeg'),
  cldRequests: document.getElementById('cldRequests'),
  cldTotalCost: document.getElementById('cldTotalCost'),
  cldInputTok: document.getElementById('cldInputTok'),
  cldOutputTok: document.getElementById('cldOutputTok'),
  cldCacheRead: document.getElementById('cldCacheRead'),
  cldInputCost: document.getElementById('cldInputCost'),
  cldOutputCost: document.getElementById('cldOutputCost'),
  cldOutputReasoning: document.getElementById('cldOutputReasoning'),
  cldCacheCost: document.getElementById('cldCacheCost'),
  cldDeltaRequests: document.getElementById('cldDeltaRequests'),
  cldDeltaInputTok: document.getElementById('cldDeltaInputTok'),
  cldDeltaOutputTok: document.getElementById('cldDeltaOutputTok'),
  cldDeltaCacheRead: document.getElementById('cldDeltaCacheRead'),
  cldModelTable: document.getElementById('cldModelTable'),
  cldModelEmpty: document.getElementById('cldModelEmpty'),
  cldProjectTable: document.getElementById('cldProjectTable'),
  cldProjectEmpty: document.getElementById('cldProjectEmpty'),
  cldVendorCard: document.getElementById('cldVendorCard'),
  cldVendorTable: document.getElementById('cldVendorTable'),
  cldVendorEmpty: document.getElementById('cldVendorEmpty'),
  // Charts (issue #50)
  cldChartsCard: document.getElementById('cldChartsCard'),
  cldChartInput: document.getElementById('cldChartInput'),
  cldChartOutput: document.getElementById('cldChartOutput'),
  cldChartReqs: document.getElementById('cldChartReqs'),
  cldChartCache: document.getElementById('cldChartCache'),
  cldSessionsList: document.getElementById('cldSessionsList'),
  cldSessionsBadge: document.getElementById('cldSessionsBadge'),
  cldSessionsEmpty: document.getElementById('cldSessionsEmpty'),

  // Misc
  frontierLink: document.getElementById('frontierLink'),
  toast: document.getElementById('toast'),
  loginOverlay: document.getElementById('loginOverlay'),
  loginForm: document.getElementById('loginForm'),
  loginPassword: document.getElementById('loginPassword'),
  loginError: document.getElementById('loginError'),
  buildReadout: document.getElementById('buildReadout'),
};
