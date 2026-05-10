# 2026-05-10 — `whisper_translate` switched from lazy to eager load

## What changed

`whisper_translate` (medium, CPU, port 8091) now starts upfront with
`engine: whisper-server` instead of being demand-spawned through
`engine: whisper-server-lazy` + `src/whisper_translate_proxy.py`.

The lazy infrastructure is **kept intact** — the proxy module, the
`whisper-server-lazy` engine branches in
`src/backend_process.py`, and the `internal_port` / `idle_seconds`
fields on `Model` are unchanged. Switching back is a config-only
change. See [the original sibling-slot changelog](20260509-add-whisper-translate-instance.md)
for why the lazy mode was introduced.

## Why

Two reasons:

1. **The lazy proxy was timing out under the tray launcher.** The tray
   reported `whisper_translate not reachable after 120s` — the FastAPI
   shim wasn't ready inside the hub-ready window, so the tray UI flagged
   it as unreachable on every cold session. Going eager removes the
   proxy entirely from the active path; the tray now sees a plain
   whisper-server bound on :8091 like its turbo sibling on :8090.
2. **The reference host has 128 GB DDR5.** The whole reason for
   lazy-load was to keep medium (~1.5 GB on CPU) out of RAM while
   `gemma4_26b` was the deep lane. With `gemma4_26b` no longer in the
   active autostart on this host (the rotation is `qwen35_4b` +
   `whisper` + `whisper_translate`, leaving ~7 GB free on the 16 GB
   GPU), 1.5 GB of resident system RAM is a non-issue and the
   "always ready" property is worth more than the savings.

## Files modified

Config:
- [`config/models.yaml`](../../config/models.yaml) — `whisper_translate`
  entry: `engine: whisper-server-lazy` → `whisper-server`; removed
  `internal_port: 18091` and `idle_seconds: 300`.

Docs (truth-update; no behaviour change):
- [`README.md`](../../README.md) — 6 spots where the slot was described
  as lazy / cold-start / 5-min-idle.
- [`docs/project-structure.md`](../project-structure.md) — module
  diagram caption + "Key facts for LLM context" entry-points bullet.
- [`docs/model-comparison.md`](../model-comparison.md) — model table
  row + roles table row.
- [`app/views/welcome.py`](../../app/views/welcome.py) — Streamlit
  welcome page (component bullet, roles table, curl example comment).
- [`launchers/run_whisper_translate.bat`](../../launchers/run_whisper_translate.bat)
  + [`.sh`](../../launchers/run_whisper_translate.sh) — banner /
  header comments.

Untouched (the lazy mechanism still works, just isn't wired in):
- `src/whisper_translate_proxy.py`
- `src/backend_process.py` (`whisper-server-lazy` branches)
- `src/model_registry.py` (`internal_port`, `idle_seconds` fields)
- `tests/test_model_registry.py::test_whisper_translate_lazy_entry`
  builds its own synthetic config in `tmp_path` and exercises the
  lazy-engine code path; it's a registry-parsing unit test that's
  independent of the live `config/models.yaml`, so it still passes.

## Validation

- `python -m pytest tests/test_model_registry.py` — green; the
  synthetic-config lazy test still passes since the engine branch is
  intact.
- `nvidia-smi` after restart — qwen35_4b + whisper turbo on GPU =
  ~8.2 GB / 16 GB used; `whisper_translate` no longer shows in GPU
  (CPU-only by design).
- Tray no longer warns "whisper_translate not reachable after 120s".

## How to revert

Set the `whisper_translate` block in `config/models.yaml` back to:

```yaml
whisper_translate:
  display_name: whisper-medium-translate
  backend: whisper
  engine: whisper-server-lazy
  port: 8091
  internal_port: 18091
  idle_seconds: 300
  # …rest unchanged
```

Then restart the tray. No code changes required.
