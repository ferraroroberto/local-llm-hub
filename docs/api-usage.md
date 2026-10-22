# Using the hub

## Use it from Python

Anthropic SDK, any backend:

```python
from anthropic import Anthropic

client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")

# Claude via subscription — use the version-free alias, not the dated display_name
msg = client.messages.create(
    model="claude_haiku",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# agentic_light role — Qwen 3.5 4B (hybrid Gated DeltaNet + sparse MoE, full GPU)
msg = client.messages.create(
    model="qwen3.5-4b",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# agentic_heavy role — Gemma 4 26B MoE (25 B / 3.8 B-active on GPU)
msg = client.messages.create(
    model="gemma4-26b-a4b-it",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)

# Stream through the normal Anthropic SDK on Claude or a llama-server model.
with client.messages.stream(
    model="claude_haiku",
    max_tokens=128,
    messages=[{"role": "user", "content": "Tell me a short story"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
    final = stream.get_final_message()
    print(f"\n{final.usage.output_tokens} output tokens")

# Or address the role directly — survives future /swap-model rotations
# unchanged. `agentic_light` and `agentic_heavy` both work the same way.
msg = client.messages.create(
    model="agentic_light",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# Gemini 3.1 Pro via your Google AI Pro subscription (Antigravity CLI)
msg = client.messages.create(
    model="gemini_pro",   # alias for "Gemini 3.1 Pro"
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# Image content blocks work on both subscription paths (claude-* and gemini-*).
# The admin SPA's Play tab grows a file uploader automatically when the
# selected model resolves to a claude/gemini backend.
import base64
with open("photo.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

msg = client.messages.create(
    model="gemini_pro",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": b64,
            }},
        ],
    }],
)
print(msg.content[0].text)
```

> A demoted candidate (e.g. `glm-4.5-air`) works the same way if you've
> brought it up ad-hoc — pass its model name as `model=`. The hub will
> return 400 if its backend isn't reachable.

OpenAI SDK (get native tool calls via `llama-server --jinja`):

```python
from openai import OpenAI
client = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")
msg = client.chat.completions.create(
    model="gemma4-26b-a4b-it",
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.choices[0].message.content)
```

Raw HTTP:

```bash
curl -s http://127.0.0.1:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4-26b-a4b-it","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'
```

List enabled models:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

**Error responses.** `/v1/messages` answers errors in the Anthropic
envelope, so the SDK's typed exceptions (`BadRequestError`,
`RateLimitError`, …) and its retry logic behave here as they do against
the real API:

```json
{"type": "error", "error": {"type": "invalid_request_error", "message": "messages must not be empty"}}
```

`error.type` follows the status: 400/422 `invalid_request_error`, 401
`authentication_error`, 403 `permission_error`, 404 `not_found_error`,
429 `rate_limit_error`, 503 `overloaded_error`, other 5xx `api_error`.
`/v1/chat/completions` and the `/admin` API deliberately keep FastAPI's
`{"detail": …}` shape — OpenAI-shape callers parse a different envelope.

## Counting input tokens

`POST /v1/messages/count_tokens` takes the same body as `/v1/messages`
(`model` + `messages` + optional `system`; `max_tokens` is ignored) and
answers with Anthropic's `input_tokens`, so a caller can size a prompt
against a model's context — or its budget — before paying to find out:

```bash
curl -s http://127.0.0.1:8000/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5-4b","system":"You are terse.","messages":[{"role":"user","content":"Count to three."}]}'
```

```json
{"input_tokens": 23, "model": "qwen3.5-4b", "backend": "openai",
 "method": "llama_server_tokenizer", "exact": true}
```

**Read `exact` before you trust the number.** The honest count differs per
backend family, so the hub resolves it per backend instead of applying one
global guess — and when it cannot measure, it says so rather than dressing
an estimate up as a measurement:

