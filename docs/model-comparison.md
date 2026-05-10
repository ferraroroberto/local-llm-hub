# Model comparison

Side-by-side technical snapshot of every model the hub currently
knows about. Designed to grow — one row per model. Add a row when
adding a new backend (registry entry + launcher + test + docs all
live elsewhere; this page is just the reference table).

Columns are deliberately short so the table renders legibly in
markdown. Where a precise number isn't meaningful for a backend
(e.g. Claude runs off-device), the cell says so directly.

## Local llama.cpp backends + Claude subscription

| Model (hub id) | Family | Params | Quant | GGUF size | Context | VRAM fit (16 GB) | Hub port | Typical tok/s* | References (official · card · benchmarks) |
|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-4-5` (aliases: `claude-sonnet-4-6`, `claude-opus-4-7`) | Claude (Anthropic) | n/a (cloud via `claude -p`) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription) | [anthropic.com/claude](https://docs.anthropic.com/en/docs/about-claude/models) · [llm-stats](https://llm-stats.com/models?provider=anthropic) |
| `qwen3.5-9b` | Qwen 3.5 (Alibaba; hybrid attn + recurrent) | 9 B dense | Q4_K_M | 5.3 GB | 65 536 (trained 262 144) | full GPU (`-ngl 99`) + `--flash-attn on`, single slot | 8081 | ~80–110 | [Qwen org on HF](https://huggingface.co/Qwen) · [GGUF we ship](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) · [MLX (Mac)](https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit) |
| `glm-4.5-air` | GLM-4.5-Air (Zhipu / zai-org) | 106 B / 12 B active MoE | Q4_K_M | 46.6 + 21.4 GB (2 shards) | 16 384 | attention on GPU, experts on RAM (`-ot .ffn_.*_exps.=CPU`) | 8082 | ~6–10 | [official card](https://huggingface.co/zai-org/GLM-4.5-Air) · [GGUF we ship](https://huggingface.co/unsloth/GLM-4.5-Air-GGUF) · [llm-stats](https://llm-stats.com/models/glm-4.5-air) |
| `gemma4-e4b-it` | Gemma 4 (Google, edge / multimodal) | 8 B dense (text-only here) | Q4_K_M | 4.7 GB | 16 384 | full GPU (`-ngl 99`) | 8086 | ~92 | [Gemma 4 page](https://deepmind.google/models/gemma/gemma-4/) · [official card](https://huggingface.co/google/gemma-4-E4B-it) · [GGUF we ship](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF) |
| `gemma4-26b-a4b-it` | Gemma 4 MoE (Google) | 25.2 B / 3.8 B active MoE | IQ4_XS (i-matrix) | 13.0 GB | 8 192 | full GPU (`-ngl 99`) + `--flash-attn on` | 8087 | ~91 | [Gemma 4 page](https://deepmind.google/models/gemma/gemma-4/) · [official card](https://huggingface.co/google/gemma-4-26B-A4B-it) · [GGUF we ship](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) |
| `whisper-large-v3-turbo` | whisper.cpp (OpenAI) | 809 M (ASR, not chat; distilled large-v3 w/ 4 decoder layers) | ggml f16 | 1.62 GB | audio (30 s chunks) | ~2 GB | 8090 | realtime-factor ~4–8× (GPU) | [whisper.cpp](https://github.com/ggerganov/whisper.cpp) · [ggml models](https://huggingface.co/ggerganov/whisper.cpp) · [turbo vs large-v3](changelog/20260422-whisper-turbo-vs-large-v3.md) · [OpenAI paper](https://arxiv.org/abs/2212.04356) |

\* Single-stream generation on an RTX 5060 Ti 16 GB, short
prompts (~100 input tokens). Ranges are indicative, not a
benchmark — prompt prefill, long contexts, and concurrent
traffic move the numbers. Measure on your workload before
committing.

**Gemma 4 reasoning note:** Both `gemma4-*` models emit a
`message.reasoning_content` field alongside `message.content` — the
model thinks step-by-step before answering, and llama-server splits
the chain-of-thought from the final reply. Set `max_tokens` generous
enough (≥ ~150 for short answers, more for complex tasks) or the
budget runs out mid-reasoning and `content` comes back empty. Clients
that only read `content` will see "no reply" without realising the
model was still thinking.

## Roles at a glance

| Role | Model | Why |
|---|---|---|
| **Default agentic / coding** | `glm-4.5-air` | 106 B MoE quality; the MoE CPU-offload keeps it viable on 16 GB VRAM. Slow but strong. |
| **Fast dense all-rounder** | `qwen3.5-9b` | The fastest "smart enough" option. Tool-call-capable via `--jinja`. |
| **Small / classifier / edge** | `gemma4-e4b-it` | 8 B dense, full GPU offload. Strict instruction-following and tight latency — the default for classification, JSON-schema work, and first-pass triage. |
| **Top quality on 16 GB GPU** | `gemma4-26b-a4b-it` | 25 B-total MoE with only 3.8 B active per token. IQ4_XS keeps the whole model on GPU; quality approaches a dense 27B at much higher tok/s thanks to MoE sparsity. |
| **Cloud parity check** | `claude-haiku-4-5` / `sonnet-4-6` / `opus-4-7` | Off-device baseline via `claude -p`; same hub, just swap the `model` string. |
| **Speech-to-text (ASR)** | `whisper-large-v3-turbo` | whisper.cpp on :8090. OpenAI-compatible `/v1/audio/transcriptions`. Port is a shared mutual-exclusion lock with the `transcribe_voice` project. Distilled large-v3 (4 decoder layers) — ~2× faster than large-v3 at near-identical WER on Spanish/English. See [turbo vs large-v3](changelog/20260424-whisper-turbo-vs-large-v3.md). Switch size by editing [config/models.yaml](../config/models.yaml) (`ggml-<size>.bin`) and re-running `python -m src.install --fix`. |

## How to add a new row to this table

1. Add the model to [config/models.yaml](../config/models.yaml)
   and the relevant host's `enabled` list (see the existing
   plan docs for the pattern).
2. Add a launcher pair in `launchers/` (`run_<id>.bat` / `.sh`) and
   a line in `launchers/run_all.*`.
3. Extend the registry test.
4. Run `python -m src.install --fix` and verify the smoke test
   shows a pass row.
5. Append a row to the table above. Keep the columns tight —
   link out to docs rather than inlining prose.

## Intentional exclusions

- **Gemma 3 family** (`gemma3-12b-it`, `gemma3-27b-it`,
  `gemma3n-e4b-it`) — superseded by the Gemma 4 entries above.
  Per the [latest-only policy](../README.md#latest-only-policy),
  older entries in the same family are removed once a comparable
  newer one ships. Original rationale for adopting Gemma 3 lives
  in [changelog/20260420-add-gemma-for-action-item-classification.md](changelog/20260420-add-gemma-for-action-item-classification.md).
- **Gemma PaliGemma / vision variants** — no image inputs in
  our workloads.
- **Hugging Face Inference API, OpenRouter, etc.** — the
  project is local-first; cloud models go through Claude's
  subscription path, not arbitrary remote APIs.
