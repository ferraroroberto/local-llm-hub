# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-10-15
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant this window. Qwen 4's smallest previewed tier (27B) is still two orders of magnitude above this role's size class and remains unshipped |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists, ninth consecutive run. One new entrant: **Mistral Large 4** (~1.05T total / ~49-52B active MoE, announced 2026-10-06) is API-preview only — no public weights or license as of 2026-10-13 — and even once shipped fails the ~144 GB combined budget at any usable quant (~563 GB at Q4_K_M) |
| `audio_transcribe` | `parakeet` (Parakeet Ultra, Mac ANE) + whisper fallback | keep | Nemotron 3.5 ASR Streaming / Granite Speech 4.1 2B (jargon axis, untested) | — | The previous run's `runtime_upgrade` to Parakeet Ultra was approved and applied via `/swap-model` on 2026-10-02 (#640). No further transcribe upgrade found this window — FluidAudio's three releases since (Nemotron 3 Diarization ANE fix, an alignment robustness fix, a TTS model) don't touch the Parakeet transcribe path |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage: Turbo → Gemma 4 26B MoE | — | Unchanged. Neither of this window's new entrants (Mistral Large 4, IBM Granite Speech 5.0 Turbo CTC) is a candidate — the former isn't ASR, the latter is English-only |

**Diff vs previous run (2026-10-01):** No verdict changes this run — all four roles hold. The headline item is a confirmation, not a finding: the 2026-10-01 run's `audio_transcribe` → Parakeet Ultra `runtime_upgrade` was approved and executed via `/swap-model` on 2026-10-02 (#640), so Parakeet Ultra is now the live incumbent rather than a pending recommendation, and this run's own verdict for the role flips from `runtime_upgrade` to `keep` because no further upgrade was found on top of it. On the text side, one new entrant surfaced and was dismissed on the same size grounds as this year's other oversized MoEs: **Mistral Large 4** (announced 2026-10-06, ~1.05T total/~49-52B active, API-preview only, weights promised "by the end of October" under an unannounced license). On the audio side, IBM's **Granite Speech 5.0 Turbo CTC** (~470M, English-only encoder-only CTC model) was sourced and dropped — it can't cover this project's ES requirement.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of October 2026.

