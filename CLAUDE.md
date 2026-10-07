# Project Instructions

Claude Code reads this file directly as project memory; other agents reach it via the `AGENTS.md` pointer.

## This repository
Local HTTP hub routing Anthropic- and OpenAI-shaped requests to multiple LLM/ASR backends, with a FastAPI + static-JS admin SPA at `/admin`. See `README.md` for setup, layout, usage.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is the repo's **only** component/runtime map (entry points, `src/server.py` routing to the Claude/Gemini/llama-server backends, process managers, `app_web/` admin sub-app, observability, config) — hand-authored, untested. Update it in the same PR as any material structural change (backend added/removed, router moved, process manager relocated). [`docs/project-structure.md`](docs/project-structure.md) points here and keeps only the per-backend request-lifecycle sequences plus the LLM key-facts briefing (#475). Model **placement** (which host owns which row) is [`config/models.yaml`](config/models.yaml)'s alone — never restate it in a doc.

**Safe restart (never blanket-kill python):** canonical restart is **`tray.bat --restart`** — kills the tray subtree, reclaims hub port **:8000** by PID scoped to this repo's `.venv`, then starts fresh. It deliberately does **not** touch `:8090` (whisper-server, mutex-shared with `voice-transcriber`) or the llama-server ports (8081/8082/8086/8087/8088). Fallback by hand only: find the owner with `Get-NetTCPConnection -LocalPort 8000`, stop that PID, relaunch via `tray.bat`. **Build confirmation:** `GET http://127.0.0.1:8000/health` returning 200 is *liveness* only — its payload (`{"status": "ok"}`) is identical across a restart. For build identity poll `GET http://127.0.0.1:8000/admin/api/version` and check `git_sha` changed — the auth-exempt endpoint `tray.bat`, `src/config_write.py` and `src/remote_stats.py`'s `peer_health()` already use.

## Verification gate

`scripts/verify-before-ship.ps1` runs CI's steps: byte-compile, unit tests (`pytest -q --ignore=tests/e2e`), then e2e (`pytest tests/e2e -q --browser chromium`: Chromium only, 46 nodes, one isolated hub per session). **Measured runtime** (Windows, quiet box, 2026-10-03): about 3 min 40 s (218 s) end to end: unit tests about 190 s (1217 tests, 166-195 s), e2e about 18 s (was about 76 s before #645). The e2e step writes `data/e2e-junit.xml` (git-ignored, `[e2e] junit_xml` in `.fleet.toml`), which `/e2e-audit` reads for per-test seconds; re-measure and update these numbers when the suite changes. The gate always runs the whole e2e suite: the `[e2e]` routing table in `.fleet.toml` is not consumed by it.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). Live, parseable block — the admin PWA is the FastAPI + static app under `app_web/static/`, mounted at `/admin`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real admin PWA
- paths:
  - app_web/static/**/*.css
  - app_web/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/admin/`
  - /admin/    (Hub · Models · Playground · Telemetry · Code Usage · Machines tabs)
