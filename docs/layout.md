# Repository layout

```
local-llm-hub/
├── .venv/                    # local virtualenv (gitignored)
├── .claude/
│   ├── commands/             # Claude Code slash commands (committed)
│   │   ├── swap-model.md         # interactive role swap (yaml + launcher + download)
│   │   └── system-specs.md       # collect Windows hardware specs
│   └── skills/
│       └── frontier-refresh/     # bi-weekly frontier research skill
│           ├── SKILL.md          #   brief + output contract + ledger (single owner)
│           ├── run-weekly.bat    #   headless runner (app-launcher job, self-skips alternate weeks)
│           └── week_parity.ps1   #   on/off-week gate, answered as an exit code
├── requirements.txt
├── requirements-dev.txt      # e2e + passkey deps (Playwright, pytest-playwright, webauthn)
├── requirements-tts.txt      # TTS deps (chatterbox-tts, snac, kokoro-onnx, soundfile — torch); Piper is a downloaded binary
├── tray.bat                  # Windows-only system-tray launcher (silent)
├── run_hub.bat / .sh         # start the FastAPI hub on :8000
├── launchers/                # per-model backends (.bat + .sh)
│   ├── run_model.* <id>          # parameterized launcher — title/port/banner
│   │                              #   pulled live from config/models.yaml; covers every
│   │                              #   id below (qwen, glm, qwen35_4b, gemma4_e4b,
│   │                              #   gemma4_26b, whisper, whisper_translate, piper,
│   │                              #   orpheus, kokoro, chatterbox)
│   └── run_all.*                # start everything enabled on this host
├── config/
│   ├── models.yaml                   # hosts + models + roles — placement truth (hosts chains + startup policy)
│   ├── diagnostics_apps.json         # process -> fleet-app attribution rules (committed)
│   ├── diagnostics_rules.json        # health-verdict thresholds (committed)
│   ├── diagnostics_settings.json     # retention + scheduled snapshot (gitignored)
│   ├── startup_profile.example.json  # template + fresh-clone default for service autostart (services-only)
│   ├── startup_profile.json          # live service-autostart profile, rewritten by the admin UI (gitignored)
│   ├── fleet_maintenance.json        # live {host: {until, reason}} reconcile drain markers (gitignored)
│   ├── webapp_config.json            # admin auth: bearer token, optional password, webauthn rp, CORS origins (gitignored)
│   ├── webapp_config.sample.json     # committed template + fresh-clone default for the above
│   ├── machine_specs_example.yaml    # template for scripts/detect_machine_specs.py's real output
│   ├── claude_pricing.json / gemini_pricing.json / openai_pricing.json  # per-model $/token
│   │                                  #   tables for the Code-tab usage/cost parsers
│   ├── dictionary_miner.json         # glossary-miner state
│   └── transcription_glossary.json / transcription_glossary.local.sample.json  # whisper
│                                      #   replacement-rules dictionary
├── webapp/                   # runtime data dir written by the /admin webapp
│   ├── cloudflared.sample.yml  # sample named-tunnel config (copy to cloudflared.yml)
│   ├── cloudflared.yml         # your own tunnel config — gitignored
│   └── auth.log                # /admin/api/login attempts (gitignored)
├── data/                     # runtime artefacts (gitignored)
│   ├── logs/                    # per-backend stdout/stderr: backend-<id>.log (+ one .log.1 backup)
│   └── diagnostics.db           # SQLite capture store
├── src/
│   ├── server.py             # FastAPI hub (both shapes) + /admin sub-app mount
│   ├── server_lifecycle.py   # startup/shutdown wiring + background resource sampler
│   ├── server_otel_receiver.py  # POST /v1/metrics OTLP receiver
│   ├── chat_translation.py   # request/response schemas, content-block extraction,
│   │                         #   prompt flattening, per-backend dispatch
│   ├── token_counting.py     # POST /v1/messages/count_tokens — per-backend input_tokens,
│   │                         #   exact on llama-server, flagged approximate on the CLIs
│   ├── anthropic_errors.py   # Anthropic {"type":"error",…} envelope for /v1/messages
│   │                         #   errors — path-scoped so /v1/chat/completions is
│   │                         #   untouched
│   ├── server_common.py      # model-resolution + OTel span helpers shared by
│   │                         #   server.py / server_audio_asr.py / server_audio_tts.py / server_images.py
│   ├── server_audio_asr.py   # /v1/audio/{transcriptions,translations,health} — whisper proxy + failover
│   ├── server_audio_tts.py   # /v1/audio/speech — TTS proxy, routes onto server_audio_asr's router
│   ├── server_audio_common.py  # header-safety + upstream-error helpers shared by the two above
│   ├── audio_proxy.py        # shared multipart bridging + default-language lookup for the whisper transcribe/translate paths
│   ├── server_images.py      # /v1/images/* handlers (generations, edits)
│   ├── server_systemone.py   # /v1/systemone — TypeSafe Jev passthrough, the only third-party egress
│   ├── comfyui_client.py     # ComfyUI prompt API client — local FLUX.1 [dev]
│   ├── image_sizes.py        # size presets + native/upscale split, one source for API + UI
│   ├── claude_cli.py         # subprocess wrapper around `claude -p`
│   ├── gemini_cli.py         # Antigravity CLI (`agy`) wrapper via ConPTY (Google AI Pro)
│   ├── openai_upstream.py    # httpx client + SSE think-strip pipeline
│   ├── http_client.py        # shared pooled httpx Client/AsyncClient (skip a fresh SSL ctx per call)
│   ├── model_registry.py     # YAML loader (resolves display_name + aliases)
│   ├── model_failover.py     # dynamic fallback across an ordered host chain
│   ├── model_reachability.py # shared "is this model answering" probe (Models tiles + Playground TTS rows)
│   ├── config_write.py       # git-backed models.yaml placement writes: validate → ruamel
│   │                         #   edit → config-bot commit → push + drift sync
│   ├── startup_profile.py    # config/startup_profile.json load/save — service flags
│   ├── fleet_reconcile.py    # additive reconcile loop: wake + start registry-desired models
│   ├── fleet_maintenance.py  # config/fleet_maintenance.json load/save — reconcile drain markers
│   ├── on_demand.py          # spawn-on-first-request / unload-when-idle model lifecycle
│   ├── host_profile.py       # pick active host row
│   ├── remote_proxy.py       # resolve a non-local model row's owning host + forward verbatim
│   ├── remote_bootstrap.py   # SSH-triggered remote hub bootstrap/sync + machine power
│   ├── remote_stats.py       # remote machine liveness + stats for the Machines console;
│   │                         #   + the cross-host peer-probe trio: remote_models/peer_health/
│   │                         #   hub_peers
│   ├── machine_console.py    # Machines-tab data layer: per-machine CPU/RAM/GPU/disk/uptime
│   ├── ssh_terminal.py       # in-browser SSH terminal, reusing app-launcher's session-host PTY
│   ├── ssh_exec.py           # the one `ssh <user>@<host> <cmd>` builder (remote_stats + remote_bootstrap)
│   ├── wake_on_lan.py        # Wake-on-LAN magic-packet builder/sender
│   ├── system_stats.py       # live RAM/CPU/GPU readings (consumed by Hub tab sparklines)
│   ├── observability.py      # OpenTelemetry bootstrap: tracing + metrics → local Langfuse
│   ├── async_fanout.py       # shared thread-safe asyncio pub/sub fan-out for SSE subscribers
│   ├── trace_id_middleware.py  # X-Trace-Id contract + request-id / anthropic-* response headers
│   ├── anthropic_headers.py  # anthropic-version / anthropic-beta parity decisions
│   ├── cors_policy.py        # CORS policy — loopback-by-default origins, SDK request headers,
│   │                         #   exposed response headers; installed outermost
│   ├── event_loop.py         # Windows proactor-loop shim so uvicorn survives client aborts
│   ├── build_info.py         # vendored scaffold helper: build_identity() -> git_sha + built_at
│   ├── process_identity.py   # this process's one build_identity() capture, shared by /admin/api/version, the index ETag and peer_health
│   ├── services.py           # host-side service helpers: Docker engine + Langfuse stack
│   ├── agentsview_service.py # optional AgentsView process lifecycle: probe/launch/stop
│   ├── code_usage.py         # host-side Claude Code usage parser + get_summary aggregation API
│   ├── usage_common.py       # shared vendor substrate: UsageRecord/FileStats + parsing helpers
│   ├── usage_pricing.py      # per-vendor $/Mtok pricing tables + record_costs
│   ├── usage_charts.py       # time-series bucketing + period-over-period comparison
│   ├── code_usage_history.py # durable daily rollups of Code-tab usage (outlives transcript pruning)
│   ├── claude_code_otel.py   # Claude Code OTel token/cost metrics receiver — data layer
│   ├── codex_usage.py        # host-side Codex (OpenAI) usage parser (~/.codex/sessions/*.jsonl)
│   ├── copilot_usage.py      # host-side GitHub Copilot usage parser
│   ├── copilot_billing.py    # GitHub Copilot billing-API daily credits poller
│   ├── agentsview_usage.py   # usage records from the optional external AgentsView service
│   ├── dictionary_miner.py   # mine recent dictation transcripts for glossary suggestions
│   ├── transcription_glossary.py  # post-process whisper transcripts through a committed glossary
│   ├── diagnostics/          # on-demand machine diagnostics — no resident process
│   │   ├── sampler.py            #   in-hub asyncio capture loop + opt-in scheduled snapshot
│   │   ├── store.py              #   SQLite store (data/diagnostics.db), migrations, retention
│   │   ├── attribution.py        #   process -> fleet-app mapping + listening-port scan
│   │   ├── rules.py              #   health-verdict engine over stored rows
│   │   ├── coverage.py           #   per-collector coverage — measured vs blind
│   │   ├── report.py             #   summary digest, baseline drift, markdown report
│   │   ├── ingest.py             #   ingest a portable foreign capture as a run
│   │   └── settings.py           #   retention + scheduled-snapshot settings
│   ├── install.py            # first-run checks + --fix
│   ├── usage_log.py          # vendored usage-log router (admin UI view/tap log)
│   ├── run_backend.py        # hub|qwen35_4b|gemma4_26b|whisper|… dispatcher
│   ├── process_supervisor.py # shared subprocess start/stop lifecycle workflow (hub + model backends)
│   ├── server_process.py     # hub port/PID lookup + ownership tri-state (used by backend_process.py, admin routers)
│   ├── backend_process.py    # per-model Popen (llama-server + whisper-server);
│   │                         #   stdout/stderr → data/logs/backend-<id>.log (child-owned)
│   ├── parakeet_server.py    # OpenAI-shape ASR server wrapping the FluidAudio Parakeet CoreML worker
│   ├── tts_server.py            # FastAPI shim for /v1/audio/speech (engine: tts-server)
│   ├── tts_engines/             # TTS engines: piper + chatterbox + orpheus + kokoro
│   │   ├── common.py                #   shared TTSEngine interface, SpeechRequest, audio helpers
│   │   ├── chatterbox.py, kokoro.py, orpheus.py, piper.py  #   one module per engine
│   │   └── __init__.py              #   build_engine() dispatch + re-exports
│   ├── webapp_config.py      # admin webapp config loader (bearer token, webauthn, allowlist, CORS origins)
│   ├── webauthn_gate.py      # passkey gate (optional — needs `webauthn` package)
│   ├── static_versioning.py  # ?v=<hash> stamping for /admin/static assets
│   ├── hub_log.py            # in-memory log ring buffer (admin Hub tab streams it)
│   ├── hub_observability.py  # live request ring, per-backend counters, SSE fan-out
│   ├── hub_process_control.py  # self stop/restart: SIGINT/SIGTERM, launchd bootout/kickstart,
│   │                         #   systemctl, respawn-watchdog spawn
│   └── _respawn_watchdog.py  # detached watchdog for the hub's admin-triggered restart
├── app_web/                  # FastAPI sub-app at /admin (HTML/JS SPA — no bundler)
│   ├── server.py             #   create_app() — middleware, routers, static mount
│   ├── middleware.py         #   bearer-token gate (loopback bypasses)
│   ├── admin_forward.py      #   forward a request into the /admin sub-app in-process
│   ├── routers/              #   misc / version / auth / webauthn / hub / install /
│   │                         #   models / startup_profile / fleet_placement /
│   │                         #   fleet_maintenance / roles / playground / services /
│   │                         #   telemetry / code_usage / glossary / hosts / machines /
│   │                         #   diagnostics
│   └── static/               #   index.html + main.js + state.js + tabs.js + api.js +
│                             #   hub.js + models.js + startup.js + fleet_placement.js +
│                             #   playground.js + playground_jev.js + code_usage.js/.css +
│                             #   diagnostics.js + glossary.js + machines.js/.css +
│                             #   machines_terminal.js + rows.js/.css + settings.js +
│                             #   langfuse.js + dormant.js + theme.js +
│                             #   usage.js + styles.css +
│                             #   manifest.webmanifest + icon-*.png/favicon.ico (generated
│                             #   by scripts/gen_icons.py, committed)
│       └── _vendored/        #   project-scaffolding components: button / card /
│                             #   disclosure / empty-state / icons (Lucide sprite) /
│                             #   modal / nav / segmented / switch / text-size /
│                             #   xterm — SPA UI glyphs +
│                             #   primitives per design.md; chartjs (Chart.js
│                             #   4.4.7 UMD, byte-for-byte) — trend charts
├── tray/                     # Windows system-tray launcher (silent pythonw)
│   ├── tray.py               #   single-file pystray + hub lifecycle owner
│   ├── icon.py               #   Lucide hub glyph (share-2), rendered via resvg,
│   │                         #   tinted live by health state
│   ├── single_instance.py    #   .tray.pid lock validated with psutil
│   └── __main__.py           #   `python -m tray` entry, writes one-shot crash log
├── scripts/
│   ├── _lib.py                # shared download/extract/flatten helpers for the two
│   │                         #   vendor-binary install scripts
│   ├── smoke_test.py
│   ├── gen_icons.py          # thin caller onto project-scaffolding's shared brand_gen.py (hub master)
│   ├── bench_orpheus.py      # measure Orpheus llama-server throughput (tok/s, e2e)
│   ├── bench_voice.py        # STT+TTS bench on any hub (RTFx/WER/latency) — placement decisions
│   ├── download_models.py    # huggingface_hub → models/
│   ├── detect_machine_specs.py   # populate config/machine_specs.yaml
│   ├── install_llama_cpp.py      # CUDA-Windows / Metal-macOS release
│   ├── install_whisper_cpp.py    # whisper.cpp CUDA-Windows release; macOS builds Metal from source → vendor/whisper.cpp/
│   ├── install_tts.py           # pip -r requirements-tts.txt + Piper/Kokoro assets + warm TTS
│   ├── install_comfyui.py       # ComfyUI clone + isolated venv + torch cu130 (sm_120 verified) → vendor/comfyui/
│   ├── portable_capture.py      # standalone psutil sampler, SSH-delivered to hub-less machines
│   ├── static_ratchet.py         # vendored from project-scaffolding: fails on a NEW duplicate block / unused import
│   ├── static_ratchet_gate.py    #   the gate's wrapper: exit 3 (detector could not run) = NOT CONFIRMED, never a pass
│   ├── classify_e2e.py           # vendored from project-scaffolding: maps the diff to an e2e tier via .fleet.toml [e2e]
│   ├── e2e_route.py              #   the gate's wrapper: an unreadable verdict runs the full suite, never less
│   └── verify-before-ship.ps1    # byte-compile + static ratchet + pytest + diff-routed Playwright on Chromium
├── assets/                   # generated by scripts/gen_icons.py, committed
│   └── stream-deck/local-llm-hub-144.png  # Elgato Stream Deck button
├── tests/                    # pytest unit/integration modules — one `test_*.py`
│   │                         #   per router/service (test_server, test_router,
│   │                         #   test_model_registry, test_install, test_streaming, …)
│   └── e2e/                  # Playwright smoke tests (Chromium): tab-level coverage
│                             #   for Code Usage, Fleet Placement, Machines, Roles, Telemetry;
│                             #   the test hub boots via _isolated_hub.py — cut off from live
│                             #   services and LAN peers, egress-guarded
├── .github/workflows/
│   └── e2e.yml               # CI: unit tests + e2e gate on windows-latest
├── vendor/
│   ├── llama.cpp/            # prebuilt llama-server binary (gitignored)
│   └── whisper.cpp/          # prebuilt whisper-server binary (gitignored)
├── models/                   # downloaded GGUFs (gitignored):
│                             #   Qwen3.5-4B (Q4_K_M), gemma-4-26B-A4B-it (IQ4_XS),
│                             #   gemma-4-E4B-it (fallback, still enabled),
│                             #   ggml-large-v3-turbo.bin (whisper turbo, transcribe),
│                             #   ggml-medium.bin (whisper medium, translate),
│                             #   plus any demoted candidates if brought up ad-hoc
└── docs/
    ├── project-structure.md
    ├── models-and-roles.md       # active rotation, roles, latest-only policy
    ├── multi-host.md             # cross-host proxying, placement, failover chains
    ├── model-lifecycle.md        # on-demand models, Linux satellite lifecycle
    ├── admin-console.md          # Hub / Settings / Machines tabs, machine identity
    ├── running.md                # run modes, tray, tunnel, LAN access
    ├── api-usage.md              # client examples, headers, CORS, passthroughs
    ├── coding-agent-usage.md     # Code-tab usage accounting
    ├── limitations.md            # intentional limits of the hub
    ├── layout.md                 # this file
    ├── architecture.mmd          # hand-authored internal-structure diagram (CLAUDE.md-bound, same-PR upkeep)
    ├── model-comparison.md       # per-model specs, quantisation, docs links
    ├── diagnostics.md            # on-demand machine diagnostics
    ├── machines.md               # fleet machine inventory + Tailscale identities
    ├── whisper-asr.md            # whisper STT backend: glossary, boosting, tuning
    ├── whisper-turbo-vs-large-v3.md  # whisper model-size decision rationale
    ├── voice-benchmark.md        # cross-host STT+TTS placement benchmark
    ├── parakeet-asr-evaluation.md    # Parakeet CoreML ASR spikes
    ├── add-tts.md                # how the TTS backend (/v1/audio/speech) slotted in
    ├── image-generation.md       # Imagen via agy + local FLUX via ComfyUI
    ├── gemini-agy-backend.md     # agy (Antigravity CLI) backend reference
    ├── glm-performance-assessment.md / glm-5.2-evaluation.md  # local-model benchmarks
    ├── orpheus-throughput.md     # Orpheus llama-server throughput bench
    ├── code-usage-agentsview.md  # Code-tab usage parsing + the optional AgentsView source
    ├── clients-telemetry-contract.md  # X-Trace-Id / OTel contract clients must follow
    ├── telemetry-langfuse.md     # Langfuse OTel stack setup
    ├── fleet-maintenance.md      # reconcile drain markers
    ├── ci-e2e-decision.md        # why CI runs pytest + Playwright on windows-latest
    ├── frontier-workflow.md      # bi-weekly refresh + /swap-model workflow
    ├── webapp-architecture-notes.md / webapp-design-language.md  # admin SPA structure + design notes
    ├── playbook-cli-backend-migration.md  # reusable method when a vendor CLI changes
    ├── system-specs/             # detect_machine_specs.py output template (gitignored real output)
    │   └── system-specs.example.md
    └── frontier/                 # bi-weekly efficient-frontier research (brief lives in the skill)
        ├── local-findings.md     #   durable cross-run findings that survive a run's own report
        └── runs/
            ├── LATEST            #   flat file containing the latest run date
            └── <YYYY-MM-DD>/     #   one dir per run
                ├── report.md     #   didactic markdown report
                ├── frontier.json #   machine-readable run data
                └── frontier.html #   standalone interactive chart
```

