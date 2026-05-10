# Telemetry & Observability for `local-llm-hub`

> **Status:** plan only, no code yet.
> **Scope:** this repo (`local-llm-hub` / `claude-local-calls`). Clients (voice-transcriber, future iOS, openClaw) are touched only via documented header conventions, not code edits.
> **Goal:** make every request through the hub *observable* — what model, how long, how many tokens, what was the input/output, did it error, who called it — using the same standards enterprise AI teams use, so the data is portable and the skill is transferable.
> **Non-goal (for now):** evaluation. Golden datasets, automated metrics, regression gates, pairwise human eval — all deferred. This plan only covers telemetry/observability. Eval lands cleanly on top once the trace data is flowing.

Sibling document for context: [`E:\automation\voice-transcriber\docs\plans\llm-eval-observability.md`](../../../voice-transcriber/docs/plans/llm-eval-observability.md) — the original cross-repo proposal that included eval. This plan extracts and broadens the *observability* half of that proposal, applies it to the hub as a whole (not just the polish endpoint), and rebases it on the industry-standard OpenTelemetry stack.

---

## TL;DR

Right now the hub's only record of activity is `INFO 14:32:01 POST /v1/messages 200 OK` in stdout. When a request is slow, errors, or returns garbage, there is no structured artifact to inspect. This plan adds three layers, in order:

1. **OpenTelemetry SDK** wired into FastAPI + every upstream caller (`claude_cli`, `openai_upstream`, the whisper proxy), emitting traces, metrics, and structured logs using the **GenAI semantic conventions** (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, etc.).
2. **Langfuse** running locally as a single Docker container, receiving the OTel data and giving us a real LLM-aware UI: per-call traces with input/output, latency/cost charts, sessions, search, eventually evals.
3. **Smart-client support** — the hub accepts W3C `traceparent` and a custom `X-Trace-Id` header from callers, propagates them through, and exposes `POST /api/trace/{id}/feedback` so clients (voice-transcriber 👍/👎, future iOS, openClaw) can attach human signal to a specific trace later.

Everything lives in this repo. Clients only need to follow a small, documented header convention.

**Difficulty:** 3/5 — the SDK wiring is mechanical; the judgement work is in *what* to capture, *how much* of the prompt/response to store (PII!), and *where* to draw span boundaries.

**Why "industry standard":** OpenTelemetry is the CNCF-graduated, vendor-neutral standard every observability tool now speaks. GenAI semantic conventions are the LLM-specific extension being adopted by Langfuse, Phoenix, Arize, Datadog, New Relic, Honeycomb, Grafana, and more. Instrument once, swap backends for free.

---

## Concepts primer (read this before the rest)

This section is intentionally didactic. If you already know what observability is, skip to "Decision".

### The problem

Today, when something looks weird in the hub, the troubleshooting loop is: tail the console, hope the bug is reproducible, add a `print()`, restart, retry, remove the `print()`. There is no after-the-fact record of *what actually happened* to a specific request. You cannot answer:

- "Why was that polish call slow last Tuesday at 3pm?"
- "Which model returned the empty response that broke voice-transcriber?"
- "Are p95 latencies for `gemma4-26b-a4b-it` getting worse over the last two weeks?"
- "How many tokens have I burned through Claude this month, broken down by client?"

Observability turns those into 30-second queries, with the data you needed already captured.

### The three pillars

The whole field has converged on three kinds of telemetry data — different but complementary:

| Pillar | What it is | Example for this hub |
|---|---|---|
| **Traces** | A timeline of one request as it flows through the system, broken into nested **spans** (units of work) | "Request `abc123` hit `/v1/messages` (root span) → routed to `qwen3.5-4b` (child span) → `llama-server` upstream call took 1.4 s (grandchild span) → SSE streamed 312 tokens → done in 1.6 s total" |
| **Metrics** | Numerical values aggregated over time | Requests per minute by model, p50/p95 latency histograms, error rate, tokens-per-second throughput |
| **Logs** | Discrete events as structured records (key-value pairs, not strings) | `{event: "claude_cli_subprocess_start", model: "claude-haiku-4-5", trace_id: "abc123", pid: 47291}` |

