# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-10-29
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant this window. Qwen 4's smallest previewed tier (27B) remains unshipped and two orders of magnitude above this role's size class |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists, tenth consecutive run. Two new entrants this window — **Muse Spark Open** (Meta, 816B/42B active, weights live 2026-10-15) and **Nemotron 3.5 Bolt** (74B/9B active, weights live ~2026-10-07/15) — both dominated: Muse Spark Open fails the ~144 GB combined budget (~459 GB at Q4_K_M) *and* ships under a restrictive non-commercial-leaning license; Nemotron 3.5 Bolt has no independently published quality benchmark to justify a local test. Mistral Large 4's open weights still have not shipped |
| `audio_transcribe` | `parakeet` (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Cohere Transcribe (2B) / Nemotron Unified ASR (0.6B) / Nemotron 3.5 Transcribe Lite (all untested, new this run) | — | Parakeet Ultra stands; no upgrade found. FluidAudio's v0.18.0 (2026-10-28, yesterday) shipped three new transcribe-adjacent models and a genuine sourcing-gap catch (Cohere Transcribe, live since March 2026, never previously tracked) — none has a domain-specific (jargon/wake-phrase) measurement against this project's own corpus, so none changes the verdict yet |
| `audio_translate` | `whisper_translate` (whisper-medium) | **upgrade** | **Canary-1b-v2** (NVIDIA, via FluidAudio ANE, v0.18.0) | — | First concrete native single-model ES→EN candidate this report has found. CC-BY-4.0, EN ↔ 24 European languages incl. Spanish, deployable on the exact Mac Mini ANE infrastructure already serving `audio_transcribe`'s Parakeet Ultra — no new hardware or process type. Not yet locally tested against this project's own audio or against the two-stage default's combined translate+polish+disfluency pass (workload H); recommend the same build-then-pre-flight path that got Parakeet Ultra approved |

**Diff vs previous run (2026-10-15):** `audio_translate` flips from `watch` to `upgrade` — NVIDIA's **Canary-1b-v2** speech-translation model shipped on the Mac Mini's existing ANE infrastructure via FluidAudio v0.18.0 (2026-10-28), the first concrete native single-model translate candidate this report has found in nine prior runs. The other three roles hold. On the audio side there is also a genuine sourcing-gap catch — **Cohere Transcribe** (2B, Apache 2.0, EN+ES, live since March 2026 and FluidAudio-integrated since April 2026) never appeared in any of the previous eight runs — plus two more new-but-untested transcribe candidates from the same FluidAudio release (Nemotron Unified ASR, Nemotron 3.5 Transcribe Lite). On the LLM side, two new oversized entrants (Muse Spark Open, Nemotron 3.5 Bolt) were sourced and dropped on the usual size/license/unverified-quality grounds, and Mistral Large 4's open weights remain unshipped as of this run — now two days from Mistral's own promised 2026-10-31 ETA.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of late October 2026.

**What changed since 2026-10-15:** Unlike the preceding quiet window, this run has a real headline — the audio annex, not the text side, produced the first genuine upgrade candidate for `audio_translate` since the report began. FluidAudio's v0.18.0 release (2026-10-28, literally one day before this run) folded NVIDIA's Canary-1b-v2 speech-translation model into the same Swift ANE worker that already serves Parakeet, which means the deployment path exists today with no new infrastructure. Separately, a sourcing-gap audit of the audio side turned up Cohere Transcribe — a model that has been live and FluidAudio-integrated since April 2026 but never once appeared in this report. On the text side nothing shipped that clears this box's budget: two new oversized MoEs (Muse Spark Open, Nemotron 3.5 Bolt) were sourced and dropped, and Mistral Large 4's open weights are still not live.

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

1. Read `docs/frontier/runs/LATEST` (2026-10-15), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — the same three unresolved entries carry forward unchanged (faster-whisper CTranslate2, FluidAudio `CustomVocabularyContext`, Nemotron 3 Nano 4B). None of this run's new candidates match an unresolved entry, so no local-findings override applies.
2. Re-read `config/models.yaml` → `roles:` — no out-of-band change since 2026-10-15 (all four roles match the previous run's recorded incumbents exactly).
3. Surveyed the external landscape for the 2026-10-15 → 2026-10-29 window using the established explicit per-name searches: "Nemotron" (no primary-source October announcement, but a secondary roundup flagged **Nemotron 3.5 Bolt** weights live 2026-10-07/15 — followed up and verified against Unsloth/llm-stats docs below), "GLM" (GLM-5.5 still unshipped past its rumored August 2026 target — sixth consecutive run with nothing), "Qwen" (no new text-model release; Qwen3.8-Omni-Flash remains the newest Qwen entry in a general release tracker, dated September), "DeepSeek" (one minor update, V4.1-Flash-1017 on 2026-10-17, coding/multi-step focused — out of scope, doesn't change the fit verdict), "Mistral" (Mistral Large 4's open weights are **still not live** — a Hugging Face listing titled `Mistral-Large-4-1T-A52B` carries an ETA of 2026-10-31, two days after this run; the repo name resolves the previous run's ~49-52B active-parameter ambiguity to ~52B, but no weights or license text are published yet). Swept GPT-OSS/Phi/Gemma/Llama/Granite/Command/Hermes-Nous/Mixtral/Falcon/OLMo/InternLM/Yi for anything new in the window — nothing found. The same sweep surfaced **Muse Spark Open** (Meta, 816B total/42B active MoE, weights live on Hugging Face 2026-10-15, "Muse Model License" with commercial-use limits) via a secondary German-language roundup — verified independently across multiple outlets citing the Hugging Face model card.
4. On the audio side: queried FluidAudio's GitHub releases directly via `gh api repos/FluidInference/FluidAudio/releases` (the authoritative source established since the 2026-10-01 run). Found **v0.18.0** (2026-10-28, one day old): "Nemotron Unified ASR, Nemotron 3.5 Transcribe Lite, Bark 3.0, Canary translation" — fetched the full release body via `gh api repos/FluidInference/FluidAudio/releases/tags/v0.18.0`. This is the run's headline finding: **Canary speech translation** (NVIDIA's Canary-1b-v2, en ↔ 24 European languages via prompt, pause-aligned long-form decoding) is now served on the Mac Mini's ANE through the same Swift worker that runs Parakeet. Verified Canary-1b-v2's own card independently (released 2025-08-14, CC-BY-4.0, 25-language transcribe + bidirectional EN↔24-language translate, FastConformer encoder + Transformer decoder) — it predates this report by months but was never wired into a locally-runnable path until yesterday's FluidAudio release, which is the actual trigger for evaluating it now. Also in v0.18.0: Nemotron Unified ASR 0.6B (25 European languages incl. Spanish, offline+streaming) and Nemotron 3.5 Transcribe Lite (encoder+CTC-only, no LLM decoder, extremely fast on ANE — FluidAudio's own benchmark reports a 49.5-minute meeting transcribed in ~5.5 s on an M5 Pro) — both untested on this project's domain corpus, so neither changes the `audio_transcribe` verdict. Separately sourced **Cohere Transcribe** (2B, Apache 2.0, 14 languages incl. EN/ES) via the HF Open ASR Leaderboard's 2026-10-09 update (secondary blog coverage) and confirmed it has been FluidAudio-integrated since **v0.14.0 (2026-04-23)** — a genuine sourcing gap: it has been live and locally deployable for over six months and never once appeared in this report's eight prior runs.
5. Computed VRAM for this window's new oversized LLM entrant (Muse Spark Open, 816B total/42B active) with the standing rule of thumb **Q4_K_M ≈ 4.5 bits/param** on total params for MoE — fails the combined ~144 GB budget by roughly 3×, same conclusion as every prior oversized entrant (see §4's worked example). Nemotron 3.5 Bolt (74B/9B active) fits the combined budget comfortably at its smallest published 2-bit quant (~22 GB) but carries no independently published quality benchmark.
6. Applied the honesty rules: date-stamped every claim; flagged Mistral Large 4's license as still unannounced and its weights as still unshipped (two days from its own ETA); flagged Nemotron 3.5 Bolt's quality as unverified (no benchmark found anywhere, including vendor self-reporting) and left it unscored (`quality: 0` placeholder, consistent with how prior unscored oversized/unverified entrants were recorded); flagged Canary-1b-v2 and the two new Nemotron ASR models as untested against this project's own audio — none gets an `upgrade`/`runtime_upgrade` label without at least a directional justification, and Canary-1b-v2's justification (same deployment infra, permissive license, native bidirectional EN/ES translate capability, concrete release trigger) is the strongest this report has seen for this role, hence the verdict change despite the absence of a local measurement.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's cautionary tale is **Muse Spark Open** — the same shape of failure as Mistral Large 4, DeepSeek V4.1 Flash, and Nemotron 3 Ultra before it, but with a second, independent failure mode stacked on top: a restrictive license.

```
816B total params, 42B active (MoE, 256 experts / 8 active per token)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling

At this report's standard assumption (Q4_K_M, ~4.5 bits/param, on TOTAL params for MoE):
  816B x 4.5 bits / 8 = ~459 GB  -- roughly 3.2x the 144 GB budget

Even at the smallest viable GGUF quant for a usable MoE (2-bit, ~2.0625 bits/param):
  816B x 2.0625 bits / 8 = ~210 GB  -- still ~1.5x past budget

And even if the budget math worked out: the weights ship under Meta's "Muse
Model License" (commercial-use limits), not a permissive license like
Llama's or Gemma's -- a second, independent reason to drop it regardless
of fit.
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across ten consecutive runs. A trillion-parameter-class flagship simply isn't the right shape for a single 16 GB + 128 GB workstation, full stop — the same conclusion reached for DeepSeek V4 Pro, Qwen3.8 2.4T, Kimi K3, GLM-5.2/5.3, DeepSeek V4.1 Flash, Nemotron 3 Ultra, and now Mistral Large 4 and Muse Spark Open.

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

No change this run. Qwen 4's smallest previewed tier is 27B — nowhere near this role's size class even once it ships.

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

No change this run.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen3 32B dense | 32B | Q4_K_M | ~19.5 GB (spill) | ~11 | 84 | 128k | Apache 2.0 | yes |
| GLM-5.3-Flash | 320B / 18B active | GGUF (Unsloth dynamic 3-bit) | ~110 GB (spill) | ~12 est | 76 est | 1M | MIT | no |
| Nemotron 3 Super | 120B / 12B active | Q4_K_M | ~70 GB (spill) | ~6 est | 78 est | 128k | NVIDIA Nemotron Open Model License | no |
| **Nemotron 3.5 Bolt** *(new)* | 74B / 9B active | GGUF (IQ2_XXS, smallest published) | ~22 GB (spill) | ~15 est | unverified | 128k-262k (sources disagree) | OpenMDW-1.1 | no |
| DeepSeek V4.1 Flash | 748B total / 8-16B active | GGUF (2-bit floor) | ~187-421 GB (doesn't fit) | n/a | unverified | 1M | MIT | no |
| Nemotron 3 Ultra | 550B / 55B active | Q4_K_M | ~309 GB (spill) | ~1-2 est | unverified | 262k | OpenMDW-1.1 | no |
| Mistral Large 4 | ~1.05T total / ~52B active | GGUF (not yet published) | ~263-563 GB (doesn't fit) | n/a — no weights | unverified | 1M (est.) | unannounced | no |
| **Muse Spark Open** *(new)* | 816B total / 42B active | GGUF (not clearly published) | ~210-459 GB (doesn't fit) | n/a | unverified | 1M | Muse Model License (restrictive) | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

**Nemotron 3.5 Bolt added** — the same family's "edge" tier alongside the already-tracked Nemotron 3.5 Lightning (30B/3B active), compressed from the larger Super VL line specifically for speed. Fits the ~144 GB combined budget comfortably at its smallest published quant (~22 GB, mostly VRAM with a small RAM spill) — unlike every other new entrant this report has tracked, hardware fit isn't the problem. The problem is that no independent quality benchmark exists anywhere, including from NVIDIA's own materials for this specific checkpoint; without that, there's nothing to justify displacing the already-tied Tier B leaders or the Tier C Qwen3 32B pick. Flagged for re-check once a benchmark surfaces.

**Muse Spark Open added** — see §4's worked example. Announced and shipped weights 2026-10-15; dominated on both size (~210-459 GB depending on quant, 1.5-3.2× the combined budget) and license (Meta's "Muse Model License" carries commercial-use limits, unlike Llama's or Gemma's terms).

### Models considered and dropped this run

- **Nemotron 3.5 Bolt (NVIDIA, 74B/9B active MoE, weights live ~2026-10-07/15)** — see Tier C above. Fits the budget; no benchmark to act on. Watch for a published quality number.
- **Muse Spark Open (Meta, 816B/42B active MoE, weights live 2026-10-15)** — see Tier C above and §4. Dominated on size *and* license.
- **DeepSeek V4.1-Flash-1017 (2026-10-17 update to the already-tracked DeepSeek V4.1 Flash)** — coding and multi-step-task improvements per the vendor's own description; coding carries 0% weight here, and the underlying model still fails the combined budget at 748B total. No change to the existing dropped entry.
- **Mistral Large 4** — open weights still not live as of this run (2026-10-29); a Hugging Face listing (`Mistral-Large-4-1T-A52B`) shows an ETA of 2026-10-31, two days away. License still unannounced. Watch next cycle — this is exactly the kind of release that could land between runs; a notable-launch re-check (per this skill's own on-demand cadence note) is reasonable once weights actually appear.
- **GLM-5.5** — still unshipped past its rumored August 2026 target. Sixth consecutive run with nothing to report for this exact name.
- Standing NO-GOs carried unchanged: DeepSeek V4.1 Flash, Nemotron 3 Ultra, GLM-5.3-Flash, Nemotron 3 Super, Qwen3.8-Flash-Next, Nemotron 3.5 Lightning, DeepSeek V4 Pro, Qwen3.8 2.4T-A95B, Kimi K3, Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2 (Tier B entry, listed above for comparison), Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`), Qwen 4 (still unshipped preview).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window. If `audio_translate` is swapped to Canary-1b-v2 after a local test (§7.5), it runs on the exact same Mac Mini ANE allocation as Parakeet Ultra (same CoreML-class path, same ballpark footprint) — it would not change any concurrency math on the tower side, only the Mac's ASR process mix.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 Parakeet Ultra still stands — no transcribe upgrade, but three new candidates sourced

No change to the live incumbent. FluidAudio shipped **v0.18.0** on 2026-10-28 — one day before this run — with three new ASR entries, none of which has been measured against this project's own domain corpus (the #138/#343 jargon/wake-phrase set):

- **Nemotron Unified ASR 0.6B** — NVIDIA's multilingual Parakeet Unified, 25 European languages including Spanish, both offline and streaming modes (0.56 s / 1.12 s / 2.08 s latency tiers). A real transcribe-role candidate in principle, but untested here.
- **Nemotron 3.5 Transcribe Lite** — the ASR encoder + CTC timestamp head of NVIDIA's Nemotron 3.5 Transcribe, with no LLM decoder. Extremely fast on the Neural Engine — FluidAudio's own benchmark reports a 49.5-minute meeting transcribed in ~5.5 s on an M5 Pro (roughly 540× real-time). Language coverage for this specific lite variant isn't independently confirmed for Spanish; flagged as an open question (§9).
- **Phonon-2** (shipped earlier, in v0.17.5, already covered by the previous run) stays English-only and doesn't help this project's EN+ES bar — no change.

Separately, a sourcing-gap audit of the audio side (prompted by how cleanly Mistral surfaced on the LLM side two runs ago after being added to the explicit per-name list) turned up **Cohere Transcribe**: a 2B-parameter, Apache 2.0-licensed ASR model covering 14 languages including English and Spanish, open-sourced by Cohere in March 2026 and FluidAudio-integrated since **v0.14.0 (2026-04-23)** — over six months before this run, and never once mentioned in any of the eight previous reports. It currently ranks competitively on the HF Open ASR Leaderboard's 2026-10-09 update (~5.77% average WER, behind Qwen3-ASR-1.7B's 5.40% and ahead of Granite Speech 4.1's 5.96%), but like every other secondary candidate here, that's a generic benchmark, not a measurement against this project's own jargon/wake-phrase domain. The verdict for `audio_transcribe` this run stays **`keep`** — none of the three new names has evidence of beating Parakeet Ultra on the axis that actually matters for this role.

### 7.2 The headline finding: Canary-1b-v2 becomes deployable for `audio_translate`

The same v0.18.0 release ships **Canary speech translation** — NVIDIA's Canary-1b-v2 (originally released 2025-08-14, CC-BY-4.0), now served through the identical FluidAudio Swift worker that already runs Parakeet on the Mac Mini's Neural Engine. Canary-1b-v2 transcribes 25 European languages and translates bidirectionally between English and 24 of them — including Spanish — via a prompt-driven FastConformer encoder + Transformer decoder, with pause-aligned long-form decoding for real dictation-length audio.

This is a materially different situation from every previous `audio_translate` entry in this report's history. The role has carried `watch` for nine consecutive runs because every candidate (Granite Speech 4.1 2B's native bidirectional translate, most recently) was a *theoretical* secondary candidate with no concrete deployment path on this fleet. Canary-1b-v2 is the first one where the deployment path **already exists as of yesterday** — same hardware (Mac Mini ANE), same engine family (FluidAudio), same operational pattern as the live `audio_transcribe` role. That's enough to justify an `upgrade` verdict rather than another `watch`, even without a local measurement yet: the question is no longer "is there a plausible alternative out there" but "does it actually perform well enough on this project's own ES audio to replace or front the lazy `whisper_translate` fallback" — a question a pre-flight test can answer directly, the same way Parakeet Ultra's pre-flight answered it for the transcribe role.

What a pre-flight test needs to check before any `/swap-model`: (1) translation quality (BLEU-style or spot-check) against this project's own ES audio samples, compared to both `whisper_translate`'s `task=translate` output and the two-stage default's output; (2) whether Canary's output needs the same LLM polishing pass workload H already applies, since Canary does translation only, not disfluency removal; (3) latency and footprint on the actual Mac Mini ANE, not just FluidAudio's own generic benchmarks.

### 7.3 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (measured/est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| ★ Parakeet Ultra (Mac ANE, current incumbent) | 0.6B | ~2 GB (CoreML) | 126.7× measured (M-series ANE), parity with v3 | ✅ (better WER) | ✅ (FLEURS 24-lang 11.67% vs v3's 14.81%) | ❌ | Live incumbent since 2026-10-02 (#640). No change this window. |
| ◇ Parakeet TDT v3 (Mac ANE, superseded 2026-10-02) | 0.6B | ~2 GB (CoreML) | 128.6× measured (ANE) | ✅ | ✅ (25 langs) | ❌ | Historical. |
| ◆ Whisper Large v3 Turbo (automatic failover) | 809M | ~1.6 GB | 40× tower / 19.3× gaming (measured, boosted) | ✅ | ✅ | ❌ | Unchanged. Still the accuracy leader on the jargon domain thanks to `--carry-initial-prompt` boosting (#91). |
| ✦ **Canary-1b-v2** (Mac ANE via FluidAudio v0.18.0) *(new — proposed `upgrade` for `audio_translate`)* | 1B | ~2 GB est (CoreML) | TBD (local test) | ✅ | ✅ (translate-capable) | ✅ (EN ↔ 24 langs incl. ES) | New this run (§7.2). First concrete, deployable native translate candidate for this role in nine runs. Pending a local pre-flight test before any swap. |
| NVIDIA Nemotron Unified ASR 0.6B *(new — untested)* | 0.6B | ~2 GB est (CoreML) | sub-100ms TTFT (vendor) | ✅ | ✅ (25 European langs) | ❌ | New this run (§7.1). Secondary transcribe candidate, no domain measurement yet. |
| NVIDIA Nemotron 3.5 Transcribe Lite *(new — untested)* | 0.6B-class (encoder+CTC, no LLM decoder) | ~1-2 GB est | ~540× est. (vendor, M5 Pro ANE) | ✅ | unclear — coverage not independently confirmed | ❌ | New this run (§7.1). Extremely fast; Spanish coverage is an open question (§9). |
| Cohere Transcribe (2B) *(new — sourcing-gap catch)* | 2B | ~2 GB est (CoreML via FluidAudio since v0.14.0) | HF Open ASR Leaderboard (2026-10-09): ~5.77% avg WER (generic) | ✅ | ✅ (14 langs total) | ❌ | New this run (§7.1). Live and FluidAudio-integrated since 2026-04-23; never previously tracked by this report — a genuine sourcing gap, now closed. |
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B (secondary candidate) | 0.6B | ~2 GB est (CoreML) | TBD (domain) | ✅ (FLEURS 7.91% — worse than Parakeet's card) | ✅ (FLEURS 4.11% — worse than Parakeet's card) | ❌ | No change this window. Still the live secondary priority for the #401 jargon axis. |
| Granite Speech 4.1 2B (secondary candidate) | 2B | ~2 GB est | TBD (domain); HF leaderboard 2026-10-09: 5.96% avg WER | ✅ | ✅ (6 langs, bidirectional AST) | ✅ | No change this window — secondary priority, now joined by Canary-1b-v2 as a second native-translate candidate (Canary has the live deployment path; Granite Speech doesn't yet). |
| ✗ IBM Granite Speech 5.0 Turbo CTC | 470M | ~1 GB | 5.00%/4.85% self-reported aggregate EN WER | ✅ | ❌ | ❌ | Dropped on scope — English-only (§7 last run). |
| ✗ Parakeet + FluidAudio CustomVocabularyContext | 0.6B + ~97 MB CTC model | ~2 GB + rescorer | n/a — disproven, not shipped | — | — | ❌ | Disproven #401 (2026-07-24). Carried `watch` per `local-findings.md`. No further FluidAudio release touches the mechanism. |
| faster-whisper Turbo (CT2) | 809M (CT2) | ~1.0 GB INT8 | measured 1.0× vs whisper.cpp — **disproven** | ✅ | ✅ | ❌ | Carried `watch` per `docs/frontier/local-findings.md` (#277). |
| Qwen3-ASR (1.7B) | 1.7B | ~1.5 GB | HF leaderboard (2026-10-09): 5.40% avg WER EN — now the top-ranked open model generically | ✅ | ✅ | ❌ | `watch`, unchanged status; updated leaderboard figure. |

### 7.4 Workload F — transcribe EN/ES

**Verdict: `keep`.** The brief's standing question about a strict EN/ES transcribe upgrade over the incumbent is answered "no" this run — three new candidates were sourced (Cohere Transcribe, Nemotron Unified ASR, Nemotron 3.5 Transcribe Lite) but none has a domain-specific measurement against this project's own jargon/wake-phrase corpus (#401/#343), and the #401 regression itself remains open and unaddressed by any of them.

### 7.5 Workload G — ES audio → English

**Verdict: `upgrade` (new this run).** The two-stage default (faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call, ~15 GB total) stays the *current* default pending a test, but Canary-1b-v2 via FluidAudio ANE (§7.2) is now a concrete architecture (iii) — a purpose-built single-model speech translator deployable today on existing hardware, distinct from both the single-model faster-whisper fallback (architecture i, a repurposed ASR decoder) and the two-stage LLM pipeline (architecture ii). It does not replace workload H's polish/disfluency pass — that still needs an LLM call regardless of which transcription/translation front-end feeds it — but it is a strong candidate to replace or front the lazy `whisper_translate` fallback leg, and potentially to simplify the default path for straightforward dictation where polish isn't needed. Recommend building + pre-flight testing before any `/swap-model` (§7.2).

### 7.6 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`). This holds regardless of which transcribe/translate front-end eventually feeds it.

### 7.7 Concurrency footprint

No change — this is a proposal, not an applied swap. If Canary-1b-v2 is adopted after testing, it runs on the same Mac Mini ANE allocation as Parakeet Ultra; whisper-turbo (gaming satellite), orpheus (tower), and piper (tower CPU) are unaffected either way.

### 7.8 Dropped

Carried from prior runs: Canary-Qwen (EN-only, a *different* NVIDIA model from Canary-1b-v2 — don't confuse the two), Parakeet v1/v2 (superseded), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B), Parakeet Redux (worse WER than both v3 and Ultra, dominated), Parakeet TDT v3 (superseded by Ultra, 2026-10-02), IBM Granite Speech 5.0 Turbo CTC (English-only). **New this run:** Qwen3.8-LiveTranslate and the broader Qwen-Audio 3.1 suite (Alibaba, launched Sept 2026) — API-only cloud services with no open-weight release timeline, out of scope for this project's local-inference requirement.

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
| 2026-10-01 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-10-01 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-10-01 | audio_transcribe | parakeet (parakeet-tdt-0.6b-v3) + whisper fallback | runtime_upgrade | Parakeet Ultra (FluidAudio v0.17.3, same speed, better WER, verified) |
| 2026-10-01 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-10-15 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-10-15 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-10-15 | audio_transcribe | parakeet (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Nemotron 3.5 ASR Streaming / Granite Speech 4.1 2B (jargon axis, untested) |
| 2026-10-15 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B |
| 2026-10-29 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-10-29 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-10-29 | audio_transcribe | parakeet (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Cohere Transcribe / Nemotron Unified ASR / Nemotron 3.5 Transcribe Lite (all untested, new this run) |
| 2026-10-29 | audio_translate | whisper_translate (whisper-medium) | upgrade | Canary-1b-v2 (NVIDIA, via FluidAudio ANE v0.18.0, pending local test) |

Reading the progression: ten consecutive runs now (2026-05-10 through 2026-10-29) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite twelve genuinely new entrants across that span — every one dominated by architecture, size/fit, license, or unverified speed/quality. `agentic_light` has been unchanged in substance since 2026-05-10 across eleven runs. `audio_transcribe` has sat in `watch`/`keep` since its last upgrade cycle completed (2026-10-01 → 2026-10-15), with this run's three new sourced candidates all untested rather than disproven — the next transcribe upgrade, if any, likely comes from testing one of them against the #401 jargon corpus. `audio_translate` breaks its longest streak in this report's history — nine straight `watch` verdicts (2026-05-10 through 2026-10-15) — on the strength of a genuinely new deployment path rather than a quality re-measurement, which is a different kind of evidence than every prior verdict change in this table.

---

## 9. Open questions / uncertainty

- **Canary-1b-v2's actual translation quality on this project's own ES audio is completely untested.** Everything in §7.2/§7.5 is directional (license, language coverage, deployment-path existence) — the next step before any `/swap-model` is a local pre-flight: BLEU-style or spot-check comparison against `whisper_translate`'s `task=translate` output and the two-stage default's output, on real dictation-length ES audio.
- **Nemotron 3.5 Transcribe Lite's language coverage for Spanish is not independently confirmed** — it inherits from Nemotron 3.5 Transcribe's lineage but FluidAudio's own release notes don't state the lite variant's exact locale list. Worth checking before including it in any transcribe-role evaluation.
- **Mistral Large 4's open weights still have not shipped** as of this run (2026-10-29) — a Hugging Face listing shows an ETA of 2026-10-31, two days away. The repo name `Mistral-Large-4-1T-A52B` resolves the previous run's active-parameter ambiguity to ~52B, but license terms remain unpublished. This is a good candidate for an on-demand re-check once the ETA passes, rather than waiting for the next scheduled run.
- **Nemotron 3.5 Bolt has no independently published quality benchmark anywhere**, including from NVIDIA's own materials for this specific checkpoint — it fits the hardware budget comfortably (unlike most new oversized entrants), so a benchmark surfacing would make it immediately actionable rather than dropped on fit grounds.
- **The Cohere Transcribe sourcing gap (live since March 2026, FluidAudio-integrated since April 2026, never tracked until this run) is a reminder that this report's "new entrants" sweep for ASR has so far leaned on FluidAudio's own release notes rather than an independent audit of the HF Open ASR Leaderboard** — the same category of gap that caught Mistral on the LLM side two runs ago. Worth periodically cross-checking the leaderboard directly rather than relying solely on FluidAudio's changelog as the audio-side discovery mechanism.
- **A rumored next-generation "Nemotron 4" (>1T params) still has no official NVIDIA confirmation** — unchanged from the previous run. Too speculative to score.
- **GLM-5.5 is still unshipped** past its own rumored August 2026 target — sixth consecutive run with no model card, benchmark, or endpoint.
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
| **audio_transcribe** | `parakeet` (Parakeet Ultra, a parakeet-tdt-0.6b-v3 derivative, Mac ANE) with `fallback: [whisper]` | 2026-07-22 (role primary, #350); 2026-10-02 (checkpoint → Ultra, #640) | A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling; #401 stays open). No further upgrade found through this run (2026-10-29) — see §7.1 for this window's three newly-sourced, still-untested candidates. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Still the configured default as of this run — the `upgrade` verdict above (Canary-1b-v2) is a recommendation pending a local pre-flight test, not yet applied. Keep defined and lazy-loaded until a test either confirms or disproves the swap. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the October 29, 2026 snapshot.*
