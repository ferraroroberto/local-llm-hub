# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-09-17
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant this window. Both of this window's new heavy-tier entrants (GLM-5.3-Flash, Nemotron 3 Super) are two orders of magnitude too large for this role |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists. Two more entrants evaluated and dominated: **GLM-5.3-Flash** (320B/18B active, MIT, a sourcing-gap catch — shipped 2026-08-26, missed by the last run) needs ~100-128 GB combined RAM+VRAM at its smallest quant and has no verified non-coding speed advantage; **NVIDIA Nemotron 3 Super** (120B/12B active, shipped 2026-09-15) doesn't fit 16 GB VRAM, has no consumer-GPU-offload speed measurement, and inherits the Nemotron family's undocumented Catalan gap (#486) — see §5 |
| `audio_transcribe` | `parakeet` (parakeet-tdt-0.6b-v3, Mac ANE) + whisper fallback | watch | NVIDIA Nemotron 3.5 ASR Streaming 0.6B (FluidAudio v0.15.7, decode-time biasing) | — | The clearest re-open signal since the #401 disproof: FluidAudio v0.15.7 (2026-09-10) shipped **decode-time custom vocabulary biasing "no CTC head required"** for Nemotron ASR models specifically — a structurally different, architecturally-guaranteed insertion mechanism, not the post-hoc rescoring that failed for Parakeet. It runs in the same Swift/FluidAudio/CoreML runtime already vendored on the Mac Mini ANE. Untested against this project's own jargon corpus, so verdict stays `watch` — but the local-findings re-open trigger is arguably met for the first time; see §7/§9 |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage: Turbo → Gemma 4 26B MoE | — | Unchanged; two-stage stays the default. Nemotron 3.5 ASR Streaming is transcription-only (no translate capability), so it is not a candidate for this role |

**Diff vs previous run (2026-09-03):** All four verdicts hold, but `audio_transcribe`'s best-alternative column changes for the second consecutive run — and this time more consequentially. FluidAudio v0.15.7 (2026-09-10) added decode-time vocabulary biasing for NVIDIA's Nemotron 3.5 ASR Streaming 0.6B model — a CoreML build already exists for Apple Silicon (benchmarked on Apple M5 Pro), meaning it drops into the exact same runtime this project already uses for the Mac ANE Parakeet worker. This is architecturally the first candidate that satisfies the #401 local-findings re-open trigger literally ("an alternative Parakeet checkpoint/architecture exposing such a hook"), not just directionally like Granite Speech 4.1 2B did last run. It is not a strict accuracy upgrade on paper — Nemotron's own FLEURS numbers (EN 7.91%, ES 4.11%) are *worse* than Parakeet's official card (EN 4.85%, ES 3.45%) — so the case for testing it is the vocabulary-biasing capability, not generic WER. Verdict stays `watch` pending a local smoke test; see §7 for the full analysis, including why Granite Speech 4.1 2B (last run's lead) is now the second-priority candidate rather than the first. On the text side, two new heavy-tier entrants were evaluated (GLM-5.3-Flash — a sourcing-gap catch from 2026-08-26 — and Nemotron 3 Super, shipped 2026-09-15) and both dominated on the same grounds as prior oversized/underverified candidates. `agentic_light` and `audio_translate` are unchanged in substance.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of September 2026.

**What changed since 2026-09-03:** Two text-side entrants, both dominated, one of them a genuine sourcing-gap catch. **GLM-5.3-Flash** (Z.ai, MIT, first natively multimodal GLM-5) actually shipped 2026-08-26 — the same week as last run's Qwen3.8-Flash-Next catch — but was missed by the 2026-09-03 report. At 320B total / 18B active, it needs Unsloth's dynamic 3-bit GGUF (~100-128 GB combined RAM+VRAM per community builds) to fit this box's ~144 GB budget at all, and no independent non-coding speed benchmark on 16 GB-class hardware with CPU offload exists — its headline benchmarks (Terminal-Bench 84.3, AutomationBench 48.8) are agentic-coding-adjacent, which this stack weights at 0%. **NVIDIA Nemotron 3 Super** (120B/12B active, hybrid Mamba-2 + Transformer + LatentMoE) shipped 2026-09-15, squarely inside this run's window. Q4_K_M GGUF needs ~65-70 GB — CPU-offload-only on this box, with no consumer-GPU-offload tok/s measurement published (only professional multi-GPU and API-provider numbers exist, none comparable to a single 16 GB card with RAM spillover). Its documented languages (EN/FR/DE/IT/JA/ES/ZH) omit Catalan — the same gap, on the same NVIDIA Nemotron Open Model License family, that sank the smaller Nemotron 3 Nano 4B (#486) and last run's Nemotron 3.5 Lightning.

The more consequential news is on the audio side. **FluidAudio v0.15.7** (2026-09-10) shipped `feat(asr/nemotron): decode-time custom vocabulary biasing (no CTC head required)` — a mechanism built specifically for NVIDIA's **Nemotron 3.5 ASR Streaming 0.6B** model, not for Parakeet. A CoreML port of that model (`FluidInference/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b-CoreML`) already exists, benchmarked on Apple Silicon (M5 Pro). This is the first release since the #401 disproof that plausibly satisfies its own re-open trigger to the letter — full analysis in §7.

GLM-5.5 remains an unconfirmed rumor; Z.ai's own $5B raise announcement (2026-09-13) is earmarked partly for next-gen models, but the company's release cadence points to a "GLM-5.4" numbering next, likely October-November, not a GLM-5.5 this window. DeepSeek V4, Kimi K3, and Mistral's Ministral 3 / Magistral 1.2 lineup (all dated to late 2025 under Mistral's `-2512`/`-2509` release-code convention, despite surfacing in September 2026 search results) carry forward unchanged — no genuinely new Mistral entrant this window.

