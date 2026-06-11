# What we did — 2026-04-22

Post-mortem of adding a local whisper.cpp ASR server to the hub as a
seventh "model" entry, and renaming the per-backend process manager to
reflect that it now manages both `llama-server` and `whisper-server`.
Pairs with [hub-with-qwen-and-glm.md](hub-with-qwen-and-glm.md).

---

## Starting point (2026-04-21)

- Hub shipping six chat backends (claude + qwen + glm + three gemma
  rows). All text, all routed by `model` name on :8000.
- Sister project `E:\automation\automation\audio\transcribe_voice`
  runs its own whisper.cpp server on :8090 and exposes OpenAI-compatible
  `/v1/audio/transcriptions` + `/v1/audio/translations`. Its
  `whisper_server/whisper_server.yaml` was already annotated
  *"intentionally identical between this repository and local-llm-hub
  so the server binds the same port"* — the design had always intended
  both projects to share :8090 as a mutual-exclusion lock.
- [scripts/install_whisper_cpp.py](../../scripts/install_whisper_cpp.py)
  had been seeded (untracked) to download the CUDA Windows build of
  whisper.cpp and normalise the binary name to `whisper-server[.exe]`.
  Everything else was missing: registry entry, process wiring, UI
  surfacing, install checks.

## Goal

Add a single new model id `whisper` to the existing scaffolding. CUDA-
backed, Windows-first, fully reusing the llama-server pattern (model
registry + backend-process manager + admin SPA Models tab + `src.install`
+ `src.run_backend`). No parallel app, no new UI surface.

## Decisions (and the options we rejected)

1. **No hub passthrough for audio endpoints.** Clients (the sister
   project, curl, any future caller) talk to whisper directly on
   `127.0.0.1:8090`. The hub stays text-only. This mirrors how
   transcribe_voice already works and avoids ~30 lines of async
   streaming code that nothing in the current workflow needs. The hub's
   only job for whisper is to surface it in `/v1/models` and return an
   actionable 400 if someone POSTs chat to `whisper-small`.
2. **Rename `src/llama_process.py` → `src/backend_process.py`.** Once
   the module handles both llama-server and whisper-server, the old
   name is a misnomer. Done while we were in there.
3. **YAML-only size selection, default small.** No CLI flags, no env
   vars. To switch to `ggml-tiny.bin` / `base` / `medium` / `large-v3`,
   edit `hf_pattern` + `model_path` in [config/models.yaml](../../config/models.yaml)
   and re-run `python -m src.install --fix`. Matches how every other
   model in the registry picks a quant.
4. **Branch on `engine: whisper-server`**, not a new manager class.
   Two functions (`build_command`, `is_reachable`) gain a small
   conditional; everything else stays shared.

## What we built

### Registry row ([config/models.yaml](../../config/models.yaml))

```yaml
whisper:
  display_name: whisper-small
  backend: whisper           # new backend type; not chat
  engine: whisper-server
  port: 8090
  hf_repo: "ggerganov/whisper.cpp"
  hf_pattern: "ggml-small.bin"
  model_path: "models/ggml-small.bin"
  args:
    - "--threads"
    - "4"
    - "--processors"
    - "1"
    - "--inference-path"
    - "/v1/audio/transcriptions"
```

Enabled on `pc-cuda`, not on `mac-mini-m4`.

### Process manager ([src/backend_process.py](../../src/backend_process.py))

Renamed from `llama_process.py`. Two engine-aware helpers, everything
else shared:

- `build_command(model)` — if `model.engine == "whisper-server"`, use
  `vendor/whisper.cpp/whisper-server[.exe]` and emit `--model <path>`
  (whisper) instead of `-m <path>` (llama). `--host` / `--port` are the
  same for both.
- `is_reachable(model)` — llama-server health checks use `/health`;
  whisper.cpp v1.8.4 has no such endpoint, so whisper probes `GET /`
  for a 200.
- Added `VENDOR_WHISPER = PROJECT_ROOT / "vendor" / "whisper.cpp"` and
  wired `start()` to extend Windows `PATH` with that directory when
  launching a whisper-engine model (CUDA DLLs ship next to the binary).
