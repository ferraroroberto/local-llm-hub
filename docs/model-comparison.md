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
| `claude-haiku-4-5` (alias `claude_haiku`) | Claude (Anthropic) | n/a (cloud via `claude -p`) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription) | [anthropic.com/claude](https://docs.anthropic.com/en/docs/about-claude/models) · [llm-stats](https://llm-stats.com/models?provider=anthropic) |
| `claude-sonnet-4-6` (alias `claude_sonnet`) | Claude (Anthropic) | n/a (cloud via `claude -p`) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription) | [anthropic.com/claude](https://docs.anthropic.com/en/docs/about-claude/models) |
| `claude-opus-4-7` (alias `claude_opus`) | Claude (Anthropic) | n/a (cloud via `claude -p`) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription) | [anthropic.com/claude](https://docs.anthropic.com/en/docs/about-claude/models) |
| `gemini-3.1-pro` (alias `gemini_pro`) | Gemini 3.1 (Google) — preview | n/a (cloud via `agy` CLI) | n/a | n/a | 1 M tokens | n/a | 8000 (hub) | n/a (AI Pro/Ultra required since 2026-03-25) | [Gemini 3.1 Pro post](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/) · [Gemini CLI](https://geminicli.com/) · [DeepMind Pro](https://deepmind.google/models/gemini/pro/) |
| `gemini-3-flash` | Gemini 3 (Google) | n/a (cloud via `agy` CLI) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription, Code Assist free tier) | [Gemini 3 Flash in CLI](https://developers.googleblog.com/gemini-3-flash-is-now-available-in-gemini-cli/) · [Gemini CLI quotas](https://geminicli.com/docs/resources/quota-and-pricing/) |
| `gemini-3.1-flash-lite` (alias `gemini_lite`) | Gemini 3.1 (Google) — GA 2026-05-07 | n/a (cloud via `agy` CLI) | n/a | n/a | per model — see docs | n/a | 8000 (hub) | n/a (subscription, Code Assist free tier) | [Flash-Lite GA](https://cloud.google.com/blog/products/ai-machine-learning/gemini-3-1-flash-lite-is-now-generally-available) |
| `qwen3.5-4b` | Qwen 3.5 (Alibaba) — hybrid Gated DeltaNet + sparse MoE | 4 B base (sparse-MoE active set) | Q4_K_M | 2.6 GB | 65 536 (262 144 native) | full GPU (`-ngl 99`) + `--flash-attn on` | 8088 | ~110 | [Qwen 3.5 announcement](https://qwen.ai/blog/qwen3.5/) · [official org](https://huggingface.co/Qwen) · [GGUF we ship](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) |
| `gemma4-e4b-it` (fallback) | Gemma 4 (Google, edge / multimodal) | 8 B dense (text-only here) | Q4_K_M | 4.7 GB | 16 384 | full GPU (`-ngl 99`) | 8086 | ~92 | [Gemma 4 page](https://deepmind.google/models/gemma/gemma-4/) · [official card](https://huggingface.co/google/gemma-4-E4B-it) · [GGUF we ship](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF) |
| `gemma4-26b-a4b-it` | Gemma 4 MoE (Google) | 25.2 B / 3.8 B active MoE | IQ4_XS (i-matrix) | 13.0 GB | 8 192 | full GPU (`-ngl 99`) + `--flash-attn on` | 8087 | ~91 | [Gemma 4 page](https://deepmind.google/models/gemma/gemma-4/) · [official card](https://huggingface.co/google/gemma-4-26B-A4B-it) · [GGUF we ship](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) |
| `whisper-large-v3-turbo` | whisper.cpp (OpenAI) | 809 M (ASR, not chat; distilled large-v3 w/ 4 decoder layers) | ggml f16 | 1.62 GB | audio (30 s chunks) | ~2 GB | 8090 | realtime-factor ~4–8× (GPU) | [whisper.cpp](https://github.com/ggerganov/whisper.cpp) · [ggml models](https://huggingface.co/ggerganov/whisper.cpp) · [turbo vs large-v3](whisper-turbo-vs-large-v3.md) · [OpenAI paper](https://arxiv.org/abs/2212.04356) |
| `whisper-medium-translate` | whisper.cpp (OpenAI) — eager CPU sibling | 769 M (medium) | ggml f16 | ~1.5 GB | audio (30 s chunks) | CPU-only | 8091 | ~realtime on 7800X3D | [official medium](https://huggingface.co/openai/whisper-medium) · [ggml models](https://huggingface.co/ggerganov/whisper.cpp) |
| `whisper-vanilla` | whisper.cpp (OpenAI) — lazy GPU, glossary-free | 809 M (same turbo model) | ggml f16 | 1.62 GB | audio (30 s chunks) | ~2 GB (only while loaded) | 8094 | realtime-factor ~4–8× (GPU) | [whisper.cpp](https://github.com/ggerganov/whisper.cpp) · [#128](https://github.com/ferraroroberto/local-llm-hub/issues/128) |

> **Demoted candidates** (kept defined in `config/models.yaml` but
> **not in the active rotation** — see `enabled:` for the active host):
> `qwen3.5-9b`, `glm-4.5-air`. Bring up ad-hoc with
> `launchers/run_qwen.bat` / `run_glm.bat` if you need them.

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
| **agentic_light** (OpenClaw fast lane) | `qwen3.5-4b` | 4 B hybrid Gated DeltaNet + sparse MoE on full GPU (~3 GB). Apache 2.0, 262 k native context. The May 2026 frontier Tier A pick — strong instruction-following at ~110 t/s for routing, classification, JSON-schema work, and first-pass triage. `gemma4-e4b-it` stays in `enabled:` as fallback. |
| **agentic_heavy** (deep lane / transcripts / docs) | `gemma4-26b-a4b-it` | 25 B-total MoE with only 3.8 B active per token. IQ4_XS keeps the whole model on GPU; quality approaches a dense 27B at much higher tok/s thanks to MoE sparsity. Strong multilingual incl. Catalan. |
| **audio_transcribe** | `whisper-large-v3-turbo` | whisper.cpp on :8090. OpenAI-compatible `/v1/audio/transcriptions`. Port is a shared mutual-exclusion lock with the `transcribe_voice` project. Distilled large-v3 (4 decoder layers) — ~2× faster than large-v3 at near-identical WER on Spanish/English. Carries the English tech-dictation glossary as its initial prompt, which biases language detection toward English; callers transcribing general multilingual audio should select `model=whisper-vanilla` (glossary-free, lazy, :8094 — #128) for unbiased auto-detect. |
| **audio_translate** | `whisper-medium-translate` | whisper.cpp medium on CPU, eager-loaded on :8091 (~1.5 GB RAM, always ready). Turbo's distilled decoder doesn't translate, so this slot fills the gap. A lazy-load mode (proxy + idle-unload) lives in `src/whisper_translate_proxy.py` for hosts that need to reclaim RAM. |
| **Cloud parity (Anthropic)** | `claude_haiku` / `claude_sonnet` / `claude_opus` | Off-device baseline via `claude -p`; same hub, just swap the alias. The aliases stay stable across version bumps — when Anthropic ships `claude-haiku-5`, only the row's `display_name` changes. Not a local role — never touched by `/swap-model`. |
| **gemini_pro (Google)** | `gemini-3.1-pro` | Subscription cloud route via the `agy` (Antigravity) CLI. AI Pro required for 3.1 Pro (paid since 2026-03-25). 1 M-token context. Image content blocks supported. |
| **gemini_lite (Google)** | `gemini-3.1-flash-lite` | Subscription cloud route via the `agy` (Antigravity) CLI. GA on 2026-05-07. Lowest-latency tier; quotas share with Gemini Code Assist. |

> Roles are declared in `config/models.yaml` → `roles:`. Update them
> via `/swap-model` in Claude Code (interactive, edits the yaml +
> writes a launcher + optionally downloads weights).

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
  newer one ships.
- **Gemma PaliGemma / vision variants** — no image inputs in
  our workloads.
- **Hugging Face Inference API, OpenRouter, etc.** — the
  project is local-first; cloud models go through Claude's
  subscription path, not arbitrary remote APIs.
- **GLM-5.2** (`zai-org/GLM-5.2`) — evaluated for the local coding
  lane and rejected: it is a single 744B-A40B MoE with no Air/Flash
  variant, and its smallest usable quant (UD-IQ2_M) needs ~245 GB
  RAM+VRAM vs. this box's ~144 GB — it does not load at any quant.
  Excellent coder, wrong size for the hardware. Revisit if a
  GLM-5.2-Air/Flash (~80–120 B) ships. Full analysis:
  [glm-5.2-evaluation.md](glm-5.2-evaluation.md) · [#141](https://github.com/ferraroroberto/local-llm-hub/issues/141).
