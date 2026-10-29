# Parakeet ASR transcribe-backend evaluation

NVIDIA's NeMo Parakeet TDT family vs `whisper-large-v3-turbo` as the
`audio_transcribe` role backend. The live assignment is
`config/models.yaml` → `roles.audio.transcribe`; this doc records why,
not what is deployed.

## Current state

- **Role:** `audio_transcribe` primary is Parakeet (`model=parakeet`,
  served on the Mac Mini's Apple Neural Engine via
  [FluidInference/FluidAudio](https://github.com/FluidInference/FluidAudio)),
  with `whisper-large-v3-turbo` (boosted, `:8090`) as the automatic
  fallback (`{model_id: parakeet, fallback: [whisper]}`). Callers that
  need the jargon-safe path send `model=whisper` explicitly — the hub
  honours that and never substitutes.
- **Checkpoint:** the worker's pinned checkpoint is FluidInference's
  `.ultra` derivative (`FluidInference/parakeet-ultra-coreml`,
  post-trained by Moondream from NVIDIA's `parakeet-tdt-0.6b-v3`), not
  the original `.v3`. `mac/parakeet-worker/` (Swift, FluidAudio) and
  `src/parakeet_server.py` are committed to the repo; install builds the
  worker via `swift build -c release`.
- **Windows+CUDA:** no viable Parakeet path exists there (see
  [Why Windows+CUDA is out](#why-windowscuda-is-out)) — the role only
  runs Parakeet on the Mac ANE.

## Why Parakeet is the role primary despite worse jargon accuracy

The driver is latency and fleet placement, not a jargon-accuracy win: a
cross-host voice benchmark measured ~65.8× RTFx on the Mac ANE vs
whisper's ~40× on the tower. Parakeet measurably loses the accuracy and
jargon comparison on this hub's English/Spanish domain-jargon dictation
workload (both on Windows+CUDA and on the Mac ANE — see below), but
whisper-turbo stays one HTTP call away as the fallback and the explicit
`model=whisper` escape hatch, so the role swap trades default-path
accuracy for default-path latency without removing the accurate path.

## Measured trade-offs

- **Word error rate.** Across two independently-measured sample sets
  (12 real dictation clips each, English+Spanish domain jargon), whisper
  consistently beats Parakeet: ~1.6% vs ~6.6% on the Windows+CUDA
  `.v3` q8_0 GGUF spike, ~3.8% vs ~7.9% on the Mac ANE `.v3` CoreML
  spike (the latter's reference is itself whisper-derived, which
  structurally favors whisper — directional, not absolute). The
  `.ultra` checkpoint narrows this further (8.85% → 7.71% WER vs `.v3`
  on the same 12 clips) but does not close it.
- **Jargon/wake-phrase survival — the structural blocker.** Parakeet has
  no decode-time term-boosting hook (`AsrManager.transcribe` takes no
  vocabulary argument); whisper has one (`--carry-initial-prompt` seeded
  from the hub's glossary) and uses it. Both `.v3` and `.ultra` drop the
  "Claude Code" wake phrase entirely (0/2) and mangle "YOLO" → "yellow"
  (1/3) — unchanged by the checkpoint swap. A post-hoc CTC rescorer
  (FluidAudio's `CustomVocabularyContext`/`VocabularyRescorer`) was
  built and evaluated as a fix and **disproven**: it can only swap an
  existing transcript word for a boosted term with sufficient acoustic
  + string similarity, so it cannot insert a dropped phrase or fix a
  short word ("YOLO") below its similarity guard. Every
  false-positive-free threshold was a no-op on both target failures,
  and every threshold that fixed one corrupted unrelated correct words
  (`mention`→`Notion`, `tab`→`Tailscale`). **Not planned** — do not
  re-attempt this approach without a decode-time biasing hook instead
  of a post-hoc rescorer.
- **Spanish code-switch.** `.v3` drops the Spanish tail ("muchas
  gracias") on one real code-switch clip; `.ultra` recovers it on that
  same clip (one clip — suggestive, not confirmed on a larger sample).
- **Multilingual coverage.** Parakeet's ~25-language coverage is
  narrower than whisper's 99 — relevant for the hub's ES/CA dictation
  workload.
- **Numeral formatting.** `.ultra` writes numerals as digits ("241",
  "1st") where `.v3` spelled them out — harmless for dictation, but a
  downstream text consumer parsing transcripts should account for it.
- **Fleet placement latency.** Routing through the Mac Mini backend over
  the LAN adds well under whisper's own loopback-CUDA latency on this
  reference network — a representative round trip measured ~0.30 s
  total, of which ~0.14 s was engine inference.

## Why Windows+CUDA is out

The runtime that runs fastest on Windows+CUDA (`mudler/parakeet.cpp`,
ggml, CUDA prebuilt) is CLI-only — no HTTP server, let alone the OpenAI
`/v1/audio/transcriptions` shape the hub's architecture needs. The one
ready-made OpenAI-shape Parakeet server (`achetronic/...`) is CPU-only
and Linux-only, which throws away the speed rationale entirely. Parakeet
only runs through the hub today on the Mac ANE via FluidAudio, which
ships a real HTTP server.

## License (CC-BY-4.0 — attribution required)

Parakeet Ultra is a derivative of NVIDIA's `parakeet-tdt-0.6b-v3`
(NVIDIA, CC-BY-4.0), post-trained by Moondream (`moondream/parakeet-ultra`,
CC-BY-4.0) and converted to CoreML by FluidInference
(`FluidInference/parakeet-ultra-coreml`, CC-BY-4.0). The credit is
carried in the README "License" section. FluidAudio itself is
Apache-2.0.

## Revisit triggers

- A Parakeet runtime ships a decode-time term-biasing hook (not a
  post-hoc rescorer) that can insert a dropped phrase or fix a
  short boosted word — the structural blocker above, not the
  checkpoint, is what needs to change.
- A Windows+CUDA Parakeet runtime ships an OpenAI-shape HTTP server out
  of the box, removing the need for a bespoke wrapper.
- The Spanish code-switch improvement under `.ultra` gets confirmed on
  a larger sample than one clip.
- The workload shifts to long-form, latency-critical, English-only
  transcription where Parakeet's throughput and clean long-form output
  (no one-phrase-per-line fragmentation) become decision-relevant over
  jargon accuracy.
