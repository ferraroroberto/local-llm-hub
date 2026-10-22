# Models and roles

## Active rotation

*Host names below (`gaming`, `tower`, `mac-mini-m4`) describe the decisions
behind each backend's placement — [`config/models.yaml`](../config/models.yaml)
alone is authoritative for **current** ownership; a host mentioned here can
move without this section being updated.*

Subscription-backed cloud routes (no GPU, no API keys, no Cloud project):

- **`claude-*`** — forwarded to the **`claude -p`** CLI on your machine,
  using your local Claude Code auth (your subscription) instead of an
  API key. Four rows: `claude-haiku-5-5` (alias `claude_haiku`),
  `claude-sonnet-5-5` (`claude_sonnet`), `claude-opus-5-5`
  (`claude_opus`), `claude-fable-5-1` (`claude_fable`). The short aliases
  are version-free, so when a new Claude release lands only the row's
  `display_name` needs updating and downstream callers keep working
  unchanged. The previous version ids (`claude-haiku-4-5`,
  `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-fable-5`) stay as extra
  aliases of their row, so a caller pinned to a raw old id gets the newest
  model instead of an error; the next bump appends its replaced id the same
  way.
- **`gemini-*`** — forwarded to the **Antigravity CLI** (`agy`), using
  your Google sign-in (no API key required). Three rows: `Gemini 3.1
  Pro` (alias `gemini_pro`), `Gemini 3.8 Flash` (alias
  `gemini_flash`), `Gemini 3.7 Flash` (alias `gemini_lite`).
  `agy` replaces the standalone `gemini` CLI, which Google deprecates
  for AI Pro / Ultra subscribers on 2026-06-18. `agy` has no per-call
  model flag, so the hub switches its globally-selected model through
  the `/model` picker before each request — see
  [src/gemini_cli.py](../src/gemini_cli.py). Quotas follow your Google
  AI Pro / Ultra plan.
- **`gemini_image`** — image **generation** via `agy`'s built-in Google
  **Imagen** tool, exposed OpenAI-shape at `POST /v1/images/generations`
  (returns `data[].b64_json`). `agy` ships no Nano Banana picker model;
  Imagen is its only image backend, hosted inside a Flash text session.
  The hub captures the generated artifact and returns it; the call lands
  in the observability ring like other traffic. Editing is also available
  (`POST /v1/images/edits`, multipart image+prompt) but is slow and
  procedural — see [docs/image-generation.md](image-generation.md).
  Both are testable from the admin Playground's Image mode.
- **`flux1_local`** (alias `flux`) — the same `POST /v1/images/generations`
  contract, served **entirely locally** by **FLUX.1 [dev]** (fp8) on ComfyUI,
  on tower's RTX 5060 Ti. Additive rather than a replacement for
  `gemini_image`: no subscription, no network call, and no content policy
  beyond the model's own. Runs `startup: on_demand` — the first request loads
  it and 15 idle minutes unload it again, so it isn't holding GPU memory
  against the always-on agentic/voice rotation. Not fast: measured ~55 s cold
  and ~42 s warm at 1024x1024 / 20 steps (ComfyUI re-streams weights from
  system RAM each run, so warm barely beats cold), for a measured ~5.7 GB peak
  on the GPU. Unlike Imagen it **honours `size`** — presets from `square` up to
  `4k`, where anything above ~2 MP is sampled at a native-safe size and then
  upscaled. Generation only; editing stays on `gemini_image`. Install with
  `scripts/install_comfyui.py` — see
  [docs/image-generation.md](image-generation.md).
- **`flux2_klein`** (alias `klein`) — **FLUX.2 [klein] 4B** on the same GPU and
  the same contract. The **fastest local model at ~14 s** per 1024²
  image and the Playground default. A *split-loader* model (separate
  transformer / text encoder / VAE, paired with Qwen3-4B) using FLUX.2's own
  ComfyUI nodes. In a controlled comparison it beats FLUX.1 on texture and
  typography but **systematically miscounts** — if the prompt says "three of
  X", use `flux1_local`. FLUX.2 [dev] 32B is deliberately not offered as a
  row: it needs ~4 hours per image on a 16 GB card, which is not a model but
  a science experiment. The measurement is kept in
  [docs/image-generation.md](image-generation.md).

Self-hosted entries in active use. A row
runs on whichever machine `config/models.yaml` gives it — not necessarily the
one you are reading this on; `127.0.0.1:<port>` below means "on the owning
host", and every one of them is reachable through this hub's own `:8000`
regardless (see [Multi-host topology](multi-host.md)):