---

## 2. System & workloads

| | |
|---|---|
| **GPU** | NVIDIA RTX 5060 Ti, 16 GB VRAM, 448 GB/s memory bandwidth (Blackwell, FP4-capable) |
| **CPU** | AMD Ryzen 7 7800X3D, 8c/16t, 96 MB L3 — the large cache makes CPU inference unusually viable |
| **RAM** | 128 GB DDR5 (running at 3600 MT/s on a 6400 kit) — huge offload headroom |
| **Storage** | 2 TB NVMe (WD_BLACK SN850X) for hot model weights |
| **OS** | Windows 11 Pro |
| **Runtimes** | llama.cpp / GGUF (primary, via this repo's launchers); Ollama, LM Studio, vLLM-under-WSL2 as references |

Composite quality-score weights (fixed by the skill brief):

- **35%** agentic / function calling — BFCL v3/v4, τ-bench, IFEval
- **25%** instruction following & writing polish — Arena-Hard, RewardBench
- **25%** multilingual quality — FLORES-200 EN↔ES, EN↔CA where measured (Catalan coverage is uneven)
- **15%** long-context document handling — needle-in-haystack, RULER

Audio workloads (transcription EN/ES, audio translation ES→EN, disfluency removal) are evaluated separately in §7. Coding benchmarks carry **0% weight**.

---

## 3. Methodology

1. Read `docs/frontier/runs/LATEST` (2026-09-03), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — three unresolved entries carry forward: the faster-whisper CTranslate2 disproof (2026-07-12), the FluidAudio `CustomVocabularyContext` disproof (2026-07-24), and the Nemotron 3 Nano 4B quality disproof (2026-08-10, #486). This run's leading ASR candidate (Nemotron 3.5 ASR Streaming via FluidAudio v0.15.7) is evaluated against the #401 entry's explicit re-open trigger — see §7/§9 for why it is judged a plausible (not yet confirmed) match.
2. Re-read `config/models.yaml` → `roles:` for the current incumbents. No out-of-band changes since the last run: `agentic_light` (qwen35_4b_nothink), `agentic_heavy` (gemma4_26b), `audio.transcribe` (parakeet + whisper fallback), and `audio.translate` (whisper_translate) are all unchanged.
3. Surveyed the external landscape for the 2026-09-03 → 2026-09-17 window, explicitly searching "Nemotron" by name per last run's sourcing-gap lesson (§9): NVIDIA Nemotron 3 Super's architecture/license/GGUF footprint/multilingual coverage (shipped 2026-09-15, inside this window), and — via that same search — the pre-existing NVIDIA Nemotron 3.5 ASR Streaming 0.6B model and its FluidAudio integration. Also checked GLM-5.3-Flash (a 2026-08-26 sourcing-gap catch), GLM-5.5's rumor status (still unconfirmed, Z.ai's $5B raise on 2026-09-13 noted), Qwen (Qwen-Drive-1.0 and Qwen-Image-3.0 shipped but both out of scope — vision-driving and image-gen, not text agentic; Qwen 4 still unshipped, rumored for the Apsara Conference 2026-09-22–24, after this run's cutoff), and Mistral/Granite/Falcon/OLMo/Hermes/GPT-OSS/Phi/Gemma for anything new (none found this window beyond already-carried entries).
4. On the audio side: checked FluidAudio's releases since 2026-09-03 — v0.15.7 (2026-09-10) is the material one, adding `feat(asr/nemotron): decode-time custom vocabulary biasing (no CTC head required)`, distinct from the Parakeet-path "vocabulary boosting" (still rescoring-based) that v0.15.6 added. Confirmed via the FluidAudio GitHub releases changelog. Checked the HF Open ASR Leaderboard for movement since Granite Speech 4.1 2B's 2026-09-03 lead (no change found this window) and confirmed Nemotron 3.5 ASR Streaming's own published FLEURS numbers (EN 7.91%, ES 4.11%) directly against Parakeet's official card (EN 4.85%, ES 3.45%) to keep the comparison honest — see §7.
5. Computed VRAM with the standing rule of thumb **Q4_K_M ≈ 4.5 bits/param** plus KV-cache where no published GGUF size exists; used community-published combined RAM+VRAM figures for GLM-5.3-Flash's Unsloth dynamic-quant builds and Nemotron 3 Super's Q4_K_M GGUF instead of the rule of thumb now that both exist.
6. Applied the honesty rules: date-stamped claims, ≤3% composite = tie (the Gemma 4 26B / Qwen 3.6 35B-A3B tie stands), licenses surfaced (MIT for GLM-5.3-Flash, NVIDIA Nemotron Open Model License for Nemotron 3 Super, OpenMDW-1.1 for Nemotron 3.5 ASR Streaming), Nemotron 3 Super's Catalan gap flagged as a risk consistent with its family's track record, and — critically — Nemotron 3.5 ASR Streaming's *worse* generic FLEURS WER vs. Parakeet stated plainly rather than glossed over, so the `audio_transcribe` verdict stays `watch` on the strength of an architectural capability match, not a benchmark win, consistent with this project's two prior ASR-claim disproofs (#274, #401).

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's cautionary tale is **NVIDIA Nemotron 3 Super 120B-A12B** — the biggest single new-entrant swing-and-miss since DeepSeek V4 Flash, for a similar reason: the math simply doesn't land on this box.

```
120B total params, 12B active (hybrid Mamba-2 + Transformer + LatentMoE)
Published GGUF: Q4_K_M ≈ 65-70 GB (community builds); NVFP4 ≈ 60 GB (needs an
  H100-class card to hold as a monolithic load — not this box's shape at all)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling
  Q4_K_M (~70 GB) DOES fit the combined budget with room to spare — but only
  as a CPU-offload load, same as glm's -ot .ffn_.*_exps.=CPU pattern
Bandwidth-bound speed estimate for CPU-offloaded MoE experts (this repo's own
  worked-example method, run fresh for a 12B-active model this time):
  12B active params x 4.5 bits/param (Q4_K_M) / 8 = ~6.75 GB moved per token
  DDR5 3600 MT/s dual-channel practical bandwidth ~50-55 GB/s
  6.75 GB / 52 GB/s ≈ ~7-8 tokens/second, theoretical ceiling (no overhead)
Real-world CPU-offload runs typically land at 60-80% of this bandwidth
  ceiling once attention/Mamba-layer compute and PCIe transfer are counted —
  so a realistic estimate is closer to 5-6 t/s, an order of magnitude slower
  than the incumbent's 99 t/s
Multilingual: NVIDIA's own documentation lists EN, FR, DE, IT, JA, ES, ZH —
  Catalan is absent, the same axis where the smaller Nemotron 3 Nano 4B
  sibling was measured and DISPROVEN for this exact role family (#486), and
  where last run's Nemotron 3.5 Lightning carried the identical risk
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across six consecutive runs. Nemotron 3 Super's architecture (hybrid Mamba+Transformer+MoE, "agent execution layer" positioning) is the family's most ambitious entrant yet, and its 12B-active design is genuinely larger and more capable in principle than Lightning's 3B-active — but a 5-6 t/s bandwidth-bound estimate against a 99 t/s incumbent isn't a frontier entry at any quality level this project could plausibly reach. Same conclusion, third Nemotron sibling in a row (#486, Lightning last run, Super this run) — the family keeps shipping architecturally interesting agentic models that don't fit this specific 16 GB + 128 GB envelope at competitive speed, and keeps omitting Catalan.

---

## 5. Results — shortlist by tier

### Tier A — Fast lane (OpenClaw routing, classification, simple tool calls)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen 3.5 4B *(incumbent)* | 4B hybrid MoE | Q4_K_M | ~3 GB | ~110 | 65 | 262k | Apache 2.0 | yes |
| ☆ Gemma 3 4B | 4B dense | Q4_K_M | ~3 GB | ~100 | 60 | 128k | Gemma | yes |
| Phi-4 Mini | 3.8B dense | Q4_K_M | ~2.5 GB | ~120 | 49 | 16k | MIT | yes (speed end) |
| Granite 4.1 8B | 8B dense | Q4_K_M | ~5 GB | ~60 est | 64 est | 128k | Apache 2.0 | no |
| Llama 3.2 3B | 3B dense | Q4_K_M | ~2 GB | ~120 | 43 | 128k | Llama 3.2 | no |

No change this run. Neither of this window's two new text entrants (GLM-5.3-Flash, Nemotron 3 Super) is remotely 4B-class — both are two orders of magnitude larger.

### Tier B — Balanced (the workhorse) — **TIED, unchanged**

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★★ Gemma 4 26B MoE *(incumbent)* | 26B / 4B active | native W4 | ~14 GB | 99 | 83 | 256k | Gemma | yes |
| ★★ Qwen 3.6 35B-A3B | 35B / 3B active | Q4_K_M | ~13.5 GB | 98 | 84 | 262k | Apache 2.0 | yes |
| ☆ GPT-OSS 20B | 21B / 3.6B active | MXFP4 | ~12 GB | ~100 | 72 | 131k | Apache 2.0 | yes |
| Nemotron 3.5 Lightning | 30B / 3B active | Q4_0 (+MTP) | ~20.1 GB | ~40 est | 75 est | 128k | OpenMDW-1.1 | no |
| Qwen3.8-27B | 27.78B dense | Q4_K_M | ~15.6 GB | ~28 est | 79 est | 262k (1M YaRN) | Apache 2.0 | no |
| Gemma 4 12B Unified | 12B dense | Q4_K_M | ~7 GB | ~45 est | 76 est | 256k | Apache 2.0 (verify) | no |
| Ministral 3 14B | 14B dense | Q4_K_M | ~8.5 GB | ~35 est | 70 est | 256k | Apache 2.0 | no |
| Mistral Small 3.2 | ~22B dense | Q4_K_M | ~13 GB | ~30 | 74 | 128k | Apache 2.0 | no |

No change this run. Both new entrants (GLM-5.3-Flash, Nemotron 3 Super) are too large for Tier B by an order of magnitude and land in Tier C instead.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen3 32B dense | 32B | Q4_K_M | ~19.5 GB (spill) | ~11 | 84 | 128k | Apache 2.0 | yes |
| GLM-5.3-Flash *(new)* | 320B / 18B active | GGUF (Unsloth dynamic 3-bit) | ~110 GB (spill) | ~12 est | 76 est | 1M | MIT | no |
| Nemotron 3 Super *(new)* | 120B / 12B active | Q4_K_M | ~70 GB (spill) | ~6 est | 78 est | 128k | NVIDIA Nemotron Open Model License | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |
| DeepSeek V4 Flash | 284B / 13B active | GGUF 3-bit (native FP4/FP8) | ~103 GB (offload) | ~10-20 est | unverified | 1M (reported) | MIT | no |

No change to the frontier. **GLM-5.3-Flash added** — a sourcing-gap catch (shipped 2026-08-26, missed by the 2026-09-03 run): Z.ai's first natively multimodal GLM-5, MIT-licensed, but its headline benchmarks are agentic-coding-adjacent (0% weight here) and no non-coding composite or 16 GB-class speed measurement exists — dominated by every entry it would need to beat on unverified grounds alone. **NVIDIA Nemotron 3 Super added** — this window's most architecturally ambitious entrant (§4's worked example), estimated at 5-6 t/s CPU-offloaded against Qwen3 32B dense's verified 11 t/s at less than a third the combined footprint, plus the family's recurring Catalan gap.

### Models considered and dropped this run

- **GLM-5.3-Flash (320B total / 18B active MoE, MIT, shipped 2026-08-26)** — first natively multimodal GLM-5. A sourcing-gap catch: it shipped in the same window as last run's Qwen3.8-Flash-Next but wasn't sourced then. Needs ~100-128 GB combined RAM+VRAM at Unsloth's smallest dynamic quant; no non-coding speed or quality measurement exists on 16 GB-class hardware. Its strong benchmarks (Terminal-Bench 84.3, AutomationBench 48.8) are agentic-coding-adjacent, out of scope for this stack's 0%-coding weighting. Evaluated in Tier C above.
- **NVIDIA Nemotron 3 Super (120B/12B active, hybrid Mamba-2+Transformer+MoE, NVIDIA Nemotron Open Model License, shipped 2026-09-15)** — this window's headline NVIDIA release, explicitly positioned for "agentic AI systems at scale." Doesn't fit 16 GB VRAM (needs ~65-70 GB Q4_K_M, CPU-offload only), no consumer-GPU-offload tok/s measurement exists (only professional 8-GPU and API-provider numbers), and its documented language list (EN/FR/DE/IT/JA/ES/ZH) omits Catalan — the third Nemotron-family model in a row to carry this exact gap (#486, Lightning last run, Super this run). Evaluated in Tier C above and §4's worked example.
- **GLM-5.5** — still rumored; Z.ai's $5B raise (2026-09-13) is earmarked partly for next-gen models, but the company's own release cadence points to "GLM-5.4" numbering next (Oct-Nov), not a GLM-5.5 imminently. Watch next cycle.
- **Qwen 4** — architecture previewed via Qwen3.8-Flash-Next (last run), but the model itself is unshipped; rumored for the Apsara Conference (2026-09-22–24), after this run's cutoff. Watch next cycle — likely to be the subject of the next frontier run.
- **DeepSeek V4 Pro / DeepSeek V4 Flash / Qwen3.8 2.4T-A95B / Kimi K3 / Qwen3.8-Flash-Next / Nemotron 3.5 Lightning** — no updates this window; standing NO-GOs carried unchanged.
- **Qwen-Drive-1.0 / Qwen-Image-3.0** — vision-language-for-driving and image-generation models respectively, shipped early September 2026; neither is a text-agentic candidate for any role this stack has.
- **Mistral's Ministral 3 / Magistral Medium & Small 1.2 / Mistral Large 3** — all use Mistral's `-2512`/`-2509` release-code convention (December/September 2025), i.e. already-known models resurfacing in current search indexes, not new September 2026 releases. No change from prior runs' treatment.
- Standing drops carried from prior runs: Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2, Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window. Neither Nemotron 3 Super nor GLM-5.3-Flash clears the bar to justify a fifth overnight-batch recipe alongside Qwen3 32B dense — both are estimated slower at a much larger combined footprint.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 The landscape in September 2026 — a decode-time biasing hook finally ships, but not where last run expected it

The genuinely new development this window is **FluidAudio v0.15.7** (2026-09-10): `feat(asr/nemotron): decode-time custom vocabulary biasing (no CTC head required)`. This is the first FluidAudio release that ships a **decode-time** vocabulary mechanism — one that can, by construction, insert a phrase the acoustic model would not otherwise have emitted, rather than only rescoring what the decoder already produced. It is built specifically for **NVIDIA's Nemotron 3.5 ASR Streaming 0.6B** model, not for Parakeet — the Parakeet path (v0.15.5/v0.15.6's "vocabulary boosting on the unified Parakeet path") remains the same post-hoc CTC rescoring mechanism disproven in #401.

This matters because the #401 local-findings entry's re-open trigger reads: *"a FluidAudio release that adds decode-time TDT vocabulary biasing... **or an alternative Parakeet checkpoint/architecture exposing such a hook**."* Nemotron 3.5 ASR Streaming, now decode-time-biasable through the exact same FluidAudio/Swift runtime this project already vendors for the Mac ANE Parakeet worker (`src/parakeet_server.py`), is plausibly that alternative architecture. A CoreML port already exists (`FluidInference/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b-CoreML`), benchmarked on Apple M5 Pro — meaning it targets the same Apple Silicon deployment path this project already uses, not a hypothetical port.

**The honest caveat, stated plainly rather than buried:** Nemotron 3.5 ASR Streaming's own published generic-benchmark numbers are *not* a WER upgrade over Parakeet. On FLEURS (with LangID, 1.12 s chunk size): Nemotron scores EN 7.91% / ES 4.11%, against Parakeet's official-card EN 4.85% / ES 3.45% — Parakeet is the more accurate model on generic speech in both languages this project cares about. The case for testing Nemotron isn't "it's more accurate" — it's "it has a real decode-time biasing hook and Parakeet structurally does not." That is precisely the capability gap that sank the #401 fix: Parakeet's TDT decoder can drop "Claude Code" entirely with no candidate word to rescore, and Granite Speech 4.1 2B's prompt-conditioned LLM-decoder approach (last run's lead) is a plausible but *unconfirmed* way around that; a purpose-built decode-time biasing hook shipped by the same runtime already in production here is a more concrete, lower-integration-risk path to the same fix. Given this project's history (#274, #401) of published-benchmark claims not surviving local domain testing, the verdict stays **`watch`, not `upgrade`** — but this is judged the strongest lead yet, ahead of Granite Speech 4.1 2B, precisely because the biasing mechanism is confirmed to exist and ship in-runtime rather than inferred from an LLM-decoder's prompt-conditioning behavior.

### 7.2 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (measured/est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| **★ Parakeet TDT v3** (Mac ANE, current primary) | 0.6B | ~2 GB (CoreML) | 65.8× measured (ANE), sub-second even on 108 s clips | ✅ | ✅ (25 langs) | ❌ | Unchanged incumbent since 2026-07-22. Better generic WER than both this run's ASR candidates on published leaderboard numbers — the gap is entirely in the (unfixable via FluidAudio's own mechanism) wake-phrase/jargon domain. |
| **◆ Whisper Large v3 Turbo** (automatic failover) | 809M | ~1.6 GB | 40× tower / 19.3× gaming (measured, boosted) | ✅ | ✅ | ❌ | Unchanged. Still the accuracy leader on this domain thanks to the `--carry-initial-prompt` boosting glossary (#91) — a lever Parakeet structurally lacks. |
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B *(new lead, untested)* | 0.6B | ~2 GB est (CoreML) | TBD (domain) | ✅ (FLEURS 7.91% WER — worse than Parakeet's card) | ✅ (FLEURS 4.11% WER — worse than Parakeet's card) | ❌ (streaming ASR only) | OpenMDW-1.1. FluidAudio v0.15.7 (2026-09-10) ships genuine decode-time keyword biasing for this model specifically — "no CTC head required." CoreML build exists for Apple Silicon. Untested against the #138/#343 jargon corpus; recommended next-step smoke test, ahead of Granite Speech 4.1 2B (§9). |
| Granite Speech 4.1 2B *(carried lead, still untested)* | 2B | ~2 GB est | TBD (domain) | ✅ | ✅ (6 langs, bidirectional AST) | ✅ | Apache 2.0. No change this window — still leads the public HF Open ASR Leaderboard at 5.33% WER. Prompt-conditioned decode-time steering through its LLM decoder is a plausible but architecturally less-proven biasing path than Nemotron 3.5 ASR Streaming's purpose-built hook; now the second-priority smoke-test candidate, not the first. |
| ✗ Parakeet + FluidAudio CustomVocabularyContext | 0.6B + ~97 MB CTC model | ~2 GB + rescorer | n/a — disproven, not shipped | — | — | ❌ | Disproven #401 (2026-07-24). Carried `watch` per `local-findings.md`. FluidAudio v0.15.5-v0.15.7's Parakeet-path work stays post-hoc rescoring throughout — the decode-time hook shipped in v0.15.7 is Nemotron-only, so the re-open trigger is not met *for Parakeet itself*, only for an alternative architecture (see §7.1/§9). |
| faster-whisper Turbo (CT2) | 809M (CT2) | ~1.0 GB INT8 | measured 1.0× vs whisper.cpp — **disproven** | ✅ | ✅ | ❌ | Carried `watch` per `docs/frontier/local-findings.md` (#277); applies to the failover leg's engine, not the primary. |
| Whisper Large v3 (faster-whisper) | 1.55B | ~2 GB | 30–50× | ✅✅ | ✅✅ | ✅ | Workload-G single-model fallback, unchanged. |
| Qwen3-ASR (1.7B / MLX build) | 1.7B | ~1.5 GB | TBD (domain) | ✅ | ✅ | ❌ | `watch`, unchanged status. FluidAudio still has no Qwen3-ASR backend (#676, removed) and no jargon-domain comparison exists. |

#### 7.2.1 Why the identified fix failed — and why this run's new lead is a stronger structural match than last run's

Unchanged root-cause analysis: FluidAudio's Parakeet-path vocabulary mechanism is a **post-hoc CTC rescorer** — it can only swap an already-emitted word for a boosted term with stronger acoustic evidence, never insert a phrase the decoder dropped entirely. That's why "Claude Code" is heard as "Yes"/"Yeah" rather than a garbled-but-present attempt: there's no candidate word to rescore **from**.

Last run's lead, Granite Speech 4.1 2B, sidesteps this by using an LLM decoder whose *generation* is conditioned on a keyword list before decoding starts — plausible in principle, proven nowhere yet against this project's corpus. This run's lead, Nemotron 3.5 ASR Streaming via FluidAudio v0.15.7, is a step more concrete: FluidAudio's own release notes describe the mechanism as decode-time biasing "no CTC head required," meaning the vendor of the exact runtime this project already integrates has built and shipped a hook explicitly designed to solve this class of problem — not a repurposed capability of an unrelated LLM-decoder architecture. That doesn't guarantee it recovers "Claude Code" or "YOLO" specifically (untested), but it lowers integration risk and raises the odds the mechanism was built with exactly this failure mode in mind. Both remain unverified locally; this run's ordering (Nemotron 3.5 ASR Streaming first, Granite Speech 4.1 2B second) reflects mechanism concreteness and runtime fit, not measured accuracy.

### 7.3 Workload F — transcribe EN/ES

**Verdict: `watch`.** No role change — `parakeet` stays primary with `whisper` as automatic failover, unchanged since 2026-07-22 (#350/#348). **Explicit answer to the brief's required question: no strict, verified upgrade over the incumbent transcribe model exists for EN/ES this run.** Nemotron 3.5 ASR Streaming via FluidAudio v0.15.7 is the strongest *candidate* surfaced yet (§7.1/§7.2.1) but is untested on this project's domain, and its own generic WER is worse than Parakeet's on paper — recommending it now, before a local test, would repeat the exact mistake #274 and #401 already taught this project not to make. Granite Speech 4.1 2B remains a live secondary candidate. Qwen3-ASR and the whisper failover's own `faster-whisper` engine swap stay `watch`/disproven per `local-findings.md`, unrelated to this verdict.

### 7.4 Workload G — ES audio → English

**Verdict unchanged: two-stage default.** faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call (~15 GB total). Single-model faster-whisper Large v3 `task=translate` stays the fallback when the LLM slot is busy — implemented today by the `whisper_translate` role slot (whisper-medium, lazy CPU), hence its `watch` verdict rather than retirement. Nemotron 3.5 ASR Streaming is transcription-only (no translate capability per its own documentation) and is therefore not a candidate for this role at all. Granite Speech 4.1 2B's native bidirectional ES↔EN speech translation remains a candidate to eventually replace the single-model fallback leg's engine — no change to the default this run.

### 7.5 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`).

### 7.6 Concurrency footprint

Unchanged from the previous run: whisper-turbo (accurate dictation) and whisper-vanilla/translate run on the gaming satellite, orpheus (expressive TTS) is on the tower, freeing the tower's GPU for agentic_heavy + agentic_light exclusively (#343/#422). Parakeet runs on the Mac Mini's ANE, its own dedicated accelerator, adding zero contention with either box. Piper (fast TTS) stays on the tower CPU. Nemotron 3.5 ASR Streaming, if tested, would need to be evaluated on the same Mac ANE (CoreML build already targets Apple Silicon) — plausibly a same-hardware swap rather than a new placement, unlike Granite Speech 4.1 2B which has no confirmed CoreML/ANE path and would need its own allocation.

### 7.7 Dropped

Carried from prior runs: Canary-Qwen (EN-only), Parakeet v1/v2 (superseded by v3), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24, §7.2.1), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B).