- `running_backends()` widened to `backend in ("openai", "whisper")`.

### Dispatcher + hub router

- [src/run_backend.py](../../src/run_backend.py) — accepts
  `backend in ("openai", "whisper")` and picks the right vendor dir for
  the Windows PATH augmentation.
- [src/server.py](../../src/server.py) — both `/v1/messages` and
  `/v1/chat/completions` handlers add an explicit 400 when the resolved
  model is whisper, with a body pointing callers at
  `http://127.0.0.1:8090/v1/audio/transcriptions`.
- `/v1/models` needed zero code changes — it iterates `enabled_models()`
  and whisper rides along for free.

### Install + download

- [src/install.py](../../src/install.py) — added `_check_whisper_cpp()`
  (gated on whether any enabled model has `engine == "whisper-server"`),
  `_fix_whisper_cpp()` that shells out to
  `scripts.install_whisper_cpp.main()`, and widened `_check_models()`
  and `_check_ports()` filters from `backend == "openai"` to
  `backend in ("openai", "whisper")`.
- [scripts/download_models.py](../../scripts/download_models.py) —
  `ggerganov/whisper.cpp` hosts `ggml-small.bin` at the repo root, so
  `huggingface_hub.hf_hub_download` fits unchanged once the backend
  filter accepts `whisper`.

### UI + launchers

- [app/views/models.py](../../app/views/models.py) — renamed
  `_render_llama_card` → `_render_local_card`; whisper cards show 🎙
  + "whisper-server", llama cards show 🦙 + "llama-server". Routing
  widened to `m.backend in ("openai", "whisper")`. No other functional
  changes — the card reuses the same start/stop/logs/health controls.
- New `run_whisper.bat` / `run_whisper.sh`. `run_all.*` gained a
  `whisper` line so "start everything" brings it up.

### Docs + tests

- [docs/model-comparison.md](../model-comparison.md) — added a
  `whisper-small` row (engine whisper.cpp, port 8090, `ggml-small.bin`
  ~466 MB, role "speech-to-text, not chat") plus a roles-at-a-glance
  row with size-switching instructions.
- [tests/test_model_registry.py](../../tests/test_model_registry.py) —
  `test_whisper_entry` asserts backend=`whisper`, engine=`whisper-server`,
  port=8090, `url == "http://127.0.0.1:8090/v1"`, with per-host
  filtering checked on both `pc-cuda` and `mac-mini-m4`.
- [README.md](../../README.md) + [docs/project-structure.md](../project-structure.md) —
  updated for the new row and the `llama_process → backend_process`
  rename.

## Surprises we hit on the way

### `--gpu 1` is not a whisper.cpp flag

Copy-pasted from an older plan draft. whisper.cpp v1.8.4 defaults GPU
on; the opt-out is `-ng` / `--no-gpu`. Removed from `args`.

### The default inference path is `/inference`, not `/v1/audio/…`

whisper-server's default route is `/inference`. The OpenAI-compatible
path only exists if you pass `--inference-path /v1/audio/transcriptions`
at startup. Added to `args` so the sister project's `TranscriptionClient`
(which posts to `/v1/audio/transcriptions`) works unchanged.

### whisper.cpp v1.8.4 exposes exactly ONE inference path per server

The sister project's client hits *two* different URL paths depending on
its `translate` flag (`/v1/audio/transcriptions` vs
`/v1/audio/translations`). whisper.cpp only accepts one
`--inference-path` at launch. The translate-path request 404s.

Not fixed in this repo — the workarounds are on the sister side: either
set `translate: false` in
`E:\automation\automation\audio\transcribe_voice`'s config (simplest),
or patch its `transcription_client.py` to POST to a single path and
pass `translate` as a form field.

### Runaway repetition loops (issue #88)

