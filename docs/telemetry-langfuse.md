# Telemetry & observability — OpenTelemetry + Langfuse

The hub emits OpenTelemetry traces, metrics, and log-record correlation
via OTLP/gRPC into a local Langfuse stack. Trace data is portable
(Langfuse is the durable store; the OTel side is vendor-neutral so the
hub can repoint at Phoenix / Grafana / Honeycomb later with one env
var change). Everything below is local-only and personal-scale by
design — no auth on the OTel pipe, default-on prompt capture, single-host
Langfuse.

## Quick start

```bat
:: 1. Start the stack (Docker Desktop must be running; pulls ~3 GB the first time)
start_langfuse.bat

:: 2. Open http://localhost:3000, create a user + project, copy the
::    public + secret keys.

:: 3. Drop the keys into .env at repo root:
::      LANGFUSE_PUBLIC_KEY=pk-lf-...
::      LANGFUSE_SECRET_KEY=sk-lf-...
::    The OTLP exporter uses these to authenticate against Langfuse's
::    /api/public/otel/v1/traces receiver; without them every span is
::    rejected and the Telemetry tab's deep-links land on
::    "Trace not found". The hub logs `auth=MISSING` at startup if either
::    is absent — easy to spot.

:: 4. Start the hub (or restart if it was already running so OTel picks up env)
run_hub.bat

:: 5. Open the admin SPA -> 📊 Telem tab
::    http://127.0.0.1:8000/admin/
```

The Telemetry tab shows: stack health, per-model leaderboard from the
hub's in-memory ring (so it works even when Langfuse is offline), and a
live trace feed with deep-links into the Langfuse UI.

## Accessing Langfuse from mobile / LAN / Tailscale / Cloudflare

The Telemetry tab's per-row 🔗 Langfuse button (and the header 🔗) build
the URL the **browser** will hit using this rule, in order:

1. If `LANGFUSE_PUBLIC_URL` is set in `.env`, use it verbatim. Right for
   anything that isn't `<same-host>:3000` — Tailscale Serve, Cloudflare
   Tunnel, a custom domain in front of a reverse proxy.
2. Otherwise reuse the hostname the SPA itself was loaded from and swap
   the port for `langfuse_port` (default `3000`). Works automatically
   across **localhost / LAN** because the hub and Langfuse share the
   machine — the hostname your browser used to reach `:8000` also
   reaches `:3000`.

For **Tailscale on mobile** the bare `<host>:3000` path *also* works in
principle, but Windows Firewall blocks inbound TCP 3000 by default
(only :8000 has a pre-existing exception from the hub's first-launch
prompt). Two ways to get past it:

### A. Open Windows Firewall for TCP 3000 (one-time, needs admin)

```powershell
New-NetFirewallRule `
  -DisplayName "Langfuse local (docker) — TCP 3000" `
  -Direction Inbound -Protocol TCP -LocalPort 3000 `
  -Action Allow -Profile Private,Domain
```

Same posture as the hub's :8000 rule. After that, your phone hits
`http://tower:3000/...` over Tailscale and Langfuse responds. Leave
`LANGFUSE_PUBLIC_URL` empty — the SPA's same-host port-swap is enough.

### B. Tailscale Serve in front of Langfuse (recommended)

No admin needed, runs over Tailscale's identity-aware proxy, and you
get a real TLS cert for free. Run **once** on the host machine:

```powershell
tailscale serve --bg --https=3000 http://localhost:3000
```

This exposes Langfuse at `https://<your-tailnet-host>.ts.net:3000/` to
every device on your tailnet. Verify with:

```powershell
tailscale serve status
# https://tower.tail1121fd.ts.net:3000 (tailnet only)
# |-- / proxy http://localhost:3000
```

Then set the public URL in `.env` so the Telemetry tab's deep-links
land at the Tailscale URL instead of the local one:

```
LANGFUSE_PUBLIC_URL=https://tower.tail1121fd.ts.net:3000
```

…and restart the hub. Tear the serve back down with
`tailscale serve --https=3000 off`.

### Cloudflare Tunnel

Same `LANGFUSE_PUBLIC_URL` slot — point it at the tunneled hostname.
Cloudflare needs its own ingress rule for Langfuse (the hub's tunnel
already covers :8000). One-line snippet for `cloudflared.yml`:

```yaml
ingress:
  - hostname: langfuse.example.com
    service: http://localhost:3000
  - hostname: hub.example.com
    service: http://localhost:8000
  - service: http_status:404
```

Then `LANGFUSE_PUBLIC_URL=https://langfuse.example.com`.

### Login is per-browser

Langfuse's sign-in session is stored as a cookie scoped to whichever
hostname you logged in on. The first time you open the deep-link on a
new device (or via a new hostname — `tower.ts.net` vs `localhost`),
Langfuse will show its sign-in screen rather than the trace. Sign in
once; the cookie persists.

## What's captured

Every routed request creates an OTel span tree:

```
Root span: POST /v1/messages                                1620 ms
  gen_ai.system = llama_cpp
  gen_ai.request.model = qwen3.5-4b
  gen_ai.request.max_tokens = 512
  gen_ai.usage.input_tokens = 248
  gen_ai.usage.output_tokens = 312
  client.id = voice-transcriber               (from X-Client-Id)
  gen_ai.prompt = "..."                        (full text by default)
  gen_ai.completion = "..."
  +-- Child: HTTP POST 127.0.0.1:8088/v1/chat/completions  1580 ms
        gen_ai.response.time_to_first_token_ms = 142   (streaming only)
        gen_ai.response.tokens_per_second = 217.4      (streaming only)
        events: [first_token @ +142 ms, last_token @ +1580 ms]
  +-- Child: claude_cli.invoke                            1450 ms   (Claude path)
        claude_cli.exit_code = 0
        claude_cli.argv_hash = 3a5b8c
  +-- Child: gemini_cli.invoke                            2100 ms   (Gemini path)
        gemini_cli.model_switched = true
        events: [model_switch]
```