The art is using each pillar for what it's good at:

- **Traces** answer "what happened to *this one* request?"
- **Metrics** answer "what's the trend across *all* requests?"
- **Logs** answer "what discrete events fired around the time of this request?"

A modern observability platform stitches them together: from a slow-trace view, you can pivot to "show me all logs for this trace" or "show me the latency metric for this model around the time this trace ran."

### What is OpenTelemetry?

[**OpenTelemetry (OTel)**](https://opentelemetry.io/) is the CNCF-graduated, vendor-neutral standard for telemetry. It defines:

- An SDK (in Python, JS, Go, Rust, etc.) you call from your code: `tracer.start_span("polish_call", attributes={"gen_ai.request.model": "qwen3.5-4b"})`.
- A wire protocol (**OTLP** — OpenTelemetry Protocol, gRPC or HTTP) for shipping that data to a backend.
- Semantic conventions: agreed-on attribute names so any tool can interpret your data.

The killer property: **you instrument your code once, against the OTel SDK. The backend is a config flip.** Switch from Langfuse to Grafana to Datadog without touching code. This is what makes OTel skills portable — every observability tool worth its salt accepts OTLP.

### GenAI semantic conventions

OTel has a [GenAI semantic convention spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — a list of standard attribute names for LLM calls. Examples:

- `gen_ai.system` — `"anthropic"`, `"openai"`, `"llama_cpp"`
- `gen_ai.request.model` — `"claude-haiku-4-5"`, `"qwen3.5-4b"`
- `gen_ai.request.temperature`, `.top_p`, `.max_tokens`
- `gen_ai.response.id`, `.finish_reasons`, `.model`
- `gen_ai.usage.input_tokens`, `.output_tokens`
- `gen_ai.operation.name` — `"chat"`, `"completion"`, `"embedding"`

Why this matters: when our hub uses these names, every LLM-aware tool (Langfuse, Phoenix, Arize, etc.) will already know how to render them — the "tokens used" panel, the "cost over time" chart, the "filter by model" dropdown all light up automatically. No custom dashboards required for the basics.

### What is Langfuse?

[**Langfuse**](https://langfuse.com/) is an open-source, self-hostable observability platform built specifically for LLM apps. It speaks OTLP, so it sits behind our OTel SDK as the destination.

Concretely, what Langfuse gives us out of the box once we send it OTel data:

- **Trace explorer**: every request as a row, filterable by model/user/tag/status. Click in to see the full prompt and full response side-by-side.
- **Sessions**: group related calls (e.g. a multi-turn conversation) into one view.
- **Latency / cost / token charts** broken down by model, by user, by time.
- **Search across prompts and outputs** — "show me every call where the response contained the word 'sorry'" is a one-line query.
- **Tags & user IDs** — bucket traces by client (`voice-transcriber`, `openclaw`, `tray-test`).
- **Score attachment** — the same row can later receive a 👍/👎, an automated eval score, or a numeric quality rating, which sets us up cleanly for the Eval phase later.

Runs as a single `docker-compose up` (Langfuse + Postgres + Clickhouse + Redis, all bundled). One process to start, one URL to bookmark.

**Alternatives considered:** Arize Phoenix (lighter, SQLite-backed, less polished UI), Helicone (cloud-only, paid), Logfire (Pydantic's offering, newer), W&B Weave (research-focused). Langfuse wins on the combination of: self-hostable, OSS, OTLP-native, polished UI, active development as of 2026.

### Smart clients & trace propagation

The W3C [**Trace Context** standard](https://www.w3.org/TR/trace-context/) defines how a trace ID flows across HTTP boundaries: the caller adds a `traceparent` header, the callee reads it, and any spans the callee creates become children of the caller's span.

For this hub, that means: when voice-transcriber calls `/v1/messages`, it can pass `traceparent: 00-<trace-id>-<parent-span-id>-01`. Our hub will:

1. Read the header and use that trace ID for its own spans (so the whole request, across both processes, shows up as one trace in Langfuse).
2. Generate one if the client didn't send one.
3. Return the trace ID back to the client in a response header (`X-Trace-Id`) so the client can correlate later actions to the same trace — e.g. "the user just tapped 👎, that's feedback on trace `abc123`".

The hub also exposes `POST /api/trace/{id}/feedback`, which clients call when the user provides any human signal. Langfuse calls these "scores"; we map our 👍/👎 onto a numeric score (`-1`, `+1`).

This is why the chosen scope is **Hub + smart clients** — not because we're adding code in voice-transcriber as part of *this* plan, but because the hub's contract needs to support it from day one. Clients adopt at their own pace by following the documented header convention.

---

## Decision

Decided up front, with reasoning preserved so future-you (or a teammate) understands why.

### Stack: OpenTelemetry SDK + Langfuse (self-hosted via docker-compose)

Three options were on the table:

| Option | Stack | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A** | OTel SDK + **Langfuse** (or Phoenix) | LLM-aware UI out of the box; OSS; OTLP-native; recognizable career artifact ("yes I run Langfuse"). | One Docker stack to maintain (small, but non-zero). | ✅ **Chosen** |
| B | OTel SDK + **Grafana stack** (Tempo/Prometheus/Loki + Grafana) | Universally recognized SRE stack; max flexibility. | 4+ containers; no LLM-native UI — you build dashboards yourself; overkill for one developer. | Rejected — heavier than needed; the LLM-specific UX is exactly what we want to learn. |
| C | OTel SDK + **local DuckDB sink** + Streamlit dashboard | Zero Docker; everything in this repo; single-machine. | Build the dashboard yourself; no community-shared mental model. | Rejected as primary, but the OTel layer means we *could* fall back to this later by swapping the exporter — no code rewrite. |

**Why A:** the OTel SDK is the portable, transferable skill. The destination (Langfuse) is a tool worth learning specifically because every "are we sure the new model is better?" enterprise conversation in 2026 is being held in front of a Langfuse-shaped UI. Doing this once, on a project we own end-to-end, is the playbook.

**Lock-in risk:** essentially zero. If Langfuse disappoints in 6 months, the hub-side instrumentation is unchanged — we just point the OTLP exporter at Phoenix or Grafana instead.

### Scope: Hub + smart clients

Three options were on the table:

| Option | Coverage | Verdict |
|---|---|---|
| 1 | Hub HTTP path only (`server.py` + upstream callers) | Too narrow — closes the door on cross-process traces. |
| 2 | Hub + Streamlit + tray (instrument the admin UI too) | Out of scope — admin UI activity is not the bottleneck and would inflate the dataset with noise. |
| 3 | Hub + smart-client contract (accept `traceparent`, expose feedback endpoint) | ✅ **Chosen** |

**Why 3:** the entire point of going through this exercise is to be able to answer questions that span the whole system, including "the user tapped 👎 — what was the actual prompt/response?" That requires the trace ID to travel from client to hub. Defining the contract now (even before any client adopts it) is free; retrofitting later is painful.

We will *not* edit the Streamlit admin UI, the tray launcher, or any client code in this plan. The Streamlit app will get one new read-only "Telemetry" tab (Phase 5) that links out to Langfuse — that is the full extent of UI changes in this repo.

---

## Architecture

```
   voice-transcriber       openClaw          curl / SDK
        │                     │                    │
        │  HTTP +             │                    │
        │  traceparent +      │                    │
        │  X-Trace-Id         │                    │
        ▼                     ▼                    ▼
 ┌──────────────────────────────────────────────────────┐
 │  FastAPI hub (src/server.py)                         │
 │  ┌─────────────────────────────────────────────┐     │
 │  │ OpenTelemetry middleware                    │     │
 │  │  - extract / generate trace_id              │     │
 │  │  - start root span: "POST /v1/messages"     │     │
 │  │  - attrs: gen_ai.system, gen_ai.request.*   │     │
 │  └─────────────────────────────────────────────┘     │
 │           │                                          │
 │           ▼  routes by model →                       │
 │  ┌──────────────┐  ┌────────────────┐  ┌──────────┐  │
 │  │ claude_cli   │  │ openai_upstream│  │ whisper  │  │
 │  │ (subprocess) │  │ (httpx + SSE)  │  │ proxy    │  │
 │  │              │  │                │  │          │  │
 │  │ child span:  │  │ child span:    │  │ ...      │  │
 │  │ "claude -p"  │  │ "llama-server" │  │          │  │
 │  └──────────────┘  └────────────────┘  └──────────┘  │
 │           │                                          │
 │  + POST /api/trace/{id}/feedback                     │
 │  + X-Trace-Id response header                        │
 └───────────────┬──────────────────────────────────────┘
                 │ OTLP (gRPC, localhost:4317)
                 ▼
 ┌──────────────────────────────────────────────────────┐
 │  Langfuse (docker-compose, localhost:3000)           │
 │   - traces UI                                        │
 │   - metrics charts                                   │
 │   - prompt/response inspector                        │
 │   - scores (= future eval ratings, thumbs)           │
 │   ─ backed by Postgres + Clickhouse + Redis          │
 └──────────────────────────────────────────────────────┘
```

Notes on the diagram:

- The OTel SDK is in-process inside the FastAPI hub. There is no separate `otel-collector` daemon in this design — Langfuse accepts OTLP directly, so a collector would be one extra hop for no value. (We can add a collector later if we want sampling, batching, or fan-out to multiple backends.)
- The OTLP connection is `localhost`-only. Langfuse runs on the same machine.
- The Streamlit app and tray launcher are deliberately not on this diagram. They are not instrumented in this plan.

---

## Phased plan

Each phase is independently deployable and verifiable. Don't start Phase N+1 until Phase N's verification passes.

### Phase 0 — Langfuse stack + dependencies (local, isolated)

**Difficulty:** 1/5 · **Time:** ~1 hour · **Risk:** none — purely additive, no hub code changes

- Add a `docker/langfuse/` directory containing the [official Langfuse self-hosted docker-compose](https://langfuse.com/self-hosting/docker-compose) (Langfuse v3 = web + worker + Postgres + Clickhouse + Redis + MinIO).
- Add `LANGFUSE_*` env var template to `.env.example`. Real values go in `.env` (gitignored, never committed) — public/secret keys are generated by Langfuse on first boot.
- Add a top-level `start_langfuse.bat` / `.sh` shortcut.
- Add OTel SDK + instrumentation libs to `requirements.txt`:
  - `opentelemetry-api`
  - `opentelemetry-sdk`
  - `opentelemetry-exporter-otlp-proto-grpc`
  - `opentelemetry-instrumentation-fastapi`
  - `opentelemetry-instrumentation-httpx` (for `openai_upstream.py`)
  - `opentelemetry-instrumentation-logging` (correlate logs with trace IDs)
- Pin versions following the existing `requirements.txt` policy (read the file first — see CLAUDE.md "Versioning policy").

**Verification:** `docker compose up` in `docker/langfuse/` brings the stack online. Browse to `http://localhost:3000`, complete first-run setup, confirm a project exists and a public/secret key pair has been generated. No hub code is touched yet — running `pytest` should still pass.

---

### Phase 1 — OTel bootstrap inside the hub

**Difficulty:** 2/5 · **Time:** ~2 hours · **Risk:** low — dead code until middleware is wired in Phase 2

- New module `src/observability.py` exposing `init_otel(service_name: str) -> None` that:
  - Reads `OTEL_*` env vars (standard OTel config — `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, etc.).
  - Configures a `TracerProvider` with a `BatchSpanProcessor` exporting via OTLP/gRPC.
  - Configures a `MeterProvider` for metrics with the same exporter.
  - Configures the logging instrumentation so every log line carries `trace_id` and `span_id` (the existing `logging` calls in `server.py` need no edits — the instrumentation injects via the LogRecord).
  - Is **idempotent** and **no-op when `OTEL_SDK_DISABLED=true`** (so tests and dev can opt out).
- Call `init_otel("local-llm-hub")` from `src/server.py` startup — but no spans are created yet by hub code.
- Update `.env.example` with the OTel vars and Langfuse OTLP endpoint snippet.

**Verification:** start the hub. `OTEL_SDK_DISABLED=true` → no behaviour change at all. `OTEL_SDK_DISABLED=false` (default) → hub starts cleanly, no errors, but Langfuse trace explorer still empty (we haven't instrumented anything yet). Run `pytest`; nothing should regress.

---

### Phase 2 — Hub HTTP path instrumentation

**Difficulty:** 2/5 · **Time:** ~3 hours · **Risk:** medium — touches the request handlers; needs careful PII handling

- Add the FastAPI auto-instrumentation: `FastAPIInstrumentor.instrument_app(app)`. This gives us a root span per request *for free*, with HTTP attrs (`http.method`, `http.route`, `http.status_code`).
- In each request handler (`/v1/messages`, `/v1/chat/completions`, `/v1/models`), add LLM-specific attributes to the root span using **GenAI semconv**:
  - `gen_ai.system` — derived from the routed backend (`anthropic`, `llama_cpp`, etc.)
  - `gen_ai.request.model` — the value of `request.model`
  - `gen_ai.request.temperature`, `.top_p`, `.max_tokens` — when present
  - `gen_ai.operation.name` — `"chat"` for messages/completions, `"models.list"` for `/v1/models`
- W3C trace context propagation: `FastAPIInstrumentor` handles `traceparent` extraction natively. Confirm it does, and add a custom step to also accept a bare `X-Trace-Id` header for clients that don't speak W3C (we generate a synthetic `traceparent` from it).
- After the response is built, add `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` to the span as available.
- Add the `X-Trace-Id` response header on every response (success or error), value = the current trace ID. Clients use this to attach feedback later.

**PII decision (must be made before this phase ships):** how much of the prompt and response do we capture?

- **Option:** capture full input/output text as span attributes (`gen_ai.prompt`, `gen_ai.completion`).
- **Option:** capture **only hashes** of input/output (BLAKE2 or SHA-256, truncated).
- **Option:** capture full text but only for traces tagged `client=tray` or `dev=true`; hash everything else.

Default for this plan: **capture full text by default**, with an env flag `OTEL_HASH_PROMPTS=true` to switch to hash-only. Rationale: this is a personal hub on `localhost`, the value of full-text capture (debugging, eval prep) is high, and the leak surface is one local DuckDB/Postgres on a machine you control. The flag exists for the day someone runs this in a less trusted environment.

**Verification:** hit the hub from `curl` with no headers — Langfuse shows a trace with model, latency, tokens, full prompt and response. Hit it with a `traceparent` header from a manual test script — Langfuse shows the trace under the parent's trace ID. Hit it with `OTEL_HASH_PROMPTS=true` — Langfuse shows hashes instead of text.

---

### Phase 3 — Upstream caller spans

**Difficulty:** 3/5 · **Time:** ~4 hours · **Risk:** medium — `openai_upstream.py` has streaming SSE, which needs care

The root span tells us how long the *whole* request took, but most of that time is upstream. We need child spans for the upstream call so we can answer "was the slow part the subprocess spawn or the model itself?"

- **`src/claude_cli.py`** — wrap the subprocess invocation in a span `claude_cli.invoke`, with attributes `claude_cli.subprocess_pid`, `claude_cli.argv_hash`, `claude_cli.exit_code`, `claude_cli.stderr_bytes`. Span duration = end-to-end subprocess time (the existing 1–2 s spawn overhead becomes a measurable, charted thing).
- **`src/openai_upstream.py`** — `httpx` is auto-instrumented by `opentelemetry-instrumentation-httpx`, which gives us spans for outgoing HTTP calls automatically. On top of that, we add LLM-specific attributes to the same span (`gen_ai.system=llama_cpp`, `gen_ai.request.model`, etc.).
- **Streaming path** — in `iter_cleaned_sse`, add **span events** at first-byte and last-byte:
  - `event: "first_token"` at the first non-empty content chunk → gives us **TTFT (time-to-first-token)**, which is *the* number that matters for UX.
  - `event: "last_token"` at stream end → gives us TPS (`output_tokens / (last_token_ts - first_token_ts)`).
  - Span attributes `gen_ai.response.time_to_first_token_ms` and `gen_ai.response.tokens_per_second` are computed from these.
- **`src/whisper_translate_proxy.py`** — wrap proxy decisions in a span `whisper_translate.proxy`, with `whisper_translate.cold_start=true|false` so we can answer "how often does the lazy-load fire?"

**Verification:** dictate a take through voice-transcriber pointing at the hub, polish it with each of `claude-haiku-4-5`, `qwen3.5-4b`, `gemma4-26b-a4b-it`. In Langfuse, each polish call shows a tree: root span → upstream span (with TTFT/TPS for the streaming ones) → done. The Claude path shows the subprocess spawn overhead distinct from the model time.

---

### Phase 4 — Metrics + smart-client feedback endpoint

**Difficulty:** 2/5 · **Time:** ~3 hours · **Risk:** low

**Metrics** — emit OTel metrics in parallel to traces. Traces are per-request and can be sampled; metrics are pre-aggregated and always exact.

- `gen_ai.client.operation.duration` — histogram, labelled by `gen_ai.request.model`, `gen_ai.system`, `error.type`. Standard GenAI semconv metric.
- `gen_ai.client.token.usage` — counter for input and output tokens, labelled by `gen_ai.request.model`, `gen_ai.token.type` ∈ `{input, output}`.
- `hub.requests.total` — counter, labelled by `route`, `client` (from `X-Client-Id` header if the client sends one, else `unknown`).
- `hub.upstream.errors.total` — counter, labelled by `gen_ai.system`, `error.type`.

**Smart-client feedback endpoint:**

- `POST /api/trace/{trace_id}/feedback` accepting `{thumbs: -1 | 0 | 1, comment?: string}`.
- Translates the call into a Langfuse [score](https://langfuse.com/docs/scores) attached to the trace (Langfuse SDK `langfuse.score()`).
- Validates `trace_id` is well-formed; does **not** validate it exists (Langfuse score-by-trace-id is idempotent — late or missing traces just get an orphan score, which is fine).
- Fire-and-forget for the client: the endpoint returns 202 immediately, score upload is best-effort. The client must not block UX on the response.

**Documented client contract** — add `docs/clients-telemetry-contract.md`:

- "Send `traceparent` if you have one (W3C spec); otherwise generate a UUID4 and send it as `X-Trace-Id`. Read `X-Trace-Id` from the response. Use that ID when calling `/api/trace/{id}/feedback`."
- A 30-line Python example for voice-transcriber to copy. No code in voice-transcriber's repo as part of *this* plan — that's the consumer's adoption decision.

**Verification:** Langfuse Metrics tab shows histograms populating during a smoke run. `curl -X POST http://127.0.0.1:8000/api/trace/<some-id-from-a-recent-trace>/feedback -d '{"thumbs": 1}'` succeeds, and Langfuse shows the score on that trace within seconds.

---

### Phase 5 — Streamlit Telemetry tab + docs

**Difficulty:** 1/5 · **Time:** ~2 hours · **Risk:** none

- Add `app/views/telemetry.py` — a small read-only Streamlit page with:
  - Status badge: "Langfuse reachable at `localhost:3000`" (or red if down).
  - The five most recent traces (Langfuse SDK `get_traces(limit=5)`), with model, latency, status, and a click-out link to Langfuse.
  - Big button: "Open Langfuse →" linking to `http://localhost:3000`.
- Wire into `app/app.py` page nav.
- Update top-level `README.md`:
  - New "Observability" section explaining the OTel + Langfuse setup.
  - Update the "Backlog" — remove "Structured logging + `/metrics` for Prometheus-style observability" (now done, differently).
- Add `docs/changelog/<YYYY-MM-DD>-telemetry-langfuse.md` recording the decision and dating it.

**Verification:** the Telemetry tab loads, shows live traces, link works.

---

## Risks and gotchas

1. **PII in spans.** The default plan captures full prompts and outputs. On a personal `localhost` deployment this is fine. The moment this hub runs anywhere else (a shared dev box, a cloud VM), flip `OTEL_HASH_PROMPTS=true`. Document this prominently in the README.
2. **Langfuse Postgres bloat.** Langfuse stores everything by default. After 6 months on a busy hub the Postgres volume will be tens of GB. Decide a retention policy — Langfuse supports per-project TTLs in the UI. Default suggestion: 90 days for traces, indefinite for scores.
3. **OTel SDK overhead.** The `BatchSpanProcessor` is async and very cheap (single-digit ms per request, batched). Verify it stays that way under streaming load — the SSE path is hot. If it ever shows up in profiles, the answer is to set `OTEL_TRACES_SAMPLER=parentbased_traceidratio` with a ratio < 1.0.
4. **`claude -p` doesn't return token counts reliably.** Some `claude_cli` invocations won't have `gen_ai.usage.*` populated. Make those attributes optional and document the gap. Don't fake numbers.
5. **Streaming + spans.** The classic mistake: closing the span when the response object is constructed, before the SSE generator has actually run. The span needs to stay open until the stream ends. Use a dedicated context manager or background task; do not use the request-scoped span for the streaming portion.
6. **Trace ID confusion.** OTel uses 128-bit hex trace IDs (W3C). Some clients (and the original cross-repo doc) use UUID4. The hub accepts both via `X-Trace-Id`, mapping UUID4 to a synthetic OTel trace ID. Document the mapping; it's not lossless but it's deterministic.
7. **Cost (Claude path) is hard.** The Claude CLI path uses your subscription, so per-call cost is nominally zero. Don't fabricate `gen_ai.usage.cost_usd` — leave it null on that path. Only populate cost on paths where the upstream actually returns it (which today is none of them).
8. **Local model non-determinism.** `llama.cpp` is mostly deterministic at temperature 0; nothing else is. Don't be surprised when the same input shows different outputs across two traces. This is a property of the world, not a bug.
9. **Docker isn't free on Windows.** Langfuse via Docker Desktop uses ~2 GB RAM idle. On the reference machine (24 GB? check `docs/system-specs/`) this is fine; on the Mac mini it might pinch when the model is also loaded. Document the RAM cost and offer an "off" mode (just don't start Langfuse — the OTel SDK silently no-ops when the OTLP endpoint is unreachable, after a short connect timeout).
10. **Verification load.** Don't trust a single smoke trace to validate Phase 3. Run at least 10 calls per backend, including one error case (deliberately misformatted request) and one streaming case, before declaring Phase 3 done.

---

## Decision points still open

These are *not* blockers for starting Phase 0, but should be settled by the time the corresponding phase lands.

1. **Sampling.** Default to 100% sampling (every request traced). If volume becomes an issue, switch to head-based sampling at the SDK. Tail-based sampling would require a collector — out of scope for this plan.
2. **Multi-host telemetry.** When the Mac mini or a sibling Windows box runs its own hub, do they ship traces to the same Langfuse, or each have their own? Default: **same Langfuse**, distinguished by `service.instance.id` (auto-set by OTel from hostname). Revisit if cross-host traces become noisy.
3. **Authentication on the feedback endpoint.** Today the hub has no auth (LAN-only). The feedback endpoint inherits that. If we ever expose the hub through a Cloudflare tunnel, the feedback endpoint needs at least a shared secret — note in the README that it's currently unauthenticated.
4. **Prompt cache visibility.** Anthropic's API returns cache-hit info; the CLI path may or may not. When the CLI exposes it (or when we add a direct API path), surface it as `gen_ai.usage.cache_read_input_tokens`.

---

## What this earns you (career-side)

- A working, demoable answer to *"how do you observe an LLM system in production?"* backed by code you wrote.
- Hands-on with OpenTelemetry — the most-asked-about observability skill in 2026 platform/SRE/AI-platform interviews.
- Hands-on with Langfuse — a tool enterprise AI teams are actively adopting, so the screenshots and vocabulary are recognizable to anyone you'd talk to.
- The data foundation for the eval phase later. When the time comes to add golden datasets and regression gates, every trace already has the trace ID, model, prompt, response, and a place to attach scores. The eval system is then just a script that reads Langfuse and writes scores back.
- A defensible answer to *"why OTel and not <vendor X>?"* — vendor neutrality, OSS ecosystem, GenAI semconv as the industry's chosen abstraction.

---

## Glossary

- **CNCF** — Cloud Native Computing Foundation. The standards body that "graduated" OTel, meaning it's the official, vendor-neutral standard.
- **GenAI semantic conventions** — OTel sub-spec defining standard attribute names for LLM telemetry. [Spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
- **OTLP** — OpenTelemetry Protocol. The wire format (gRPC or HTTP) the SDK uses to ship data to a backend.
- **Span** — a unit of work inside a trace. Has a name, start time, end time, attributes, and zero or more child spans.
- **TTFT** — time-to-first-token. The latency a user *feels* when the model starts streaming, vs. the total round-trip.
- **TPS** — tokens per second. Streaming throughput once generation has begun.
- **W3C Trace Context** — the standard for `traceparent` HTTP headers that lets traces span multiple processes/services.
- **PII** — personally identifiable information. In this context, means: anything from the user's actual prompt or the model's response that you would not want a third party to read.
- **Langfuse** — the chosen self-hosted LLM observability backend. [langfuse.com](https://langfuse.com/).
- **Phoenix** — Arize's open-source LLM observability tool. Lighter alternative to Langfuse, considered and rejected for this plan.
- **Score** — Langfuse's term for a numeric label attached to a trace. Used here for 👍/👎 feedback; will later carry automated eval scores.

---

## Appendix A — example trace shape (target)

What a single `/v1/messages` call to `qwen3.5-4b` should look like in Langfuse after Phase 3:

```
Trace abc123def456...  (4-character trace_id prefix shown)
├── Root span: POST /v1/messages                          1620 ms
│   attrs:
│     http.method = POST
│     http.route = /v1/messages
│     http.status_code = 200
│     gen_ai.system = llama_cpp
│     gen_ai.request.model = qwen3.5-4b
│     gen_ai.request.max_tokens = 512
│     gen_ai.request.temperature = 0.7
│     gen_ai.usage.input_tokens = 248
│     gen_ai.usage.output_tokens = 312
│     gen_ai.response.finish_reasons = ["stop"]
│     client.id = voice-transcriber
│     gen_ai.prompt = "Polish this transcript: ..."
│     gen_ai.completion = "Here is the polished version: ..."
│
└── Child span: HTTP POST 127.0.0.1:8088/v1/chat/completions  1580 ms
    attrs:
      http.url = http://127.0.0.1:8088/v1/chat/completions
      http.status_code = 200
      gen_ai.response.time_to_first_token_ms = 142
      gen_ai.response.tokens_per_second = 217.4
    events:
      - "first_token" @ +142 ms
      - "last_token"  @ +1580 ms

Scores:
  thumbs = +1  (attached 4 minutes later via /api/trace/{id}/feedback)
```

---

## Appendix B — example client snippet (voice-transcriber side)

For the eventual client adoption, here's the pattern the docs will recommend. **No code in this repo or in voice-transcriber as part of this plan** — this is documentation for whoever does the client integration later.

```python
import uuid
import httpx

# Generate a trace ID per polish call
trace_id = uuid.uuid4().hex

resp = httpx.post(
    "http://127.0.0.1:8000/v1/messages",
    json={"model": "qwen3.5-4b", "messages": [...]},
    headers={"X-Trace-Id": trace_id, "X-Client-Id": "voice-transcriber"},
)

# Use the *server's* canonical trace ID for any follow-up
canonical_trace_id = resp.headers.get("X-Trace-Id", trace_id)
polished = resp.json()

# ... user sees the result and taps 👎 ...

httpx.post(
    f"http://127.0.0.1:8000/api/trace/{canonical_trace_id}/feedback",
    json={"thumbs": -1, "comment": "removed too much"},
    timeout=2.0,  # fire-and-forget; do not block the UX
)
```

That's the entire client-side surface area. Two headers on the way in, one optional POST after the user reacts.