whisper.cpp periodically emitted a runaway repetition loop — the same
short phrase repeated dozens of times until the clip ended (`"all these
things that we're doing…"`, `"And I think that's a very important
thing."` ×40). It is the classic Whisper decoder hallucination, and the
hub was exposed to it because `--max-context` defaults to **`-1`**
(unlimited). whisper.cpp transcribes in ~30 s windows and feeds the
previous window's decoded text forward as the next window's initial
prompt; once the greedy decoder emits a repeated n-gram on a
low-information stretch (filler, a pause, silence, noise), that text is
carried forward and self-reinforces into an escalating cascade.

Fix (launch-flag only, both whisper rows in `config/models.yaml`):

- **`--max-context 0`** — disables the cross-window text carry-over. This
  is the highest-leverage guard and is equivalent to OpenAI Whisper's
  `condition_on_previous_text=False`. Tradeoff: marginally less coherence
  across sentence boundaries (e.g. consistent spelling of a name) —
  negligible for short dictation clips.
- **`--suppress-nst`** — suppresses non-speech tokens that also seed
  silence/noise hallucinations.

Already on by default in the vendored build (verified via
`whisper-server --help`): temperature fallback and the entropy threshold
(`-et 2.40`). VAD (`--vad` + `--vad-model`) would filter non-speech
before the decoder and help further, but it needs a separate Silero VAD
model download — deferred until the two no-download guards above prove
insufficient.

## Transcription glossary — deterministic replacement (issue #90)

Whisper reliably mis-transcribes a handful of domain terms — most
notably **"Claude Code" → "cloud code"** (it hears "Claude" as "Cloud"
regardless of any initial prompt, so recognition-level biasing can't fix
it). The hub's audio proxy applies a committed glossary to the
transcript text **after** whisper returns it, deterministically fixing
these.

This is the *replacement* half of a two-part, Wispr-Flow-style
dictionary. The *boosting* half (next section) biases recognition; the
two share one config file but are independent.

### Config — `config/transcription_glossary.json`

```json
{
  "replacements": [
    { "from": "cloud code", "to": "Claude Code" },
    { "from": "open claw",  "to": "openClaw" },
    { "from": "quen",       "to": "Qwen" }
  ],
  "boost_terms": ["Claude Code", "Codex", "Qwen", "Langfuse", "openClaw"]
}
```

