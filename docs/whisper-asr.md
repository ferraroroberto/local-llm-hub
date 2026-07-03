# Whisper ASR backend

Reference for the whisper.cpp speech-to-text backend: how the hub
surfaces it, the port/mutual-exclusion contract, the decoder tuning that
keeps transcripts clean, and the two-part transcription dictionary
(deterministic replacement + recognition boosting). For *which* weights
each role ships see [whisper-turbo-vs-large-v3.md](whisper-turbo-vs-large-v3.md);
for per-model specs see [model-comparison.md](model-comparison.md).

## Backend contract

Whisper is registered like any other model row in
[config/models.yaml](../config/models.yaml), with `backend: whisper` and
`engine: whisper-server`. It reuses the shared llama-server scaffolding
(model registry + per-backend process manager + Models tab +
`src.install` + `src.run_backend`) — there is **no parallel app and no
new UI surface**.

- **The hub stays text-only for chat.** Both `/v1/messages` and
  `/v1/chat/completions` return an actionable **400** if a caller POSTs
  chat to a whisper row, pointing them at the audio endpoint. `/v1/models`
  lists whisper rows for free (it iterates `enabled_models()`).
- **Audio has two entry points.** Clients can POST directly to the
  whisper-server port for lowest overhead, or through the hub's audio
  proxy (`:8000/v1/audio/*`) to land in the observability ring **and**
  get the glossary post-processing pass (below). Direct hits to the
  whisper port bypass both.