- **`qwen3.5-4b`** — local `llama-server` running
  [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)
  on `127.0.0.1:8088` (4 B hybrid Gated DeltaNet + sparse MoE, full
  GPU offload, Apache 2.0, 262 k native context). Fills the
  `agentic_light` role: OpenClaw fast lane, classification, edge.
  A **virtual no-think alias** `qwen3.5-4b-nothink` shares this same
  `:8088` backend — no second process, no extra VRAM — and makes the hub
  inject `chat_template_kwargs={enable_thinking:false}` into every
  request, so clients that can't send that field themselves (e.g. Home
  Assistant's `extended_openai_conversation`) still reach Qwen's fast,
  no-reasoning path. A caller that sends its own `chat_template_kwargs`
  always wins.

  **The no-think path is the default.** Three aliases, one
  `:8088` process:

  | address as | thinking | use for |
  |---|---|---|
  | `agentic_light` | **off** | the default fast lane — classification, routing, extraction |
  | `agentic_light_nothink` | off | same row; the name Home Assistant sends |
  | `agentic_light_think` | on | when you actually want reasoning |

  Why: on the agentic benchmark the thinking row ran 2.42 s median at 192 output tokens and
  **truncated on 17 of 23 prompts** at a 1024-token budget, while the
  no-think row ran 0.85 s at 42 tokens with zero truncations. This was a
  latency-and-reliability decision, not a measured quality win — the
  thinking arm has no valid quality score precisely because it truncated.
  Reasoning stays available one alias across.
- **`gemma4-26b-a4b-it`** — local `llama-server` running
  [unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF)
  on `127.0.0.1:8087` (25 B / 3.8 B-active MoE, IQ4_XS i-matrix quant
  — whole model on GPU in 16 GB VRAM). Fills the `agentic_heavy` role:
  deep agentic, transcript polishing, document work, EN↔ES↔CA. Also
  addressable as `model="agentic_heavy"` for the same reason. **On-demand**
  (`startup: on_demand`, `idle_unload_minutes: 30`): the first
  request loads it (~tens of seconds), 30 idle minutes unload it — freeing
  ~13.4 GB of tower's VRAM for the voice path between uses.
