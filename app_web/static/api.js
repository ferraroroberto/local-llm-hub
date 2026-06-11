/* HTTP + SSE helpers, bearer-token plumbing, login overlay, toast. */

import { els, state, TOKEN_KEY } from './state.js';

// --------------------------------------------------------------- tokens
export function tokenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const t = (params.get('token') || '').trim();
  if (!t) return null;
  params.delete('token');
  const newQuery = params.toString();
  const newUrl =
    window.location.pathname +
    (newQuery ? '?' + newQuery : '') +
    window.location.hash;
  window.history.replaceState({}, '', newUrl);
  return t;
}

export function readToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
export function writeToken(t) { if (t) localStorage.setItem(TOKEN_KEY, t); }
export function clearToken() { localStorage.removeItem(TOKEN_KEY); }

export function urlWithToken(path) {
  const token = readToken();
  if (!token) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set('token', token);
  return url.pathname + url.search + url.hash;
}

// --------------------------------------------------------------- fetch
export async function api(path, opts) {
  opts = opts || {};
  const headers = new Headers(opts.headers || {});
  const token = readToken();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    showLogin();
    throw new Error('auth required');
  }
  return res;
}

export async function jsonApi(path, opts) {
  const res = await api(path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { body = null; }
  if (!res.ok) {
    const detail = (body && body.detail) || ('HTTP ' + res.status);
    const err = new Error(detail);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// JSON POST helper.
export function postJson(path, payload) {
  return jsonApi(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

// JSON PUT helper.
export function putJson(path, payload) {
  return jsonApi(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

// --------------------------------------------------------------- SSE
// EventSource doesn't support custom headers, so we attach the bearer
// token via ?token=… (the BearerTokenMiddleware accepts that form).
// Returns the EventSource — caller is responsible for .close().
export function eventStream(path, handlers) {
  const url = urlWithToken(path);
  const es = new EventSource(url);
  if (handlers && handlers.message) {
    es.onmessage = function (ev) {
      let data = ev.data;
      try { data = JSON.parse(ev.data); } catch (_) { /* keep raw */ }
      handlers.message(data, ev);
    };
  }
  if (handlers && handlers.error) {
    es.onerror = handlers.error;
  }
  return es;
}

// --------------------------------------------------------------- login
export function showLogin() {
  if (!els.loginOverlay) return;
  els.loginOverlay.hidden = false;
  if (els.loginPassword) { els.loginPassword.value = ''; els.loginPassword.focus(); }
}

export function hideLogin() {
  if (els.loginOverlay) els.loginOverlay.hidden = true;
}

export function wireLoginForm(onLoginSuccess) {
  if (!els.loginForm) return;
  els.loginForm.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    els.loginError.hidden = true;
    const password = els.loginPassword.value;
    try {
      const res = await fetch('/admin/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const body = await res.json().catch(function () { return null; });
      if (!res.ok || !body || !body.token) {
        const msg = (body && body.detail) || 'Login failed';
        els.loginError.textContent = msg;
        els.loginError.hidden = false;
        return;
      }
      writeToken(body.token);
      hideLogin();
      if (onLoginSuccess) onLoginSuccess();
    } catch (exc) {
      els.loginError.textContent = String(exc.message || exc);
      els.loginError.hidden = false;
    }
  });
}

// --------------------------------------------------------------- toast
let toastTimer = null;
export function toast(msg, kind) {
  if (!els.toast) return;
  els.toast.textContent = msg;
  els.toast.className = 'toast ' + (kind || '');
  els.toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(function () {
    els.toast.hidden = true;
  }, kind === 'error' ? 4500 : 2200);
}

// --------------------------------------------------------------- fmt
export function fmtBytes(n) {
  if (!Number.isFinite(n)) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

export function fmtAge(ts) {
  if (!ts) return '';
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 1000) return 'just now';
  if (ms < 60_000) return Math.floor(ms / 1000) + 's ago';
  if (ms < 3_600_000) return Math.floor(ms / 60_000) + 'm ago';
  return Math.floor(ms / 3_600_000) + 'h ago';
}
