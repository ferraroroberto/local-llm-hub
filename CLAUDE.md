# Project Instructions

This file is the project memory; agents that look for `AGENTS.md` reach it through that pointer.

## This repository
Local HTTP hub routing Anthropic- and OpenAI-shaped requests to multiple LLM/ASR backends, with a FastAPI + static-JS admin SPA at `/admin`. See `README.md` for setup, run and the doc index; [`docs/layout.md`](docs/layout.md) holds the file tree.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is the repo's **only** component/runtime map (entry points, `src/server.py` routing to the subscription-CLI and llama-server backends, process managers, `app_web/` admin sub-app, observability, config) — hand-authored, untested. Update it in the same PR as any material structural change (backend added/removed, router moved, process manager relocated). [`docs/project-structure.md`](docs/project-structure.md) points here and keeps only the per-backend request-lifecycle sequences plus the LLM key-facts briefing (#475). Model **placement** (which host owns which row) is [`config/models.yaml`](config/models.yaml)'s alone — never restate it in a doc.

**Safe restart (never blanket-kill python):** canonical restart is **`tray.bat --restart`** — kills the tray subtree, reclaims hub port **:8000** by PID scoped to this repo's `.venv`, then starts fresh. It deliberately does **not** touch `:8090` (whisper-server, mutex-shared with `voice-transcriber`) or the llama-server ports (8081/8082/8086/8087/8088). Fallback by hand only: find the owner with `Get-NetTCPConnection -LocalPort 8000`, stop that PID, relaunch via `tray.bat`. **Build confirmation:** `GET http://127.0.0.1:8000/health` returning 200 is *liveness* only — its payload (`{"status": "ok"}`) is identical across a restart. For build identity poll `GET http://127.0.0.1:8000/admin/api/version` and check `git_sha` changed — the auth-exempt endpoint `tray.bat`, `src/config_write.py` and `src/fleet/remote_stats.py`'s `peer_health()` already use.

## Verification gate

`scripts/verify-before-ship.ps1` runs CI's steps plus the ratchets: byte-compile, **static ratchet**, **docs ratchet**, comment ratchet, **structure ratchet**, **instruction ratchet**, **doc-refs ratchet**, **dead-code ratchet**, **conventions ratchet**, unit tests (`pytest -q --ignore=tests/e2e`), then e2e (`pytest tests/e2e -q --browser chromium`: Chromium only, one isolated hub per session), routed by the diff: `scripts/e2e_route.py` runs the vendored `scripts/classify_e2e.py` against `.fleet.toml [e2e]` and the gate runs nothing (docs/backend-only, with the router's reason printed), the static target, one surface's slice, or the full suite. Anything the router cannot read (non-zero exit, timeout, no or unknown `E2E_TIER`, a run tier with no target), an unclassified path or an empty diff runs the full suite and says why; it never runs less.

**Ratchets** (each `scripts/<name>_ratchet.py`, vendored: re-vendor with `/propagate-vendored <name>_ratchet`, never edit; config `.fleet.toml [<name>_ratchet]`; baseline `.<name>-ratchet-baseline.json`). Static, docs, instruction, doc-refs, dead-code and conventions read the whole tree, so they are never path-routed. Exit 3 (a detector could not run, or a file could not be read or measured) fails the step as NOT CONFIRMED, never a pass or a skip: the gate calls static and docs only through `python -m scripts.static_ratchet_gate` / `python -m scripts.docs_ratchet_gate`, and the others directly. Shrink a baseline with `--update-baseline`, in the PR that removes debt; only `--allow-additions` adds, and it shows in review.

- **Static:** fails on a duplicate block (jscpd via `npx`, so Node is a prerequisite), an unused import/variable (ruff `F401,F841`) or a likely bug or security pattern (a curated ruff `B`/`S` set, `S113` strict) that is not in the baseline; `vendor/`, `data/`, `models/` and `_vendored/` are excluded.
- **Docs:** fails when `README.md` passes 40 KiB or its baselined size, or when `README.md` or a `docs/**/*.md` file gains an issue reference, a dated heading or a changelog heading beyond its baseline (generated `docs/frontier/runs/` reports excluded); docs state the present-tense why, history lives in git, PRs and issues. A marker you must keep is baselined with `--update-baseline --allow-additions`.
- **Structure:** fails when a new file passes 1,000 lines, a new function 100 lines or a directory 40 modules, when a baselined offender grows past its recorded size, or when the root folders fall outside `shape = "service"` plus `extra_roots` in `.fleet.toml`. The baseline is empty and nothing is excluded but the generated `docs/frontier/runs/` reports; the flat `src/` sits just under its 40-module limit, so a new module goes in a subpackage (`docs/layout.md`), not the flat directory. A new root folder is declared in `extra_roots` and given a line in `docs/layout.md`, not allowed by default.
- **Instruction:** fails when `CLAUDE.md`, `AGENTS.md` or a skill gains a prompt-audit lint-rule hit or passes a size cap beyond the baseline.
- **Doc-refs:** fails when `README.md`, `CLAUDE.md` or a `docs/**/*.md` file names a repo path, `python -m` module, relative link or loopback port that does not exist; another service's port or another repo's path named on purpose goes in `allow_refs`, not the baseline.
- **Dead-code:** fails when a Python function, class or method appears that nothing in the repo refers to (route handlers, `__all__` exports, tests and decorated hooks are never reported). Delete or call a flagged definition; a real framework entry point goes in `allow` / `entry_decorators`.
- **Conventions:** three fleet conventions as `ast` rules, failing when a file's hit count for one rises: `no-window` (a `subprocess` spawn without `creationflags=`), `logging-not-print` (a `print` in `src/`, `app_web/` or `tray/`, command-line entry points exempt) and `no-drive-path` (a string literal starting with a drive letter). `secret-compare` and `git-wrapper` are off because the hub has no shared helper for either. A call that must break a rule carries `# <rule>-exempt: <reason>`.

**Measured runtime** (Windows, quiet box, 2026-10-15): about 6 min 30 s (390 s) end to end: unit tests about 302 s, e2e about 79 s. Test counts are deliberately not recorded here (they go stale every PR): `pytest --collect-only -q --ignore=tests/e2e | tail -1` prints the unit count, `pytest tests/e2e --collect-only -q --browser chromium | tail -1` the e2e node count. Whenever the e2e step runs it writes `data/e2e-junit.xml` (git-ignored, `[e2e] junit_xml` in `.fleet.toml`), which `/e2e-audit` reads for per-test seconds; a skipped step leaves the previous file untouched. The e2e figure above is the full suite; re-measure and update these numbers when the suite changes.

**Design inventory.** Each e2e surface is tagged with the vendored `surface(page, "<tab>.<surface>")` hook (`tests/e2e/_surface.py`, inert unless `DESIGN_SURFACE_CAPTURE` is set) at the first test that opens it, after that test's assertions, directly in the test body (the hook deselects any test whose own source never calls it). A new surface takes one such call; `/design-review measure local-llm-hub --e2e` walks them on a disposable hub and reports any overlay still not walked.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). Live, parseable block — the admin PWA is the FastAPI + static app under `app_web/static/`, mounted at `/admin`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real admin PWA
- paths:
  - app_web/static/**/*.css
  - app_web/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/admin/`
  - /admin/    (Hub · Models · Playground · Code · Machines tabs, plus the Settings dialog)
