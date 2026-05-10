# What we did — 2026-05-10 (later)

Bumped the qwen llama-server context window from **16 K → 64 K** to
support longer openClaw conversations.

## Change

`config/models.yaml` qwen.args:

| arg | before | after |
|---|---|---|
| `-c` | `16384` | `65536` |
| `--flash-attn` | (absent) | `on` |
| `--parallel` | (auto, 4) | `1` |

Other args unchanged (`--jinja`, `-ngl 99`, `--alias qwen3.5-9b`,
`--reasoning-format none` from the earlier landing today).

## Why these three together

- **`-c 65536`** is the actual ask — 4× the previous context.
- **`--flash-attn on`** is required at 64 K. The fused attention
  kernel cuts the attention scratch buffer dramatically and is
  faster on RTX cards. No quality cost.
- **`--parallel 1`** because openClaw is the primary client and we'd
  rather hand it the entire 64 K window than split the KV cache
  across 4 slots that compete. Removes any chance two openClaw calls
  trip over each other's KV.

## VRAM accounting (16 GB RTX 5060 Ti)

Estimate going in (before measuring):

| | FP16 KV |
|---|---|
| 16384 | ~1.2 GB |
| 65536 | ~4.7 GB |
| 131072 | ~9.5 GB |

Actual numbers from the qwen boot log after the change:

```
ggml_cuda_init: ... Total VRAM: 16310 MiB
load_tensors:        CUDA0 model buffer size =  4861.28 MiB
llama_kv_cache:      CUDA0 KV buffer size  =  2048.00 MiB
sched_reserve:       CUDA0 compute buffer  =   493.00 MiB
llama_memory_recurrent: CUDA0 RS buffer    =    50.25 MiB
llama_params_fit_impl: projected ... 7452 MiB used vs. 15106 MiB free
                       will leave 7653 MiB free
```

So actual KV is **~2 GB at 64 K**, less than half my up-front
estimate. Reason: Qwen3-9B is a hybrid architecture — only 8 of the
40 layers use full attention; the rest are recurrent and use a
small `RS` buffer instead of a per-token KV. That changes the
scaling: KV grows linearly only with the 8 attention layers, not
all 40.

Free VRAM after load: **~7.6 GB**. Plenty of room to push further if
needed.

## How far we could push without quantizing KV

Linear extrapolation of the measured KV cost (≈32 KB per token on 8
attention layers, fp16):

| `-c` | KV est. | Total est. | Fits in 16 GB? |
|---|---|---|---|
| 65536 (now) | 2.0 GB | 7.5 GB | ✅ measured |
| 131072 | 4.1 GB | 9.6 GB | ✅ comfortable |
| 196608 | 6.1 GB | 11.6 GB | ✅ tight |
| 262144 (full trained) | 8.2 GB | 13.7 GB | ⚠️ very tight, leave nothing for compute peaks |

So **128 K is a no-brainer if 64 K turns out to be too small**, just
swap `65536 → 131072` in the YAML. **256 K** (the model's full
trained context) likely needs `-ctk q8_0 -ctv q8_0` (Q8 KV
quantization) to leave compute headroom.

## Validation

- Killed the prior qwen on :8081 (PID 43240), restarted via
  `python -m src.run_backend qwen`, polled `/v1/models` until
  ready (~few seconds — KV alloc isn't slow on this GPU).
- Verified boot log: `n_ctx = 65536`, `n_seq_max = 1`,
  `flash_attn = enabled`, `kv_unified = false`, single slot.
- Smoke through the hub: `POST :8000/v1/chat/completions`
  qwen3.5-9b non-stream "what is 2+2?" → `200`,
  `content = "\n\n2+2 equals 4."`.

## What this does NOT change

- Hub code is untouched — this is purely a launcher-side knob.
- GLM, Gemma 4 E4B, Gemma 4 26B-A4B remain at their previous
  context budgets. Bump them similarly if a real workload demands
  it; don't preemptively widen everything (KV fights for the same
  VRAM as the model when you swap models in/out).
- `--reasoning-format none` and the `<think>` strip from the
  earlier landing today
  ([20260510-openai-streaming-and-think-strip.md](20260510-openai-streaming-and-think-strip.md))
  still apply — the new context window doesn't change how thinking
  is handled.