- **`whisper-large-v3-turbo`** — `whisper-server`
  ([ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp))
  running [ggml-large-v3-turbo.bin](https://huggingface.co/ggerganov/whisper.cpp).
  Reach it identically from any machine: POST to *this* hub's
  `:8000/v1/audio/transcriptions` and the request is served locally or
  proxied to the owning host, landing in the observability ring either way.
  It carries the repo's first production failover chain, so a degraded CPU
  rung can stand in for the daily owner. Fills the `audio_transcribe` role —
  as its *fallback*; parakeet on the Mac Mini is the primary. Ownership,
  ports, and the chain live in [`config/models.yaml`](../config/models.yaml)
  alone; the topology mechanism is walked through under
  [Multi-host topology](multi-host.md). On a host
  that actually runs the backend you can also POST straight to its `:8090` for
  lower overhead (skipping the ring); that port is a shared mutual-exclusion
  lock with the voice-transcriber app.
- **`whisper-medium-translate`** — sibling whisper-server running
  `ggml-medium.bin` on CPU. Same OpenAI-compatible `/v1/audio/transcriptions`
  shape; supports `task=translate` (turbo is transcription-only — its decoder
  distill drops translation). Eager-loaded (~1.5 GB RAM, 0 MB VRAM, always
  ready). Fills the `audio_translate` role; address it through this hub's
  `:8000/v1/audio/translations`. A lazy-load mode is also available —
  add `startup: on_demand` + `idle_unload_minutes` to the row (the same
  generic mechanism `whisper-vanilla` below uses) — for hosts that
  need to reclaim RAM when translate is rare. Current host placement lives
  in [`config/models.yaml`](../config/models.yaml).
- **`whisper-vanilla`** — the same `ggml-large-v3-turbo.bin` as the turbo
  row, configured for **unbiased language auto-detection**. The escape
  hatch for callers transcribing general multilingual audio (e.g. Spanish
  voice notes) that the English tech-dictation glossary would otherwise
  force-Englishize. Select it with `model="whisper-vanilla"` on the
  standard `:8000/v1/audio/transcriptions` request — no `language` needed.
  Two things make detection unbiased, **both required**:
  it carries **no** dictation glossary (`--carry-initial-prompt`/`--prompt`
  bias detection toward English), **and** the hub's audio-transcribe
  dispatch (`server_audio_asr.py`) injects `language=auto` into every
  request that omits one — because whisper-server otherwise forces
  `language=en` per request regardless of its launch-level `--language`
  flag, so dropping the glossary alone is not enough. A caller that sends
  its own `language` always wins. Lazy-loaded (`startup: on_demand`,
  spawn-on-request via `src/on_demand.py`, idle-unload after 5 min)
  so it costs no VRAM when idle. Host placement lives in
  [`config/models.yaml`](../config/models.yaml) alongside the other two
  whisper slots.
- **`piper-tts`** — fast local text-to-speech (the inverse of whisper),
  served by the in-repo FastAPI shim [src/tts_server.py](../src/tts_server.py)
  on `127.0.0.1:8096`. OpenAI-compatible `POST /v1/audio/speech`. POST to
  the hub's proxy at `:8000/v1/audio/speech` with `model="audio_speech"`
  (captured in the observability ring). Uses the standalone Piper binary plus
  ONNX voices in `models/piper/`; default voice is `amy`
  (`en_US-amy-medium`; `ryan`, `ryan-high`, `lessac` remain selectable).
  `piper.exe` runs **resident** (one process per
  voice+speed, ONNX voice loaded once and reused) so short phrases skip the
  per-request model-load tax: integrated latency for `Arming the perimeter.`
  is ~0.06 s direct to `:8096` and ~0.06 s through the hub (warm, connection
  reused). Fills the `audio_speech` role and is auto-loaded by the tray.
- **`orpheus-tts`** — expressive local text-to-speech, served
  by the in-repo FastAPI shim [src/tts_server.py](../src/tts_server.py) on
  `127.0.0.1:8093`. OpenAI-compatible `POST /v1/audio/speech`. POST to the
  hub's proxy at `:8000/v1/audio/speech` (captured in the observability ring)
  or directly to `:8093` for lower overhead. Orpheus-3B is LLM-based, the
  most natural/expressive local voice and faster than real-time on GPU; its
  reference runtime (vLLM) has no usable Windows build, so the shim runs the
  GGUF on the vendored `llama-server` (loopback `:18093`) and decodes its
  audio tokens with the SNAC codec **on CPU** (`--device cpu` — measured
  faster than GPU SNAC and cheaper on VRAM; the llama child stays fully on
  GPU). Primary host plus degraded fallback chain live in
  [`config/models.yaml`](../config/models.yaml). Address explicitly as
  `model="orpheus-tts"` when expressiveness matters more than latency.
- **`kokoro-tts`** — low-footprint Kokoro-82M TTS on `127.0.0.1:8095`, served
  by the same [src/tts_server.py](../src/tts_server.py) OpenAI-compatible
  `/v1/audio/speech` shim. It uses `kokoro-onnx` with the int8 ONNX model and
  packed voice styles in `models/kokoro/`. Start it from the Models tab or
  `launchers/run_model.bat kokoro`, then call the hub with `model="kokoro-tts"`.
  Default voice is `am_michael`, chosen as the closest built-in starting point
  for a Jarvis-like assistant voice. Spanish is available explicitly as
  `ef_dora` (female) or `em_alex` (male); those profiles select Spanish
  phonemization rather than the English default. ONNX Runtime CUDA is used when available,
  but the current Windows path measures roughly 2.2 s direct / 2.5 s through
  the hub for a short phrase, so it is kept as an option rather than the
  `audio_speech` role default until you intentionally repoint that role.
- **`chatterbox-tts`** — second TTS engine on `127.0.0.1:8092`, **on demand**
  (not autostarted). Resemble AI's Chatterbox (~0.5 B, torch) with an
  emotion/"tone" dial (`exaggeration` + `cfg_weight`) and optional zero-shot
  voice cloning. Start it from the Models tab or
  `launchers/run_model.bat chatterbox`. See
  [docs/add-tts.md](add-tts.md) for the engine choice, request shape,
  and the Orpheus GGUF caveat.

**Role vs explicit model on `/v1/audio/*`.** The two are different
contracts. Sending **no `model`** (or the role alias `audio_transcribe` /
`audio_translate`) addresses the *role*, and the hub is free to fall back
across the models in `roles.audio.<role>` when the primary's backend is down —
that is the point of the chain. Sending an **explicit model id** (`whisper`,
`parakeet`, `whisper-vanilla`) is **strict**: it is served by that model or it
fails. Matching is case-insensitive, so `Whisper` is the same strict request as
`whisper`. The rejections are three distinct conditions with three distinct
messages: `400` when the id names something that isn't a transcription backend,
`503 "not enabled on this host"` when it names a transcription model this hub
doesn't serve (a routing fact — nothing is down), and `503 "whisper-server not
running on :<port>"` when the model *is* served here and its backend is
genuinely dead. It is never quietly answered by a different model. (Host-level
failover for the *same* id — the host chain — is not a substitution and still
applies.) A `model=` the registry doesn't know, e.g. the OpenAI SDK's default
`whisper-1`, isn't a model request at all and still addresses the role.

Either way the substitution is **observable**. Every `/v1/audio/transcriptions`
and `/v1/audio/translations` response — including a strict rejection — carries
`X-Hub-Requested-Model`, `X-Hub-Served-Model`, and `X-Hub-Served-Host` (additive
— clients ignore them; the served pair is empty when the request was rejected
before any model ran, and the trio is not emitted on `/v1/audio/speech`, which
has no chain to substitute across). The request ring records `served_model` and
`served_host` alongside `model` (`GET /admin/api/hub/requests/recent`), and the
admin Hub/Telemetry rows render `requested → served @host`. `served_host` is the
dimension a cross-host drill actually needs: a `model=whisper` answered by a
machine with no whisper bound is a legitimate proxy hop to whisper's owner, and
without them no field would say so. Use all of it, not a bare 200, when verifying a
failover drill — see [docs/fleet-maintenance.md](fleet-maintenance.md).

> **Role-addressed audio records.** For a request that
> addresses the *role* (no `model`, or `audio_transcribe` / `audio_translate`),
> the record's `model` field holds the **role alias** rather than the
> concrete model that ran — the concrete one is in `served_model`. The same
> value feeds the OTel `gen_ai.request.model` attribute (with the served id in
> `gen_ai.response.model` and the host in `hub.served_host`), so any dashboard
> grouping audio traffic by *request* model buckets under `audio_transcribe`, not
> `parakeet`. The hub's own per-model counters are unaffected: they key
> off `served_model` when one exists. Group by `served_model` /
> `gen_ai.response.model` to bucket by the model that ran.
>
> The counter key also canonicalises for an **explicit** model request: it is
> the registry row id (`whisper`) rather than whatever alias the client
> typed (`whisper-large-v3-turbo`), because `served_model` records the row that
> actually ran. A dashboard keyed on a display name will not match it.

**Transcription glossary.** Requests that go through the hub's audio
proxy (`:8000/v1/audio/*`) get a deterministic post-processing pass that
fixes persistent domain-term misspellings (e.g. "cloud code" → "Claude
Code"). The rules live in [config/transcription_glossary.json](../config/transcription_glossary.json)
— an ordered list of literal `{"from","to"}` replacements
(case-insensitive, word-boundary, longest-phrase-first) plus a
`boost_terms` vocabulary. Edit it from the **📖 button on any whisper row
in the Models tab** (an inline editor; replacement edits apply without a
restart, boost-term edits on the next whisper start), or hand-edit the
JSON. **✨ Suggest from transcripts** mines the last *N* days of real
dictation from voice-transcriber's session API and proposes additions to
review. Direct hits to `:8090`/`:8091` bypass the glossary (and the
observability ring). See
[docs/whisper-asr.md](whisper-asr.md) for the schema, the
in-app editor + miner, and the companion recognition-boosting mechanism.
NVIDIA Parakeet on Windows+CUDA (`parakeet.cpp`) was evaluated as a
*replacement* for this role and rejected — ~4× worse WER and no boosting
lever on this jargon-heavy workload. Parakeet running on the **Mac Mini's
Apple Neural Engine** (via FluidAudio/CoreML) is a different story: it is the `audio_transcribe` role's primary, with this whisper-turbo
row as its automatic fallback (callers that need whisper's jargon accuracy
send `model=whisper` explicitly) — see
[Multi-host topology](multi-host.md) and
[docs/parakeet-asr-evaluation.md](parakeet-asr-evaluation.md) for the
full trade-off writeup.

## Demoted candidates (kept defined, not in active rotation)

`glm-4.5-air` is **defined in `config/models.yaml`** but not in any
host's `enabled:` list. Bring it up ad-hoc via
`launchers/run_model.bat glm`. See
[docs/frontier-workflow.md](frontier-workflow.md)
for the reasoning.

`qwen3.5-9b` is **active on `mac-mini-m4`** (not `tower`) and is reachable through the Windows hub's own `base_url` like
any other model. See [Multi-host topology](multi-host.md).

GLM **5.2** (the newer flagship) was evaluated for the local coding lane
and rejected — it is a single 744B-A40B MoE with no Air/Flash variant,
and even its smallest quant needs ~245 GB RAM+VRAM vs. this box's
~144 GB, so it does not load. Revisit if a GLM-5.2-Air/Flash ships; see
[docs/glm-5.2-evaluation.md](glm-5.2-evaluation.md).

`gemma4-e4b-it` is the previous `agentic_light` role-holder, replaced
by `qwen3.5-4b` via `/swap-model`. It is **kept in
`enabled:`** on the reference host for ad-hoc bring-up via
`launchers/run_model.bat gemma4_e4b`, but no longer autostarted.

## Roles & bi-weekly refresh

The active local roles live in `config/models.yaml` → `roles:` — that file
is the authority; this table is a snapshot of it:

| Role | Model | Why |
|---|---|---|
| `agentic_light` | `qwen35_4b_nothink` | OpenClaw fast lane / classify / edge — no-think by default; reasoning via `agentic_light_think` |
| `agentic_heavy` | `gemma4_26b` | Deep agentic, transcripts, docs, ES↔EN↔CA |
| `audio_transcribe` | `parakeet` (fallback: `whisper`) | EN/ES audio → text — parakeet on the Mac ANE is primary; whisper-turbo takes over if it is down |
| `audio_translate` | `whisper_translate` | ES audio → English (eager CPU sibling) |
| `audio_speech` | `piper` | text → speech (Piper fast default; Orpheus/Kokoro/Chatterbox on demand) |

Two Claude Code entry points drive the refresh:

- **`/frontier-refresh`** — the research skill
  (`.claude/skills/frontier-refresh/SKILL.md`, the single owner of the
  brief, cadence, and output contract). Regenerates
  `docs/frontier/runs/<today>/{report.md,frontier.json,frontier.html}`,
  repoints `LATEST`, and posts the per-role verdict as a comment on the
  always-open **frontier ledger issue
  ([#272](https://github.com/ferraroroberto/local-llm-hub/issues/272))** —
  its last comment is always the current state. **Read-only on the
  registry** — produces artifacts only, never rewires anything. Runs
  unattended **bi-weekly** (the skill's `run-weekly.bat`, registered in
  app-launcher's Jobs tab weekly FRI 02:30, self-skipping alternate
  weeks) and on demand any time.
- **`/swap-model`** — interactive role swap. Reads the latest run +
  current roles, asks one question at a time (which role, which target,
  hf_repo if not registered, download now?), shows the planned diff,
  then edits `config/models.yaml` + writes a launcher pair + (optionally)
  shells out to `scripts/download_models.py`.

To browse a run interactively, open `GET /admin/frontier` (the admin SPA
footer's 📈 Frontier link), or the standalone chart directly at
`docs/frontier/runs/<date>/frontier.html`. To act on a run, run
`/swap-model` from Claude Code.

Side-by-side technical specs + docs links for all active models live in
[docs/model-comparison.md](model-comparison.md). The latest research
brief and run sit under [docs/frontier/](frontier/).

Point any client — the official `anthropic` or `openai` SDKs, openClaw,
a curl one-liner — at `http://127.0.0.1:8000` and swap backends by
changing the `model` string. Claude requests bill your subscription;
local model requests never leave the machine.

## Latest-only policy

This repo intentionally ships **one model per role**. When a newer
release in the same family covers the same use case on the reference
hardware (e.g. Gemma 4 superseding Gemma 3), the older entry is
removed — registry, launchers, weights, and docs all go. The current
lineup and what each model is for live in
[docs/model-comparison.md](model-comparison.md). Older entries
survive only in `git log` for historical context.

**Exception — one model per role, not per family.** The whisper
backend has two slots — `whisper` (turbo, 8090, transcription) and
`whisper_translate` (medium, 8091, translation). Turbo is
distill-decoded and was *not* trained on the translate task, so we
keep medium in a sibling slot for the rare cases when translation is
needed. Medium runs eager on CPU (~1.5 GB RAM) so the first translate
call is instant. The single-slot rule still applies *per role* —
there is one transcription model and one translation model.