**What changed since 2026-10-01:** This is a quiet, two-week window — the kind the skill's own honesty rules expect "no change" to be a valid and common outcome. The one piece of state that *did* change is procedural rather than a new finding: the previous run's audio recommendation was acted on (`/swap-model`, 2026-10-02, #640), so this run opens with Parakeet Ultra already live rather than pending. On the research side, **Mistral Large 4** was announced 2026-10-06 (API-preview only, weights due "by end of October") and **IBM Granite Speech 5.0 Turbo CTC** shipped (~August 2026, sourced this run) — both evaluated and dismissed, neither changing any role.

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

1. Read `docs/frontier/runs/LATEST` (2026-10-01), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — the same three unresolved entries carry forward unchanged (faster-whisper CTranslate2, FluidAudio `CustomVocabularyContext`, Nemotron 3 Nano 4B). None of this run's new candidates match an unresolved entry, so no local-findings override applies.
2. Re-read `config/models.yaml` → `roles:` for the current incumbents. One out-of-band change confirmed: `audio_transcribe`'s `parakeet` model_id now resolves to the Parakeet Ultra checkpoint (comment `#350, #640`), per the previous run's approved `/swap-model` swap on 2026-10-02.
3. Surveyed the external landscape for the 2026-10-01 → 2026-10-15 window using the established explicit per-name searches: "Nemotron" (no new shipped entrant — only an unconfirmed, single-source rumor of a next-gen "Nemotron 4" targeting "late fall 2026", no spec/benchmark/date), "GLM" (GLM-5.5 still unshipped past its rumored August 2026 target; no GLM-5.4 — fifth consecutive run with nothing to report), "Qwen" (no new release; Qwen 4 remains an unshipped Apsara preview), "DeepSeek" (no new model — the one October item found was a funding-round report, not a release). Added "Mistral" to the explicit per-name list this run after noticing it had never been searched by name despite being a tracked family (Ministral 3 14B, Mistral Small 3.2, Mistral Medium 3.5 already in the shortlist) — this caught **Mistral Large 4** (announced 2026-10-06). Swept GPT-OSS/Phi/Gemma/Llama/Granite/Command/Hermes-Nous/Mixtral/Falcon/OLMo/InternLM/Yi for anything new in the window — nothing found beyond already-carried entries.
4. On the audio side: queried FluidAudio's GitHub releases directly via `gh api repos/FluidInference/FluidAudio/releases` (the authoritative source established by the 2026-10-01 run, after a web search once misreported release years). Found three releases since v0.17.3: **v0.17.4** (2026-09-25, Nemotron 3 Diarization Neural-Engine compile fix on M3 — diarization, not transcription, out of scope for `audio_transcribe`), **v0.17.5** (2026-10-01, already covered by the previous run), and **v0.17.7** (2026-10-08, Paradee-8M — a new 8M-parameter TTS model distilled from Kokoro-82M, out of scope for any of this project's ASR roles). None touches the Parakeet transcribe path or the #401 jargon mechanism. Separately sourced **IBM Granite Speech 5.0 Turbo CTC** (~August 2026): an encoder-only CTC redesign that drops the LLM-decoder backbone used in Granite Speech 4.x, trading multilingual + translate capability for a smaller, English-only model — evaluated and dropped on scope (§7).
5. Computed VRAM for this window's one oversized new entrant (Mistral Large 4) with the standing rule of thumb **Q4_K_M ≈ 4.5 bits/param** on total params for MoE — fails the combined ~144 GB budget by a wide margin, same conclusion as every prior oversized entrant (see §4).
6. Applied the honesty rules: date-stamped every claim, flagged Mistral Large 4's license and exact active-parameter count as **unverified** (sources disagree between ~49B and ~52B active, and no license text has been published), and explicitly avoided scoring Mistral Large 4 or Granite Speech 5.0 Turbo CTC on quality/speed since neither has public weights or a domain-relevant benchmark run — `quality: 0` / `tps: 0` placeholders in `frontier.json`, consistent with how prior oversized/unshippable entrants (DeepSeek V4.1 Flash, Nemotron 3 Ultra) were recorded.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's cautionary tale is **Mistral Large 4** — the same shape of failure as DeepSeek V4.1 Flash and Nemotron 3 Ultra before it, just from a different lab.

```
~1.05T total params, ~49-52B active (sources disagree on the exact figure)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling

At this report's standard assumption (Q4_K_M, ~4.5 bits/param):
  1.05T x 4.5 bits / 8 = ~591 GB  -- roughly 4x the 144 GB budget

Even at the smallest viable GGUF quant for a usable MoE (2-bit, ~2.0 bits/param):
  1.05T x 2.0 bits / 8 = ~263 GB  -- still ~1.8x past budget
```

