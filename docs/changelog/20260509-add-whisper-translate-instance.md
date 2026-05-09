# What we did — 2026-05-09

Added a second whisper slot — `whisper_translate` — for the
*translate* role, alongside the existing `whisper` (turbo) slot for
the *transcribe* role. This is a deliberate role-based exception to
the single-slot policy from
[20260424-whisper-model-swap-to-turbo.md](20260424-whisper-model-swap-to-turbo.md).

## Why

Sister project `voice-transcriber` (sibling of `transcribe_voice`) is
adding a 🌐 Translate toggle and consumes our whisper backend on
:8090. Turbo is transcription-only — its decoder distill drops the
translate-task training data, so `task=translate` requests against
turbo return garbage or English-passthrough. Two viable fixes:

1. swap turbo back to large-v3 (slower for the much-more-common
   transcribe path),
2. run a second whisper alongside turbo with a translate-capable
   model.

We picked (2). Translate is rare-use; transcribe is hot-path. We
don't want to slow down dictation just to support occasional
translation.

## Why lazy

Translate is *very* rare on the reference machine. We don't want
medium (~1.5 GB) sitting in RAM permanently when the feature might
not get used for days. Whisper.cpp has no built-in
shutdown-on-idle, so we wrote a tiny FastAPI proxy
(`src/whisper_translate_proxy.py`) that:

- binds the contract port (8091) on boot,
- spawns `whisper-server` on an internal loopback port (18091) only
  when a POST arrives,
- streams the multipart request through to it,
- resets an idle timer on every request,
- SIGTERMs the child after `idle_seconds` (default 300) of no
  traffic — RAM goes back to ~30 MB for the resident proxy.

The proxy itself runs all the time once started; only the model
bytes cycle. From voice-transcriber's perspective the contract is
identical to the turbo slot — just a different port.

## Why CPU

The reference 16 GB GPU already runs gemma4_26b at ~13.4 GB plus the
turbo whisper at ~1.6 GB. Adding medium on GPU would either evict
gemma or fail. Medium on CPU with `-ng` is real-time-ish for short
dictation clips, and free of GPU contention. Worst case for translate
latency (hot, after warm-up) is a few seconds for a typical 30-s
clip — fine for the rare use this is for.

## Files touched

| file                                         | change |
| -------------------------------------------- | ------ |
| [config/models.yaml](../../config/models.yaml) | new `whisper_translate` row (engine `whisper-server-lazy`, port 8091, internal_port 18091, idle_seconds 300, model_path `models/ggml-medium.bin`, args `-ng …`); host `pc-cuda.enabled` extended |
| [src/model_registry.py](../../src/model_registry.py) | `Model` dataclass gains `internal_port` + `idle_seconds` (both Optional) |
| [src/backend_process.py](../../src/backend_process.py) | `_is_whisper` recognises both `whisper-server` and `whisper-server-lazy`; new `_is_lazy_whisper`; `build_command` returns the proxy launch line for the lazy engine |
| [src/whisper_translate_proxy.py](../../src/whisper_translate_proxy.py) | new — FastAPI proxy + `_ChildSupervisor` + idle watchdog |
| [launchers/run_whisper_translate.bat](../../launchers/run_whisper_translate.bat) + [.sh](../../launchers/run_whisper_translate.sh) | new — thin `python -m src.run_backend whisper_translate` wrappers |
| [launchers/run_all.bat](../../launchers/run_all.bat) + [.sh](../../launchers/run_all.sh) | include the new launcher |
| [tests/test_model_registry.py](../../tests/test_model_registry.py) | new `test_whisper_translate_lazy_entry` covers both slots |
| [README.md](../../README.md) | new bullet, layout tree entry, run table line, audio example, updated architecture diagram, new "Exception" paragraph in the latest-only-policy section |
| [docs/project-structure.md](../project-structure.md) | module diagram + key facts updated |
| [docs/changelog/20260424-whisper-model-swap-to-turbo.md](20260424-whisper-model-swap-to-turbo.md) | addendum neutralising the "no parallel rows" non-goal with a pointer to this doc |

## Contract for sister project (voice-transcriber)

| field    | value                                                              |
| -------- | ------------------------------------------------------------------ |
| host     | 127.0.0.1                                                          |
| port     | 8091 (configurable on their side; we don't expect them to change) |
| endpoint | `POST /v1/audio/transcriptions` — same OpenAI-compatible shape as turbo |
| task     | default `transcribe`; pass `task=translate` form-field for translation |
| response | `{"text": "..."}` — same JSON shape as the turbo slot              |
| latency  | first call after idle: ~3-5s (medium loads on CPU). Subsequent calls within 5 min are fast. After 5 min idle, model unloads — next call cold-starts again |
| readiness | `GET http://127.0.0.1:8091/` returns 200 once the proxy is up, regardless of whether the child is loaded. Don't probe, just POST and accept the cold-start latency |

Voice-transcriber should hit `:8091` for translate, `:8090` for
transcribe. Same multipart format on both. Nothing else changes.

## Verification

1. `python -m py_compile src/whisper_translate_proxy.py
   src/backend_process.py src/model_registry.py` → clean.
2. `python -m pytest -q tests/test_model_registry.py` →
   `test_whisper_translate_lazy_entry` passes alongside the existing
   whisper test.
3. `python -m src.install` → `whisper_translate` shows up as
   `missing` until medium is downloaded.
4. `python scripts/download_models.py --only whisper_translate` →
   pulls ~1.5 GB into `models/ggml-medium.bin`.
5. `python -m src.run_backend whisper_translate` → proxy logs
   `whisper-medium-translate proxy ready on :8091 (internal :18091,
   idle=300s, model=models/ggml-medium.bin)` and the whisper-server
   child is **not** running.
6. First `curl -F file=@spanish.wav -F task=translate
   http://127.0.0.1:8091/v1/audio/transcriptions` → ~3-5s wait, then
   200 + `{"text": "Hello, ..."}`. Proxy log shows the child
   spawning.
7. Second curl within 5 min → fast (child already loaded).
8. Wait 5+ min → proxy log shows `stopping whisper child
   (idle>300s)`. Next curl cold-starts again.
9. Turbo on :8090 unaffected: `curl -F file=@english.wav
   http://127.0.0.1:8090/v1/audio/transcriptions` still returns
   transcription unchanged.

## Out of scope

- No tray autostart for `whisper_translate`. Tray menu still
  exposes start/stop like every other backend, but it doesn't
  auto-launch on tray boot.
- No hub-side audio proxy. The hub on :8000 still does not handle
  audio endpoints; voice-transcriber hits :8090 / :8091 directly.
- No model selector for the translate slot. If a user wants
  `large-v3` for higher quality, they swap `hf_pattern` and
  `model_path` in the registry — same single-slot-per-role
  philosophy.
