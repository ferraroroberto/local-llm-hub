# Local LLM Hub

A tiny local HTTP hub that routes `POST /v1/messages` (Anthropic shape) and
`POST /v1/chat/completions` (OpenAI shape) to several backends by `model` name,
plus a local whisper.cpp ASR pair and a text-to-speech pair, both reachable
through the hub's `/v1/audio/*` proxy (observable) or directly on their own
ports. Audio runs both directions: speech→text via `/v1/audio/transcriptions`
and text→speech via `/v1/audio/speech`. Image **generation** is available
OpenAI-shape at `POST /v1/images/generations`, from either Google Imagen (via
the Antigravity CLI) or FLUX.1 [dev] running locally on the GPU.

Point any client — the official `anthropic` or `openai` SDKs, openClaw, a curl
one-liner — at `http://127.0.0.1:8000` and swap backends by changing the
`model` string. Claude requests bill your subscription; local model requests
never leave the machine.

## Documentation index

[`config/models.yaml`](config/models.yaml) alone is authoritative for which
host owns which model and which roles point where; the docs describe the
mechanisms.

- **Models and roles** — subscription routes, self-hosted rotation, the role table, the refresh workflow and the latest-only policy: [docs/models-and-roles.md](docs/models-and-roles.md). Side-by-side specs: [docs/model-comparison.md](docs/model-comparison.md).
- **Architecture** — request flow at a glance: [docs/architecture-overview.md](docs/architecture-overview.md); component map: [docs/architecture.mmd](docs/architecture.mmd); per-backend request lifecycles: [docs/project-structure.md](docs/project-structure.md).
- **Multi-host fleet** — cross-host proxying, the Mac Mini, registry-derived placement, ordered failover chains: [docs/multi-host.md](docs/multi-host.md).
- **Model lifecycle** — on-demand load/idle-unload and the Linux systemd satellite: [docs/model-lifecycle.md](docs/model-lifecycle.md).
- **Admin console** — Hub, Settings, Machines, machine identity, diagnostics: [docs/admin-console.md](docs/admin-console.md).
- **Running** — launchers, tray, Cloudflare tunnel, server adoption, LAN access: [docs/running.md](docs/running.md).
- **API usage** — Python/curl examples, token counting, audio and image calls, headers, CORS, the TypeSafe passthrough: [docs/api-usage.md](docs/api-usage.md).
- **Coding-agent usage** — the Code tab's usage accounting: [docs/coding-agent-usage.md](docs/coding-agent-usage.md).
- **Telemetry** — Langfuse stack and client trace contract: [docs/telemetry-langfuse.md](docs/telemetry-langfuse.md), [docs/clients-telemetry-contract.md](docs/clients-telemetry-contract.md).
- **Speech and images** — [docs/whisper-asr.md](docs/whisper-asr.md), [docs/add-tts.md](docs/add-tts.md), [docs/image-generation.md](docs/image-generation.md).
- **Search** — self-hosted SearXNG: [docs/searxng.md](docs/searxng.md).
- **Limitations** — what the hub intentionally does not do: [docs/limitations.md](docs/limitations.md).
- **Repository layout** — annotated file tree: [docs/layout.md](docs/layout.md).

## Scope & usage policy

This is a **personal playground** for running your own experiments
against your own Claude Code and Google AI Pro subscriptions and your
own local GPU on devices you personally own. It is **not** a hosted
service, a multi-tenant proxy, or a way to share subscription access.

To stay clearly within Anthropic's and Google's terms, please use it
only as intended:

- ✅ **Do** use it locally to call Claude or Gemini from your own
  scripts, agents, and tools on devices you personally own.
- ✅ **Do** use it on a trusted LAN to reach your own second machine
  or VM (e.g. a local agent runtime).
- ✅ **Do** route non-cloud traffic to the local qwen/gemma backends
  as much as you like — those are your own weights on your own silicon.
- ❌ **Don't** share the endpoint with other people — for Claude or
  Gemini, that would be sharing subscription access, which neither
  Anthropic's [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
  nor Google's [Additional Terms](https://policies.google.com/terms/generative-ai)
  allow.
- ❌ **Don't** port-forward it to the public internet or host it
  behind a domain.
- ❌ **Don't** build a product, commercial service, or large automated
  pipeline on top of the Claude or Gemini paths — for anything beyond
  personal experimentation use the paid Anthropic API or Vertex AI /
  Gemini API, which their respective usage policies and commercial
  terms are designed for.