Unlike Nemotron 3 Nano 4B (which fit the budget and only failed on quality, see `local-findings.md`), Mistral Large 4 doesn't clear the storage bar at any quant this box could hold — and that's before accounting for the fact that no weights are even public yet to test. Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across nine consecutive runs. A 1T-class flagship simply isn't the right shape for a single 16 GB + 128 GB workstation, full stop — the same conclusion reached for DeepSeek V4 Pro, Qwen3.8 2.4T, Kimi K3, GLM-5.2/5.3, DeepSeek V4.1 Flash, and Nemotron 3 Ultra.

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
| DeepSeek V4.1 Flash | 748B total / 8-16B active | GGUF (2-bit floor) | ~187-421 GB (doesn't fit) | n/a | unverified | 1M | MIT | no |
| Nemotron 3 Ultra | 550B / 55B active | Q4_K_M | ~309 GB (spill) | ~1-2 est | unverified | 262k | OpenMDW-1.1 | no |
| **Mistral Large 4** *(new)* | ~1.05T total / ~49-52B active | GGUF (not yet published) | ~263-591 GB (doesn't fit) | n/a — no weights | unverified | 1M (est.) | unannounced | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

**Mistral Large 4 added** — a genuine sourcing gap closed before it could repeat: announced 2026-10-06 (1.05T total, ~49-52B active MoE, 1.6B vision encoder), but API-preview only — no public weights, no confirmed license (reported as a custom license, not Mistral Large 3's Apache 2.0) as of 2026-10-13. Not yet testable even if it did fit; and it doesn't fit — ~591 GB at this report's standard Q4_K_M assumption, ~263 GB at a 2-bit floor, both well past the ~144 GB combined budget (§4's worked example). Flagged as a priority re-check once weights land (expected "end of October" per Mistral's own post).

### Models considered and dropped this run

- **Mistral Large 4 (announced 2026-10-06, ~1.05T total/~49-52B active MoE)** — see Tier C above. Watch next cycle: check whether weights actually land by end of October as promised, and what license they carry.
- **IBM Granite Speech 5.0 Turbo CTC (~August 2026, 470M, encoder-only)** — ASR entrant, not an LLM; see §7. English-only, dropped on scope for this project's EN+ES requirement.
- **Nemotron 4 (rumored, "late fall 2026")** — a single August 2026 report describes NVIDIA developing a >1T-parameter Nemotron-4, with final training not yet complete and no official date. No spec sheet, no benchmark, no confirmation from NVIDIA. Not a candidate — too speculative to score; watch for an official announcement.
- **GLM-5.5** — still unshipped past its rumored August 2026 target; no GLM-5.4 found. Fifth consecutive run with nothing to report for this exact name.
- Standing NO-GOs carried unchanged: DeepSeek V4.1 Flash, Nemotron 3 Ultra, GLM-5.3-Flash, Nemotron 3 Super, Qwen3.8-Flash-Next, Nemotron 3.5 Lightning, DeepSeek V4 Pro, Qwen3.8 2.4T-A95B, Kimi K3, Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2 (Tier B entry, listed above for comparison), Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`), Qwen 4 (still unshipped preview).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window. Parakeet Ultra's live deployment (since 2026-10-02, #640) runs on the exact same Mac Mini ANE allocation as its v3 predecessor (same CoreML path, same ~2 GB), so it changes no concurrency math anywhere in the fleet.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 Parakeet Ultra is now live — no further upgrade found this window

The 2026-10-01 run's `runtime_upgrade` recommendation was approved by the owner and applied via `/swap-model` on **2026-10-02** (#640): `config/models.yaml`'s `audio_transcribe.model_id: parakeet` now resolves to the Parakeet Ultra checkpoint rather than v3. Per the role's own change record (report §10), the swap's pre-flight re-ran the #138/#343 jargon corpus and found no regression (WER 7.71% vs 8.85% against archived references), and confirmed Ultra's license as CC-BY-4.0 — the same terms as v3, resolving the previous run's §9 open question about license inheritance.

**This run's own survey found no further transcribe upgrade.** FluidAudio shipped three releases since v0.17.3 (checked via `gh api repos/FluidInference/FluidAudio/releases`, the authoritative source established last run):

- **v0.17.4** (2026-09-25) — a Neural Engine compile fix for Nemotron 3 **Diarization** on M3-class Macs. Diarization, not transcription; out of scope for this role.
- **v0.17.5** (2026-10-01) — already covered by the previous run (the "iterative" alignment robustness fix to the disproven CustomVocabularyContext mechanism, #962).
- **v0.17.7** (2026-10-08) — **Paradee-8M**, a new 8M-parameter TTS model distilled from Kokoro-82M. Text-to-speech, not ASR; out of scope for any role this report tracks.

None of these touches the Parakeet transcribe path or the #401 jargon mechanism. The verdict for `audio_transcribe` this run is **`keep`** — Parakeet Ultra stands as both incumbent and frontier pick.

### 7.2 A new entrant sourced and dropped: IBM Granite Speech 5.0 Turbo CTC

IBM shipped **Granite Speech 5.0 Turbo CTC** (~late August 2026): a redesign that drops the LLM-decoder backbone used in Granite Speech 4.x in favor of an encoder-only CTC architecture. The result is much smaller (470M vs the 4.1 line's 2B) and faster, with IBM's own self-reported aggregate English WER of 5.00% (Apache-licensed variant) / 4.85% (an NC variant) on public OpenASR English test sets. It is **English-only** — the architectural trade that bought the speed and size win also dropped the multilingual + translate capability that made Granite Speech 4.1 2B relevant to this project in the first place. This project's workload F needs EN **and** ES, and workload G needs translate capability; Granite Speech 5.0 Turbo CTC meets neither bar regardless of its English WER, so it's dropped on scope rather than evaluated further. **Granite Speech 4.1 2B remains the relevant Granite candidate and is unaffected by this sibling release.**

### 7.3 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (measured/est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| ★ Parakeet Ultra (Mac ANE, current incumbent) | 0.6B | ~2 GB (CoreML) | 126.7× measured (M-series ANE), parity with v3 | ✅ (better WER) | ✅ (FLEURS 24-lang 11.67% vs v3's 14.81%) | ❌ | Live incumbent since 2026-10-02 (#640). Same API, same speed, measurably better WER on every published axis than its predecessor. CC-BY-4.0 license confirmed at swap time. Untested on this project's #138/#343 jargon corpus beyond the swap's own pre-flight (no regression found) — the jargon-fix question is orthogonal, not blocking. |
| ◇ Parakeet TDT v3 (Mac ANE, superseded 2026-10-02) | 0.6B | ~2 GB (CoreML) | 128.6× measured (ANE) | ✅ | ✅ (25 langs) | ❌ | Was the incumbent 2026-07-22 → 2026-10-02 (#350 → #640). Formally replaced in `config/models.yaml`; retained here for historical completeness. |
| ◆ Whisper Large v3 Turbo (automatic failover) | 809M | ~1.6 GB | 40× tower / 19.3× gaming (measured, boosted) | ✅ | ✅ | ❌ | Unchanged. Still the accuracy leader on the jargon domain thanks to `--carry-initial-prompt` boosting (#91) — a lever neither Parakeet variant has. |
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B (secondary candidate) | 0.6B | ~2 GB est (CoreML) | TBD (domain) | ✅ (FLEURS 7.91% — worse than Parakeet's card) | ✅ (FLEURS 4.11% — worse than Parakeet's card) | ❌ | No change this window. FluidAudio's decode-time biasing hook still targets this model only. Still the live secondary priority for the #401 jargon axis. |
| Granite Speech 4.1 2B (secondary candidate) | 2B | ~2 GB est | TBD (domain) | ✅ | ✅ (6 langs, bidirectional AST) | ✅ | No change this window — still leads the public HF Open ASR Leaderboard at 5.33% WER. Secondary priority, unaffected by its sibling 5.0 Turbo CTC release (§7.2). |
| ✗ IBM Granite Speech 5.0 Turbo CTC *(new — dropped on scope)* | 470M | ~1 GB | TBD (domain) — 5.00%/4.85% self-reported aggregate EN WER | ✅ | ❌ | ❌ | New this run (§7.2). English-only encoder-only redesign; doesn't meet this project's EN+ES bar for either workload F or G. |
| ✗ Parakeet + FluidAudio CustomVocabularyContext | 0.6B + ~97 MB CTC model | ~2 GB + rescorer | n/a — disproven, not shipped | — | — | ❌ | Disproven #401 (2026-07-24). Carried `watch` per `local-findings.md`. No further FluidAudio release this window touches the mechanism. |
| faster-whisper Turbo (CT2) | 809M (CT2) | ~1.0 GB INT8 | measured 1.0× vs whisper.cpp — **disproven** | ✅ | ✅ | ❌ | Carried `watch` per `docs/frontier/local-findings.md` (#277); applies to the failover leg's engine, not the primary. |
| Qwen3-ASR (1.7B / MLX build) | 1.7B | ~1.5 GB | TBD (domain) | ✅ | ✅ | ❌ | `watch`, unchanged status. |

### 7.4 Workload F — transcribe EN/ES

**Verdict: `keep`.** The brief's standing question about a strict EN/ES transcribe upgrade over the incumbent was answered "yes" last run and acted on this window (Parakeet Ultra, live since 2026-10-02). This run's survey found no further upgrade on top of Ultra. The wake-phrase/jargon regression (#401) remains open and separate from this verdict; Nemotron 3.5 ASR Streaming and Granite Speech 4.1 2B stay the live, untested secondary candidates for that axis.

### 7.5 Workload G — ES audio → English

**Verdict unchanged: two-stage default.** faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call (~15 GB total). Single-model faster-whisper Large v3 `task=translate` stays the fallback when the LLM slot is busy. Neither of this window's new entrants (Mistral Large 4, Granite Speech 5.0 Turbo CTC) is a candidate — the former isn't ASR, the latter is English-only. Granite Speech 4.1 2B's native bidirectional ES↔EN speech translation remains a candidate to eventually replace the single-model fallback leg's engine — no change to the default this run.

### 7.6 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`).

### 7.7 Concurrency footprint

Unchanged. Parakeet Ultra runs on the exact same Mac Mini ANE CoreML path as the incumbent it replaced, at the same ~2 GB footprint, so whisper-turbo (gaming satellite), orpheus (tower), and piper (tower CPU) are all unaffected.

### 7.8 Dropped

Carried from prior runs: Canary-Qwen (EN-only), Parakeet v1/v2 (superseded by v3, now also by Ultra), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24, §7.3), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B), Parakeet Redux (worse WER than both v3 and Ultra, dominated). **New this run:** Parakeet TDT v3 (superseded by its own Ultra successor, 2026-10-02), IBM Granite Speech 5.0 Turbo CTC (English-only, §7.2).

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

