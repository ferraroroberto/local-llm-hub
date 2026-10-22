# Architecture at a glance

```
openClaw / anthropic SDK / openai SDK / curl
                    │
                    ▼  http://<lan>:8000
   ┌────────────── FastAPI hub (src/server.py) ───────────────┐
   │  route by `model`:                                       │
   │    claude-*               → call_claude()   (claude -p subprocess)  │
   │    gemini-* / gemini_*    → call_gemini()   (agy CLI via ConPTY)    │
   │    POST /v1/images/generations (gemini_image) → call_gemini_image() │
   │      (agy Imagen tool; returns data[].b64_json, observable)         │
   │    POST /v1/images/generations (flux1_local) → ComfyUI :8188        │
   │      (local FLUX.1 [dev] fp8, on_demand; same b64_json shape)       │
   │    POST /v1/images/edits (multipart image+prompt) → edit (slow)     │
   │    qwen3.5-4b             → llama-server 127.0.0.1:8088             │
   │    gemma4-26b-a4b-it      → llama-server 127.0.0.1:8087             │
   │    whisper-* (via chat shape) → 400 "use /v1/audio/* or direct"    │
   │    POST /v1/audio/transcriptions → whisper / parakeet (role chain) │
   │      (model=whisper-vanilla → glossary-free turbo, lazy)           │
   │    POST /v1/audio/translations   → whisper-medium (translate)      │
   │    POST /v1/audio/speech         → proxy to tts shim :8092/:8093/:8095/:8096 │
   │      (the audio proxy lands requests in the observability ring)    │
   │    GET  /v1/audio/health         → probe whisper/tts; 503 if down   │
   │      (preflight liveness — never sends a doomed transcription)      │
   │    POST /v1/systemone            → api.typesafe.ai (third-party, US) │
   │      (TypeSafe Jev passthrough; opt-in, never a fallback)    │
   └──────────────────────────────────────────────────────────┘

audio clients  ──►  hub 127.0.0.1:8000 /v1/audio/*  ──►  whisper-server / tts shim  (proxied, observable)
audio clients  ──►  tts shim       127.0.0.1:8096   (piper, text→speech, auto-loaded; fast CPU)
audio clients  ──►  tts shim       127.0.0.1:8093   (orpheus, text→speech, on demand; llama-server :18093 + SNAC)
audio clients  ──►  tts shim       127.0.0.1:8095   (kokoro, text→speech, on demand; ONNX Runtime)
audio clients  ──►  tts shim       127.0.0.1:8092   (chatterbox, text→speech, on demand; direct)
                          (whisper speaks /v1/audio/transcriptions, the tts shim /v1/audio/speech;
                           POST via the hub proxy for observability, or direct to the port — on the
                           host that owns the backend — to skip it)

STT is NOT provisioned on this box in the normal case — the tower is only
whisper's degraded `cpu: true` last rung. Callers still POST to this hub's
:8000 and it proxies to whichever host owns each row; weights, builds, and
`enabled:` rows belong on the owning machine, not here:
  whisper / whisper_translate / whisper_vanilla  → see config/models.yaml (address: config/machines.local.yaml)
  parakeet (transcribe primary)                  → see config/models.yaml (address: config/machines.local.yaml)

Demoted candidates and any model proxied through another host's own
`base_url` (e.g. a Mac-Mini-owned row reached as
`this hub 127.0.0.1:8000 → mac hub <address>:8000 → llama-server`) are listed
in [Demoted candidates](models-and-roles.md#demoted-candidates-kept-defined-not-in-active-rotation);
current ownership for every row is `config/models.yaml` alone.
```

Which machine owns which row is [`config/models.yaml`](../config/models.yaml)'s
answer alone — the block above sketches request flow, not topology.
[multi-host.md](multi-host.md) walks the current
tower / mac-mini-m4 / gaming split.

See [architecture.mmd](architecture.mmd) for the full component
diagram (the one structural map under a same-PR upkeep contract),
[project-structure.md](project-structure.md) for the per-backend
request-lifecycle sequences and the LLM key-facts briefing,
[whisper-asr.md](whisper-asr.md) for the whisper ASR backend
(glossary, boosting, tuning), and
[add-tts.md](add-tts.md) for the text-to-speech backend
(`/v1/audio/speech`).

