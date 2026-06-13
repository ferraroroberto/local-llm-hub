/* Playground tab — stacked label/input pairs, More-options details,
 * segmented max-tokens with a numeric override, full-width Send.
 */

import { els, state } from './state.js';
import { api, jsonApi, toast } from './api.js';

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

export async function fetchTtsModels() {
  try {
    const body = await jsonApi('/admin/api/playground/tts_models');
    const models = body.models || [];
    if (!els.ttsModel) return;
    els.ttsModel.innerHTML = '';
    // No TTS backend enabled on this host → hide the whole card.
    if (els.ttsCard) els.ttsCard.hidden = models.length === 0;
    models.forEach(function (m) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.display_name + (m.engine ? ' (' + m.engine + ')' : '');
      els.ttsModel.appendChild(opt);
    });
  } catch (_) { /* ignore */ }
}

export function wirePlayground() {
  if (els.playgroundSendBtn) {
    els.playgroundSendBtn.addEventListener('click', sendPrompt);
  }
  wireTts();
  if (els.playgroundClearBtn) {
    els.playgroundClearBtn.addEventListener('click', function () {
      els.playgroundReply.textContent = '';
      els.playgroundUsage.innerHTML = '';
      els.playgroundLatency.textContent = '';
    });
  }
  // Segmented max-tokens — clicking a preset highlights it AND mirrors
  // the value into the numeric override. Typing into the override clears
  // the active preset highlight (numeric override wins).
  if (els.playgroundMaxTokensSeg) {
    els.playgroundMaxTokensSeg.addEventListener('click', function (ev) {
      const btn = ev.target.closest('button[data-value]');
      if (!btn) return;
      const val = parseInt(btn.dataset.value, 10) || 512;
      els.playgroundMaxTokensSeg.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('active', b === btn);
      });
      if (els.playgroundMaxTokens) els.playgroundMaxTokens.value = String(val);
    });
  }
  if (els.playgroundMaxTokens) {
    els.playgroundMaxTokens.addEventListener('input', function () {
      // User typed into the override — clear preset highlights so it's
      // visually clear the value comes from the input, not a preset.
      if (!els.playgroundMaxTokensSeg) return;
      const current = String(parseInt(els.playgroundMaxTokens.value, 10) || 0);
      els.playgroundMaxTokensSeg.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('active', b.dataset.value === current);
      });
    });
  }
}

function wireTts() {
  // Live value readouts for the two range sliders.
  if (els.ttsExaggeration && els.ttsExaggerationVal) {
    els.ttsExaggeration.addEventListener('input', function () {
      els.ttsExaggerationVal.textContent = els.ttsExaggeration.value;
    });
  }
  if (els.ttsCfgWeight && els.ttsCfgWeightVal) {
    els.ttsCfgWeight.addEventListener('input', function () {
      els.ttsCfgWeightVal.textContent = els.ttsCfgWeight.value;
    });
  }
  if (els.ttsSpeakBtn) {
    els.ttsSpeakBtn.addEventListener('click', speak);
  }
}

async function speak() {
  if (!els.ttsModel || !els.ttsModel.value) {
    toast('No TTS model available.', 'error');
    return;
  }
  const text = (els.ttsInput.value || '').trim();
  if (!text) {
    toast('Text is empty.', 'error');
    return;
  }
  els.ttsSpeakBtn.disabled = true;
  els.ttsLatency.textContent = 'synthesizing…';

  const fd = new FormData();
  fd.append('model', els.ttsModel.value);
  fd.append('input', text);
  fd.append('voice', (els.ttsVoice.value || '').trim());
  fd.append('response_format', els.ttsFormat ? els.ttsFormat.value : 'wav');
  if (els.ttsExaggeration) fd.append('exaggeration', els.ttsExaggeration.value);
  if (els.ttsCfgWeight) fd.append('cfg_weight', els.ttsCfgWeight.value);

  const t0 = performance.now();
  try {
    const res = await api('/admin/api/playground/speak', { method: 'POST', body: fd });
    if (!res.ok) {
      let msg = 'HTTP ' + res.status;
      try { const b = await res.json(); msg = b.detail || msg; } catch (_) { /* ignore */ }
      els.ttsLatency.textContent = '';
      toast(msg, 'error');
      return;
    }
    const blob = await res.blob();
    const elapsed = (performance.now() - t0).toFixed(0);
    els.ttsLatency.textContent = elapsed + ' ms · ' + Math.round(blob.size / 1024) + ' KB';
    if (els.ttsAudio.dataset.url) URL.revokeObjectURL(els.ttsAudio.dataset.url);
    const url = URL.createObjectURL(blob);
    els.ttsAudio.dataset.url = url;
    els.ttsAudio.src = url;
    els.ttsAudio.hidden = false;
    els.ttsAudio.play().catch(function () { /* autoplay may be blocked; controls remain */ });
  } catch (exc) {
    els.ttsLatency.textContent = '';
    toast(String(exc.message || exc), 'error');
  } finally {
    els.ttsSpeakBtn.disabled = false;
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
  if (els.playgroundAttachment && els.playgroundAttachment.files && els.playgroundAttachment.files[0]) {
    fd.append('attachment', els.playgroundAttachment.files[0]);
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
