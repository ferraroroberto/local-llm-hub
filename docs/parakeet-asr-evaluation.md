# Parakeet (`parakeet.cpp`) ASR transcribe-backend evaluation — 2026-06-17

Spike for [#123](https://github.com/ferraroroberto/local-llm-hub/issues/123).
Follows the faster-whisper NO-GO ([#92](https://github.com/ferraroroberto/local-llm-hub/issues/92)),
which named NVIDIA Parakeet (TDT 0.6B v3) — not faster-whisper — as the
higher-value ASR target *if* a transcribe-backend swap is ever justified. This
spike measured it on the reference box against the hub's real workload and
reached a decision.

**Decision: NO-GO.** Keep `whisper-large-v3-turbo` (boosted, `:8090`) as the
`audio_transcribe` role. Parakeet is ~3× faster but ~4× *less* accurate on this
English+Spanish domain-jargon workload, has no recognition-boosting lever, and
ships no OpenAI-compatible server on Windows+CUDA. Revisit only if those change
(see [When to revisit](#when-to-revisit)).

---

## What Parakeet is, and which port actually fits

"Parakeet" is NVIDIA's NeMo ASR family. The 2026 interest is **TDT 0.6B v3** —
on the HF Open ASR leaderboard it posts ~6.3% English WER vs Whisper large-v3's
~7.4%, at far higher throughput. The hub's architecture (a C++ server binary on
a port speaking OpenAI's `/v1/audio/transcriptions`) only fits if a Parakeet
runtime can do the same. The landscape is **fragmented**, and no single port
gives Windows + CUDA *and* the OpenAI shape:

| Project | Runtime | Windows | GPU | OpenAI server | Boosting |
| --- | --- | --- | --- | --- | --- |
| **mudler/parakeet.cpp** | ggml/C++ (GGUF) | ✅ prebuilt | ✅ **CUDA** | ❌ CLI only | ❌ none |
| achetronic/parakeet | ONNX/Go | ❌ Linux | ❌ CPU-only | ✅ yes | ❌ `prompt` ignored |
| groxaxo/…-fastapi-openai | Python/FastAPI | unclear | varies | ✅ yes | ❌ |
| Frikallo/parakeet.cpp | Axiom/Metal | ❌ | Metal (Apple) | ❌ | ❌ |

So the runtime that runs fast on the reference hardware (**mudler/parakeet.cpp**,
ggml, Windows-CUDA prebuilt, GGUF weights from `mudler/parakeet-cpp-gguf`) is
**CLI-only** — it has no HTTP server at all, let alone the OpenAI shape. A "go"
would mean writing and maintaining a thin FastAPI wrapper around its CLI / C-API
(the same shape as `src/tts_server.py` wrapping `llama-server`). The only
ready-made OpenAI-shape Parakeet server (achetronic) is **CPU-only and
Linux-only**, which throws away the entire speed rationale. **This answers
acceptance criterion #2: not directly — only via a wrapper we'd have to build.**

## How it was measured

Everything ran on the reference Windows box — **RTX 5060 Ti (16 GB), CUDA
12.0** — fully reproducible from `.scratch/parakeet-bench/` (gitignored):

- **Parakeet:** `mudler/parakeet.cpp` v0.2.0 Windows-CUDA prebuilt
  (`parakeet-cli`), model `tdt-0.6b-v3-q8_0.gguf` (q8_0 = near-lossless; the
  vendor reports all quants at ~WER-0 vs NeMo, so quant is not the variable).
  `parakeet-cli bench` loads the model once (406 ms) and reports per-clip *warm*
  inference time — the correct number to compare against a warm server.
- **Whisper:** the live `:8090` `whisper-large-v3-turbo`, **boosted** exactly as
  shipped (`--max-context 64 --carry-initial-prompt`, glossary `boost_terms`).
  Round-trip HTTP latency over loopback (warm).
- **Clips:** 12 real dictation recordings pulled from the **voice-transcriber
  archive** (`E:\automation\voice-transcriber\archive`, 392 sessions) — 6 s to
  127 s, 531 s total, 6 jargon-heavy + 6 general English, plus one English↔
  Spanish code-switch.

**WER caveat (important).** I cannot listen to the audio, so there is no truly
independent gold transcript. References were **adjudicated from both engines'
outputs plus the known domain vocabulary**, so neither engine is treated as
ground truth; genuinely ambiguous spots are reconstructed by judgement. Filled
pauses (`uh`/`um`) and number formatting (`64` vs "sixty-four") are normalised
out of refs *and* hypotheses so neither style is penalised. The WER figures are
**comparative, not absolute** — but the gap is far larger than the curation
uncertainty.

## Results

| Metric (12 clips, 531 s) | Parakeet TDT v3 q8_0 (CUDA, warm) | whisper-large-v3-turbo (boosted) |
| --- | --- | --- |
| **Word error rate** | **6.62 %** | **1.63 %** |
| **Throughput** | **159.9× realtime** | 53.8× realtime |
| **Total warm inference** | 3.3 s | 9.9 s |
| **Jargon-term survival** | 16 / 23 | **21 / 23** |

### What the numbers mean

- **Speed: Parakeet wins (~3×), but it doesn't matter here.** Whisper turbo
  already runs at ~54× realtime on this GPU — a 30 s dictation clip transcribes
  in ~0.5 s. Faster than "instant" is not a benefit for human-in-the-loop
  dictation. Parakeet's lead would matter for a *different* use case (long-form
  batch, latency-critical, English-only) — not this one.
- **Accuracy: whisper wins decisively (~4×).** On this English-heavy domain
  workload whisper-turbo-boosted lands ~1.6 % WER vs Parakeet's ~6.6 %.
- **Jargon: whisper wins, structurally.** Parakeet mangled the exact terms the
  hub cares about — Orpheus → "off-heos"/"off-seus", Sonnet → "Sonic",
  Chatterbox → "chapter box", Kokoro → "coco roll", YOLO → "yellow", PC →
  "APC" — and **has no lever to fix them**: there is no `initial_prompt` /
  `hotwords` flag (`parakeet-cli transcribe` exposes only `--decoder --lang
  --threads --timestamps --json`). Whisper hits these because of the #91
  `--carry-initial-prompt` boost sourced from the committed glossary. Both
  engines miss "cloud code" → "Claude Code" equally — the #90 replacement
  glossary fixes that downstream for both, so it is not a differentiator.
- **Multilingual gap, confirmed empirically.** Despite v3's multilingual
  billing, Parakeet **dropped the Spanish tail "muchas gracias"** entirely (clip
  `j2`); whisper caught it. The hub's workload includes ES/CA dictation, which
  Parakeet's ~25-language coverage serves worse than Whisper's 99.
- **One genuine Parakeet strength:** clean flowing prose on long clips. On the
  127 s clip whisper fragmented into one-phrase-per-line; Parakeet produced
  well-punctuated continuous text. Nice, but cosmetic — the hub's downstream
  consumers don't depend on it, and an LLM cleanup pass (the #92 follow-up) would
  normalise either engine's formatting anyway.

## Decision: NO-GO

Migrating the `audio_transcribe` role from whisper-turbo to Parakeet would, on
the hub's real workload, **regress accuracy (~1.6 % → ~6.6 % WER) and jargon
recognition (21/23 → 16/23)**, lose Spanish code-switch coverage, and **add a
bespoke OpenAI-shape HTTP wrapper to build and maintain** (no Windows+CUDA
Parakeet server exists) — in exchange for speed the use case does not need. The
one structural blocker is decisive on its own: Parakeet has **no
recognition-boosting mechanism**, so the jargon gap cannot be closed the way
whisper closes it (#91), and domain-term accuracy is the whole reason this line
of spikes (#90/#91/#92) exists.

This reinforces #92's conclusion: the higher-value next step toward the
voice "gold standard" is **not** an ASR backend swap — it is the optional
**ASR → local-LLM cleanup pass** using the hub's existing models.

## When to revisit

Reopen this only if **all three** flip:

1. A `parakeet.cpp` build serves OpenAI `/v1/audio/transcriptions` on
   Windows+CUDA out of the box (no bespoke wrapper).
2. Parakeet gains a term-boosting lever (`initial_prompt` / `hotwords`
   equivalent) so domain jargon can be biased like whisper's #91 path.
3. The workload shifts to long-form, English-only, latency-critical
   transcription where Parakeet's throughput and clean long-form output become
   decision-relevant.

Until then, `whisper-large-v3-turbo` (boosted) + the #90 glossary + a future
LLM cleanup pass is the better-fitting stack.
