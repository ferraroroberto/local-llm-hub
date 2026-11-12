# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-11-12
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant this window either. Qwen 4's smallest previewed tier (27B) remains unshipped and two orders of magnitude above this role's size class |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists, eleventh consecutive run. Mistral Large 4's open weights shipped 2026-10-29 (confirmed, same day as the previous run) but still fail the ~144 GB combined budget by ~1.8-3.9x. Nemotron 3.5 Bolt gained real published benchmarks this window — mixed/coding-skewed, loses to Gemma on IFBench (the polish axis), and still has zero multilingual verification, the same gap that sank Nemotron 3 Nano 4B locally (#486) |
| `audio_transcribe` | `parakeet` (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Cohere Transcribe / Nemotron Unified ASR / Nemotron 3.5 Transcribe Lite (all untested, carried from last run) | — | Parakeet Ultra stands. FluidAudio shipped three releases this window (v0.18.1, v0.18.2, v0.18.3) that tighten the Parakeet vocabulary-boosting CTC rescorer, but it is the same swap-only mechanism already disproven locally (#401) — it still cannot insert a dropped phrase, so the standing jargon/wake-phrase regression is unaddressed and the re-open trigger is not met |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B MoE | — | **Correction to this run's own first draft:** the local pre-flight test this report recommended on 2026-10-29 already ran and closed (#920 → PR #990, merged 2026-11-07, five days before this run) — see `docs/frontier/local-findings.md`'s new entry. Canary-1b-v2 beats the configured role model `whisper_translate` on speed/reliability (BLEU gap inside noise) but loses to the two-stage default on quality (25.4 vs 32.6 BLEU), and no hub-side server exists for it; the issue closed with no Canary integration planned. Reverts to the pre-2026-10-29 pattern |

**Diff vs previous run (2026-10-29):** This run initially drafted `audio_translate` as a carried-forward `upgrade` (Canary-1b-v2), matching the previous run's recommendation and assuming no local test had happened yet. **That assumption was wrong and has been corrected before shipping this report.** A sonnet subagent dispatched in step 8 (filing the issue the `upgrade` verdict calls for) found the pre-flight test had already run and closed five days before this run started: issue [#920](https://github.com/ferraroroberto/local-llm-hub/issues/920), shipped via [PR #990](https://github.com/ferraroroberto/local-llm-hub/pull/990) (merged 2026-11-07), write-up at `docs/canary-translate-preflight.md`. The test found Canary-1b-v2 beats the literal configured role model (`whisper_translate`) on speed and reliability but loses to the de facto quality default (the two-stage `whisper` → `gemma4_26b` pipeline) on BLEU, and building the hub-side integration for a model that still loses to the existing default was judged not worth it — no swap, no Canary server planned. `docs/frontier/local-findings.md` did not get this disproof recorded in PR #990 as the skill's local-findings contract requires; this run adds that entry now. `audio_translate` therefore reverts from `upgrade` to `watch`, matching the pattern that held for nine consecutive runs before 2026-10-29. The other three roles hold in substance: `agentic_heavy`'s two standing open threads both resolved with more information but no change of verdict — Mistral Large 4 confirmed shipped its open weights on 2026-10-29 and still fails the hardware budget by 1.8-3.9x; Nemotron 3.5 Bolt gained its first published benchmarks (mixed/coding-skewed, loses to Gemma on IFBench) but remains unverified on the multilingual axis, so it stays `watch`, not `upgrade`. Two new oversized/out-of-scope LLM entrants were sourced and dropped: Kimi K3.1 Pro (version bump of the already-tracked Kimi K3) and Ripple-65B Preview (a from-scratch 65B dense coding model, 0% weight here). A new open question was flagged: PLaMo 3 Translate (Preferred Networks) could matter for workload E but has no confirmed Spanish/Catalan coverage yet. On the audio side, FluidAudio shipped v0.18.1/v0.18.2/v0.18.3 — tighter guards on the same disproven CTC vocabulary rescorer (no change to `audio_transcribe`).

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of mid-November 2026.

**What changed since 2026-10-29:** The headline of this run is a correction, not a new finding — the previous run's `audio_translate` `upgrade` recommendation for Canary-1b-v2 was already tested and closed out locally (issue #920, PR #990, merged 2026-11-07) before this run started, and the result was "no swap." This run's step 8 (filing the issue the `upgrade` verdict calls for) caught that the recommendation was stale and corrected it rather than re-filing duplicate work. On the LLM side, both of the previous run's standing open threads (Mistral Large 4's unshipped weights, Nemotron 3.5 Bolt's missing benchmark) resolved with new information, and neither changes a verdict.

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

1. Read `docs/frontier/runs/LATEST` (2026-10-29), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — at read time, three entries were unresolved (faster-whisper CTranslate2, FluidAudio `CustomVocabularyContext`, Nemotron 3 Nano 4B) and none matched this run's new candidates. A fourth entry (Canary-1b-v2 for `audio_translate`) was added **during** this run, after step 8 surfaced that the candidate had already been tested and resolved locally — see §0's diff line.
2. Re-read `config/models.yaml` → `roles:` — no out-of-band change since 2026-10-29 (all four roles match the previous run's recorded incumbents exactly).
3. Surveyed the external landscape for the 2026-10-29 → 2026-11-12 window on two parallel tracks (text LLMs and ASR), each closing out a standing open thread from the previous run before sweeping for new entrants.
4. **Mistral Large 4:** confirmed open weights landed 2026-10-29 at `mistralai/Mistral-Large-4-1T-A52B`, ~1T total/~52B active MoE, under Mistral's "Ecosystem Open-Weight License" (a modified-MIT-style term with reported reseller restrictions per secondary coverage — not fully permissive). BF16 needs data-center-class hardware; even the smallest practical GGUF floor computed in the previous run (~263 GB at 2-bit) is ~1.8x this box's ~144 GB combined budget, and the standard Q4_K_M rule of thumb (~562.5 GB) is ~3.9x over. Vendor claims best-open-weight-for-coding (0% weight here) and general capability; no independent, workload-relevant benchmark found.
5. **Nemotron 3.5 Bolt** (74B/9B active): gained its first published benchmarks this window. Artificial Analysis Intelligence Index 21.4 (vs. Nemotron 3 Ultra's 22.9). Leads Terminal-Bench 2.1 (~51 vs. Qwen 3.6 35B-A3B's 44.4, Gemma 4 26B's 37.2 — a coding-agent benchmark, arguably 0%-weighted here) and BrowseComp (66.75 vs. Qwen's 48.74); **loses to Gemma on IFBench** (66.17 vs. 77.25 — the 25%-weighted polish axis) and roughly ties Qwen on PinchBench. Measured decode speed (~84 tok/s on unspecified comparable hardware, not confirmed on this exact GPU) trails both incumbents (Qwen ~97, Gemma ~99-101). **No multilingual (ES/CA) benchmark exists anywhere for this checkpoint** — the exact gap that sank Nemotron 3 Nano 4B locally (#486, see `local-findings.md`). Fits the combined budget at its smallest published 2-bit checkpoint (~22-24 GB).
6. **GLM-5.5:** still unshipped past its rumored August 2026 target — seventh consecutive run with nothing for this exact name.
7. General sweep across Qwen (Qwen 4 remains unconfirmed/in training, "very soon" per Alibaba with no date), Llama, Gemma, Phi, Granite, and non-coding DeepSeek updates: nothing new in this specific window. Two genuinely new items surfaced and dropped: **Kimi K3.1 Pro** (Moonshot, 2.8T total/104B active — same size class as the already-NO-GO Kimi K3, just a version bump) and **Ripple-65B Preview** (Ricursive, 65B dense, Apache 2.0, trained from scratch on 12T tokens, ~130 GB BF16/~57 GB NVFP4) — fits the combined budget with heavy CPU offload but is an explicit coding model (Terminal-Bench-focused, 0% weight here), has no multilingual data, and would run at dense-70B-class speeds (~4 tok/s class), dominated by the already-tracked Qwen3 32B dense. Also surfaced but not deeply vetted: **PLaMo 3 Translate** (Preferred Networks, 0.8B-31B, official GGUF) — a translation-specialist family that could matter for workload E, but no confirmed Spanish/Catalan coverage was found in this pass; flagged as an open question (§9) rather than a scored candidate.
8. On the audio side: queried FluidAudio's GitHub releases directly. Three new tags landed in this window — **v0.18.1** (2026-11-01), **v0.18.2** (2026-11-01), **v0.18.3** (2026-11-07) — all tuning the existing Parakeet `VocabularyBoostingSession` CTC rescorer (new `Thresholds` struct, a `detectedTerms` floor, tighter false-positive guards, preserved punctuation around replacements). This is the identical post-hoc-swap mechanism already built and disproven locally (#401, 2026-07-24): it can still only **swap** an existing token for a vocabulary term, never **insert** one the TDT decoder dropped entirely, so "Claude Code"→"Yes" and "YOLO"→"yellow" remain structurally unrecoverable. The re-open trigger (decode-time biasing that can insert a dropped phrase) is **not met** — no verdict change for `audio_transcribe`.
9. **Canary-1b-v2 for `audio_translate`** — researched as "untested, upgrade recommendation carried from last run" per the previous run's report, citing Canary's own HF benchmark card showing a quality drop on clips >15s. This assessment was carried into the draft verdict table. **Step 8's dispatch to file the issue the `upgrade` verdict calls for caught the error**: issue #920 had already been filed off the 2026-10-29 run, built, pre-flight-tested on 16 real FLEURS clips (4 of them >15s, specifically to probe the chunking weakness), and closed via PR #990 on 2026-11-07 with a "no swap" verdict. Full numbers in §7.2 and the new `local-findings.md` entry. The report and `frontier.json` were corrected before shipping rather than let the stale recommendation stand.
10. Applied the honesty rules: date-stamped every claim; left Nemotron 3.5 Bolt's composite quality unscored (no multilingual data to complete the weighted score, same reasoning as last run); recorded the Canary-1b-v2 local disproof in `docs/frontier/local-findings.md` so future runs don't re-recommend it from published numbers alone; flagged PLaMo 3 Translate's language coverage as unconfirmed rather than guessing.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's worked example updates the previous run's Mistral Large 4 placeholder now that its weights are actually public, with real numbers instead of an HF-listing estimate.

```
Mistral Large 4 ("Ecosystem Open-Weight License"), open weights live 2026-10-29
~1,000B total params, ~52B active (MoE)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling

At this report's standard assumption (Q4_K_M, ~4.5 bits/param, on TOTAL params for MoE):
  1000B x 4.5 bits / 8 = ~562.5 GB  -- roughly 3.9x the 144 GB budget

Even at the smallest viable GGUF quant for a usable MoE (2-bit, ~2.0625 bits/param):
  1000B x 2.0625 bits / 8 = ~258 GB  -- still ~1.8x past budget

License is a modified-MIT-style term, not as clean as Mistral's own Apache-licensed
small models -- a secondary, independent reason it would need scrutiny even if the
hardware fit worked out.
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across eleven consecutive runs. A trillion-parameter-class flagship simply isn't the right shape for a single 16 GB + 128 GB workstation, full stop — the same conclusion reached for DeepSeek V4 Pro, Qwen3.8 2.4T, Kimi K3 (and now K3.1 Pro), GLM-5.2/5.3, DeepSeek V4.1 Flash, Nemotron 3 Ultra, Muse Spark Open, and now a confirmed Mistral Large 4.

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
| **Nemotron 3.5 Bolt** *(updated)* | 74B / 9B active | GGUF (2-bit, smallest published) | ~22-24 GB (spill) | ~15 est (this box) / ~84 vendor-measured (unspecified hardware) | unverified (AA Index 21.4; no multilingual data) | 128k-262k (sources disagree) | OpenMDW-1.1 | no |
| **Ripple-65B Preview** *(new)* | 65B dense | NVFP4 | ~57 GB (spill) | ~4 est | n/a — coding model | 128k est | Apache 2.0 | no |
| DeepSeek V4.1 Flash | 748B total / 8-16B active | GGUF (2-bit floor) | ~187-421 GB (doesn't fit) | n/a | unverified | 1M | MIT | no |
| Nemotron 3 Ultra | 550B / 55B active | Q4_K_M | ~309 GB (spill) | ~1-2 est | unverified | 262k | OpenMDW-1.1 | no |
| **Mistral Large 4** *(now confirmed shipped 2026-10-29)* | ~1T total / ~52B active | GGUF (2-bit floor ~258 GB; Q4_K_M ~562.5 GB) | ~258-563 GB (doesn't fit) | n/a | unverified | 1M (est.) | Mistral Ecosystem Open-Weight License (modified MIT-style) | no |
| Muse Spark Open | 816B total / 42B active | GGUF (not clearly published) | ~210-459 GB (doesn't fit) | n/a | unverified | 1M | Muse Model License (restrictive) | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| **Kimi K3.1 Pro** *(new)* | 2.8T total / 104B active | n/a | ~620 GB+ (doesn't fit) | n/a | unverified | 1M | Modified MIT (check) | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

**Mistral Large 4 confirmed shipped 2026-10-29** — the ETA flagged two runs in a row (originally 2026-10-31) landed two days early, the same day the previous frontier run closed. See §4's worked example for the real-numbers math; dominated on size regardless of the exact quant floor, with a license that also needs scrutiny.

**Nemotron 3.5 Bolt gained real benchmarks** — Artificial Analysis Index 21.4, leads on agentic/coding-adjacent axes (Terminal-Bench, BrowseComp — largely 0%-weighted here), but loses to Gemma on IFBench (the 25%-weighted polish axis) and has zero multilingual verification. Still not actionable; the benchmark data narrows the question to "does it hold up on ES/CA" rather than "does any benchmark exist at all."

**Kimi K3.1 Pro and Ripple-65B Preview added, both dropped** — Kimi K3.1 Pro is a straight version bump of the already-NO-GO Kimi K3 (same 2.8T-class size). Ripple-65B Preview fits the combined budget with heavy offload but is an explicit from-scratch coding model with no multilingual training data — dominated by the already-tracked Qwen3 32B dense on this project's non-coding weighting.

### Models considered and dropped this run

- **Mistral Large 4 (confirmed shipped 2026-10-29)** — see Tier C above and §4. Dominated on size regardless of quant floor.
- **Nemotron 3.5 Bolt (benchmarks now published)** — see Tier C above. Fits the budget; loses on the polish axis and has no multilingual data to complete the composite score. Watch for a multilingual benchmark.
- **Kimi K3.1 Pro (Moonshot, 2.8T/104B active, version bump of Kimi K3)** — same NO-GO class, no change in substance.
- **Ripple-65B Preview (Ricursive, 65B dense, Apache 2.0, coding-focused)** — dominated by Qwen3 32B dense on this project's non-coding weighting; no multilingual data.
- **GLM-5.5** — still unshipped past its rumored August 2026 target. Seventh consecutive run with nothing to report for this exact name.
- Standing NO-GOs carried unchanged: DeepSeek V4.1 Flash, Nemotron 3 Ultra, GLM-5.3-Flash, Nemotron 3 Super, Qwen3.8-Flash-Next, Nemotron 3.5 Lightning, DeepSeek V4 Pro, Qwen3.8 2.4T-A95B, Kimi K3, Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2 (Tier B entry, listed above for comparison), Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`), Muse Spark Open, Qwen 4 (still unshipped preview).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window. The `audio_translate` Canary-1b-v2 pre-flight (§7.2) concluded with no swap and no hub-side Canary server planned, so no change to the Mac Mini's ASR process mix either.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 Parakeet Ultra still stands — FluidAudio shipped three releases, none changes the verdict

No change to the live incumbent. FluidAudio shipped **v0.18.1** (2026-11-01), **v0.18.2** (2026-11-01), and **v0.18.3** (2026-11-07) in this window, all tuning the `VocabularyBoostingSession` CTC rescorer first built and disproven locally against this project's own jargon corpus (#401, 2026-07-24):

- **v0.18.1** added a `Thresholds` struct (`cbw`, `minSimilarity`, `marginSeconds`) and a `detectedTerms` floor, and preserved names/possessives/neighbouring words during a swap.
- **v0.18.2** then *tightened* the mechanism further — vocabulary boosting "no longer gives the context-bias boost against a word TDT heard confidently," kept punctuation around replacements, and explicitly documented the floors as an "opt-in" feature with a "measured trade-off."
- **v0.18.3** (2026-11-07) shipped no further changes to this mechanism.

This is still the same post-hoc **swap**-only rescorer this report disproved in July — it can replace an acoustically/string-similar word with a vocabulary term, but it structurally cannot **insert** a phrase the TDT decoder dropped entirely. "Claude Code" heard as "Yes"/"Yeah" and "YOLO" blocked by the short-word similarity guard against "yellow" remain unrecoverable by this mechanism, no matter how the thresholds are tuned. The `local-findings.md` re-open trigger (decode-time biasing that can **insert** a dropped phrase, not just swap an emitted one) is **not met** — if anything, the tighter false-positive guards make an insertion-style fix *less* likely to emerge from this code path, not more.

Carried unchanged from last run: **Cohere Transcribe** (2B, Apache 2.0, live since March 2026), **Nemotron Unified ASR 0.6B**, and **Nemotron 3.5 Transcribe Lite** remain untested against this project's own domain corpus. No HF Open ASR Leaderboard update was found in this window.

### 7.2 Canary-1b-v2: the pre-flight test already ran — no swap

**This section corrects an error in this run's own research pass.** The previous run (2026-10-29) recommended Canary-1b-v2 as an `upgrade` for `audio_translate`, pending a local pre-flight test. This run's draft initially treated that test as still-pending and carried the recommendation forward — but the test had already run and closed, five days before this run started: issue [#920](https://github.com/ferraroroberto/local-llm-hub/issues/920), built and measured via `scripts/canary_preflight.py`, shipped through [PR #990](https://github.com/ferraroroberto/local-llm-hub/pull/990) (merged 2026-11-07), full write-up at `docs/canary-translate-preflight.md`.

**What the test found:** 16 real Spanish FLEURS clips (12 ≤15s, 4 long at 16.6-30.3s — specifically chosen to exercise Canary's 15s decode-window/VAD-splitting path, exactly the weakness this report's own benchmark-card research flagged). Corpus BLEU:

| Path | BLEU (16) | Median latency | Median RTFx |
|---|---|---|---|
| Canary-1b-v2 (Mac ANE) | 25.4 | 1.2 s | 8.3 |
| `whisper_translate` (the configured role model) | 21.5 | 8.7 s | 1.3 |
| Two-stage (`whisper` → `gemma4_26b`, the de facto quality default) | 32.6 | 3.0 s | 3.7 |

**Swap candidate: yes against `whisper_translate`, no against the two-stage default.** Canary is ~7x faster than `whisper_translate` and never answered in the wrong language (`whisper_translate` returned Spanish text on 2 of 16 clips) — the BLEU gap (+3.9) is inside the noise of 16 clips, but speed and reliability are not. Against the two-stage path, Canary loses by +7.1 BLEU (a gap "probably real but not firmly established at this sample size"), largely because two-stage paraphrases toward more natural English — a stylistic rather than clearly-proven fidelity gain. Canary's own failure modes matched this report's own prediction from the benchmark card: one dropped mid-sentence clause on a 22s clip, stray punctuation at two of four long-clip VAD joins.

**Why no swap:** even where Canary wins (vs. the literal configured role), nothing in the hub can serve it yet — no Python wrapper, backend row, or `/v1/audio/translations` route branch exists for the scratch `CanaryWorker.swift` build. Building that integration for a model that still loses to the existing two-stage default on quality was judged not worth the engineering cost; the issue closed with no hub-side Canary server planned. Full detail recorded in a new `docs/frontier/local-findings.md` entry (added by this run, since PR #990 didn't record it there per the skill's local-findings contract) — future runs should cite that entry rather than re-proposing Canary as an `upgrade` from published numbers alone.

**Genuinely open still:** the pre-flight used read, fluent FLEURS speech with no fillers or false starts — Canary has not been tested on spontaneous dictation, the role's actual use case. A full ES test-set run (~350 sentences vs. 16) would also narrow the BLEU gap's confidence interval. Both are noted as open questions in `local-findings.md` and carried into §9 below.

### 7.3 ASR candidate comparison (EN + ES)

Unchanged from the previous run's §7.3 table for every row except Canary-1b-v2, now marked `tested — no swap` (see §7.2) rather than `upgrade`, and the `parakeet-fluidaudio-vocab` row's note updated for this window's FluidAudio tuning (§7.1). See `frontier.json` → `asr_models` for the full machine-readable table.

### 7.4 Workload F — transcribe EN/ES

**Verdict: `keep`.** Unchanged. The three candidates sourced last run (Cohere Transcribe, Nemotron Unified ASR, Nemotron 3.5 Transcribe Lite) remain untested against this project's own jargon/wake-phrase corpus (#401/#343), and this window's FluidAudio releases tuned the vocabulary-boosting mechanism without resolving that regression.

### 7.5 Workload G — ES audio → English

**Verdict: `watch` (reverted from last run's `upgrade`).** The two-stage default (faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call, ~15 GB total) stays both the configured default in practice and the quality leader, now confirmed by the §7.2 pre-flight test (32.6 BLEU vs. Canary's 25.4). Canary-1b-v2 remains a real option for the weaker `whisper_translate` leg specifically (it beats that model on speed and reliability), but the hub has no server for it yet and nobody has decided that leg's quality bar is worth the integration cost — see §7.2's "what a swap needs." No architecture change this run.

### 7.6 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`). This holds regardless of which transcribe/translate front-end eventually feeds it. The §7.2 pre-flight test used read speech only, so it does not speak to this workload either way.

### 7.7 Concurrency footprint

No change. The §7.2 pre-flight concluded with no swap and no Canary integration planned, so the Mac Mini's ASR process mix (Parakeet Ultra only) is unaffected.

### 7.8 Dropped

Carried from prior runs: Canary-Qwen (EN-only, a *different* NVIDIA model from Canary-1b-v2 — don't confuse the two), Parakeet v1/v2 (superseded), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24, re-tuned but not re-opened this window), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B), Parakeet Redux (worse WER than both v3 and Ultra, dominated), Parakeet TDT v3 (superseded by Ultra, 2026-10-02), IBM Granite Speech 5.0 Turbo CTC (English-only), Qwen3.8-LiveTranslate / Qwen-Audio 3.1 suite (API-only cloud, no open-weight release). **New this run: Canary-1b-v2** — tested locally and found not to beat the two-stage default (§7.2); see `docs/frontier/local-findings.md`.

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
| 2026-11-12 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-11-12 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-11-12 | audio_transcribe | parakeet (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Cohere Transcribe / Nemotron Unified ASR / Nemotron 3.5 Transcribe Lite (still untested) |
| 2026-11-12 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B (Canary-1b-v2 pre-flight-tested 2026-11-07, #920/PR #990 — beats whisper_translate, loses to two-stage, no swap) |

Reading the progression: eleven consecutive runs now (2026-05-10 through 2026-11-12) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite thirteen genuinely new entrants across that span — every one dominated by architecture, size/fit, license, or unverified speed/quality. `agentic_light` has been unchanged in substance since 2026-05-10 across twelve runs. `audio_transcribe` has sat in `watch`/`keep` since its last upgrade cycle completed (2026-10-01 → 2026-10-15), with this run's FluidAudio releases tuning but not resolving the standing #401 regression. `audio_translate`'s one-run `upgrade` streak for Canary-1b-v2 closed out with a local test rather than a swap — the role reverts to the `watch` pattern that held for the nine runs before 2026-10-29, now with a locally-measured answer instead of an open question.

---

## 9. Open questions / uncertainty

- **Canary-1b-v2 on spontaneous dictation is still untested.** The §7.2 pre-flight used read, fluent FLEURS speech with no fillers or false starts — the role's actual use case (short Spanish dictation) has not been exercised. This is now the concrete open item, not "does Canary work at all" (answered: yes, partially — see §7.2).
- **The two-stage quality edge over Canary (32.6 vs. 25.4 BLEU) is not firmly established at 16 clips.** A full ES FLEURS run (~350 sentences) would narrow the interval; `docs/frontier/local-findings.md`'s new entry names this as the re-open trigger most likely to matter.
- **Nemotron 3.5 Bolt's multilingual coverage is still entirely unverified** — benchmarks now exist (Artificial Analysis Index 21.4, mixed agentic/coding results, a clear IFBench loss to Gemma), but none of them touch Spanish or Catalan. This is now the single most concrete "what would it take to act on this" question on the LLM side: a multilingual benchmark surfacing would make this immediately re-evaluable.
- **PLaMo 3 Translate's Spanish/Catalan coverage is unconfirmed** — Preferred Networks' 0.8B-31B translation-specialist family (official GGUF builds) could matter for workload E, but no language-coverage data was found in this pass. Needs dedicated follow-up before it can be scored as a candidate.
- **Nemotron 3.5 Transcribe Lite's language coverage for Spanish is still not independently confirmed** — unchanged from the previous run.
- **GLM-5.5 is still unshipped** past its own rumored August 2026 target — seventh consecutive run with no model card, benchmark, or endpoint.
- **A rumored next-generation "Nemotron 4" (>1T params) still has no official NVIDIA confirmation** — unchanged from the previous run. Too speculative to score.
- Standing carries: Gemma 4 12B Unified license still unverified on the model card; Catalan on Qwen 3.6 35B-A3B still needs a local smoke test; MiniMax M3 at UD-Q2 still academic; Mistral Large 4's exact license terms (reseller restrictions reported by secondary sources) worth independent verification before this question can be closed outright, even though the size verdict alone is already decisive.

---

## 10. Current decisions (live, edited by `/swap-model`)

The decisions below mirror `config/models.yaml` → `roles:` at the time
this section was last updated. `/swap-model` rewrites both this section
and the yaml together, so the two stay in sync.

| Role | Model | Decided | Why |
|---|---|---|---|
| **agentic_light** | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | 2026-05-10 (model); 2026-08-12 (routing default, #489) | Tier A top pick since 2026-05-10 — hybrid Gated DeltaNet + sparse MoE on a 4B base, Q4_K_M ~3 GB, 262k native ctx, 201 languages, Apache 2.0. The role pointer moved to the no-think virtual alias on 2026-08-12 (same underlying weights); the thinking variant stays one alias away as `agentic_light_think`. |
| **agentic_heavy** | `gemma4_26b` (gemma4-26b-a4b-it) | 2026-05-10 | Tier B top pick. 99 t/s, 256k ctx, strong multilingual including Catalan. Tied with Qwen 3.6 35B-A3B (Apache 2.0) — Gemma stays default on Catalan track record. |
| **audio_transcribe** | `parakeet` (Parakeet Ultra, a parakeet-tdt-0.6b-v3 derivative, Mac ANE) with `fallback: [whisper]` | 2026-07-22 (role primary, #350); 2026-10-02 (checkpoint → Ultra, #640) | A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling; #401 stays open). No further upgrade found through this run (2026-11-12) — see §7.1 for this window's FluidAudio vocabulary-boosting tuning, which does not resolve the regression. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Still the configured default — the local pre-flight test (#920/PR #990, 2026-11-07) found Canary-1b-v2 beats this specific model but loses to the two-stage default, and no hub-side Canary server is planned. No swap. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the November 12, 2026 snapshot.*