Metrics (exported every 15 s):

| Name | Type | Labels |
|---|---|---|
| `gen_ai.client.operation.duration` | histogram (ms) | `gen_ai.request.model`, `gen_ai.system`, `error.type` |
| `gen_ai.client.token.usage` | counter | same + `gen_ai.token.type` (input/output) |
| `hub.requests.total` | counter | `route`, `client` |
| `hub.upstream.errors.total` | counter | `gen_ai.system`, `error.type` |

## Trace ID contract

Two ways for a client to correlate the call later (for feedback,
debugging, or cross-system tracing):

- **W3C `traceparent`** — pass it on the request and the hub uses it
  natively. Standard OpenTelemetry propagation; works out of the box.
- **`X-Trace-Id`** — any UUID4, hyphenated or not, or a 32-char hex
  string. The hub maps your value deterministically (BLAKE2b → 16
  bytes) onto an OTel trace ID, so two requests with the same
  `X-Trace-Id` land in the same Langfuse trace.

Every response carries `X-Trace-Id: <32 hex>` set to the actual OTel
trace ID. Read it after the call to attach feedback later — see
[`clients-telemetry-contract.md`](clients-telemetry-contract.md) for a
copy-paste Python example.

## Prompt / completion capture (PII)

By default the hub stores **raw prompt + completion text** as span
attributes — this is a personal-localhost hub, debug value is high.

Flip to BLAKE2b hashes any time you bind beyond loopback or
share-screen something you might not want fully captured:

```bat
set OTEL_HASH_PROMPTS=true
```

(Or set it in `.env`.) Restart the hub. The Telemetry tab's "PII" chip
flips to `hashed` so you can tell at a glance.

## Disabling telemetry entirely

```bat
set OTEL_SDK_DISABLED=true
```

Hub keeps serving traffic; spans + metrics become no-ops; the
Telemetry tab shows "OTel disabled" but the in-memory leaderboard
still works (it doesn't depend on OTel).

## Feedback / scores

The Telemetry tab's 👍 / 👎 buttons POST to
`/admin/api/trace/{trace_id}/feedback`. The hub forwards to Langfuse's
`score()` API in a background task (returns 202 in <50 ms).

Same endpoint works for clients — see the client contract doc.

## Architecture

```
   voice-transcriber       openClaw          curl / SDK
        |                     |                    |
        |  HTTP + traceparent + X-Trace-Id         |
        v                     v                    v
 +------------------------------------------------------+
 |  FastAPI hub (src/server.py)                         |
 |  - TraceIdHeaderMiddleware    (X-Trace-Id <-> OTel)  |
 |  - FastAPIInstrumentor        (root span per req)    |
 |  - GenAI attrs on handlers    (gen_ai.* semconv)     |
 |  +-> src/claude_cli.py        claude_cli.invoke span |
 |  +-> src/gemini_cli.py        gemini_cli.invoke span |
 |  +-> src/openai_upstream.py   httpx auto-span + TTFT |
 |  +-> whisper proxy            whisper.proxy span     |
 +-------------------+----------------------------------+
                     | OTLP/gRPC (localhost:4317)
                     v
 +------------------------------------------------------+
 |  Langfuse (docker compose, localhost:3000)           |
 |    Postgres + Clickhouse + Redis + MinIO             |
 +------------------------------------------------------+
                     ^
                     | langfuse.score() (background task)
                     |
 +------------------------------------------------------+
 |  POST /admin/api/trace/{id}/feedback                 |
 +------------------------------------------------------+
```

## Files

| Path | Role |
|---|---|
| `docker/langfuse/docker-compose.yml` | Local Langfuse v3 stack |
| `start_langfuse.bat` / `.sh` | Idempotent stack-start shortcut |
| `.env.example` | Schema for OTel + Langfuse env vars |
| `src/observability.py` | OTel bootstrap, GenAI helpers, metric instruments |
| `src/trace_id_middleware.py` | X-Trace-Id contract (in / out) |
| `app_web/routers/telemetry.py` | Health, trace feed, metrics, feedback endpoint |
| `app_web/static/telemetry.{js,css}` | SPA Telemetry tab |
| `docs/clients-telemetry-contract.md` | Client-side recipe |

## Limitations / known gaps

- **No streaming on `/v1/messages`** — the hub still returns a single
  JSON for Anthropic-shape stream requests, so TTFT/TPS only land for
  OpenAI-shape streams against local llama-server.
- **Claude `usage` is best-effort** — `claude -p` returns it sometimes;
  when absent we leave `gen_ai.usage.*` unset rather than fake zeros.
- **Gemini `usage` is always zero** — the `agy` CLI does not surface
  token counts at all.
- **Single-host Langfuse** — distinguished by `service.instance.id`
  (hostname-PID). Multiple hubs pointing at one Langfuse works fine.
- **`whisper_translate_proxy.py` is its own process** — it does not
  call `init_otel()`, so its internal `whisper_translate.proxy` span is
  only present when the proxy lives inside the hub (most setups).