- **`build_command` / `is_reachable` branch on `engine`.** A whisper row
  emits `--model <path>` (not llama's `-m`), and its health probe hits
  `GET /` for a 200 (whisper.cpp has no `/health`). The launcher extends
  the Windows `PATH` with `vendor/whisper.cpp/` so the CUDA DLLs shipped
  next to the binary resolve.

### Ports and the shared-port lock

The transcribe row runs on **:8090**, which is a **mutual-exclusion lock
shared with the `transcribe_voice` / voice-transcriber sister project** —
both projects intentionally bind the same port so only one whisper-server
is ever live. `config/models.yaml` and the sister project's
`whisper_server.yaml` are deliberately identical on this point. Whichever
project starts first owns the port; the other detects it as
externally-running and does not stomp it. The `:8090` kill is guarded in
the safe-restart path for exactly this reason.

The translate row runs on a **separate port** (medium model, CPU). This
is required, not incidental: **whisper.cpp exposes exactly one inference
path per server process** (`--inference-path`, set at launch), so a single
server cannot serve both `/v1/audio/transcriptions` and
`/v1/audio/translations`. Transcribe and translate are therefore distinct
rows on distinct ports. The default route is `/inference`; the
OpenAI-compatible path only exists because the row passes
`--inference-path /v1/audio/transcriptions`.

## Decoder tuning — loop guard and boosting

Two launch-flag decisions shape transcript quality. Both live in the
whisper rows' `args` in `config/models.yaml`.

### Runaway-repetition guard

whisper.cpp can emit a runaway repetition loop — the same short phrase
repeated dozens of times on a low-information stretch (filler, a pause,
silence, noise). It is the classic Whisper decoder hallucination: each
~30 s window feeds its decoded text forward as the next window's initial
prompt, so once the greedy decoder emits a repeated n-gram it
self-reinforces into an escalating cascade. `--max-context` defaults to
`-1` (unlimited carry-over), which leaves the door open.

Guards (launch flags only):

- **`--max-context 0`** disables the cross-window text carry-over — the
  highest-leverage guard, equivalent to OpenAI Whisper's
  `condition_on_previous_text=False`. Trade-off: marginally less coherence
  across sentence boundaries, negligible for short dictation clips.
- **`--suppress-nst`** suppresses non-speech tokens that also seed
  silence/noise hallucinations.

Temperature fallback and the entropy threshold (`-et 2.40`) are on by
default in the vendored build. VAD (`--vad` + a Silero VAD model) would
filter non-speech before the decoder but needs a separate model download —
deferred unless the two no-download guards prove insufficient.

### Recognition boosting (the `--max-context` trade-off)

Boosting biases the decoder to *hear* domain terms correctly in the first
place, via the whisper **initial prompt**. Two things must line up:

- whisper.cpp **≥ v1.8.5** added **`--carry-initial-prompt`**, which
  re-injects the prompt into every 30 s window so it doesn't age out on
  long dictation.
- `--max-context 0` **nullifies** the initial prompt (the prompt shares
  the context budget that `--max-context` bounds), and
  `--carry-initial-prompt` does **not** override that. Boosting therefore
  needs `--max-context > 0`.

Measured on a 90 s clip (turbo, `--suppress-nst`):

| config | result |
| ------ | ------ |
| `--max-context 0`, no prompt | baseline |
| `--max-context 0` + prompt + `--carry-initial-prompt` | byte-identical to baseline — prompt had zero effect |
| `--max-context 64` + prompt + `--carry-initial-prompt` | prompt honoured — glossary capitalisation applied, no runaway repetition |

So the **transcribe row** uses **`--max-context 64 --carry-initial-prompt`**
— 64 is the smallest budget that re-enables boosting while still holding
loops at zero. This partially relaxes the `--max-context 0` guard,
accepted as the cost of boosting. The **translate row** keeps
**`--max-context 0`** and is **not** boosted — the English tech glossary
is off-topic for a translation model, so it keeps the strongest loop
guard.

The boosting prompt is **not** hardcoded in `config/models.yaml`. A row
opts in with `--carry-initial-prompt`; at launch
[src/backend_process.py](../src/backend_process.py) `_whisper_boost_args`
reads `boost_terms` from `config/transcription_glossary.json` and appends
`--prompt "Glossary: <terms>."`. One home for the vocabulary, shared with
the replacement rules below. (Because `args` are passed as an argv list,
the spaces/commas in the prompt are one token — no shell-quoting artifact.)

Because the transcribe row carries the English tech-dictation glossary as
its initial prompt, it biases language detection toward English. Callers
transcribing general multilingual audio should select the glossary-free
`whisper-vanilla` row instead (lazy-loaded, unbiased auto-detect).

## Transcription dictionary

A two-part, Wispr-Flow-style dictionary. The **boosting** half (above)
biases recognition; the **replacement** half (below) deterministically
fixes what boosting can't. Both halves share one config file,
[config/transcription_glossary.json](../config/transcription_glossary.json),
but are independent.

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

- **`replacements`** — an *ordered* list of literal `{from, to}` rules.
  Matching is **case-insensitive**, **word-boundary-anchored** (`\bquen\b`
  won't touch "quench"), and **longest-phrase-first** (a short rule can't
  pre-empt a longer overlapping one). The replacement is the literal `to`
  value, so any cased form of the source collapses to one canonical
  spelling. This is the deterministic backstop for acoustically-strong
  misses that biasing can't fix — most notably **"Claude Code" → "cloud
  code"** (whisper hears "Claude" as "Cloud" regardless of any prompt).
  Seed rules are deliberately conservative — multi-word / unambiguous
  phrases only (`codecs → Codex` and standalone `cloud → Claude` are
  excluded as too risky for a default; add them per-term locally).
- **`boost_terms`** — vocabulary for the recognition-boosting prompt;
  ignored by the replacement engine.

### How replacement is wired

- [src/transcription_glossary.py](../src/transcription_glossary.py) loads
  and compiles the rules (cached) and exposes
  `apply_to_response(content, content_type, rules)`.
- [src/server.py](../src/server.py)'s audio proxy calls it on the upstream
  response before returning, for both transcribe and translate paths. It
  rewrites the `text` field of `json` responses, each `segments[].text` of
  `verbose_json`, and the whole body of `text/*` responses
  (`text`/`srt`/`vtt`); unknown/binary content types pass through. The
  call is wrapped defensively — a broken glossary can never break
  transcription.
- **Only the proxied path** (`:8000/v1/audio/*`) gets the glossary. Direct
  hits to the whisper port bypass it (and the observability ring).

### Editing in-app + transcript mining

The dictionary does not need hand-editing. In the admin SPA's **Models**
tab, every whisper row carries a 📖 button that expands an inline editor
for the shared `config/transcription_glossary.json` (one dictionary feeds
all whisper backends):

- **Replacements** — add / edit / delete / reorder the ordered rules.
  Saving writes the JSON and clears the rule cache, so replacement edits
  apply to the next request **without a restart**.
- **Boost terms** — add / remove chips. Boosting is a launch-time arg, so
  boost-term edits bind on the **next whisper start**.
- **✨ Suggest from transcripts** runs a miner that reads the last *N* days
  of real dictation from **voice-transcriber's session API** over loopback
  (the canonical corpus owner — the hub stores no transcript text of its
  own) and proposes additions to accept/reject. It never writes the
  dictionary unattended.

Surfaces:
[app_web/routers/glossary.py](../app_web/routers/glossary.py)
(`GET`/`PUT /admin/api/glossary`, `POST /admin/api/glossary/mine`) and
[src/dictionary_miner.py](../src/dictionary_miner.py) (frequency +
capitalisation heuristics for `boost_terms`, plus an optional LLM
clustering pass — run through the hub itself — that groups a canonical
term with its common mis-transcriptions into candidate `replacements`,
degrading gracefully to heuristics-only if the hub call fails). Miner
config lives in
[config/dictionary_miner.json](../config/dictionary_miner.json)
(`voice_transcriber_base_url`, `default_days`, `max_tokens`, `min_count`,
`use_llm`, `llm_model`); the baked defaults apply when it is absent.
