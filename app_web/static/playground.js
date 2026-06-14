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
      opt.dataset.engine = m.engine || '';
      els.ttsModel.appendChild(opt);
    });
    _syncStreamCheckbox();
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

// Disable the Stream checkbox (and uncheck it) when the selected TTS engine
// does not support true streaming.  Only Orpheus has an incremental decoder;
// Chatterbox synthesises the whole clip first and then yields it as one chunk,
// which causes the Web Audio scheduler to cut audio short (#109).
function _syncStreamCheckbox() {
  if (!els.ttsModel || !els.ttsStream) return;
  const sel = els.ttsModel.options[els.ttsModel.selectedIndex];
  const engine = sel ? (sel.dataset.engine || '') : '';
  const orpheus = engine === 'orpheus';
  els.ttsStream.disabled = !orpheus;
  if (!orpheus) els.ttsStream.checked = false;
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
  if (els.ttsModel) {
    els.ttsModel.addEventListener('change', _syncStreamCheckbox);
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

  const streaming = !!(els.ttsStream && els.ttsStream.checked);
  if (streaming) {
    try {
      await speakStream(text);
    } catch (exc) {
      els.ttsLatency.textContent = '';
      toast(String(exc.message || exc), 'error');
    } finally {
      els.ttsSpeakBtn.disabled = false;
    }
    return;
  }

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

// Streaming synthesis: request headerless PCM16 with stream_format=audio and
// schedule each chunk on a Web Audio timeline so playback starts as soon as
// the first frames arrive. Reports time-to-first-audio vs total — the whole
// point of the streaming endpoint (issue #102). The native <audio> element
// can't consume a chunked POST, hence Web Audio rather than ttsAudio.src.
async function speakStream(text) {
  const fd = new FormData();
  fd.append('model', els.ttsModel.value);
  fd.append('input', text);
  fd.append('voice', (els.ttsVoice.value || '').trim());
  fd.append('response_format', 'pcm');   // headerless PCM16 for Web Audio
  fd.append('stream', 'true');
  if (els.ttsExaggeration) fd.append('exaggeration', els.ttsExaggeration.value);
  if (els.ttsCfgWeight) fd.append('cfg_weight', els.ttsCfgWeight.value);

  els.ttsAudio.hidden = true;            // streamed playback uses Web Audio
  // Create + resume the AudioContext *inside* the click gesture (before the
  // first await) so browser autoplay policy lets it produce sound.
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  try { await ctx.resume(); } catch (_) { /* ignore */ }

  const t0 = performance.now();
  const res = await api('/admin/api/playground/speak', { method: 'POST', body: fd });
  if (!res.ok) {
    let msg = 'HTTP ' + res.status;
    try { const b = await res.json(); msg = b.detail || msg; } catch (_) { /* ignore */ }
    els.ttsLatency.textContent = '';
    ctx.close().catch(function () { /* ignore */ });
    toast(msg, 'error');
    return;
  }

  const sampleRate = parseInt(res.headers.get('X-Sample-Rate') || '24000', 10) || 24000;
  let playHead = ctx.currentTime + 0.08;  // small lead-in to avoid underrun
  let ttfa = null;
  let totalSamples = 0;
  let leftover = new Uint8Array(0);
  const reader = res.body.getReader();

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value || value.length === 0) continue;
    if (ttfa === null) {
      ttfa = performance.now() - t0;
      els.ttsLatency.textContent = 'first audio ' + ttfa.toFixed(0) + ' ms · playing…';
    }
    // Merge any odd trailing byte from the previous chunk, then split into
    // whole int16 samples (carry the remainder forward).
    const merged = new Uint8Array(leftover.length + value.length);
    merged.set(leftover, 0);
    merged.set(value, leftover.length);
    const usable = merged.length - (merged.length % 2);
    leftover = merged.slice(usable);
    if (usable === 0) continue;
    const i16 = new Int16Array(merged.buffer.slice(0, usable));
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const buf = ctx.createBuffer(1, f32.length, sampleRate);
    buf.copyToChannel(f32, 0);
    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(ctx.destination);
    node.start(playHead);
    playHead += buf.duration;
    totalSamples += f32.length;
  }

  const total = performance.now() - t0;
  const audioSec = totalSamples / sampleRate;
  els.ttsLatency.textContent =
    'first audio ' + (ttfa || 0).toFixed(0) + ' ms · total ' + total.toFixed(0) +
    ' ms · ' + audioSec.toFixed(1) + ' s audio';
  // Let scheduled buffers finish, then release the context.
  setTimeout(function () { ctx.close().catch(function () { /* ignore */ }); },
    Math.max(0, (playHead - ctx.currentTime) * 1000) + 500);
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
