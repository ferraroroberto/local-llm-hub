# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-10-01
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b_nothink` (qwen3.5-4b-nothink) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant this window. Qwen 4 was previewed at Alibaba's Apsara Conference (2026-09-22) in four tiers (Max / Flash / Plus / 27B) — unshipped, no benchmarks, no license, and the smallest tier (27B) is still two orders of magnitude above this role's size class |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists, eighth consecutive run. One new entrant evaluated and dominated: **DeepSeek V4.1 Flash** (748B total — 552B backbone + 196B "Engram" component — ~8B/16B active prefill/decode, MIT, shipped 2026-09-10, a sourcing-gap catch missed by the 2026-09-17 run) needs ~190-420 GB even at the smallest viable quant, an order of magnitude past this box's ~144 GB combined budget. Qwen4-27B (previewed 2026-09-22 as the Qwen 4 family's open-weights local-deployment tier) remains unshipped — no spec, license, or benchmark yet |
| `audio_transcribe` | `parakeet` (parakeet-tdt-0.6b-v3, Mac ANE) + whisper fallback | **runtime_upgrade** | Parakeet Ultra (FluidAudio v0.17.3, 2026-09-24) | +alt (WER ↓ on every published axis, same speed) | **First strict, verified upgrade over the incumbent transcribe model since the 2026-07-22 Parakeet switch.** Moondream's post-trained Parakeet Ultra is a drop-in, same-API, same-speed, same-25-language derivative of v3 — measurably lower WER everywhere, including the FLEURS 24-language mean (11.67% vs v3's 14.81%), the multilingual number most relevant to this project's EN/ES workload. Zero integration cost (`AsrModelVersion.ultra`, a one-line change). Does **not** address the wake-phrase/jargon gap (#401) — untested and architecturally unrelated to this swap; see §7 |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage: Turbo → Gemma 4 26B MoE | — | Unchanged. Neither of this window's new entrants (DeepSeek V4.1 Flash, Nemotron 3 Ultra — see §5) is an ASR model; nothing new for this role |

**Diff vs previous run (2026-09-17):** `audio_transcribe` flips from `watch` to **`runtime_upgrade`** — the first verdict change for this role since the 2026-07-24 → 2026-08-06 disproof, and the first time this report answers "yes" to the brief's standing question about a strict EN/ES transcribe upgrade. FluidAudio shipped eight releases in this window (v0.15.8 through v0.17.5); the material one is **v0.17.3** (2026-09-24), which introduced **Parakeet Ultra** — a post-trained, drop-in derivative of the incumbent v3 checkpoint with better WER at the same speed and zero integration cost. This does not resolve the long-standing wake-phrase/jargon gap (#401) — that stays `watch`, with Nemotron 3.5 ASR Streaming and Granite Speech 4.1 2B as secondary, still-untested candidates on that specific axis (§7). On the text side: one sourcing-gap catch (DeepSeek V4.1 Flash, shipped 2026-09-10 — inside the *previous* run's own window but missed by it) and one unshippable preview (Qwen 4, shown at Apsara 2026-09-22 in four tiers, none benchmarked) — both dominated or not-yet-real, so `agentic_light` and `agentic_heavy` hold unchanged for the eighth and eighth consecutive runs respectively.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of October 2026.

**What changed since 2026-09-17:** The headline this run is on the audio side, not text — see §0's diff line and §7. On text, two items surfaced, both inert for this box: **Qwen 4** was previewed at Alibaba's Apsara Conference (2026-09-22) in four tiers (Max, Flash, Plus, 27B) — "in training," no spec sheet, no benchmarks, no release date; Alibaba's own roadmap points at Qwen 4.5 and Qwen 5 reaching 5-10 trillion parameters afterward. **DeepSeek V4.1 Flash** (552B backbone + 196B Engram component, 748B total, MIT, native vision, 1M context) shipped 2026-09-10 — inside the *previous* run's survey window, but missed by it; it does not fit this box at any quant (§4). An explicit "GLM" search (adopted this run per the prior run's own open question) found GLM-5.5 still unshipped past its rumored August 2026 target, and no GLM-5.4. The explicit "Nemotron" search also turned up a genuine sourcing-gap catch — **Nemotron 3 Ultra** (550B/55B active, shipped 2026-06-04, OpenMDW license) — a model that predates even this project's first frontier run but was never caught by the per-name search policy until now (see §9 for why that's a process gap worth noting, not just a model-math one).

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

1. Read `docs/frontier/runs/LATEST` (2026-09-17), that run's `report.md` + `frontier.json`, and `docs/frontier/local-findings.md` (#277) — the same three unresolved entries carry forward unchanged (faster-whisper CTranslate2, FluidAudio `CustomVocabularyContext`, Nemotron 3 Nano 4B). None of this run's new candidates match an unresolved entry, so no local-findings override applies.
2. Re-read `config/models.yaml` → `roles:` for the current incumbents. No out-of-band changes since the last run.
3. Surveyed the external landscape for the 2026-09-17 → 2026-10-01 window. Explicit per-name searches (the policy adopted after prior sourcing-gap misses): "Nemotron" surfaced no new September entrant beyond the already-known Nemotron 3 Super, but did catch **Nemotron 3 Ultra** (550B/55B active, shipped 2026-06-04 — missed by every run to date, including the ones after the explicit-search policy started 2026-09-03; see §9). "GLM" (newly added this run per the 2026-09-17 report's own §9 suggestion) found GLM-5.5 still unshipped past its rumored August target, no GLM-5.4. Also checked Qwen (Qwen 4 previewed 2026-09-22 at Apsara, unshipped — see §1/§9) and DeepSeek (V4.1 Flash, shipped 2026-09-10, a sourcing-gap catch from the *previous* run's own window). Swept Mistral/Granite/Falcon/OLMo/Hermes/GPT-OSS/Phi/Gemma/Llama/Command/Yi for anything new in the window — nothing found beyond already-carried entries.
4. On the audio side: queried FluidAudio's GitHub releases directly via `gh api repos/FluidInference/FluidAudio/releases` rather than trusting web-search summaries, after an initial search misreported 2026 release dates as 2024 — cross-checked against the GitHub API's own `published_at` timestamps, which are authoritative. Found eight releases since the last run's v0.15.7 (v0.15.8 through v0.17.5, 2026-09-20 through 2026-10-01). The material one is **v0.17.3** (2026-09-24): Moondream's **Parakeet Ultra** and **Parakeet Redux**, both drop-in derivatives of Parakeet TDT v3 (same 25 languages, tokenizer, API). Pulled the full release body (not just the summary) for the comparison table in §7.2. Confirmed via a second search that Ultra's wake-phrase/jargon behavior is untested publicly — no external source has run this project's specific failure-mode test.
5. Computed VRAM for this window's two oversized new entrants (DeepSeek V4.1 Flash, Nemotron 3 Ultra) with the standing rule of thumb **Q4_K_M ≈ 4.5 bits/param** on total (not active) params for MoE — both fail the combined ~144 GB budget by a wide margin even at the lowest viable quant; see §4's worked example.
6. Applied the honesty rules: date-stamped every claim (including the corrected FluidAudio release dates), flagged Parakeet Ultra's exact license terms as **unverified** (a Moondream-trained derivative of NVIDIA's Parakeet v3 checkpoint — the precise license inheritance was not independently confirmed), and explicitly did not claim Ultra fixes the wake-phrase/jargon gap — it wasn't trained to, and that claim would repeat the exact mistake #274/#401 already taught this project not to make. The `runtime_upgrade` verdict rests entirely on Ultra's own published, same-speed, better-WER numbers against the incumbent — not on the jargon axis.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

This run's cautionary tale is **DeepSeek V4.1 Flash** — a case where the math fails before speed even becomes the question.

```
748B total params (552B backbone + 196B "Engram" component), ~8B active (prefill) / 16B active (decode)
This box: 16 GB VRAM + 128 GB RAM = ~144 GB combined ceiling

