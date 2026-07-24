# Parakeet ASR transcribe-backend evaluation — 2026-06-17 (Windows+CUDA), updated 2026-07-02 (Mac Mini ANE, then accepted as a selectable alternative same day)

Spike for [#123](https://github.com/ferraroroberto/local-llm-hub/issues/123)
(Windows+CUDA), extended for [#138](https://github.com/ferraroroberto/local-llm-hub/issues/138)
(Mac Mini Apple Neural Engine / CoreML — a different runtime, not a re-run).
Follows the faster-whisper NO-GO ([#92](https://github.com/ferraroroberto/local-llm-hub/issues/92)),
which named NVIDIA Parakeet (TDT 0.6B v3) — not faster-whisper — as the
higher-value ASR target *if* a transcribe-backend swap is ever justified.

**Decision (Windows+CUDA, #123): NO-GO.** Keep `whisper-large-v3-turbo`
(boosted, `:8090`) as the `audio_transcribe` role. Parakeet is ~3× faster but
~4× *less* accurate on this English+Spanish domain-jargon workload, has no
recognition-boosting lever, and ships no OpenAI-compatible server on
Windows+CUDA. See [When to revisit](#when-to-revisit).

**Decision (Mac Mini ANE, #138): accepted as a selectable, non-default
alternative** (superseding the original "NO-GO for now" call below — see
[2026-07-02 update, part 2](#update-2026-07-02-part-2-accepted-as-a-selectable-alternative)).
Faster end-to-end (even over the LAN) and jargon survival is much closer than
the Windows spike, but it drops the "Claude Code" wake phrase and still
mangles "YOLO"→"yellow" every time. Neither is a blocker for every use case —
e.g. Home Assistant voice commands care about speed more than perfect jargon
recognition — so Parakeet is enrolled as `model=parakeet`, explicitly *not*
the `audio_transcribe` role default (whisper-turbo keeps that). A real
term-boosting lever exists in this runtime (unlike Windows+CUDA) but wasn't
integrated in this pass. See the
[2026-07-02 update](#update-2026-07-02-mac-mini-ane-coreml--spike-for-138)
below.

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

---

## Update 2026-07-02: Mac Mini ANE (CoreML) — spike for #138

[#138](https://github.com/ferraroroberto/local-llm-hub/issues/138) revisits
Parakeet on a **different deployment target**: the Mac Mini's Apple Neural
Engine via CoreML, using [FluidInference/FluidAudio](https://github.com/FluidInference/FluidAudio)
(Swift, `parakeet-tdt-0.6b-v3-coreml`). This directly answers revisit-condition
#1 above (a real OpenAI-shape server exists on this runtime) and partially
answers #2 (a term-boosting lever exists, though not yet integrated — see
below). Decision below is scoped to *this* runtime; the Windows+CUDA NO-GO
above is unchanged.

**Original decision: NO-GO for now** on enrolling Parakeet-on-ANE as a
selectable `audio_transcribe` alternative. It is faster end-to-end (including
the LAN hop) than the boosted whisper-turbo path, and jargon survival is much
closer than the Windows+CUDA spike suggested — but it drops the "Claude Code"
wake phrase outright in both clips that opened with it, and still mangles
"YOLO" → "yellow" every time. Revisit if the custom-vocabulary integration
(below) closes those two gaps.

**This call was revisited the same day — see
[part 2](#update-2026-07-02-part-2-accepted-as-a-selectable-alternative)
below**, once the underlying multi-host routing infrastructure (#178) made
"enroll as a selectable, non-default alternative" a real, low-risk option
distinct from "swap the `audio_transcribe` default."

### What was built

- Provisioned the Mac Mini (`roberto.local`, Apple M4, 16 GB, macOS 26.2)
  entirely over SSH/sudo — no GUI touched. Xcode Command Line Tools installed
  headlessly via `softwareupdate --install`; Homebrew via the no-sudo
  `git clone` method (the official installer's multi-step `sudo` script
  doesn't survive a non-tty SSH session cleanly).
- A small Swift package (`~/parakeet-worker` on the Mac, not committed —
  see below) wraps `FluidAudio`'s `AsrManager`: loads
  `AsrModels.downloadAndLoad(version: .v3)` once, then serves transcription
  requests over stdin/stdout (one JSON result per line, model stays warm).
- A thin FastAPI wrapper (`parakeet_server.py`, also on the Mac only) exposes
  OpenAI-shaped `POST /v1/audio/transcriptions` on `:8098`, converting
  uploads to 16 kHz mono via `afconvert` and forwarding to the worker.
  **Not the hub's own `tts-server`/whisper-server pattern** — this is spike
  scaffolding to answer the evaluation question, not a production install.
  Wiring an approved backend into the hub properly is tracked separately
  (enroll on `mac-mini-m4` in `config/models.yaml`, generalize
  `_pick_whisper_port` role resolution — #138's own acceptance criteria,
  deferred pending a clearer go).

### How it was measured

12 real dictation clips pulled fresh from the **voice-transcriber archive**
(`E:\automation\voice-transcriber\archive`, ~1700 sessions as of 2026-07-02)
— the original #123 clip selection no longer exists on disk
(`.scratch/parakeet-bench/` is gitignored and was cleaned up after that spike
closed), so this is a new but comparably-structured sample: 6 jargon-heavy +
6 general, one of the jargon clips containing the same "Orpheus... muchas
gracias" English→Spanish code-switch #123 used. 561 s total, 7 s–213 s
range (word-count-weighted, not time-weighted like #123 — see below).
Reproducible from `.scratch/parakeet-bench/` (gitignored; clips are personal
dictation audio, not committed).

- **Parakeet:** the from-scratch FluidAudio worker above, called over the
  LAN from the Windows PC (full HTTP round trip, not loopback).
- **Whisper:** the live hub `:8000` → `:8090` `whisper-large-v3-turbo`
  (boosted), same as #123, also over HTTP (loopback on the Windows box).
- **Reference for WER:** the *original* voice-transcriber archived
  transcript for each clip (produced live by the user's daily-driver boosted
  whisper.cpp pipeline) — not a third fresh engine call. This is the closest
  available approximation to ground truth without listening to the audio,
  but **structurally favors whisper**: the reference is itself a
  whisper-family transcript, so it shares whisper's phrasing/punctuation
  conventions in a way Parakeet's differently-styled output doesn't get
  credit for. Filler words (`uh`/`um`) and case/punctuation were normalized
  out of both hypothesis and reference before scoring, same as #123.

### Results

| Metric (12 clips) | Parakeet TDT v3 (Mac ANE, CoreML, over LAN) | whisper-large-v3-turbo (boosted, loopback) |
| --- | --- | --- |
| **Word error rate** (vs archived ref) | 7.9 % | 3.8 % |
| **Total wall-clock, all 12 clips** | **7.9 s** | 16.5 s |
| **Per-clip engine-only inference** (7–92 s clips) | 0.09 s – 0.27 s | n/a (not separately instrumented) |

The WER gap here (≈2×) is narrower than the Windows+CUDA spike's ≈4× gap —
partly a real runtime/model difference, partly reference bias (above). Take
the ratio as directional, not absolute.

**The wall-clock result is the headline finding.** Parakeet-on-ANE, called
over the LAN from the Windows PC (network hop + `afconvert` + CoreML
inference), was faster in total wall-clock than whisper-turbo-boosted called
over *loopback* on the reference CUDA box — 7.9 s vs 16.5 s for the same 12
clips, roughly 2× faster despite paying a network round trip whisper doesn't.
A single representative round trip (7.7 s clip): **0.30 s total** (Windows
client → Mac Mini server → response), of which only 0.14 s was engine
inference — the rest is network + format conversion. This is the concrete
number for the "how much latency does routing through a Mac Mini backend
add" question that motivated this spike: on a same-LAN reference, the answer
is "well under whisper's own loopback CUDA latency," not "an expensive extra
hop."

### Jargon-term survival (why NO-GO despite the speed/WER story)

Across the 6 jargon clips, tracking domain terms the hub's #90/#91 boosting
line of work cares about:

| Term | Parakeet (ANE) | whisper-turbo (boosted) |
| --- | --- | --- |
| "Claude Code" (×2, wake phrase) | **dropped entirely, both times** ("Yes, new issue..." / "Yeah, look at this...") | ✅ both |
| "YOLO" (×3) | **"yellow", all three times** (reproduces #123's Windows-CUDA finding exactly) | ✅ all three |
| "Orpheus" | ✅ correct | ✅ correct |
| "muchas gracias" (ES tail) | ✗ missing | ✗ **also missing this run** — inconclusive vs #123's confirmed multilingual-gap finding; needs a re-test isolating this clip |
| "Shift-Tab" | ✗ → "shift dub" | ✅ |
| "PTI", "Crosslinked", "Elgato", issue number | ✅ all | ✅ all |
| "Dangerous Skip Permission" | ✅ correct | ✗ whisper mis-heard as "Dangerous Escape Permission" |
| "Auto Approve" | ✅ correct | ✗ whisper mis-heard as "Auto-Proof" |

Two findings cut against #123's Windows-CUDA verdict: "Orpheus" is now
recognized correctly (the v3 CoreML build may simply be a better checkpoint
than the Windows GGUF quant), and whisper-turbo made two of its own domain
errors that Parakeet didn't. But the **dropped wake phrase is a new,
practical blocker** #123 never tested — "Claude Code" is the trigger phrase
for voice-command dictation flows, and losing it silently (no error, just
absent from the transcript) is worse than a garbled-but-present transcription.

### Custom vocabulary / term-boosting — real, but not integrated today

Unlike the Windows+CUDA `parakeet.cpp` build #123 evaluated (no boosting
lever at all), **FluidAudio does ship one**:
`CustomVocabularyContext`/`CustomVocabularyTerm`, documented in
[`Documentation/ASR/CustomVocabulary.md`](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/CustomVocabulary.md).
It's a post-processing rescorer, not a decoder bias: a separate CTC head
scores each vocabulary term against per-frame log-probabilities and replaces
transcript words when a boosted term has stronger acoustic evidence
(default context-biasing weight 3.0). This is a plausible fix for exactly
the "YOLO"→"yellow" and dropped-"Claude Code" failures above.

**Not validated this session** — confirmed to exist and read the source
(`VocabularyRescorer`, `CtcKeywordSpotter`), but wiring it requires loading a
second CTC model and the token-timing rescoring pipeline
(`TranscribeCommand.swift`'s implementation is ~50 lines beyond what the
thin worker does today), a meaningfully bigger lift than fit in this spike.
The full `fluidaudiocli` also currently fails to build on this toolchain
(Swift 6.3.3 / Xcode CLT 26.6) — an unrelated compiler type-checking timeout
in `NemotronMultilingualFleursBenchmark.swift`, not something blocking the
library itself (our own worker, which only depends on the `FluidAudio`
library target, builds and runs fine).

### TTS: what was and wasn't measured

Per #138's acceptance criteria, a same-text (~15-word sentence) latency
comparison across what's actually running today:

| Path | Wall-clock | Notes |
| --- | --- | --- |
| Piper (`audio_speech` role, CPU, Windows) | **0.33 s** | hub's fast default |
| macOS `say` / `AVSpeechSynthesizer` (Mac Mini) | 1.04 s (for 4.5 s of audio) | confirmed working, on-device, zero setup |
| Orpheus (GGUF+SNAC, CUDA, Windows) | 3.61 s | hub's expressive/reference default |
| Kokoro (ONNX, CUDA, Windows, `:8095`) | not measured | backend wasn't warm (not in `tray.autostart_models`); starting it costs a cold-load cycle not worth conflating with steady-state numbers in this pass |
| Kokoro-on-Mac (MLX/CoreML) | not attempted | no ready FluidAudio path found; would need its own CoreML/MLX integration, same scope as the ASR worker |
| FluidAudio `KokoroAne` / `PocketTTS` | not attempted | present in the FluidAudio library (per its README) but require their own model downloads/integration — out of today's time budget |

**Apple-native TTS finding:** confirms the issue's own expectation — there is
no supported local "Apple Intelligence" neural TTS API distinct from
AVFoundation. `say`/`AVSpeechSynthesizer` is the only on-device path, it
works today with zero setup, and it's a legitimate pragmatic fallback/quick
option (1.04 s for a ~4.5 s utterance), but nothing here beat or clearly
complemented Orpheus/Piper enough to warrant enrolling it as a real
`audio_speech` alternative — it's closer to "already available if ever
needed" than "measured and chosen."

**Not a complete bake-off.** The interesting comparison — a genuine
Apple-native/CoreML TTS (KokoroAne, PocketTTS) vs Kokoro-on-the-5060 — is
still open. Flagging rather than silently dropping it: this needs its own
follow-up pass once/if the ASR side's custom-vocabulary integration is
judged worth the additional Mac Mini Swift-engineering investment.

### When to revisit (Mac-ANE specific)

1. ~~Custom-vocabulary boosting gets wired into the worker and closes the
   "Claude Code" / "YOLO" gaps above.~~ **DISPROVEN 2026-07-24 (#401) — see
   the update at the end of this doc.** The FluidAudio rescorer was wired in
   and cannot close either gap without corrupting clean dictation; item
   closed, not deferred.
2. The Kokoro-on-Mac vs Kokoro-on-5060 TTS comparison actually runs.
3. The Spanish code-switch finding gets re-tested in isolation (this run was
   inconclusive — both engines missed it, unlike #123's confident whisper-catches-it
   result on a differently-sourced clip).

---

## Update 2026-07-02, part 2: accepted as a selectable alternative

The NO-GO-for-now call above was a **default-swap** recommendation: don't
make Parakeet the `audio_transcribe` role's backend, because it would
regress every caller of that role, including ones (like the "Claude Code"
wake phrase) that can't tolerate the accuracy loss. That conclusion still
holds and is unchanged.

What changed is the option space. Building out generic multi-host model
routing ([#178](https://github.com/ferraroroberto/local-llm-hub/issues/178))
— a model can be "owned" by one hub and proxied through any other, with
start/stop/log/ping all working transparently across machines — made a third
option available that the original spike didn't consider: enroll Parakeet as
an **explicitly-selected, non-default** model (`model=parakeet`), reachable
from either hub, sitting *alongside* whisper-turbo rather than replacing it.
Callers that need the wake phrase and jargon accuracy keep using the default
role unchanged; callers that care more about latency than perfect recognition
(the concrete example: Home Assistant voice commands) can opt in explicitly.

This is a different, lower-risk decision made with full knowledge of the
measured trade-offs above (dropped wake phrase, "YOLO"→"yellow", narrower
language coverage) — not a reversal of the accuracy findings themselves, and
not a call to revisit custom-vocabulary integration (still open, still
tracked in [When to revisit](#when-to-revisit-mac-ane-specific) above).
Enrolled together with #178 and
[#179](https://github.com/ferraroroberto/local-llm-hub/issues/179) (Mac
Mini status indicator) on branch `feat/138-mac-mini-parakeet-tts-spike`;
`mac/parakeet-worker/` (the Swift FluidAudio package) and
`src/parakeet_server.py` are now committed to the repo instead of living
only on the Mac, so the install is reproducible (`python -m src.install
--fix` builds the worker via `swift build -c release`) rather than
hand-provisioned.

Mac-native TTS (`say`-based `mac_say` model, also scoped in the original
#138 plan) was **not** built in this pass — deferred, not attempted; the
`say`/`AVSpeechSynthesizer` measurement above still stands as the reference
number if it's picked up later.

---

## Update 2026-07-24: custom-vocabulary rescorer wired and DISPROVEN (#401)

[When to revisit](#when-to-revisit-mac-ane-specific) item 1 above — "wire
custom-vocabulary boosting into the worker and close the 'Claude Code' /
'YOLO' gaps" — was built end-to-end and **does not work**. The FluidAudio
rescorer flagged as a "plausible fix" in *Custom vocabulary / term-boosting*
above cannot recover either failure without corrupting correctly-transcribed
words. The spike was **not merged**; this section is the durable record.
Parakeet stays a selectable, non-default `audio_transcribe` backend and
whisper-turbo remains the default jargon-safe path — no role change.

### What was wired

FluidAudio 0.15.4's *only* vocabulary mechanism for the Parakeet TDT 0.6B v3
model is a **post-hoc CTC rescorer** — there is no decode-time TDT biasing
hook (`AsrManager.transcribe` takes no vocabulary argument, and
`SlidingWindowAsrManager` applies the same rescorer *after* decode). The
worker was extended to run FluidAudio's own `TranscribeCommand.swift`
reference pipeline: `CustomVocabularyContext.loadWithCtcTokens` (which loads
a separate ~97 MB CTC-110M encoder — the 0.6B TDT model has no built-in CTC
head, so Approach 2 is mandatory) → `CtcKeywordSpotter.spotKeywordsWithLogProbs`
→ `VocabularyRescorer.ctcTokenRescore`, seeded from the hub's existing
`boost_terms` glossary via `src/parakeet_server.py`'s single loader. It
builds clean (`swift build -c release`, Swift 6.3.3 / FluidAudio 0.15.4) —
the `fluidaudiocli` build-timeout noted above never blocked the library
target the worker depends on.

### Before/after: threshold sweep over the #138/#343 jargon clips

Ran the rescorer against the jargon clips (`jargon_01/02` open with the
"Claude Code" wake phrase; `jargon_04/06` end with "issue YOLO") at four
`(context-biasing-weight, min-similarity)` settings, plus an isolating
two-term run:

| Setting (cbw / minSim) | "Claude Code" drop recovered | "YOLO"→"yellow" recovered | Clean words corrupted |
| --- | --- | --- | --- |
| 4.5 / 0.55 (size-default, 28 terms) | ✗ | ✗ | ✗ `mention`→`Notion`, `left, right`→`Playwright`, `tab`→`Tailscale`, `model`→`Codex` |
| 3.0 / 0.65 | ✗ | ✗ | some remain |
| 2.0 / 0.72 | ✗ | ✗ | none (general clips clean) |
| 1.5 / 0.80 | ✗ | ✗ | none |
| 4.5 / 0.50, vocab = {Claude Code, YOLO} only | ✓ | ✓ | ✗ `so they both mention`→`the YOLO both YOLO`, `side of`→`Claude Code`, `feature to`→`YOLO` |

The result is a hard precision/recall wall: **every false-positive-free
setting is a no-op on both target failures, and every setting that recovers a
target simultaneously corrupts correct words.** There is no usable threshold
in between.

### Root cause

The rescorer only **swaps an existing transcript word** for a vocabulary term
when that term has both stronger CTC acoustic evidence *and* enough string
similarity to the word it would replace. It cannot **insert** a token the TDT
decoder never emitted. That breaks both target cases:

- **"Claude Code" is dropped, not mis-spelled** — the decoder emits an
  unrelated word ("Yes" / "Yeah") where the phrase was spoken.
  `similarity("Yes", "Claude Code") ≈ 0`, so no false-positive-safe threshold
  swaps it in; only recklessly-low thresholds do, and those rewrite unrelated
  words elsewhere. A dropped phrase is structurally unreachable for a
  swap-only rescorer.
- **"YOLO" (4 chars) is gated by the short-word guard** — terms ≤4 chars need
  ≥0.80 similarity, and `similarity("yellow", "YOLO")` is below that, so
  FP-safe settings never fire it; permissive settings fire "YOLO" onto
  unrelated short fragments ("so they", "feature to").

For daily-driver dictation, silently rewriting a correctly-transcribed word
(`mention`→`Notion`) is worse than a known-absent wake phrase, so no
permissive setting is shippable — and the FP-safe settings change nothing
while adding a ~97 MB model load plus per-request CTC inference. Hence
**not planned**.

### Consequence

- [When to revisit](#when-to-revisit-mac-ane-specific) item 1 is **closed as
  disproven**, not deferred. Items 2 (Kokoro-on-Mac TTS) and 3 (Spanish
  code-switch isolation) remain open and untouched by this pass.
- No change to the `audio_transcribe` role or its glossary: whisper-turbo
  already recognizes both "Claude Code" and "YOLO" correctly (see the
  survival table above), so it stays the default and the jargon-safe path;
  parakeet stays selectable non-default for latency-sensitive callers.
