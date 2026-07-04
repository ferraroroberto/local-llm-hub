# Orpheus TTS throughput — why ~150 tok/s is the floor

Why Orpheus speech synthesis takes ~2 s for a short phrase, what was
measured to find a faster path, and why the answer is "the engine is
near its hardware ceiling — lean on streaming, not flags." Investigation
for issue #105. Pairs with [add-tts.md](add-tts.md) (how Orpheus slots
into the hub) and the streaming work in #102 (perceived latency).

## TL;DR

- Orpheus runs as a GGUF on a loopback `llama-server` child; total
  synthesis time is set by how fast that child generates SNAC audio
  tokens (`src/tts_engines/orpheus.py::OrpheusEngine`).
- On the reference GPU (RTX 5060 Ti, 16 GB) it generates **~150 tok/s**.
- That rate is **memory-bandwidth bound**, not a missing flag. The model
  is fully GPU-offloaded and llama.cpp already auto-enables flash
  attention. Adding `--flash-attn on`, `-b/-ub` batch sizes, or
  `--no-mmap` leaves the rate unchanged (measured below).
- ~150 tok/s is **~65 % of this card's bandwidth ceiling** for a
  ~1.94 GB resident model. The only physically faster route is a lower
  quant, which would regress SNAC audio quality — out of scope per the
  issue's acceptance ("no regression to streamed output quality").
- **No llama-server flags were changed.** Time-to-first-audio is already
  addressed by streaming (#102); total synthesis time is at the
  hardware floor.

## How to reproduce

`scripts/bench_orpheus.py` is the committed harness.

```powershell
# Flag sweep — spawns a scratch llama-server per flag set on :18099,
# hits /completion with the canonical Orpheus prompt, reports tok/s.
.venv\Scripts\python.exe scripts\bench_orpheus.py --reps 5

# End-to-end — times the live hub's POST /v1/audio/speech (:8000).
.venv\Scripts\python.exe scripts\bench_orpheus.py --hub-e2e --reps 5
```

The phrase defaults to `"this is a test"` (the ~1.8 s clip #105 cites).
Measurements below were taken on the reference GPU with the normal
autostart set resident (qwen3.5-4b + whisper turbo + whisper medium),
i.e. realistic contention, not an idle card.

## Measurement: flag sweep

Median of 5 reps each, scratch `llama-server`, same Orpheus Q4_K_M GGUF.
The base flags (`-ngl 99 --no-webui -c 8192`) are constant; each row
adds one variable.

| flag set                | tok/s | wall ms | vs base |
| ----------------------- | ----: | ------: | ------: |
| baseline (`-c 8192`)    | 150.9 |  1674.2 |   1.00x |
| `--flash-attn on`       | 146.8 |  1548.6 |   0.97x |
| ` + --no-mmap`          | 149.0 |  1631.2 |   0.99x |
| ` + -b 2048 -ub 512`    | 148.1 |  1566.2 |   0.98x |
| ` + greedy sampler`     | 146.0 | 28311.9 |   0.97x |

Every variant lands within noise of the baseline. Two notes:

- **flash attention is already on.** The llama-server load log prints
  `flash_attn = auto` → `set to enabled`, so passing `--flash-attn on`
  explicitly is a no-op on this build. That is why it does not help.
- **greedy sampling is not viable.** With `temperature 0 / top_k 1` the
  model stopped emitting its end token and ran to the 4096-token
  `n_predict` cap (28 s) — and was no faster per token. The production
  sampler (`temperature 0.6 / top_p 0.9 / repeat_penalty 1.1`) stays.

## Measurement: the engine is fully offloaded

From the `llama-server` load log for the Orpheus GGUF:

```
model params          = 3.78 B   (type 3B)
load_tensors: offloaded 29/29 layers to GPU
load_tensors:   CUDA0 model buffer size =  1987.29 MiB
load_tensors: CPU_Mapped model buffer size =  258.63 MiB   (token embeddings)
llama_kv_cache: CUDA0 KV buffer size      =   896.00 MiB   (n_ctx 8192)
sched_reserve:  CUDA0 compute buffer size =   318.52 MiB
llama_context:  flash_attn = auto -> enabled
```

All 29 layers are on the GPU; nothing is spilling to CPU at generation
time (the 258 MiB CPU-mapped block is the input token-embedding table,
read once per token for the single new token — negligible). `-ngl 99` is
doing its job.

`--parallel 1` was tried and **reverted**: KV is sized by `n_ctx`, not
per slot, so the cache is 896 MiB whether the server runs 1 slot or the
default 4 — single-slot frees no VRAM here. `-c 8192` is likewise kept:
a smaller context would not free meaningful VRAM and 8192 is needed for
longer inputs (Orpheus emits ~107 audio tokens per second of speech, so
8192 ≈ 76 s of audio headroom).

## Why ~150 tok/s is the ceiling

Autoregressive token generation is memory-bandwidth bound: each token
streams the resident weights through the GPU's memory bus once.

- Resident weights: **~1.94 GB** (`CUDA0 model buffer 1987 MiB`).
- RTX 5060 Ti memory bandwidth: **~448 GB/s** (GDDR7, 128-bit bus).
- Theoretical ceiling: 448 / 1.94 ≈ **231 tok/s**.
- Measured: **~150 tok/s ≈ 65 %** of theoretical — the normal real-world
  fraction for llama.cpp single-stream decode.

The 5060 Ti is a narrow-bus (128-bit) Blackwell card; its bandwidth is
roughly a third of a 5090's (~1792 GB/s). The intuition in #105 that "a
Blackwell-class GPU should be several times faster" holds for the big
cards, not for this one — here ~150 tok/s is expected, not a regression.

To reach the issue's >300 tok/s target you would need to roughly halve
bytes-per-token, i.e. a ~2-bit quant. That is far too lossy for SNAC
audio-token fidelity and would audibly degrade the voice, so it is ruled
out by the "no quality regression" acceptance criterion.

## End-to-end latency

Live hub `POST /v1/audio/speech` for `"this is a test"`, median of 5:

| measurement              | median e2e |
| ------------------------ | ---------: |
| before (default flags)   |   2074 ms  |
| after (no flag change)   |   2038 ms  |

Identical within run-to-run noise (output length varies slightly per
synthesis because sampling is stochastic). Of that ~2 s, generation is
~1.67 s (210 tokens at ~150 tok/s) and SNAC decode + hub overhead is the
remaining ~0.4 s. Generation dominates and is at the hardware floor.

## Conclusion

The total synthesis time is set by the loopback `llama-server`'s
generation rate, which on this GPU is at its memory-bandwidth ceiling
with the model fully offloaded and flash attention already on. No
`llama-server` flag raises it, and the only faster path (a lower quant)
violates the quality bar. The correct lever for user-perceived latency
is **streaming** (#102: first audio in ~1 s while the rest generates),
not engine flags. A code change was deliberately *not* made beyond an
explanatory comment in `_spawn_llama`; re-run `scripts/bench_orpheus.py`
after any future model or GPU swap to re-check the floor.