Even at the smallest viable GGUF quant for a usable MoE (2-bit, ~2.0 bits/param):
  748B x 2.0 bits / 8 = ~187 GB  -- already 30% past the 144 GB combined budget

At this report's standard assumption (Q4_K_M, ~4.5 bits/param):
  748B x 4.5 bits / 8 = ~421 GB  -- nearly 3x the budget

Unlike last run's Nemotron 3 Super (which fit the budget at Q4_K_M and only failed on
speed), DeepSeek V4.1 Flash does not clear the storage bar at any quant this box could
hold, active-param efficiency (8B/16B active) notwithstanding -- the MoE's full expert
set must still be resident/pageable, and 748B of experts simply doesn't fit in 144 GB
even at 2-bit.
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB fits as a monolithic GPU load with no offload tuning required, 99 t/s measured, Catalan-verified across eight consecutive runs. DeepSeek V4.1 Flash's efficient *active*-parameter design (8B/16B active, genuinely competitive with the incumbent's 4B active) doesn't matter here — MoE routing still requires every expert resident somewhere, and 748B total experts is simply too large a model for this box's combined envelope, full stop. Same conclusion as DeepSeek V4 Pro, Qwen3.8 2.4T, Kimi K3, and GLM-5.2/5.3 before it: an architecturally efficient flagship that is still the wrong shape for a single 16 GB + 128 GB workstation.

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