- **`replacements`** — an *ordered* list of literal `{"from","to"}`
  rules. Matching is **case-insensitive**, **word-boundary-anchored**
  (`\bquen\b` won't touch "quench"), and **longest-phrase-first** (a
  short rule can't pre-empt a longer overlapping one). The replacement
  is the literal `to` value, so any cased form of the source collapses
  to the one canonical spelling.
- **`boost_terms`** — vocabulary for the recognition-boosting prompt
  (next section); ignored by the replacement engine.

Seed rules are deliberately **conservative** — multi-word / unambiguous
phrases only. Excluded as too risky for a default: `codecs → Codex`
("codecs" is a real word) and standalone `cloud → Claude`. Add those
per-term locally if you want them.

### How it's wired

- [src/transcription_glossary.py](../src/transcription_glossary.py) loads
  + compiles the rules (cached; edits take effect on hub restart) and
  exposes `apply_to_response(content, content_type, rules)`.
- [src/server.py](../src/server.py) `_proxy_audio` calls it on the
  upstream response before returning, for both the transcribe and
  translate paths. It rewrites the `text` field of `json` responses,
  each `segments[].text` of `verbose_json`, and the whole body of
  `text/*` responses (`text`/`srt`/`vtt`). Unknown/binary content types
  pass through untouched. The call is wrapped defensively — a broken
  glossary can never break transcription.
- **Only the proxied path** (`:8000/v1/audio/*`) gets the glossary.
  Direct hits to `:8090`/`:8091` bypass it (and the observability ring).

Unit coverage:
[tests/test_transcription_glossary.py](../tests/test_transcription_glossary.py)
— ordering, word-boundary, case-insensitivity, no over-match, the three
response shapes, and an end-to-end proxy rewrite.

## Recognition boosting & the whisper.cpp ≥1.8.5 upgrade (issue #91)

The *boosting* half of the dictionary biases the decoder to **hear**
domain terms correctly in the first place, so fewer corrections are
needed downstream. Whisper's native lever is the **initial prompt**, but
two things have to line up:

- whisper.cpp **v1.8.5** (2026-05-29, ggml-org/whisper.cpp#3781) added
  the server flag **`--carry-initial-prompt`** ("always prepend initial
  prompt"), which re-injects the prompt into every 30 s sliding window so
  it doesn't age out on long dictation.
- The **`--max-context 0`** loop fix (#88) *nullifies* the initial prompt
  (the prompt shares the context budget `max-context` bounds, so `0`
  zeroes it) — and **`--carry-initial-prompt` does not override that**.
  Boosting therefore needs `--max-context > 0`.

### The upgrade (vendored binary → v1.8.6)

- [scripts/install_whisper_cpp.py](../../scripts/install_whisper_cpp.py)
  pins a **known-good release tag** (`PINNED_TAG = "v1.8.6"`, the newest
  patch on the 1.8.5 line) instead of floating `latest`, so the feature
  set is deterministic. The org moved `ggerganov` → `ggml-org`; the API
  redirects either way. Bump the tag deliberately when a newer one is
  vetted.
- A **`--force`** flag purges the vendored tree and reinstalls, so the
  binary can actually be *upgraded* (the default fast-path exits early
  when a runnable binary already exists). If whisper-server is running it
  holds the `.exe`/DLLs locked and `--force` raises a clear "stop the
  server first" error. `already_installed()` retries the first `--help`
  exec a few times — right after a forced extract it can transiently fail
  while the OS flushes the ~450 MB `cublasLt64_12.dll` to disk.
- To upgrade: stop the whisper servers (`:8090`/`:8091`, mutex-shared
  with `voice-transcriber` — the `:8090` kill is guarded, so stop them by
  hand), then `python scripts\install_whisper_cpp.py --force`.

### The `max-context` decision (measured on v1.8.6)

Same 90 s clip, `whisper-cli`, turbo model, `--suppress-nst`:

| config | result |
| ------ | ------ |
| `--max-context 0`, no prompt | baseline |
| `--max-context 0` + `--prompt` + `--carry-initial-prompt` | **byte-identical to baseline** — prompt had zero effect |
| `--max-context 64` + `--prompt` + `--carry-initial-prompt` | prompt **honoured** — glossary capitalization + sentence casing applied, no runaway repetition |

So the turbo (transcribe) row uses **`--max-context 64
--carry-initial-prompt`** — 64 is the smallest budget that re-enables
boosting while still holding loops at 0 repetitions (64–224 both held in
#91 testing; the real 90 s clip showed no loop at 64). This *partially
relaxes* the #88 `--max-context 0` guard, accepted as the cost of
boosting. The translate row (medium, ES→EN) keeps **`--max-context 0`**
and is **not** boosted — the English-tech glossary is off-topic for it,
so it keeps the strongest loop guard.

### Where the vocabulary lives

The boosting prompt is **not** hardcoded in `config/models.yaml`. The row
just opts in with `--carry-initial-prompt`; at launch
[src/backend_process.py](../src/backend_process.py) `_whisper_boost_args`
reads `boost_terms` from `config/transcription_glossary.json` and appends
`--prompt "Glossary: <terms>."`. One home for the vocabulary, shared with
the #90 replacement rules — edit the JSON, restart the whisper backend.
(`config/models.yaml` `args` are passed as a subprocess argv list, so the
spaces/commas in the prompt are one token — no shell-quoting artifact.)

### Validation run (#91)

- `install_whisper_cpp.py --force` → vendored binary at **v1.8.6**;
  `whisper-server --help` lists `--carry-initial-prompt`, `--prompt`,
  `--max-context`.
- `build_command(whisper)` emits the boosted argv; the live `:8090`
  server's command line carries `--max-context 64 --carry-initial-prompt
  … --prompt "Glossary: Claude Code, Codex, Qwen, Langfuse, openClaw."`.
- A real 90 s clip POSTed to `:8090` transcribed cleanly (carry-prompt
  sentence casing visible) with **no runaway repetition**.
- Unit gate: `pytest --ignore=tests/e2e` green, incl. the new
  [tests/test_whisper_boost_args.py](../tests/test_whisper_boost_args.py).
- **Caveat (same as #88):** the original `#88` looping clip and the
  jargon dictation clips were never archived, so seed-term correction
  (e.g. `codecs → Codex`) and the loop regression were not replayed
  against the exact audio; the result rests on the confirmed prompt
  mechanism + a clean real-clip run. The #90 replacement rules are the
  deterministic backstop for the acoustically-strong cases boosting
  can't fix (e.g. "cloud code" → "Claude Code").

## Verification (what we actually ran)

1. `python -m src.install --fix` — pulled the CUDA whisper.cpp release
   into `vendor/whisper.cpp/` and `ggml-small.bin` into `models/`.
   Overall: ok.
2. `python -m src.run_backend whisper` — log showed whisper-server
   listening on `0.0.0.0:8090`.
3. `curl http://127.0.0.1:8090/` → 200.
4. `curl -F file=@test_silence.wav -F response_format=json
    http://127.0.0.1:8090/v1/audio/transcriptions`
   → `{"text":" [BLANK_AUDIO]\n"}`.
5. **Mutual-exclusion cross-check** — from the sister project:
   `& e:/automation/automation/.venv/Scripts/python.exe
     e:/automation/automation/audio/transcribe_voice/launcher.py server status`
   reported
   `✅ running (external — started elsewhere) @ http://127.0.0.1:8090 [external]`.
   Exactly what we wanted: one project owns the port, the other
   detects it cleanly and doesn't try to stomp it.
6. Hub text routes rejected whisper with a 400 pointing callers at
   :8090.
7. `python -m pytest -q` — registry test, install test, server test all
   pass.

## Shape of the change

Minimal, as intended — most of the existing scaffolding absorbed
whisper without modification:

| area                              | change |
| --------------------------------- | ------ |
| `config/models.yaml`              | +1 model row, +1 entry in `pc-cuda.enabled` |
| `src/backend_process.py` (renamed) | 2 small engine-branches + `VENDOR_WHISPER` |
| `src/run_backend.py`              | widen backend filter, pick vendor dir |
| `src/server.py`                   | explicit 400 in two handlers |
| `src/install.py`                  | 1 new check + 1 new fix + 2 widened filters |
| `scripts/download_models.py`      | widen backend filter |
| `app/views/models.py`             | import rename + card-icon branch |
| `tests/test_model_registry.py`    | +1 test |
| `scripts/smoke_test.py`           | skip whisper rows |
| `run_whisper.{bat,sh}`            | new (trivial) |
| `run_all.{bat,sh}`                | +1 line |
| `docs/` + `README.md`             | updates |

No new modules. No hub passthrough. No parallel app.

## What's next

- Decide how to resolve the one-inference-path limitation for the
  sister project's default translate-true config. Three options ranked
  by cost: flip sister config to `translate: false` (zero code);
  teach sister `transcription_client.py` to POST to the single
  configured path with a `translate` form field (one file, small); run
  two whisper-server processes on different ports (overkill, burns
  VRAM twice).
- If a second whisper size ever gets used regularly, consider adding a
  second registry row (e.g. `whisper_large_v3` on a different port)
  rather than toggling `model_path`. Current YAML-only size selection
  is fine for the single-model case.

---

## Addendum 2026-04-24 — model upgrade: small → large-v3-turbo

The backend now ships `ggml-large-v3-turbo.bin` (~1.62 GB) instead of
`ggml-small.bin` (~466 MB). Same registry slot (`whisper`), same port
(8090), same launcher name; only the weights file and `display_name`
changed (`whisper-small` → `whisper-large-v3-turbo`). Whisper-turbo is
a distilled variant of large-v3 (4 decoder layers vs 32) — ~2× faster
than large-v3 at near-identical WER on well-resourced languages like
Spanish/English, which covers the `transcribe_voice` use case.

See [whisper-turbo-vs-large-v3.md](whisper-turbo-vs-large-v3.md)
for the rationale and the `small` / `large-v3` / `turbo` / `q5_0`
trade-off table.

The above historical references to `whisper-small` / `ggml-small.bin`
are intentionally left intact as a record of the original design.