---

## 8. Progression

Cumulative run-over-run history — one row per role per run; this table only grows.

| run date | role | incumbent | verdict | best alternative |
|----------|------|-----------|---------|-------------------|
| 2026-05-10 | agentic_light | gemma4_e4b (gemma4-e4b-it) | upgrade | Qwen 3.5 4B |
| 2026-05-10 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-05-10 | audio_transcribe | whisper (large-v3-turbo) | runtime_upgrade | faster-whisper Turbo |
| 2026-05-10 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-07-12 | agentic_light | qwen35_4b (qwen3.5-4b) | keep | Gemma 3 4B (Catalan niche) |
| 2026-07-12 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-07-12 | audio_transcribe | whisper (large-v3-turbo) | runtime_upgrade | faster-whisper Turbo |
| 2026-07-12 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-07-24 | agentic_light | qwen35_4b (qwen3.5-4b) | keep | Gemma 3 4B (Catalan niche) |
| 2026-07-24 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-07-24 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | runtime_upgrade | parakeet + FluidAudio CustomVocabularyContext |
| 2026-07-24 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-08-06 | agentic_light | qwen35_4b (qwen3.5-4b) | keep | Gemma 3 4B (Catalan niche) |
| 2026-08-06 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-08-06 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | watch | — (FluidAudio fix disproven #401) |
| 2026-08-06 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-08-20 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-08-20 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-08-20 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | watch | — (no domain-tested alternative) |
| 2026-08-20 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-09-03 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-09-03 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-09-03 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | watch | Granite Speech 4.1 2B (candidate, untested) |
| 2026-09-03 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-09-17 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-09-17 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-09-17 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | watch | Nemotron 3.5 ASR Streaming (FluidAudio v0.15.7 decode-time biasing, candidate, untested) |
| 2026-09-17 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |

Reading the progression: seven consecutive runs now (2026-05-10 through 2026-09-17) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite eight genuinely new entrants across that span — every one dominated by architecture, size/fit, or unverified speed. `agentic_light` has been unchanged in substance since 2026-05-10 across eight runs. `audio_transcribe` has held `watch` for the fourth consecutive run, but its `best alternative` column has now changed twice in a row (2026-09-03, 2026-09-17) — the first time in the report's history this has happened, reflecting a genuinely fast-moving ASR-biasing landscape even though no verdict has actually flipped since 2026-07-24. `audio_translate` has been stable since 2026-05-10.

---

## 9. Open questions / uncertainty

- **NVIDIA Nemotron 3.5 ASR Streaming 0.6B (via FluidAudio v0.15.7) is now the recommended #1 local smoke test for `audio_transcribe`**, ahead of Granite Speech 4.1 2B. Test its decode-time keyword biasing against the #138/#343 jargon corpus (the "Claude Code" wake-phrase and "YOLO"/"yellow" cases specifically) before any verdict change. This project has twice recommended-then-disproven ASR claims that didn't survive contact with this exact corpus (#274, #401); a mechanism shipped by the same runtime already in production here reduces integration risk but does not exempt it from that same discipline. If it also fails, that result plus Granite Speech 4.1 2B's own (still-pending) test would be worth writing up together in a single issue/local-findings entry, since both would then share the "prompt/keyword-list biasing doesn't transfer to this project's specific dropped-word failure mode" root cause.
- **Nemotron 3 Super is architecturally the family's most ambitious `agentic_heavy` challenger yet but fails on the same three axes as last run's Lightning**: no fitting single-GPU quant, no consumer-hardware CPU-offload speed measurement (only professional multi-GPU and API numbers exist), and an undocumented Catalan capability. If idle capacity allows, the same `-ot .ffn_.*_exps.=CPU` split already proven for `glm` is the cheapest way to get a real local tok/s number, cross-referenced against #486's Catalan prompt set.
- **GLM-5.3-Flash has no non-coding composite quality evidence** — its headline benchmarks are all agentic-coding-adjacent (Terminal-Bench, AutomationBench), which this stack weights at 0%. Not urgent to chase further given the VRAM footprint already rules it out.
- **No fix currently exists for Parakeet's wake-phrase/jargon gap via FluidAudio's Parakeet-specific mechanism itself** — v0.15.7's decode-time biasing hook is Nemotron-only, and the Parakeet path (v0.15.5/v0.15.6) remains unchanged post-hoc rescoring (§7.2.1). The literal re-open trigger — "an alternative Parakeet checkpoint/architecture exposing such a hook" — is arguably met by Nemotron 3.5 ASR Streaming, pending the local test above.
- **Qwen3-ASR still has no domain-specific (jargon-heavy dictation) WER comparison**, and FluidAudio's experimental backend remains removed (#676). Unchanged status.
- **DeepSeek V4 Flash's composite quality on non-coding workloads is still unverified** — carried unchanged from the last three runs; not urgent given it still doesn't clearly beat Qwen3 32B dense's already-verified profile.
- **GLM-5.5's release is still a rumor** — Z.ai's 2026-09-13 $5B raise announcement is the newest signal, but points to "GLM-5.4" numbering next (Oct-Nov), not imminent GLM-5.5. Sixth consecutive run with no model card, benchmark, or endpoint for that exact name.
- **Qwen 4 is the clearest "watch next cycle" item on the text side** — rumored for the Apsara Conference (2026-09-22–24), which falls entirely inside the gap before the next scheduled bi-weekly run. Worth an out-of-cadence check if it ships with major headline claims.
- **Sourcing-gap pattern, now three occurrences deep:** GLM-5.3-Flash (shipped 2026-08-26, caught this run instead of last) and Nemotron 3.5 ASR Streaming (shipped months earlier per its arXiv writeup, caught only via this run's explicit "Nemotron" search) both follow the same pattern as Nemotron 3 Nano 4B (#486) and Nemotron 3.5 Lightning (last run): a relevant model existed before the report that first surfaced it. The explicit per-name "Nemotron" search (adopted last run) paid off again this run by also surfacing the ASR model; consider adding "GLM" as a second explicitly-searched name given two consecutive misses (GLM-5.3 itself was a near-miss two runs ago, GLM-5.3-Flash this run).
- Standing carries: Gemma 4 12B Unified license still unverified on the model card; Catalan on Qwen 3.6 35B-A3B still needs a local smoke test; MiniMax M3 at UD-Q2 still academic.

---

## 10. Current decisions (live, edited by `/swap-model`)

The decisions below mirror `config/models.yaml` → `roles:` at the time
this section was last updated. `/swap-model` rewrites both this section
and the yaml together, so the two stay in sync.

| Role | Model | Decided | Why |
|---|---|---|---|
| **agentic_light** | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | 2026-05-10 (model); 2026-08-12 (routing default, #489) | Tier A top pick since 2026-05-10 — hybrid Gated DeltaNet + sparse MoE on a 4B base, Q4_K_M ~3 GB, 262k native ctx, 201 languages, Apache 2.0. The role pointer moved to the no-think virtual alias on 2026-08-12 (same underlying weights); the thinking variant stays one alias away as `agentic_light_think`. |
| **agentic_heavy** | `gemma4_26b` (gemma4-26b-a4b-it) | 2026-05-10 | Tier B top pick. 99 t/s, 256k ctx, strong multilingual including Catalan. Tied with Qwen 3.6 35B-A3B (Apache 2.0) — Gemma stays default on Catalan track record. |
| **audio_transcribe** | `parakeet` (parakeet-tdt-0.6b-v3, Mac ANE) with `fallback: [whisper]` | 2026-07-22 | Changed directly via #350 (fleet placement/latency, #343 benchmark), not `/swap-model`. A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling). The identified remediation (FluidAudio custom-vocabulary) was tried and disproven (#401) — no current fix path via Parakeet itself; Nemotron 3.5 ASR Streaming (FluidAudio v0.15.7 decode-time biasing) and Granite Speech 4.1 2B are both untested candidate replacements — see §7. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Strict frontier reading recommends `watch` — the two-stage path (Turbo → Gemma 4 26B) is the default, leaving this slot as a fallback only. Keep defined and lazy-loaded; no active maintenance. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the September 17, 2026 snapshot.*