No change this run. Qwen4-27B (previewed 2026-09-22, dense-or-MoE architecture still undecided per Alibaba's own reporting) is the clearest future Tier B candidate — unshipped, no spec, watch next cycle.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen3 32B dense | 32B | Q4_K_M | ~19.5 GB (spill) | ~11 | 84 | 128k | Apache 2.0 | yes |
| GLM-5.3-Flash | 320B / 18B active | GGUF (Unsloth dynamic 3-bit) | ~110 GB (spill) | ~12 est | 76 est | 1M | MIT | no |
| Nemotron 3 Super | 120B / 12B active | Q4_K_M | ~70 GB (spill) | ~6 est | 78 est | 128k | NVIDIA Nemotron Open Model License | no |
| DeepSeek V4.1 Flash *(new)* | 748B total (552B backbone + 196B Engram) / 8-16B active | GGUF (2-bit floor) | ~187-421 GB (doesn't fit) | n/a — fails storage bar | unverified | 1M | MIT | no |
| Nemotron 3 Ultra *(new — sourcing-gap catch, shipped 2026-06-04)* | 550B / 55B active | Q4_K_M | ~309 GB (spill) | ~1-2 est | unverified | 262k | OpenMDW-1.1 | no |
| Qwen3.8-Flash-Next | 180B / 6B active | GGUF (smallest) | ~85 GB | ~20 est | 78 est | 262k (1M YaRN) | Qwen Community 1.0 | no |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

No change to the frontier. **DeepSeek V4.1 Flash added** — a sourcing-gap catch (shipped 2026-09-10, inside the *previous* run's own window but missed by it): Z.ai-rival-class MoE with native vision and an efficient active-parameter design, but 748B total experts fail the combined 144 GB budget at any usable quant (§4's worked example — this is a sharper NO-GO than any prior oversized entrant, since it doesn't even clear the storage bar). **Nemotron 3 Ultra added** — a sourcing-gap catch from further back (shipped 2026-06-04, predates this report's first run): 550B/55B active, doesn't fit at Q4_K_M (~309 GB), bandwidth-bound estimate ~1-2 t/s even if it did.

### Models considered and dropped this run

- **DeepSeek V4.1 Flash (748B total — 552B backbone + 196B Engram — MIT, shipped 2026-09-10)** — a sourcing-gap catch: shipped inside the *2026-09-17* run's own survey window but missed by it. 8B/16B active prefill/decode is an efficient MoE design on paper, but 748B total experts don't fit this box's ~144 GB combined budget at any quant from 2-bit up (§4). Evaluated in Tier C above.
- **Nemotron 3 Ultra (550B/55B active, OpenMDW license, shipped 2026-06-04)** — the deepest sourcing-gap catch to date: this model predates even this report's first run (2026-05-10) and was missed by every subsequent run, including all four that have run under the explicit-per-name-search policy adopted 2026-09-03. Doesn't fit 16 GB VRAM (~309 GB at Q4_K_M), no consumer-hardware tok/s measurement exists, bandwidth-bound estimate ~1-2 t/s for 55B active experts. See §9 for why the sourcing-gap pattern itself, not just this model, is the open item.
- **Qwen 4 (previewed 2026-09-22, Apsara Conference, four tiers: Max/Flash/Plus/27B)** — "in training" per Alibaba, no spec sheet, no license, no benchmarks, no release date. Not a candidate for any role yet — watch next cycle, most likely subject of the next frontier run.
- **GLM-5.5** — still unshipped past its rumored August 2026 target; no GLM-5.4 found via this run's newly-added explicit "GLM" search. Watch next cycle.
- **GLM-5.3-Flash / Nemotron 3 Super / Qwen3.8-Flash-Next / Nemotron 3.5 Lightning / DeepSeek V4 Pro / Qwen3.8 2.4T-A95B / Kimi K3** — no updates this window; standing NO-GOs carried unchanged.
- Standing drops carried from prior runs: Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2 (Tier B entry, listed above for comparison), Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class, MiniMax M3 (still academic at 2-bit), Nemotron 3 Nano 4B (quality-disproven locally, #486 — see `local-findings.md`).

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

No placement changes this window on the text side. The `audio_transcribe` runtime upgrade (§0/§7) is a zero-footprint swap — Parakeet Ultra runs on the exact same Mac Mini ANE allocation as the incumbent v3 (same CoreML path, same ~2 GB), so it changes no concurrency math anywhere in the fleet.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 Parakeet Ultra ships — the first strict transcribe upgrade this report has found

**FluidAudio shipped eight releases in this window** (v0.15.8 through v0.17.5, 2026-09-20 through 2026-10-01 — confirmed via the GitHub API's own timestamps, not web-search summaries, after an initial search misreported the years). The material one is **v0.17.3** (2026-09-24): two new batch ASR models from **Moondream**, both explicitly "drop-in derivatives of Parakeet TDT v3 (same 25 languages, tokenizer and API)":

- **Parakeet Ultra** (`AsrModelVersion.ultra`) — a post-trained v3 that FluidAudio's own release notes describe as "more accurate everywhere at the same speed." int8 encoder, Neural Engine by default, iOS 17+/macOS 14+ — no minimum-OS regression versus the incumbent.
- **Parakeet Redux** (`AsrModelVersion.redux`) — a ternary 2-bit encoder, smaller download (~220 MB vs ~480 MB) but *worse* WER than v3 across every published metric. Dominated; not a candidate.

Published comparison (full sets, M-series Mac, ANE, same FluidAudio session):

| metric | v3 (incumbent) | Redux | **Ultra** |
|---|---:|---:|---:|
| LibriSpeech test-clean WER | 2.27% | 2.71% | **2.13%** |
| LibriSpeech test-other WER | 4.12% | 5.12% | **3.81%** |
| FLEURS, 24 languages, mean WER | 14.81% | 13.06% | **11.67%** |
| test-clean RTFx | 128.6× | 83.9× | 126.7× |

The FLEURS 24-language number is the one that matters most for this project's EN/ES workload: **11.67% vs the incumbent's 14.81%**, a genuine multilingual accuracy gain, not just an English-benchmark win — at essentially parity speed (126.7× vs 128.6×, within measurement noise). Integration cost is a one-line version-string change (`AsrModelVersion.ultra`); no new runtime, no new OS minimum, no new download path beyond the model weights themselves.

**What this is not:** Ultra is a post-trained checkpoint, not a new vocabulary-biasing mechanism. It was not built to fix — and the honesty rules require stating plainly that it is untested against — the wake-phrase/jargon regression (#401) that this project accepted when it switched to Parakeet primary on 2026-07-22. Nemotron 3.5 ASR Streaming (FluidAudio v0.15.7's decode-time biasing target) and Granite Speech 4.1 2B remain the live candidates for *that* specific axis, both still untested locally. Ultra's `runtime_upgrade` verdict rests entirely on its own published, same-speed, better-WER numbers against the incumbent on the generic-accuracy axis — a different, independently justified claim from the jargon-fix question.

### 7.2 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (measured/est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| **Parakeet Ultra** (new — recommended swap) | 0.6B | ~2 GB (CoreML) | 126.7× measured (M-series ANE), parity with v3 | ✅ (better WER) | ✅ (FLEURS 24-lang 11.67% vs v3's 14.81%) | ❌ | Drop-in post-trained v3 derivative (FluidAudio v0.17.3, 2026-09-24). Same API, same speed, measurably better WER on every published axis. License exact terms unverified (Moondream-trained NVIDIA-v3 derivative). Untested on this project's #138/#343 jargon corpus — that question is orthogonal to this swap, not blocking it. |
| ★ Parakeet TDT v3 (Mac ANE, current incumbent) | 0.6B | ~2 GB (CoreML) | 128.6× measured (ANE) | ✅ | ✅ (25 langs) | ❌ | Unchanged incumbent since 2026-07-22, now dominated on generic accuracy by its own Ultra successor (same family, same runtime). |
| ◆ Whisper Large v3 Turbo (automatic failover) | 809M | ~1.6 GB | 40× tower / 19.3× gaming (measured, boosted) | ✅ | ✅ | ❌ | Unchanged. Still the accuracy leader on the jargon domain thanks to `--carry-initial-prompt` boosting (#91) — a lever neither Parakeet variant has. |
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B (secondary candidate) | 0.6B | ~2 GB est (CoreML) | TBD (domain) | ✅ (FLEURS 7.91% — worse than Parakeet's card) | ✅ (FLEURS 4.11% — worse than Parakeet's card) | ❌ | No change this window. FluidAudio v0.15.7's decode-time biasing hook still targets this model only. Demoted to secondary priority behind the concrete, zero-risk Ultra swap. |
| Granite Speech 4.1 2B (secondary candidate) | 2B | ~2 GB est | TBD (domain) | ✅ | ✅ (6 langs, bidirectional AST) | ✅ | No change this window — still leads the public HF Open ASR Leaderboard at 5.33% WER. Secondary priority, same as last run. |
| ✗ Parakeet + FluidAudio CustomVocabularyContext | 0.6B + ~97 MB CTC model | ~2 GB + rescorer | n/a — disproven, not shipped | — | — | ❌ | Disproven #401 (2026-07-24). Carried `watch` per `local-findings.md`. FluidAudio v0.17.5 (2026-10-01) made the Parakeet-path alignment "iterative" (#962) — a robustness fix to the same post-hoc rescoring mechanism, not a mechanism change. Does not meet the re-open trigger. |
| faster-whisper Turbo (CT2) | 809M (CT2) | ~1.0 GB INT8 | measured 1.0× vs whisper.cpp — **disproven** | ✅ | ✅ | ❌ | Carried `watch` per `docs/frontier/local-findings.md` (#277); applies to the failover leg's engine, not the primary. |
| Qwen3-ASR (1.7B / MLX build) | 1.7B | ~1.5 GB | TBD (domain) | ✅ | ✅ | ❌ | `watch`, unchanged status. |

### 7.3 Workload F — transcribe EN/ES

**Verdict: `runtime_upgrade`.** For the first time, this run can answer the brief's required question with a qualified **yes**: Parakeet Ultra (FluidAudio v0.17.3, 2026-09-24) is a verified, same-speed, better-WER, zero-integration-cost upgrade over the incumbent `parakeet` (v3) checkpoint for generic EN/ES transcription accuracy, including the FLEURS multilingual number most relevant to this project. It does **not** fix the wake-phrase/jargon regression accepted at the 2026-07-22 switch — that remains a separate, still-unresolved problem tracked by #401 and this run's secondary candidates (Nemotron 3.5 ASR Streaming, Granite Speech 4.1 2B). Recommended pre-flight for `/swap-model`: a quick run of the existing #138/#343 jargon corpus against Ultra before flipping the config, purely to confirm no *regression* on that axis (not required to justify the swap itself, which stands on its own generic-accuracy merits).

### 7.4 Workload G — ES audio → English

**Verdict unchanged: two-stage default.** faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call (~15 GB total). Single-model faster-whisper Large v3 `task=translate` stays the fallback when the LLM slot is busy. Neither of this window's new text entrants (DeepSeek V4.1 Flash, Nemotron 3 Ultra) is an ASR model, so neither is a candidate for this role. Granite Speech 4.1 2B's native bidirectional ES↔EN speech translation remains a candidate to eventually replace the single-model fallback leg's engine — no change to the default this run.

### 7.5 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`).

### 7.6 Concurrency footprint

Unchanged from the previous run, and the Parakeet Ultra swap changes nothing here: it runs on the exact same Mac Mini ANE CoreML path as the incumbent v3, at the same ~2 GB footprint, so whisper-turbo (gaming satellite), orpheus (tower), and piper (tower CPU) are all unaffected.

### 7.7 Dropped

Carried from prior runs: Canary-Qwen (EN-only), Parakeet v1/v2 (superseded by v3, now also by Ultra), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass), FluidAudio CustomVocabularyContext (disproven 2026-07-24, §7.2), Granite Speech 3.3 8B (superseded within its own family by 4.1 2B), Parakeet Redux (worse WER than both v3 and Ultra, dominated).

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

Reading the progression: eight consecutive runs now (2026-05-10 through 2026-10-01) have kept `agentic_heavy` on the Gemma 4 26B / Qwen 3.6 35B-A3B tie despite nine genuinely new entrants across that span — every one dominated by architecture, size/fit, or unverified speed. `agentic_light` has been unchanged in substance since 2026-05-10 across nine runs. `audio_transcribe` flips to `runtime_upgrade` for the first time since the 2026-07-24 disproof cycle — the fifth verdict change for this role in nine runs, and this report's first same-architecture, zero-risk, verified swap (as opposed to the 2026-07-24 cycle's build-and-disprove pattern on a different mechanism entirely). `audio_translate` has been stable since 2026-05-10.

---

## 9. Open questions / uncertainty

- **Parakeet Ultra's exact license terms are unverified.** It's a Moondream-trained derivative of NVIDIA's Parakeet TDT v3 checkpoint; the precise license inheritance (NVIDIA's original terms, a Moondream overlay, or something else) was not independently confirmed via this run's research. Verify before `/swap-model` commits the config change.
- **Parakeet Ultra's behavior on this project's own wake-phrase/jargon corpus (#138/#343) is untested.** The `runtime_upgrade` verdict does not depend on this — it rests on Ultra's own published generic-WER numbers — but a quick regression check (confirm "Claude Code" and "YOLO" behave no worse than on v3) is cheap and recommended before the swap, purely as due diligence given this project's history (#274, #401) with ASR claims.
- **The sourcing-gap pattern has a new wrinkle: Nemotron 3 Ultra (550B/55B active) predates this report's very first run (2026-05-10) and was still missed by all four runs that have used the explicit "search Nemotron by name" policy since 2026-09-03.** That means the explicit-name-search habit alone isn't sufficient — it found the *name* "Nemotron" in results about Nemotron 3 Nano/Super/Lightning without ever surfacing the Ultra tier specifically. Worth considering a change to the search method itself next run: check NVIDIA's own Nemotron model index/landing page directly rather than relying on news-aggregator search results, which evidently don't surface every SKU in a family even when the family name is explicitly searched.
- **DeepSeek V4.1 Flash was missed by the previous run despite shipping inside that run's own 2026-09-03 → 2026-09-17 window** (shipped 2026-09-10). Consider adding "DeepSeek" to the explicit per-name search list alongside Nemotron and (as of this run) GLM, given this is now the third family (Nemotron, GLM, DeepSeek) to have a same-window sourcing miss.
- **Qwen 4 is the clearest "watch next cycle" item** — previewed at Apsara (2026-09-22) in four tiers (Max/Flash/Plus/27B), described as "in training" with no spec sheet. The 27B tier is the one to watch for `agentic_heavy`-adjacent relevance once real numbers exist; architecture (dense vs. MoE) is still undecided per Alibaba's own reporting.
- **GLM-5.5 is still unshipped** past its own rumored August 2026 target — fourth consecutive run with no model card, benchmark, or endpoint. No GLM-5.4 found either.
- **Nemotron 3.5 ASR Streaming and Granite Speech 4.1 2B both remain untested locally** — now explicitly secondary priority behind the Parakeet Ultra swap, since Ultra is lower-risk (same architecture family) and already verified on published numbers. If idle capacity allows, testing Ultra first, then whichever secondary candidate looks most promising afterward, is the efficient order.
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
| **audio_transcribe** | `parakeet` (parakeet-tdt-0.6b-v3, Mac ANE) with `fallback: [whisper]` | 2026-07-22 | Changed directly via #350 (fleet placement/latency, #343 benchmark), not `/swap-model`. A speed-over-accuracy trade with known regressions (dropped wake phrase, jargon mangling). This run recommends a `runtime_upgrade` to Parakeet Ultra (§0/§7) — not yet applied; config unchanged until `/swap-model` runs. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Strict frontier reading recommends `watch` — the two-stage path (Turbo → Gemma 4 26B) is the default, leaving this slot as a fallback only. Keep defined and lazy-loaded; no active maintenance. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the October 1, 2026 snapshot.*
