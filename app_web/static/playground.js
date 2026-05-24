/* Playground tab — prompt + reply + token breakdown + image attach. */

import { els, state } from './state.js';
import { api, jsonApi, toast, readToken } from './api.js';

export async function fetchPlaygroundModels() {
  try {
    const body = await jsonApi('/admin/api/playground/models');
    const models = body.models || [];
    if (!els.playgroundModel) return;
    els.playgroundModel.innerHTML = '';
    models.forEach(function (m) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.display_name + ' (' + m.backend + ')';
      opt.dataset.backend = m.backend;
      els.playgroundModel.appendChild(opt);
    });
  } catch (_) { /* ignore */ }
}

export function wirePlayground() {
  if (els.playgroundSendBtn) {
    els.playgroundSendBtn.addEventListener('click', sendPrompt);
  }
  if (els.playgroundClearBtn) {
    els.playgroundClearBtn.addEventListener('click', function () {
      els.playgroundReply.textContent = '';
      els.playgroundUsage.innerHTML = '';
      els.playgroundLatency.textContent = '';
    });
  }
}

async function sendPrompt() {
  const modelSel = els.playgroundModel;
  const prompt = (els.playgroundPrompt.value || '').trim();
  if (!modelSel.value) {
    toast('Pick a model first.', 'error');
    return;
  }
  if (!prompt) {
    toast('Prompt is empty.', 'error');
    return;
  }
  els.playgroundSendBtn.disabled = true;
  els.playgroundReply.textContent = '…';
  els.playgroundUsage.innerHTML = '';
  els.playgroundLatency.textContent = 'sending…';

  const fd = new FormData();
  fd.append('model', modelSel.value);
  fd.append('prompt', prompt);
  fd.append('max_tokens', String(parseInt(els.playgroundMaxTokens.value, 10) || 512));
  const system = (els.playgroundSystem.value || '').trim();
  if (system) fd.append('system', system);
  if (els.playgroundImage && els.playgroundImage.files && els.playgroundImage.files[0]) {
    fd.append('image', els.playgroundImage.files[0]);
  }

  const t0 = performance.now();
  try {
    const res = await api('/admin/api/playground/send', { method: 'POST', body: fd });
    const body = await res.json().catch(function () { return null; });
    if (!res.ok) {
      const msg = (body && body.detail) || ('HTTP ' + res.status);
      els.playgroundReply.textContent = '[' + res.status + '] ' + msg;
      els.playgroundLatency.textContent = '';
      toast(msg, 'error');
      return;
    }
    const elapsed = (performance.now() - t0).toFixed(0);
    els.playgroundLatency.textContent = elapsed + ' ms';
    els.playgroundReply.textContent = body.text || '(no text)';
    renderUsage(body.usage || {});
  } catch (exc) {
    els.playgroundReply.textContent = String(exc.message || exc);
    toast(String(exc.message || exc), 'error');
  } finally {
    els.playgroundSendBtn.disabled = false;
  }
}

function renderUsage(usage) {
  const grid = els.playgroundUsage;
  if (!grid) return;
  grid.innerHTML = '';
  const rows = [
    ['Input', usage.input_tokens || 0],
    ['Output', usage.output_tokens || 0],
    ['Cache read', usage.cache_read_input_tokens || 0],
    ['Cache write', usage.cache_creation_input_tokens || 0],
  ];
  rows.forEach(function (r) {
    const div = document.createElement('div');
    div.innerHTML = '<span class="muted">' + r[0] + '</span><span>' + r[1] + '</span>';
    grid.appendChild(div);
  });
}
