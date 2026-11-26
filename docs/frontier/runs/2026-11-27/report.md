# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-11-27
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | Qwen4-Max launched 2026-11-16 but is API-only, closed-weight — a reversal from Qwen 3.8-Max's open release. Alibaba promises smaller open-weight Qwen4 tiers "soon" with no size or date. No shippable 4B-class challenger this window |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists, twelfth consecutive run. Mistral Large 4's license reseller/MaaS-hosting carve-out independently confirmed this window — doesn't change the hardware-fit verdict (still 1.8-3.9x over budget). Two new entrants sourced and dropped: Hermes 4.3 36B (English-only per its own model card — disqualified on the 25%-weighted multilingual axis) and Tencent Hy4 Flash (220B/12B active, oversized) |
| `audio_transcribe` | `parakeet` (Parakeet Ultra, Mac ANE) + whisper fallback | watch | Nemotron 3.5 ASR Streaming Multilingual (decode-time vocabulary biasing confirmed shipped 2026-09-10, untested locally) | — | Parakeet TDT's own vocabulary path is confirmed unchanged (swap-only CTC rescoring) through FluidAudio's newest release, v0.18.4 (19 Nov) — verified directly against the dated GitHub releases list. But a different FluidAudio-servable model, Nemotron ASR Streaming Multilingual, is confirmed to have gained genuine decode-time, insertion-capable vocabulary biasing on 2026-08-19 (PR #866, shipped v0.15.7) — satisfying the literal second clause of issue #401's re-open trigger. Never re-measured against this project's own jargon corpus, so verdict moves from `keep` to `watch` rather than jumping to an upgrade |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B MoE | — | No change. The 2026-11-07 pre-flight test (#920/PR #990) still stands: Canary-1b-v2 beats whisper_translate on speed/reliability but loses to the two-stage default on BLEU. No spontaneous-dictation benchmark data has appeared anywhere since |

**Diff vs previous run (2026-11-12):** `agentic_light` and `agentic_heavy` hold in substance — no new 4B-class or 15-35B-class entrant clears the bar, and this window's two new text-LLM candidates (Hermes 4.3 36B, Tencent Hy4 Flash) were sourced and dropped on multilingual and size grounds respectively. `audio_translate` is unchanged — no new evidence on either open item (spontaneous-dictation testing, sample-size confidence) since the 2026-11-07 pre-flight. The one substantive change is `audio_transcribe`: it moves from `keep` to `watch`. A direct, dated verification against FluidAudio's GitHub releases confirmed Parakeet TDT's vocabulary mechanism is unchanged (still the swap-only CTC rescorer disproven in issue #401), but also surfaced that FluidAudio's *Nemotron ASR Streaming Multilingual* integration quietly gained genuine decode-time, insertion-capable vocabulary biasing back on 2026-08-19 (PR #866, shipped in release v0.15.7, 2026-09-10) — a fact no prior frontier run connected to issue #401's re-open trigger, whose second clause ("an alternative Parakeet checkpoint/architecture exposing such a hook") this satisfies on its face. Per the local-findings.md contract, meeting the trigger's wording still requires re-measurement against this project's own corpus before any verdict upgrade — which has not happened — so the honest verdict is `watch` with a named, concrete next step, not an `upgrade`. No actionable verdict (upgrade/runtime_upgrade/retire) exists this run, so step 8 files no new issues.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of late November 2026.

**What changed since 2026-11-12:** The headline of this run is on the ASR side, not the LLM side. A direct verification pass against FluidAudio's actual GitHub releases — rather than inferring from version numbers or secondary coverage — found that issue #401's re-open trigger is now technically met, but by a different model (Nemotron ASR Streaming Multilingual) than the one the regression actually affects (Parakeet TDT). That nuance moves `audio_transcribe` from `keep` to `watch` without justifying a swap. On the LLM side, both roles hold: no new entrant this window changes either verdict.

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

1. Read `docs/frontier/runs/LATEST` (2026-11-12), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — four entries unresolved at read time (faster-whisper CTranslate2, FluidAudio `CustomVocabularyContext`, Nemotron 3 Nano 4B, Canary-1b-v2); none matched a candidate strong enough to flip this run's verdicts.
2. Re-read `config/models.yaml` → `roles:` — no out-of-band change since 2026-11-12 (all four roles match the previous run's recorded incumbents exactly).
3. Dispatched two parallel research passes (text LLMs; ASR) covering the 2026-11-12 → 2026-11-27 window, each resolving the previous run's standing open threads before sweeping for new entrants.
4. **Qwen 4**: Qwen4-Max launched 2026-11-16 but is API-only, closed-weight — a reversal from Qwen 3.8-Max's open release. Alibaba's own launch post promises smaller open-weight Qwen4 tiers "soon," no size or date given. Nothing shippable in the 4B class.
5. **GLM-5.5**: still unshipped. The "August 2026" target referenced in prior runs traces to a third-party (JPMorgan) projection, never a Zhipu commitment — eighth consecutive run with nothing for this exact name.
6. **Nemotron 3.5 Bolt multilingual**: still no FLORES/ES benchmark for the 74B-A9B checkpoint itself. NVIDIA's own technical report for the related, smaller Nemotron 3.5 Bolt E4B checkpoint lists Spanish as a supported language but gives no translation score — adjacent, not resolving.
7. **Mistral Large 4 license**: independently confirmed via secondary coverage that the "Mistral Ecosystem Open-Weight License" permits download/modify/fine-tune/internal-and-product commercialization but excludes third-party hosting/reseller (MaaS) use, which needs a separate paid agreement — not OSI-compliant on that carve-out. Exact clause wording still not read directly from the license file. Doesn't change the hardware-fit verdict (still 1.8-3.9x over the ~144 GB combined budget).
8. **PLaMo 3 Translate**: Spanish confirmed as a target language in Preferred Networks' own documentation. Catalan: still no evidence found (the only Catalan hit in this pass was an unrelated research model, PLUME). Remains an open question, not a scored candidate — this is a text-translation specialist, outside the four tracked roles.
9. **Nemotron 3 Nano 4B**: no new release or fine-tune found; NVIDIA's model card still omits Catalan from its supported-language list. Re-open trigger not met — not re-tested, per the local-findings.md contract (#486).
10. General sweep across Qwen, Llama, Mistral/Ministral/Magistral, Gemma, Phi, DeepSeek (non-coder), Command, Hermes/Nous, Yi, Mixtral, GLM, InternLM, Falcon, OLMo, gpt-oss, Granite, Kimi, Nemotron, plus newcomers, for anything new in the last 2-4 weeks at the ~4-9B or ~15-35B tiers: two new candidates surfaced. **Hermes 4.3 36B** (Nous Research, dense, built on ByteDance Seed-OSS-36B-Base, up to 512K context, Apache 2.0) has strong RefusalBench/AIME scores but its own HF model card lists English as the *only* supported language — disqualified outright by the 25%-weighted multilingual axis. **Tencent Hy4 Flash** (220B total/12B active MoE, Q4_K_M ≈157 GB) — oversized, dominated on size, no multilingual data sourced. A third item, **Arete-27B** (a Nous fine-tune of the already-dominated Qwen3.8-27B dense, targeting reduced political-topic refusal rate, general capability unchanged within noise), was sourced and dropped as a non-capability fine-tune. **Granite 4.2** (IBM, 3B/8B/30B, shipped 2026-08-25, predates this window but re-surfaced in the sweep) has no multilingual benchmark and would likely lag the tier-B MoE leaders on decode speed as a dense 30B — sourced and dropped, not scored.
11. On the audio side: first pass flagged a suspicious, version-ordering-inconsistent claim about FluidAudio shipping decode-time vocabulary biasing (secondary-source-derived, citing "v0.15.7" appearing chronologically *after* v0.18.x releases — semantic version numbers are not guaranteed chronological across branches). Rather than pass that claim through unverified, a second, targeted verification pass fetched github.com/FluidInference/FluidAudio/releases directly. **Confirmed dated facts**: the newest release is v0.18.4 (19 Nov 2026); v0.15.7 is real but was published 10 Sep 2026 — *older* than v0.18.1/v0.18.2 (01 Nov) and v0.18.3 (07 Nov). v0.18.1 through v0.18.4 all tune the same Parakeet `VocabularyBoostingSession` CTC rescorer (thresholds, floors, punctuation preservation) — no insertion capability added to that path; the #401 re-open trigger for Parakeet TDT specifically is **not met**. Separately, **PR #866** ("feat(asr/nemotron): decode-time custom vocabulary biasing, no CTC head required"), merged 2026-08-19 and shipped in v0.15.7 (2026-09-10), adds genuine decode-time, insertion-capable shallow-fusion biasing — but explicitly for FluidAudio's `StreamingNemotronMultilingualAsrManager` (Nemotron ASR Streaming Multilingual), not Parakeet TDT; the PR's own reviewer notes state the Parakeet CTC path is untouched. Read literally, this satisfies the re-open trigger's second clause ("an alternative Parakeet checkpoint/architecture exposing such a hook") — a connection no prior frontier run made, because no prior run looked past the Parakeet-specific releases. Measured generically (Earnings22-kws keyword recall 39.3%→59.7%, LibriSpeech WER 19.77%→18.68%), never against this project's own wake-phrase corpus.
12. Also sourced this window on the ASR side: the HF Open ASR Leaderboard added two private accent test sets (Egyptian/Nigerian English, 2026-11-06) with no change to the default average metric and no Spanish-specific update. Two new open-weight ASR entrants — **Salesforce SOV-Transcribe-1** (MoE, ~53 GB BF16, claims #1 open-weight on the Artificial Analysis WER Index) and **Reson8 Rumble-2-Flash** (0.5B, 45 languages) — both report multilingual averages that include Spanish but no per-language breakout and no domain-specific data; sourced as watch-only. A new **IBM Granite Speech 4.1 2B Plus** sub-variant adds explicit keyword-prompted ASR (its own keyword-F1 metric) — directly relevant in spirit to the #401 jargon problem but untested against this project's corpus and not FluidAudio/ANE-servable.
13. Applied the honesty rules: date-stamped every claim; did not upgrade `audio_transcribe`'s verdict past `watch` despite the re-open trigger's literal wording being met, because the local-findings.md contract requires re-measurement first, and none has happened; flagged PLaMo 3 Translate's Catalan coverage and Nemotron 3.5 Bolt's multilingual gap as still-open rather than guessing.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's worked example repeats the previous run's Mistral Large 4 math, now with the license carve-out independently confirmed rather than just reported.

```
Mistral Large 4 ("Ecosystem Open-Weight License" — reseller/MaaS hosting excluded,
independently confirmed 2026-11-27), open weights live since 2026-10-29
~1,000B total params, ~52B active (MoE)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling

At this report's standard assumption (Q4_K_M, ~4.5 bits/param, on TOTAL params for MoE):
  1000B x 4.5 bits / 8 = ~562.5 GB  -- roughly 3.9x the 144 GB budget

Even at the smallest viable GGUF quant for a usable MoE (2-bit, ~2.0625 bits/param):
  1000B x 2.0625 bits / 8 = ~258 GB  -- still ~1.8x past budget

The license carve-out (no third-party hosting/reseller use without a separate paid
agreement) is a second, independent reason to scrutinize even if the hardware fit
worked out -- which it doesn't.
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across twelve consecutive runs. A trillion-parameter-class flagship simply isn't the right shape for a single 16 GB + 128 GB workstation, full stop — the same conclusion reached for DeepSeek V4 Pro, Qwen3.8 2.4T, Kimi K3 (and K3.1 Pro), GLM-5.2/5.3, DeepSeek V4.1 Flash, Nemotron 3 Ultra, Muse Spark Open, Mistral Large 4, and now this window's Hermes 4.3 36B (on multilingual grounds, not just size) and Hy4 Flash.

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

No change this run. Qwen4-Max launched closed-weight 2026-11-16; the smallest *open* Qwen4 tier remains unshipped and un-dated.

### Tier B — Balanced (the workhorse) — **TIED, unchanged**

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★★ Gemma 4 26B MoE *(incumbent)* | 26B / 4B active | native W4 | ~14 GB | 99 | 83 | 256k | Gemma | yes |
| ★★ Qwen 3.6 35B-A3B | 35B / 3B active | Q4_K_M | ~13.5 GB | 98 | 84 | 262k | Apache 2.0 | yes |
| ☆ GPT-OSS 20B | 21B / 3.6B active | MXFP4 | ~12 GB | ~100 | 72 | 131k | Apache 2.0 | yes |
| Nemotron 3.5 Lightning | 30B / 3B active | Q4_0 (+MTP) | ~20.1 GB | ~40 est | 75 est | 128k | OpenMDW-1.1 | no |
| Qwen3.8-27B | 27.78B dense | Q4_K_M | ~15.6 GB | ~28 est | 79 est | 262k (1M YaRN) | Apache 2.0 | no |
| Arete-27B | 27B dense (fine-tune) | Q4_K_M | ~16 GB | ~28 est | n/a (unchanged from base) | 262k | Apache 2.0 | no |
| Gemma 4 12B Unified | 12B dense | Q4_K_M | ~7 GB | ~45 est | 76 est | 256k | Apache 2.0 (verify) | no |
| Ministral 3 14B | 14B dense | Q4_K_M | ~8.5 GB | ~35 est | 70 est | 256k | Apache 2.0 | no |
| Mistral Small 3.2 | ~22B dense | Q4_K_M | ~13 GB | ~30 | 74 | 128k | Apache 2.0 | no |
| Granite 4.2 (30B) | 30B dense | Q4_K_M | ~18 GB | ~25 est | n/a | 128k-512k | Apache 2.0 | no |

No change this run.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen3 32B dense | 32B | Q4_K_M | ~19.5 GB (spill) | ~11 | 84 | 128k | Apache 2.0 | yes |
| GLM-5.3-Flash | 320B / 18B active | GGUF (Unsloth dynamic 3-bit) | ~110 GB (spill) | ~12 est | 76 est | 1M | MIT | no |
| Nemotron 3 Super | 120B / 12B active | Q4_K_M | ~70 GB (spill) | ~6 est | 78 est | 128k | NVIDIA Nemotron Open Model License | no |
| Nemotron 3.5 Bolt | 74B / 9B active | GGUF (2-bit, smallest published) | ~22-24 GB (spill) | ~15 est (this box) / ~84 vendor-measured (unspecified hardware) | unverified (AA Index 21.4; no multilingual data) | 128k-262k (sources disagree) | OpenMDW-1.1 | no |
| **Hermes 4.3 36B** *(new)* | 36B dense | Q4_K_M | ~22 GB (spill) | ~20 est | n/a — English-only | 512k | Apache 2.0 | no |
| **Hy4 Flash** *(new)* | 220B / 12B active | Q4_K_M | ~157 GB (doesn't fit) | ~10 est | n/a | 128k est | unverified | no |
| Ripple-65B Preview | 65B dense | NVFP4 | ~57 GB (spill) | ~4 est | n/a — coding model | 128k est | Apache 2.0 | no |
| DeepSeek V4.1 Flash | 748B total / 8-16B active | GGUF (2-bit floor) | ~187-421 GB (doesn't fit) | n/a | unverified | 1M | MIT | no |
| Nemotron 3 Ultra | 550B / 55B active | Q4_K_M | ~309 GB (spill) | ~1-2 est | unverified | 262k | OpenMDW-1.1 | no |
| Mistral Large 4 | ~1T total / ~52B active | GGUF (2-bit floor ~258 GB; Q4_K_M ~562.5 GB) | ~258-563 GB (doesn't fit) | n/a | unverified | 1M (est.) | Mistral Ecosystem Open-Weight License (reseller/MaaS carve-out confirmed) | no |
| Muse Spark Open | 816B total / 42B active | GGUF (not clearly published) | ~210-459 GB (doesn't fit) | n/a | unverified | 1M | Muse Model License (restrictive) | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| Kimi K3.1 Pro | 2.8T total / 104B active | n/a | ~620 GB+ (doesn't fit) | n/a | unverified | 1M | Modified MIT (check) | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

**Mistral Large 4's license carve-out independently confirmed** — reseller/MaaS hosting excluded without a separate paid agreement. No change to the hardware-fit verdict (1.8-3.9x over budget regardless).

**Hermes 4.3 36B and Hy4 Flash added, both dropped** — Hermes 4.3 36B is a strong agentic/math dense model but its own HF model card lists English as the only supported language, a hard disqualifier on the 25%-weighted multilingual axis. Hy4 Flash (220B/12B active) needs ~157 GB at Q4_K_M — oversized for the ~144 GB combined budget.

**Arete-27B added, dropped** — a Nous Research fine-tune of the already-dominated Qwen3.8-27B dense, targeting reduced political-topic refusal rate; general capability (Hermes Bench) unchanged within noise. Not a quality-axis improvement.

**Granite 4.2 (30B) re-surfaced, dropped** — shipped 2026-08-25, predates this window but was missing from prior sweeps. No multilingual benchmark; dense 30B architecture would likely lag the tier-B MoE leaders on decode speed.

### Models considered and dropped this run

- **Hermes 4.3 36B (Nous Research, dense, English-only per its own model card)** — disqualified outright by the multilingual axis.
- **Tencent Hy4 Flash (220B/12B active MoE)** — dominated on size.
- **Arete-27B (Nous fine-tune of Qwen3.8-27B)** — no quality-axis improvement, dominated base model already NO-GO on speed.
- **Granite 4.2 (3B/8B/30B)** — sourced for completeness; no multilingual data, dense 30B dominated on speed.
- Standing NO-GOs carried unchanged: DeepSeek V4.1 Flash, Nemotron 3 Ultra, GLM-5.3-Flash, Nemotron 3 Super, Qwen3.8-Flash-Next, Nemotron 3.5 Lightning, Nemotron 3.5 Bolt (still no multilingual data), DeepSeek V4 Pro, Qwen3.8 2.4T-A95B, Kimi K3 / K3.1 Pro, Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2 (Tier B entry, listed above for comparison), Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`), Muse Spark Open, Mistral Large 4, Ripple-65B Preview, Qwen4-Max (closed-weight, not eligible).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window. No ASR architecture change either (§7), so the Mac Mini's process mix is unaffected.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 Parakeet Ultra still stands — but a different FluidAudio model now has the capability #401 needs, untested

No change to the live incumbent's own vocabulary path, confirmed by a direct check of FluidAudio's dated GitHub releases rather than inference from version numbers: the newest release is **v0.18.4** (2026-11-19), and v0.18.1 through v0.18.4 all tune the same `VocabularyBoostingSession` CTC rescorer for Parakeet TDT — thresholds, floors, punctuation preservation. No insertion-capable mechanism has shipped for Parakeet TDT specifically; "Claude Code" heard as "Yes"/"Yeah" and "YOLO" blocked by the short-word similarity guard against "yellow" remain structurally unrecoverable by this path. **The #401 re-open trigger is not met for Parakeet TDT.**

But the same verification pass found something genuinely new: **PR #866** ("feat(asr/nemotron): decode-time custom vocabulary biasing, no CTC head required"), merged **2026-08-19**, shipped in release **v0.15.7** (2026-09-10) — true decode-time, insertion-capable shallow-fusion biasing, but for FluidAudio's `StreamingNemotronMultilingualAsrManager` (Nemotron ASR Streaming Multilingual), not Parakeet TDT. The PR's own reviewer notes confirm Parakeet's CTC path is untouched by this change. Read against the literal wording of issue #401's re-open trigger — "a FluidAudio release that adds decode-time TDT vocabulary biasing… **or an alternative Parakeet checkpoint/architecture exposing such a hook**" — this satisfies the second clause: a different FluidAudio-servable ASR architecture now has an insertion-capable hook. No prior frontier run made this connection, because no prior run looked past the Parakeet-specific release notes to the Nemotron integration's own PR history.

This is dated, verified evidence, not speculation — but it is also, by the local-findings.md contract's own wording, not sufficient on its own: the trigger text requires **re-measurement on the same jargon corpus before any verdict upgrade**. That re-measurement has not happened. Measured generically (Earnings22-kws keyword recall 39.3%→59.7%, LibriSpeech WER 19.77%→18.68%), the mechanism has never been run against "Claude Code"/"YOLO". **Verdict: `watch`, not `upgrade`** — the concrete next step is a local pre-flight test against the same corpus issue #401 used, the same pattern #920/PR #990 followed for Canary-1b-v2.

Carried unchanged from last run: **Cohere Transcribe** (2B, Apache 2.0), **Nemotron Unified ASR 0.6B**, and **Nemotron 3.5 Transcribe Lite** remain untested against this project's own domain corpus. The HF Open ASR Leaderboard added two private accent test sets (Egyptian/Nigerian English, 2026-11-06) with no change to the default metric and no Spanish-specific update.

### 7.2 Canary-1b-v2 — no new data this window

Unchanged from the previous run's §7.2. The 2026-11-07 pre-flight test (issue #920, PR #990) still stands: 16 real Spanish FLEURS clips found Canary-1b-v2 beats `whisper_translate` (25.4 vs 21.5 BLEU, ~7x faster, never answered in the wrong language) but loses to the two-stage default (`whisper` → `gemma4_26b`, 32.6 BLEU). No spontaneous-dictation or expanded-sample-size data has appeared anywhere since, and no hub-side Canary server exists or is planned. See `docs/frontier/local-findings.md` for the full record.

### 7.3 ASR candidate comparison (EN + ES)

Unchanged from the previous run's §7.3 table for every carried row. Two new entrants this window, both watch-only: **Salesforce SOV-Transcribe-1** (MoE, ~53 GB BF16, claims #1 open-weight on the Artificial Analysis WER Index, 6-language multilingual mean WER 4.88% incl. Spanish per vendor but no per-language breakout — likely oversized for this box's hosts without heavy offload) and **Reson8 Rumble-2-Flash** (0.5B, 45 languages, 9-language average 6.96% WER incl. Spanish per vendor, no per-language breakout). Also new: an **IBM Granite Speech 4.1 2B Plus** sub-variant adding explicit keyword-prompted ASR (base WER 5.33% vs Plus 5.71%, better keyword F1) — relevant in spirit to the #401 jargon problem but not FluidAudio/ANE-servable and untested against this project's corpus. The elevated **Nemotron ASR Streaming Multilingual** entry (§7.1) is the priority item among all of these — it is the only one with a verified, insertion-capable jargon-relevant mechanism, even though it hasn't been domain-tested yet either. See `frontier.json` → `asr_models` for the full machine-readable table.

### 7.4 Workload F — transcribe EN/ES

**Verdict: `watch`** (changed from last run's `keep` — see §0/§7.1). The standing untested candidates (Cohere Transcribe, Nemotron Unified ASR, Nemotron 3.5 Transcribe Lite) remain untested; the new, higher-priority item is Nemotron ASR Streaming Multilingual's confirmed decode-time biasing capability, which has never been run against this project's own jargon/wake-phrase corpus (#401/#343).

### 7.5 Workload G — ES audio → English

**Verdict: `watch`.** Unchanged. The two-stage default (faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call, ~15 GB total) stays both the configured default in practice and the quality leader, confirmed by the 2026-11-07 pre-flight test (32.6 BLEU vs. Canary's 25.4). No architecture change this run.

### 7.6 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`). This holds regardless of which transcribe/translate front-end eventually feeds it.

### 7.7 Concurrency footprint

No change. No swap happened on either audio role this run, so the Mac Mini's ASR process mix (Parakeet Ultra only) is unaffected.

### 7.8 Dropped

Carried from prior runs: Canary-Qwen (EN-only, a *different* NVIDIA model from Canary-1b-v2), Parakeet v1/v2 (superseded), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24, confirmed unchanged through v0.18.4 this window), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B), Parakeet Redux (worse WER than both v3 and Ultra, dominated), Parakeet TDT v3 (superseded by Ultra, 2026-10-02), IBM Granite Speech 5.0 Turbo CTC (English-only), Qwen3.8-LiveTranslate / Qwen-Audio 3.1 suite (API-only cloud, no open-weight release). No new drops this window — Salesforce SOV-Transcribe-1, Reson8 Rumble-2-Flash, Granite Speech 4.1 2B Plus, and Nemotron ASR Streaming Multilingual are all carried as `watch`, not dropped.

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
| 2026-11-27 | agentic_light | qwen35_4b_nothink (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) |
| 2026-11-27 | agentic_heavy | gemma4_26b (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B (tie) |
| 2026-11-27 | audio_transcribe | parakeet (Parakeet Ultra, Mac ANE) + whisper fallback | watch | Nemotron 3.5 ASR Streaming Multilingual (decode-time vocabulary biasing confirmed shipped 2026-09-10, satisfies #401's re-open trigger's second clause, untested locally) |
| 2026-11-27 | audio_translate | whisper_translate (whisper-medium) | watch | two-stage Turbo → Gemma 4 26B (no new data since the 2026-11-07 pre-flight) |

Reading the progression: twelve consecutive runs now (2026-05-10 through 2026-11-27) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite fourteen genuinely new entrants across that span — every one dominated by architecture, size/fit, license, or unverified speed/quality. `agentic_light` has been unchanged in substance since 2026-05-10 across thirteen runs. `audio_translate` has held `watch` for eleven of the last twelve runs (the one exception, 2026-10-29's `upgrade`, closed out with a local test rather than a swap). `audio_transcribe` moves to `watch` this run for a reason distinct from every prior `watch` cycle on this role — not a generic untested candidate, but a specific, dated, verified capability gap: a FluidAudio-servable model already has the mechanism issue #401 needs, and nobody has pointed it at the actual jargon corpus yet.

---

## 9. Open questions / uncertainty

- **Nemotron ASR Streaming Multilingual has never been tested against this project's own jargon/wake-phrase corpus (#401/#343).** This is now the single most concrete, actionable open item across both LLM and ASR tracks — the mechanism (decode-time, insertion-capable vocabulary biasing) is confirmed to exist and confirmed to be absent from Parakeet TDT's own path, so a local pre-flight test (the same pattern #920/PR #990 used for Canary-1b-v2) is the natural next step, not a re-survey.
- **Canary-1b-v2 on spontaneous dictation is still untested.** Unchanged from last run — only read FLEURS speech has been measured.
- **The two-stage quality edge over Canary (32.6 vs. 25.4 BLEU) is still not firmly established at 16 clips.** Unchanged — a full ES FLEURS run (~350 sentences) would narrow the interval.
- **Nemotron 3.5 Bolt's multilingual coverage is still entirely unverified** for the 74B-A9B checkpoint itself — the related, smaller E4B checkpoint's technical report lists Spanish as supported but gives no translation score, which is adjacent evidence, not resolving.
- **PLaMo 3 Translate's Catalan coverage is unconfirmed** — Spanish is now confirmed as a target language, but Catalan still has no evidence either way. Needs dedicated follow-up before it can be scored as a candidate, and it sits outside the four tracked roles regardless (a text-translation specialist, not an LLM role or an ASR role).
- **Nemotron 3.5 Transcribe Lite's language coverage for Spanish is still not independently confirmed** — unchanged from the previous run.
- **GLM-5.5 is still unshipped** — the "August 2026" figure referenced in prior runs was always a third-party projection, not a Zhipu commitment. Eighth consecutive run with no model card, benchmark, or endpoint.
- **A rumored next-generation "Nemotron 4" (>1T params) still has no official NVIDIA confirmation** — unchanged from the previous run. Too speculative to score.
- Standing carries: Gemma 4 12B Unified license still unverified on the model card; Catalan on Qwen 3.6 35B-A3B still needs a local smoke test; MiniMax M3 at UD-Q2 still academic; the exact clause wording of Mistral Large 4's license carve-out is still unread directly from the license file (secondary coverage only), though the size verdict alone is already decisive regardless.

---

## 10. Current decisions (live, edited by `/swap-model`)

The decisions below mirror `config/models.yaml` → `roles:` at the time
this section was last updated. `/swap-model` rewrites both this section
and the yaml together, so the two stay in sync.

| Role | Model | Decided | Why |
|---|---|---|---|
| **agentic_light** | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | 2026-05-10 (model); 2026-08-12 (routing default, #489) | Tier A top pick since 2026-05-10 — hybrid Gated DeltaNet + sparse MoE on a 4B base, Q4_K_M ~3 GB, 262k native ctx, 201 languages, Apache 2.0. The role pointer moved to the no-think virtual alias on 2026-08-12 (same underlying weights); the thinking variant stays one alias away as `agentic_light_think`. |
| **agentic_heavy** | `gemma4_26b` (gemma4-26b-a4b-it) | 2026-05-10 | Tier B top pick. 99 t/s, 256k ctx, strong multilingual including Catalan. Tied with Qwen 3.6 35B-A3B (Apache 2.0) — Gemma stays default on Catalan track record. |
| **audio_transcribe** | `parakeet` (Parakeet Ultra, a parakeet-tdt-0.6b-v3 derivative, Mac ANE) with `fallback: [whisper]` | 2026-07-22 (role primary, #350); 2026-10-02 (checkpoint → Ultra, #640) | A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling; #401 stays open). This run (2026-11-27) found a dated, verified lead worth a local test — Nemotron ASR Streaming Multilingual's decode-time vocabulary biasing (§7.1) — but no swap: untested against the actual jargon corpus. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Still the configured default — the local pre-flight test (#920/PR #990, 2026-11-07) found Canary-1b-v2 beats this specific model but loses to the two-stage default, and no hub-side Canary server is planned. No swap. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the November 27, 2026 snapshot.*
