# Client-side telemetry contract

How to make your own scripts/agents/services correlate with traces in
the local-llm-hub's Langfuse — without taking a dependency on the OTel
SDK on the client side.

## What the hub does for you

For every request to `/v1/messages` or `/v1/chat/completions`:

- The hub creates an OpenTelemetry trace and emits it to Langfuse.
- The response carries `X-Trace-Id: <32 hex>` — the actual trace ID.
- If you pass `X-Client-Id: <name>`, the span gets a `client.id`
  attribute (handy for filtering "all voice-transcriber calls" in
  Langfuse search).

## Two ways to bring your own trace ID

| Header | When to use |
|---|---|
| **`traceparent`** (W3C) | Your client already speaks OpenTelemetry — pass it through and the hub will join the existing trace. |
| **`X-Trace-Id`** | Your client is a script with no OTel dependency. Mint a UUID4, pass it in, and the hub maps it deterministically to an OTel trace ID. |

Pick **one**. If you send both, `traceparent` wins.

## Copy-paste recipe (Python, ~30 lines)

```python
import uuid
import httpx

HUB = "http://127.0.0.1:8000"

trace_id = uuid.uuid4().hex                  # any UUID4 in any form
headers = {
    "X-Trace-Id": trace_id,
    "X-Client-Id": "voice-transcriber",      # optional: filter label in Langfuse
}

resp = httpx.post(
    f"{HUB}/v1/messages",
    json={
        "model": "qwen3.5-4b",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "Polish this transcript: ..."}],
    },
    headers=headers,
    timeout=120.0,
)
resp.raise_for_status()

# Always read the canonical trace ID from the response. The hub may map
# your X-Trace-Id deterministically — the response header tells you what
# the final OTel trace_id is, which is what Langfuse stores it under.
canonical_trace_id = resp.headers.get("X-Trace-Id", trace_id)
polished = resp.json()

# Later: the user reacts. Attach a score to the trace. Fire-and-forget —
# the endpoint returns 202 in <50 ms; the Langfuse upload happens in a
# background task. Do not block your UX on this.
httpx.post(
    f"{HUB}/admin/api/trace/{canonical_trace_id}/feedback",
    json={"thumbs": +1, "comment": "Captured the meaning well."},
    timeout=2.0,
)
```

## Feedback endpoint shape

```
POST /admin/api/trace/{trace_id}/feedback
Content-Type: application/json

{
  "thumbs":  -1 | 0 | +1,        // required
  "comment": "free text"         // optional, max 2000 chars
}
```

- `thumbs` becomes a Langfuse score named `thumbs` with value
  `-1 / 0 / 1`.
- The endpoint returns `{"accepted": true, ...}` with status `202` as
  soon as the body is validated; the actual Langfuse upload runs in a
  background task.
- Score uploads are best-effort: if Langfuse is offline or the API keys
  aren't set, the score is logged and dropped (no error to the
  client). Don't rely on feedback for correctness.

## Auth

The feedback endpoint inherits the hub's existing bearer-token auth:

- Loopback callers (same machine) bypass.
- LAN / Cloudflare tunnel callers must present
  `Authorization: Bearer <token>` — the token configured in
  `config/webapp_config.json`.

## Trace ID format

- **Accepted on input** via `X-Trace-Id`:
  - 32-char lowercase hex (a real OTel trace ID)
  - any UUID, hyphenated or compact
  - any other string is BLAKE2b-hashed into a 16-byte trace ID
    (useful for short stable IDs like "session-42")
- **Emitted in `X-Trace-Id` response header**: always 32-char lowercase
  hex (the actual OTel trace ID). Use this when posting feedback.

## What lands in Langfuse

Per call, you get a trace with:

- The full prompt + completion text (or BLAKE2b hashes when the hub is
  running with `OTEL_HASH_PROMPTS=true`)
- `gen_ai.request.model`, `gen_ai.system`, latency, token counts
- TTFT + tokens/second when the call went through the streaming path
- Whichever scores you POST through the feedback endpoint

Open the Telemetry tab in the hub admin SPA
(`http://127.0.0.1:8000/admin/`) for a live view of every trace as it
lands, with deep-links into the Langfuse UI for full inspection.