| Backend | `method` | `exact` | How |
| --- | --- | --- | --- |
| `openai` (llama-server: qwen, gemma, glm) | `llama_server_tokenizer` | ✅ yes | `POST /apply-template` renders the messages through the model's own chat template, `POST /tokenize` counts the result (`add_special: true`, matching what llama-server does for a real completion) |
| `openai`, pre-`/apply-template` llama-server | `llama_server_tokenizer_untemplated` | ❌ no | Real vocabulary, but the per-turn template tokens are missing, so the true prompt is *larger*. Upgrade llama-server for an exact count |
| `claude` (`claude -p` CLI) | `character_heuristic` | ❌ no | `len(text) / 4` over the caller's own system + message text |
| `gemini` (`agy` CLI) | `character_heuristic` | ❌ no | Same heuristic — `agy` exposes no tokenizer either |
| remote-owned rows (`host:`/`hosts:` elsewhere) | *whatever the owner reports* | *passed through* | Proxied to the owning host's hub, which repeats this decision against the backend actually in front of it |
| `whisper` / `tts` | — | — | 400, same rejection `/v1/messages` gives ("POST audio to … instead") |

Every non-exact response also carries a `warning` string naming the method
and its blind spots (e.g. image/document blocks, which are not counted at
all). An unknown model 400s exactly as `/v1/messages` does, in the same
Anthropic error envelope.

**Why the CLI backends can't be exact.** `claude-*` and `gemini-*` are
driven through a subscription CLI, not an API key, so there is no
`count_tokens` call to forward to and no published offline tokenizer for
the current model generation. The only exact number available would come
from running the generation — which is the cost this endpoint exists to
avoid.

**Measured delta on the claude path** (`claude-haiku-4-5`,
`system: "You are terse."` + one 15-char user turn): the hub returned
`input_tokens: 8`; the identical body through `/v1/messages` reported
`input_tokens: 10`, so the heuristic is within ~20% of the caller's *own*
content. The call's real input is larger because `claude -p` adds Claude
Code's own base prompt. The CLI runs isolated (see
[limitations.md](limitations.md)), so a one-word text call's total input
(input + cache) is ~440 tokens and its wall time ~2.4 s (CLI 2.1.270).
Budget against `input_tokens` from this endpoint as "how big is my
prompt", never as "what will this call cost".

**On-demand models.** Counting against a `startup: on_demand` row
(`gemma4-26b-a4b-it`, `glm-4.5-air`) loads it, because its tokenizer is the
only thing that can answer exactly — same spawn-and-wait a real request
does, and the same idle watchdog unloads it afterwards.

Generate an image — OpenAI Images shape, returns `data[].b64_json`. Swap
`model` for `flux1_local` to run it locally on the GPU (FLUX.1 [dev]) instead
of Google Imagen; the request and response shapes are identical. On the local
model you can also pass `size` — a preset (`square`, `widescreen`, `hd`, `4k`,
…) or `"WIDTHxHEIGHT"` on a 16-pixel grid; Imagen ignores it:

```python
import base64
from openai import OpenAI
client = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")
r = client.images.generate(model="gemini_image", prompt="a red apple on white")
open("apple.png", "wb").write(base64.b64decode(r.data[0].b64_json))
```

```bash
curl -s http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini_image","prompt":"a red apple on white"}'
```

Transcribe audio via whisper — through the hub's proxy at
`:8000/v1/audio/transcriptions`, which works from any machine (the hub
resolves the owning host and lands the call in the observability ring):

```bash
# through the hub proxy (portable — works whether or not this box owns whisper)
curl -s -F file=@clip.wav -F response_format=json \
  http://127.0.0.1:8000/v1/audio/transcriptions

# only on the host that actually runs the backend: direct to
# whisper-server, lower overhead, skips the observability ring
curl -s -F file=@clip.wav -F response_format=json \
  http://127.0.0.1:8090/v1/audio/transcriptions
```

Translate non-English audio to English via the translate slot (medium runs
eager on CPU, ~1.5 GB RAM, on the owning host):

```bash
# portable: through the hub proxy, which bridges task=translate → translate=true
curl -s -F file=@spanish.wav -F task=translate \
  http://127.0.0.1:8000/v1/audio/translations

# on the owning host only: whisper-server honors translate=true (not OpenAI's
# task=translate) on the direct-to-port path
curl -s -F file=@spanish.wav -F translate=true \
  http://127.0.0.1:8091/v1/audio/transcriptions
```

