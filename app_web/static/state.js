/* Shared singletons: state, DOM-element references, polling intervals.
 *
 * Auth: a bearer token is stored in localStorage. The page extracts it
 * from ?token=… on first load and strips it from the URL. On 401, the
 * login overlay shows; password → /admin/api/login → bearer token.
 */

export const TOKEN_KEY = 'llmhub.token';

export const STATUS_POLL_MS = 4000;
export const COUNTERS_POLL_MS = 4000;
export const MODELS_POLL_MS = 5000;
export const STATS_POLL_MS = 2000;

export const state = {
  tab: 'hub',
  status: null,           // /admin/api/hub/status payload
  models: [],             // /admin/api/models payload
  counters: {},           // /admin/api/hub/counters payload
  liveRequests: [],       // ring synced from SSE stream
  recentErrors: [],
  logLines: [],
  logPaused: false,
  installRows: [],
  version: null,
  hubStreamCtl: null,     // EventSource abort handles
  hubLogStreamCtl: null,
};

// ES modules are deferred; document.getElementById is safe at top level.
export const els = {
  hubStatusDot: document.getElementById('hubStatusDot'),
  hubStatusText: document.getElementById('hubStatusText'),
  tabHub: document.getElementById('tabHub'),
  tabModels: document.getElementById('tabModels'),
  tabPlayground: document.getElementById('tabPlayground'),
  paneHub: document.getElementById('paneHub'),
  paneModels: document.getElementById('paneModels'),
  panePlayground: document.getElementById('panePlayground'),

  // Hub tab
  hubRestartBtn: document.getElementById('hubRestartBtn'),
  hubStopBtn: document.getElementById('hubStopBtn'),
  hubLocalUrl: document.getElementById('hubLocalUrl'),
  hubLanUrl: document.getElementById('hubLanUrl'),
  hubPid: document.getElementById('hubPid'),
  hubUptime: document.getElementById('hubUptime'),
  hubSparklines: document.getElementById('hubSparklines'),
  installCard: document.getElementById('installCard'),
  installSummary: document.getElementById('installSummary'),
  installRows: document.getElementById('installRows'),
  installFixAllBtn: document.getElementById('installFixAllBtn'),
  installRefreshBtn: document.getElementById('installRefreshBtn'),
  liveRequestsList: document.getElementById('liveRequestsList'),
  liveRequestsBadge: document.getElementById('liveRequestsBadge'),
  liveRequestsEmpty: document.getElementById('liveRequestsEmpty'),
  countersTable: document.getElementById('countersTable'),
  recentErrorsList: document.getElementById('recentErrorsList'),
  recentErrorsBadge: document.getElementById('recentErrorsBadge'),
  recentErrorsEmpty: document.getElementById('recentErrorsEmpty'),
  hubLog: document.getElementById('hubLog'),
  hubLogPauseBtn: document.getElementById('hubLogPauseBtn'),

  // Models tab
  modelsList: document.getElementById('modelsList'),
  modelsEmpty: document.getElementById('modelsEmpty'),

  // Playground
  playgroundModel: document.getElementById('playgroundModel'),
  playgroundSystem: document.getElementById('playgroundSystem'),
  playgroundPrompt: document.getElementById('playgroundPrompt'),
  playgroundImage: document.getElementById('playgroundImage'),
  playgroundMaxTokens: document.getElementById('playgroundMaxTokens'),
  playgroundSendBtn: document.getElementById('playgroundSendBtn'),
  playgroundClearBtn: document.getElementById('playgroundClearBtn'),
  playgroundLatency: document.getElementById('playgroundLatency'),
  playgroundReply: document.getElementById('playgroundReply'),
  playgroundUsage: document.getElementById('playgroundUsage'),

  // Misc
  toast: document.getElementById('toast'),
  loginOverlay: document.getElementById('loginOverlay'),
  loginForm: document.getElementById('loginForm'),
  loginPassword: document.getElementById('loginPassword'),
  loginError: document.getElementById('loginError'),
  buildReadout: document.getElementById('buildReadout'),
};
