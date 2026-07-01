# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Local HTTP hub routing Anthropic-shaped and OpenAI-shaped requests to multiple LLM/ASR backends, with a FastAPI + static-JS admin SPA mounted at `/admin`.
See `README.md` for setup, layout, and usage.

**Safe restart (never blanket-kill python):** the canonical restart is **`tray.bat --restart`** — the orphan-proof reclaim-then-start that kills the tray subtree, then reclaims the hub port **:8000** by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. It deliberately does **not** touch `:8090` (whisper-server, mutex-shared with `voice-transcriber`) or the llama-server model ports (8081/8082/8086/8087). To restart by hand only as a fallback, find the owner with `Get-NetTCPConnection -LocalPort 8000` and stop that PID, then relaunch via `tray.bat`. **Build confirmation:** `GET http://127.0.0.1:8000/health` returns 200 once the hub is back up (the `/health` payload also carries `version`).

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the admin PWA is the FastAPI + static app under `app_web/static/`, mounted at `/admin`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real admin PWA
- paths:
  - app_web/static/**/*.css
  - app_web/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/admin/`
  - /admin/    (Hub · Models · Playground · Telemetry · Code Usage tabs)