Or with the OpenAI SDK:

```python
from openai import OpenAI
asr = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")
with open("clip.wav", "rb") as f:
    r = asr.audio.transcriptions.create(model="whisper-large-v3-turbo", file=f)
print(r.text)
```

Synthesize speech (text → audio) via the TTS backend — through the hub's
proxy at `:8000/v1/audio/speech` (captured in the observability ring) or
directly to the backend port (lower overhead):

```bash
# through the hub proxy (lands in the observability ring)
# audio_speech → Piper; voice picks amy (default), ryan, ryan-high, or lessac
curl -s -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"audio_speech","input":"Hey, listen to this.","voice":"amy","response_format":"wav"}' \
  --output reply.wav

# kokoro-tts → Kokoro-82M; empty/default voice uses am_michael
curl -s -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"kokoro-tts","input":"Arming the perimeter.","voice":"am_michael","response_format":"wav"}' \
  --output kokoro-reply.wav

# Spanish female voice; use em_alex for the male profile
curl -s -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"kokoro-tts","input":"Hola, esta es una prueba de voz en español.","voice":"ef_dora","response_format":"wav"}' \
  --output kokoro-spanish.wav
```

```python
from openai import OpenAI
tts = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")
# model="audio_speech" → Piper (auto-loaded); model="orpheus-tts" → Orpheus.
audio = tts.audio.speech.create(model="audio_speech", voice="amy", input="Hey, listen to this.")
audio.stream_to_file("reply.wav")
```

`exaggeration` / `cfg_weight` are Chatterbox's emotion/"tone" dial; `voice`
selects a Piper voice (`amy`, `ryan`, `ryan-high`, `lessac`), an Orpheus preset
(`tara`, `leah`, …), a Kokoro voice id (`am_michael`, `af_bella`,
`am_fenrir`, `ef_dora` (Spanish female), `em_alex` (Spanish male), …), or a Chatterbox cloning clip at
`config/tts_voices/<voice>.wav`. Piper and Kokoro honor `speed` in the
0.5–2.0 range; Chatterbox/Orpheus accept it for API compatibility. Add
`"stream_format":"audio"` to **stream** audio incrementally when the engine
supports it; otherwise the backend returns a single final chunk. The hub
exposes every enabled TTS model on the same route, so a client switches
engines just by changing `model`. Defaults, formats, streaming, voice
cloning, and the Orpheus GGUF caveat are in [add-tts.md](add-tts.md).
An explicit unknown model or voice returns HTTP 400; only omitted fields use
the configured defaults.

The admin Playground's Speech mode lists every configured voice model, marks
stopped models, and filters language and voice choices to the selected engine.
It also shows only controls the engine supports.

**Playground layout.** The Playground is one composer with a **Chat ·
Image · Speech · Decide** switch; the last mode is remembered on the device
(`llmhub.playground.mode`, Chat on a first visit or when storage is blocked),
and "Send a test request" on Hub always lands on Chat. Each mode keeps its
primary fields on the face (model and prompt or text; for Speech also language,
voice and streaming; for Decide the state and questions, under the visible
third-party privacy line) and its rare ones under **More options** (Chat:
system prompt, attachment, max tokens; Image: refine and reference image;
Speech: format and the voice sliders). Each mode has its own result card, so a
request keeps running across a switch and a result never shows in another mode.
On a wide window (1100px+, mouse) the composer sits left of the result. The language selector is UI metadata: calls
still use the same `model` and `voice` fields as Home Automation, App Launcher,
and WhatsApp Radar.

## API headers

What the hub reads off a request and what it writes back, so an
`anthropic`-SDK client sees the header vocabulary it expects.

**Accepted on the way in:**

| Header | Effect |
| --- | --- |
| `Authorization: Bearer <token>` | Authenticates a non-loopback caller. |
| `x-api-key: <token>` | Same thing — the Anthropic SDK's own credential header, so `Anthropic(api_key="<hub token>", base_url=…)` authenticates with no `default_headers` special-casing. A wrong value is rejected exactly as a wrong bearer token is. |
| `?token=<token>` | Same thing, on the URL (bookmarked/shared admin links). |
| `anthropic-version` | Echoed back. The hub does not vary its wire shape by version, so this is an acknowledgement, not a negotiation. |
| `anthropic-beta` | Echoed back, **never honoured** — see the caveat below. |
| `X-Trace-Id` | Bridged into a W3C `traceparent`, so repeat calls land in one Langfuse trace. |

