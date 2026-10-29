# GLM-4.5-Air performance assessment

> **Historical — GLM-4.5-Air was demoted on 2026-05-10** and is no
> longer in any host's active rotation (kept defined in
> `config/models.yaml` for ad-hoc bring-up only via `run_model.bat glm`).
> `qwen3.5-9b` was demoted the same day but is active again — see
> `config/models.yaml`'s `enabled:` lists for current placement, never
> restated here. `agentic_light` is `qwen3.5-4b` and `agentic_heavy` is
> `gemma4-26b-a4b-it` ([models-and-roles.md](models-and-roles.md)). This
> doc is kept as the durable performance record behind GLM's demotion.

Follow-up to the original three-backend hub bring-up (Claude + Qwen +
GLM); see [project-structure.md](project-structure.md) for the current
architecture.

## Measured

Morning test on the Windows PC (5060 Ti 16 GB + 128 GB RAM): ~400 input
tokens / ~1000 output tokens in ~120 s (~10 tok/s generation), CPU
pinned at 100 %, GPU at ~15 %. That rate matched the research doc's
predicted 8–15 tok/s floor for this config — not a misconfiguration.

## Why CPU is pegged and GPU is idle

GLM-4.5-Air's launch args put every layer on GPU (`-ngl 99`) but then
override that for expert tensors (`-ot ".ffn_.*_exps.=CPU"`) — and for
an MoE model the experts are 80–90% of total parameters. Only
attention weights, the router, embeddings and the KV cache sit on GPU;
every generated token moves hidden states GPU→CPU, runs the experts on
CPU, and moves the result back. This is the intended trade-off on a
16 GB card — the alternative is "cannot load at all" — and it leaves
~7–9 GB of VRAM unused (`CUDA0 model buffer ≈ 4 GB` + `KV buffer ≈ 3 GB`
against the 5060 Ti's 16 GB).

## Why it was demoted

Openclaw is agentic — each turn is a small generation that triggers a
tool call feeding back into another small generation, so
time-to-first-token and turn count dominate over raw tok/s. Even with
the hybrid-offload/FlashAttention/KV-quant tuning tried during this
assessment (realistic ceiling ~18–25 tok/s), GLM stayed too slow for
tight agent loops next to Qwen3.5 dense (~65 tok/s). `qwen3.5-4b`
(fast lane) and `gemma4-26b-a4b-it` (deep lane, MoE sparse) replaced it
in the active roles; GLM remains available for ad-hoc bring-up when a
task specifically needs the bigger model.

## Revisit triggers

- A GLM quant/offload change that closes most of the ~2.5x gap to
  Qwen3.5 dense on this hardware.
- A workload that favors GLM's larger active-parameter count over
  agentic turn latency (non-agentic batch work, not openclaw).