Reading the progression: nine consecutive runs now (2026-05-10 through 2026-10-15) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite ten genuinely new entrants across that span — every one dominated by architecture, size/fit, or unverified speed. `agentic_light` has been unchanged in substance since 2026-05-10 across ten runs. `audio_transcribe` completes its upgrade cycle this run: `watch` (2026-08-06 through 2026-09-17) → `runtime_upgrade` (2026-10-01, recommendation) → `keep` (2026-10-15, recommendation executed and no further upgrade found) — the cleanest full cycle this report has produced for any role. `audio_translate` has been stable since 2026-05-10.

---

## 9. Open questions / uncertainty

- **Mistral Large 4's exact active-parameter count and license are both unverified.** Reports disagree between ~49B and ~52B active parameters, and Mistral has not published license terms — early reporting suggests a custom license rather than Large 3's Apache 2.0. Re-check both once weights land (promised "by end of October" 2026 per Mistral's own announcement).
- **Mistral Large 4's open weights have not shipped as of this run (2026-10-15).** Mistral's Hugging Face org had nothing newer than 2026-10-08 as of a 2026-10-13 check. Next run should verify whether the promised late-October release actually landed — this is exactly the kind of "announced but not yet real" item that created sourcing-gap misses for DeepSeek and Nemotron earlier this year; tracking it now avoids repeating that pattern.
- **A rumored next-generation "Nemotron 4" (>1T params) has no official NVIDIA confirmation** — a single August 2026 report describes it as possibly ready "as early as late this fall," with final training incomplete. Too speculative to score; watch for an official announcement before the next run.
- **Parakeet Ultra's behavior on this project's own wake-phrase/jargon corpus (#138/#343) has only been checked as part of the swap pre-flight** (no regression found, per report §10's change record) — a dedicated, deeper test against the full corpus (not just a regression check) is still open, as is testing either of the two untested secondary candidates (Nemotron 3.5 ASR Streaming, Granite Speech 4.1 2B) against it.
- **GLM-5.5 is still unshipped** past its own rumored August 2026 target — fifth consecutive run with no model card, benchmark, or endpoint. No GLM-5.4 found either.
- **"Mistral" was added to the explicit per-name search list this run** after noticing it had never been searched by name despite tracking three Mistral-family models already (Ministral 3 14B, Mistral Small 3.2, Mistral Medium 3.5) — this immediately caught Mistral Large 4. Worth treating as a standing reminder: the explicit per-name list (Nemotron, GLM, Qwen, DeepSeek, now Mistral) should periodically be re-audited against which families are actually tracked in the shortlist, since a tracked family with no explicit search is exactly how a sourcing gap happens.
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
| **audio_transcribe** | `parakeet` (Parakeet Ultra, a parakeet-tdt-0.6b-v3 derivative, Mac ANE) with `fallback: [whisper]` | 2026-07-22 (role primary, #350); 2026-10-02 (checkpoint → Ultra, #640) | Role primary changed directly via #350 (fleet placement/latency, #343 benchmark), not `/swap-model`. A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling; #401 stays open). The `runtime_upgrade` verdict (2026-10-01 run, §0/§7) was applied 2026-10-02 after the owner approved it: CC-BY-4.0 confirmed (same as v3), pre-flight showed no regression on the #138/#343 axis, WER vs archived refs 7.71 % vs 8.85 %, same ANE path and speed. Model id stays `parakeet`; only the worker's pinned checkpoint moved `.v3` → `.ultra`. This run (2026-10-15) found no further upgrade on top of it. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Strict frontier reading recommends `watch` — the two-stage path (Turbo → Gemma 4 26B) is the default, leaving this slot as a fallback only. Keep defined and lazy-loaded; no active maintenance. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the October 15, 2026 snapshot.*