Loopback callers still bypass authentication entirely — none of the above
changes that.

**Emitted on the way out:**

| Header | Meaning |
| --- | --- |
| `request-id` | Correlation ID for the request, on every response except `/admin/static/*`. Aliased to the trace ID, so it equals `X-Trace-Id` and is the same value the hub logs (`request-id=… POST /v1/messages -> 200`) and Langfuse traces. When telemetry is off (`OTEL_SDK_DISABLED=true`) there is no trace ID, so the hub mints a random one and no `X-Trace-Id` is emitted. |
| `X-Trace-Id` | Unchanged — the existing contract, emitted whenever a span is live. |
| `anthropic-version` | The version the caller sent, or `2023-06-01` when it sent none. |
| `anthropic-beta` | The betas the caller asked for, verbatim. |
| `Warning: 299 local-llm-hub "…"` | Accompanies any echoed `anthropic-beta`. |

**`anthropic-beta` caveat — read this before relying on one.** The hub
implements **no** Anthropic beta feature. It accepts the header (rejecting
it would 400 an SDK caller who set a beta the hub has no opinion about) and
echoes it back as a receipt, but every requested beta that is not
implemented also gets an RFC 7234 advisory on the same response:

```
anthropic-beta: context-1m-2025-08-07
Warning: 299 local-llm-hub "anthropic-beta received but not implemented: context-1m-2025-08-07"
```

The echo means "received", never "honoured". The implemented set is
`IMPLEMENTED_BETAS` in `src/anthropic_headers.py` — empty, and a value only
goes in there when the beta is genuinely wired up.

Check it from loopback:

```bash
curl -is -X POST http://127.0.0.1:8000/v1/messages \
  -H "content-type: application/json" \
  -H "anthropic-beta: context-1m-2025-08-07" \
  -d '{"model":"claude_haiku","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}' \
  | findstr /i "request-id x-trace-id anthropic- warning"
```

## CORS — calling the hub from a browser

Page JavaScript can call the hub directly. A sister webapp on this
machine does **not** need to proxy `/v1/messages` through its own backend
just to satisfy the browser.

**What is allowed out of the box:** every loopback origin — `http` or
`https`, `localhost` / `127.x.x.x` / `[::1]`, any port. Nothing else.

That default is not arbitrary: loopback is *already* the hub's trust
boundary (a loopback caller skips the bearer token entirely), so letting a
loopback origin read the response grants nothing it did not already have.
A page served from somewhere else still makes its request from
`127.0.0.1`, so it is the **origin** check — not the client IP — that
stops it reading the answer.

**Adding an origin:** name it in `config/webapp_config.json` (template:
`config/webapp_config.sample.json`).

```json
{
  "cors_allow_origins": ["https://llm.example.com"]
}
```

Exact origins only — scheme + host + port, no path, no trailing slash.
This is read once at startup, so restart the hub (`tray.bat --restart`)
after changing it; that is unlike `auth_token`, which is re-read per
request.

**No wildcard ships, and `"*"` is not a way to get one.** A `"*"` entry is
dropped with a warning rather than honoured. `allow_origins: ["*"]` is
precisely the shape that turns a loopback-trusting service into one any
page in any tab can drive, and the hub declines to offer it. For the same
reason `allow_credentials` is off: the hub sets no cookies and uses no
HTTP-auth realm — it authenticates from `Authorization` / `x-api-key` /
`?token=`, all of which a plain cross-origin `fetch` carries fine — so the
credentialed-CORS risk class is declined outright instead of being
contained by the origin list.

**CORS is not an auth bypass.** The middleware sits outermost so a
preflight `OPTIONS` — which a browser sends with no credentials, by
design — is answered instead of 401'd. The real request behind it travels
the full stack and meets the bearer gate exactly as before.