- ❌ **Don't** hammer `claude -p` or the `agy` CLI in tight loops; keep
  volume at human-in-the-loop speeds so you don't abuse the service or
  get rate-limited. The Antigravity CLI quota follows your Google AI
  Pro / Ultra plan and is shared with the Antigravity IDE, so heavy hub
  use can also starve your IDE assistant. The local backends are
  rate-limited only by your GPU.

If your use case goes beyond "me, tinkering on my own machine,"
switch to the Anthropic API for Claude. When in doubt, check
[anthropic.com/legal](https://www.anthropic.com/legal/) or email
`support@anthropic.com`. This repo is provided as-is, with no
guarantee that it complies with Anthropic's terms for any particular
use.


## Setup

One command does everything — deps, llama.cpp binary, GGUF downloads
for the models enabled for this host:

```bat
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m src.install --fix
```

The installer reads [config/models.yaml](config/models.yaml), figures
out which host row you are (by `LOCAL_LLM_HUB_HOST` env var, else
hostname match, else `default: true`), and only downloads what that
host's `enabled` list asks for. On the reference Windows PC that's
the active rotation: Qwen 3.5 4B (~2.6 GB), Gemma 4 26B-A4B IQ4_XS
(~13.4 GB), whisper-large-v3-turbo (~1.62 GB), whisper-medium for
translate (~1.5 GB), plus Gemma 4 E4B (~5 GB) kept as the
agentic_light fallback, plus the llama.cpp + whisper.cpp CUDA
binaries under `vendor/`. Other hosts' `enabled:` lists vary — see
[config/models.yaml](config/models.yaml) for current per-host placement.

The demoted candidate `glm-4.5-air` is in the registry but **not** in
any host's `enabled:` list, so the installer ignores it. To bring it up
ad-hoc, add it to `enabled:` and re-run `--fix`, or just download
manually and launch via `launchers/run_model.bat glm`. `qwen3.5-9b` is
active on its owning host's own `enabled:` list; see
[config/models.yaml](config/models.yaml) for current placement.

On a host with a TTS role enabled, `--fix` also pip-installs
[requirements-tts.txt](requirements-tts.txt) (`chatterbox-tts`, `snac`,
`kokoro-onnx`, `soundfile` — the full set pulls torch, ~2 GB, which is why
it's kept out of the base `requirements.txt` so non-TTS hosts like the Mac
mini stay lean), installs CUDA torch / ONNX Runtime GPU on NVIDIA hosts,
downloads Piper's binary/voices and Kokoro's ONNX assets, and pre-warms the
Chatterbox / SNAC / Piper / Kokoro weights so the first
`/v1/audio/speech` request isn't a cold download. To do it by hand:

```bat
.venv\Scripts\python -m pip install -r requirements-tts.txt
```

Plain check (no changes):

```bat
.venv\Scripts\python -m src.install
```

Or open **Settings › Health & install** (the gear) on the admin webapp's
**🛰 Hub** tab (`http://127.0.0.1:8000/admin/`) — same checks, same
fixes, one button per row plus a **Fix-all**.

Requires the `claude` CLI on `PATH` (Claude Code) if any `claude-*`
model is enabled for your host.

Requires the **Antigravity CLI** (`agy`) on `PATH` if you want to use
any `gemini-*` model — install it from
[antigravity.google](https://antigravity.google) and sign in once with
your Google account. `agy` replaces the standalone `gemini` CLI, which
Google deprecates for AI Pro / Ultra subscribers on 2026-06-18. On
Windows the Gemini path also needs `pywinpty` (in `requirements.txt`):
`agy`'s print mode renders to a console, so the hub drives it under a
ConPTY. Without `agy`, requests targeting `gemini-*` return 502 with a
clear "CLI not found" message — the rest of the hub keeps working.

### Machine specs (optional)

[config/machine_specs_example.yaml](config/machine_specs_example.yaml)
documents the hardware schema this hub uses to reason about local
model fit (VRAM, system RAM, GPU compute capability). To populate the
real file for your host, run the detection script:

```bat
.venv\Scripts\python scripts\detect_machine_specs.py
```

```bash
./.venv/bin/python scripts/detect_machine_specs.py
```

Or copy the example manually and edit it:

```bat
copy config\machine_specs_example.yaml config\machine_specs.yaml
```

`config/machine_specs.yaml` is gitignored. AI coding agents working in
this repo will read it (when present) to recommend model sizes and
quantizations that actually fit your hardware. Optional — the hub
itself runs fine without it.


## Run

```bat
run_hub.bat        :: FastAPI hub on :8000 (the admin webapp lives inside it at /admin)
tray.bat           :: Windows system-tray launcher: starts the hub, toggles models, opens /admin
launchers\run_model.bat <id>   :: one model backend, id from config/models.yaml
launchers\run_all.bat          :: every backend enabled for this host
```

On macOS / Linux use `./run_hub.sh` and `./launchers/run_all.sh`. Open
`http://127.0.0.1:8000/admin/` for the admin webapp (Hub / Models / Play /
Code / Machines tabs). Launcher details, the tray, the Cloudflare tunnel and
LAN access: [docs/running.md](docs/running.md).

**Restart.** `tray.bat --restart` reclaims the hub port `:8000` by PID
(scoped to this repo's `.venv`) and starts fresh; it leaves `:8090`
(whisper-server) and the llama-server model ports alone.

| Touched | Restart |
| --- | --- |
| Hub code (`src/`, `app_web/`, config loaders) | `tray.bat --restart` |
| A model backend or TTS shim (llama-server row, whisper-server row, `src/tts_server.py`) | Stop/start that model from the Models tab or `launchers/run_model.bat <id>` — not a hub restart |

## Test

The verification gate runs byte-compile, unit tests and the Chromium e2e suite
(same steps as CI, except that locally the e2e stage is routed by the diff: a
docs-only change skips it and prints why, and anything the router cannot read
runs the full suite):

```powershell
.\scripts\verify-before-ship.ps1
```

Unit tests alone (fast, no real `claude` / GPU calls):

```bat
.venv\Scripts\python -m pytest -q --ignore=tests/e2e
```

Static ratchet (new duplicate blocks and unused imports/variables; needs
Node for `npx jscpd` and `ruff` from `requirements-dev.txt`). Existing debt
lives in `.static-ratchet-baseline.json` and can only shrink; run
`.venv\Scripts\python scripts\static_ratchet.py --update-baseline` in the
PR that removes some:

```bat
.venv\Scripts\python -m scripts.static_ratchet_gate
```

Docs ratchet (the history-free docs convention: `README.md` stays under
40 KiB, and `README.md` plus `docs/**/*.md` gain no issue reference, dated
heading or changelog heading). Existing markers live in
`.docs-ratchet-baseline.json` and can only shrink; run
`.venv\Scripts\python scripts\docs_ratchet.py --update-baseline` in the PR
that rewrites some as present-tense prose:

```bat
.venv\Scripts\python -m scripts.docs_ratchet_gate
```

End-to-end smoke test (requires hub + whichever backends you care
about running):

```bat
.venv\Scripts\python scripts\smoke_test.py
```

It iterates every enabled model from the registry, skips backends
whose port isn't reachable, and reports per-model pass/fail.

## Observability

The hub emits OpenTelemetry traces + metrics via OTLP/HTTP into Langfuse's
`/api/public/otel` receiver on `:3000`, authenticated by the project key
pair, in a local Langfuse stack started by `start_langfuse.bat`. The admin SPA's
**Hub** tab shows stack health, a per-model leaderboard, and a
live trace feed with deep-links into the Langfuse UI for inspection.

Default mode captures raw prompts and completions — fine on a personal
localhost hub, but flip `OTEL_HASH_PROMPTS=true` (in `.env`) any time
the hub binds beyond loopback. `OTEL_SDK_DISABLED=true` turns
telemetry off entirely.

## License

[MIT](LICENSE). Use it, fork it, break it — just keep the copyright
notice. Note that the license covers *this code* only; your use of the
underlying `claude` CLI is still governed by Anthropic's terms (see
[Scope & usage policy](#scope--usage-policy) above) and the model
weights follow their own licenses
([Gemma terms](https://ai.google.dev/gemma/terms),
[Whisper / OpenAI MIT](https://github.com/openai/whisper/blob/main/LICENSE),
[Parakeet Ultra](https://huggingface.co/moondream/parakeet-ultra) (CC-BY-4.0:
post-trained by Moondream from NVIDIA's
[parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3),
CoreML build by [FluidInference](https://huggingface.co/FluidInference/parakeet-ultra-coreml);
runs through [FluidAudio](https://github.com/FluidInference/FluidAudio), Apache-2.0),
plus [Qwen](https://huggingface.co/Qwen) /
[GLM](https://huggingface.co/zai-org) for the demoted candidates).