**Readable response headers.** A header a browser cannot read is the same
as not sending it, so the hub's own contracts are on `expose_headers`:
`request-id`, `X-Trace-Id`, `anthropic-version`, `anthropic-beta`,
`Warning`, `WWW-Authenticate`. Request headers are a named list (not `*`)
covering both SDKs' vocabulary — `authorization`, `content-type`,
`x-api-key`, `anthropic-version`, `anthropic-beta`, the OpenAI
`openai-*` headers, and the `x-stainless-*` telemetry headers both
generated SDKs attach unconditionally. The full policy, and why each
piece is the way it is, is `src/cors_policy.py`.

From a page on any loopback origin:

```js
const r = await fetch("http://127.0.0.1:8000/v1/models");
console.log(r.headers.get("X-Trace-Id"), await r.json());
```

Check the preflight by hand — allowed, then refused:

```bash
curl -is -X OPTIONS http://127.0.0.1:8000/v1/messages \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type, x-api-key"

curl -is -X OPTIONS http://127.0.0.1:8000/v1/messages \
  -H "Origin: https://evil.example" \
  -H "Access-Control-Request-Method: POST"
```

## TypeSafe System One passthrough (`/v1/systemone`)

`POST /v1/systemone` forwards a TypeSafe "System One" request to `https://api.typesafe.ai/v1/systemone` and returns the vendor's status and JSON unchanged — the `answers` map with its per-option `probabilities` and `confidence`, plus `model` (the versioned id, e.g. `jev-1.13.0`) and `usage`. The request body is TypeSafe's own shape (`model`, `state`, typed `questions`); see TypeSafe's HTTP reference (`https://docs.typesafe.ai/api.md`). Jev is a decision model, not a chat model, so it is deliberately **not** reachable through `/v1/messages` / `/v1/chat/completions` and not listed in `GET /v1/models`.

In the admin SPA, Jev shows up in two places, both admin-only. The **Models** tab has a read-only *TypeSafe Jev* tile: backend `typesafe`, `api.typesafe.ai (US)`, the aliases, and a `no key` badge when `TYPESAFE_API_KEY` is unset. It has no start/stop/ping, because every probe would be a billed evaluation. The **Playground** has a *Decide* mode (Jev): a state box plus a questions JSON box, prefilled with the vendor quickstart. It shows each answer as probability bars with the chosen option or score and its confidence. The mode calls this route over loopback, so its evaluations land in the request ring like any other caller's. A vendor 401 comes back as a 502 naming the rejected key, so it can't be mistaken for the admin session expiring.

> **Privacy:** this route sends the request content to a **third-party API hosted in the United States**. It is the hub's only internet egress; it is opt-in — a caller has to call this route explicitly, and nothing in the hub falls back to it.

Set `TYPESAFE_API_KEY` in `.env` (read per call, so no restart is needed after adding it). The key is never logged or returned. The hub does **not** retry — TypeSafe's SDKs already back off on 429/529 — and passes `Retry-After` through. Hub-originated failures carry a `hub_error` field so they can't be mistaken for vendor errors:

| Condition | Status | Body |
|---|---|---|
| `TYPESAFE_API_KEY` unset | 503 | `hub_error: typesafe_not_configured` (nothing is sent) |
| Vendor rejects the key / validation / rate limit / overload | 401 / 422 / 429 / 529 | the vendor's body, unchanged |
| Vendor unreachable | 502 | `hub_error: typesafe_unreachable` |
| No answer within 60 s | 504 | `hub_error: typesafe_timeout` |

Every call lands in the admin request ring with the requested and served model, latency, token usage and the question count (`3 questions`); request and answer bodies go to Langfuse under the same `OTEL_HASH_PROMPTS` rule as other routes, and vendor error text is kept out of the ring when hashing is on.

```bash
curl -s http://127.0.0.1:8000/v1/systemone -H 'content-type: application/json' -d '{
  "model": "jev-latest",
  "state": "Newsletter issue about local LLM hubs.",
  "questions": {"relevant": {"type": "noul", "instructions": "Is this relevant to AI engineering?"}}
}'
```

Fleet-wide survey of which other call sites could route to this model, plus a
local/open-source alternative spike: [jev-call-site-survey.md](jev-call-site-survey.md).

