# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-05-10 (refreshed)
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, equivalently, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — there's a strictly better choice on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**:

- A 70B model that's dominant on a 5090 is not on the frontier of a 5060 Ti; it falls off into the "needs heavy CPU offload" zone where its speed drops below smaller models.
- A coding-specialist that wins SWE-bench is irrelevant here — coding is out of scope, so it gets evaluated only on the workloads we actually run.

This report identifies the frontier for *this* box and *these* workloads, as of May 2026.

**What changed since the previous run on this date:** The April 2026 release wave landed — most importantly **Qwen 3.6 35B-A3B** (2026-04-16, Apache 2.0, 98 t/s on the 5060 Ti at full 262k ctx) which now **ties Gemma 4 26B MoE** on the Tier B frontier. The Tier A pick also shifts from Qwen3 4B (2507) to **Qwen 3.5 4B** (Feb 2026, hybrid Gated DeltaNet + sparse MoE, 262k native ctx). Mistral Medium 3.5 (Apr 29) is added but immediately dropped on hardware grounds — 128B dense doesn't fit this box.

---

## 2. System & workloads

| | |
|---|---|
| **GPU** | NVIDIA RTX 5060 Ti, 16 GB VRAM, 448 GB/s memory bandwidth (Blackwell, FP4-capable) |
| **CPU** | AMD Ryzen 7 7800X3D, 8c/16t, 96 MB L3 — the large cache makes CPU inference unusually viable |
| **RAM** | 128 GB DDR5 (running at 3600 MT/s on a 6400 kit) — huge offload headroom |
| **Storage** | 2 TB NVMe (WD_BLACK SN850X) for hot model weights |
| **OS** | Windows 11 Pro |
| **Likely runtimes** | Ollama, LM Studio, llama.cpp / GGUF; vLLM via WSL2 if production-grade serving is needed |

Workload mix:

**Text workloads (LLM tier)** — composite quality score weights:

- **35%** agentic / function calling — BFCL v3/v4, τ-bench
- **25%** instruction following & writing polish — IFEval, Arena-Hard, RewardBench
- **25%** multilingual quality — FLORES-200 EN↔ES, EN↔CA where measured (Catalan coverage is uneven)
- **15%** long-context document handling — needle-in-haystack, RULER

**Audio workloads (ASR tier)** — evaluated separately in §7:

- Transcription: EN, ES audio → text in source language. Optimize for low WER and high RTFx (realtime factor).
- Audio translation: ES audio → English text. Two architectures evaluated — single-model (Whisper translate task) vs. two-stage (ASR → LLM).
- Transcript disfluency removal — strip filler words, false starts, repetitions. Currently handled by a post-processing model; the analysis assesses whether to keep it as a separate component or fold it into the LLM polishing pass.

Coding benchmarks (HumanEval, MBPP, SWE-bench, LiveCodeBench, etc.) carry **0% weight**.

---

## 3. Methodology

1. Surveyed open-weights families released or majorly updated since Q3 2025: Qwen, Llama, Gemma, Mistral, Phi, DeepSeek (non-coder), GPT-OSS, GLM, Hermes, Granite, Ministral, Kimi.
2. For each model: captured params (total/active for MoE), license, native context, and recommended quantization (Q4_K_M for GGUF, MXFP4 for GPT-OSS native, Q5/Q6 for higher-quality runs).
3. Computed VRAM cost using the rule of thumb **Q4_K_M ≈ 4.5 bits/param** plus KV-cache.
4. Pulled tok/s expectations on RTX 5060 Ti from published 2026 benchmarks (njannasch.dev for Qwen 3.6, Hardware-Corner, the arXiv Blackwell SME guide). Where 5060 Ti numbers were missing, triangulated from RTX 4070 / 4060 Ti 16 GB.
5. Pulled quality signals from the 2026 leaderboards (Vellum, BFCL v3/v4, FLORES-200 reports, Awesome Agents' tool-use leaderboard, the HF Open ASR Leaderboard's Q1 2026 multilingual update).
6. Built the composite score per workload weights, plotted speed vs. quality, dropped models dominated on both axes. Applied the ≤3% tie rule from the brief — Gemma 4 26B MoE and Qwen 3.6 35B-A3B land within 1% of each other on the composite, both stay on the frontier.

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant (smaller = lighter footprint).
- **Color** — tier: A fast / B balanced / C quality.
- **Filled border** — on the Pareto frontier. **Hollow** — dominated by another model.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

A model with a higher bubble *and* a more rightward bubble dominates one further down and to the left. The frontier is the curve along the upper-right edge.

### Worked memory example (so the math isn't a black box)

For **Qwen3 32B dense at Q4_K_M:**

```
weights ≈ 32B × 4.5 bits / 8 bits per byte = 18 GB
KV cache @ 8k ctx ≈ ~1.5 GB
total ≈ 19.5 GB
VRAM available ≈ 16 GB → ~3.5 GB spills to system RAM
```

3.5 GB on the 96 MB-L3 7800X3D over PCIe is the bottleneck — token generation drops from ~30 t/s (full GPU) to ~10–12 t/s. That's why Qwen3 32B dense lives in tier C even though it's the BFCL leader among open weights.

Compare **Qwen 3.6 35B-A3B** at Q4_K_M:

```
weights ≈ ~13.5 GB (3B active params during decode → bandwidth-bound on the active set)
KV cache @ 32k ctx ≈ ~2 GB
total ≈ ~15.5 GB → fits fully on a 16 GB GPU
result: 98 t/s on the 5060 Ti at full 262k context (njannasch.dev, 2026-04-22)
```

Same trick that put Gemma 4 26B MoE on the frontier: a tiny active-parameter footprint means decode bandwidth pressure is set by 3B, not 35B. **MoE remains the single biggest reason the frontier looks the way it does on consumer GPUs.**

---

## 5. Results — shortlist by tier

> Notation: ★ = strongly recommended primary pick. ☆ = solid alternative. Tied entries marked ★★ when ≤3% composite gap.

### Tier A — Fast lane (OpenClaw routing, classification, simple tool calls)

- **★ Qwen 3.5 4B** *(new top pick)* — Feb 2026 release. Hybrid Gated DeltaNet + sparse MoE on a 4B base. Q4_K_M ~3 GB. ~110 t/s. 262k native ctx. 201 languages, including good ES coverage; Catalan acceptable but less benchmarked than Gemma. Apache 2.0. Replaces Qwen3 4B (2507) on the frontier.
- **☆ Gemma 3 4B** — better creative-writing polish, weaker function calling. Q4 ~3 GB. ~100 t/s. Strong multilingual including Catalan — keeps it relevant as a CPU-side specialist on the 7800X3D. Gemma license.
- **Avoid for this stack:** Phi-4 Mini (English-strong but multilingual gap kills it for ES/CA), Llama 3.2 3B (community testing in 2026 reports unreliable triage behavior), Qwen3 4B 2507 (now dominated by the 3.5 4B successor).

### Tier B — Balanced (the workhorse for transcripts, documents, deep agentic) — **TIED**

- **★★ Gemma 4 26B MoE (4B active)** — 99 t/s, ~14 GB at native W4, full 256k context, strong multilingual including Catalan. The 2026 default workhorse and the incumbent in `roles.agentic_heavy`. Gemma license (commercial OK with terms).
- **★★ Qwen 3.6 35B-A3B** *(new entrant)* — Apr 16, 2026 release. 98 t/s, ~13.5 GB at Q4_K_M, 262k native ctx (extensible to ~1M), 201 languages. Apache 2.0 — cleaner license than Gemma. Composite quality ties with Gemma 4 26B (within 1%). **Catalan track record is shorter than Gemma's** — this is the only meaningful gap.
- **☆ GPT-OSS 20B (3.6B active, native MXFP4)** — Apache 2.0, exceptional agentic / tool use, fits comfortably in 16 GB, runs hot (488 t/s short-context API workloads on the 5060 Ti per the Blackwell SME paper). Multilingual is unverified for ES/CA — test before relying on it for translation. Good third option if you lean agentic.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive use)

- **★ Qwen3 32B dense at Q4_K_M** — BFCL v3 leader among open weights at 75.7%. ~10–12 t/s with the ~3.5 GB CPU spill. Best when correctness matters and latency doesn't, but increasingly competing with Qwen 3.6 35B-A3B which delivers similar quality at 9× the speed and full GPU residency.
- **☆ Llama 3.3 70B at Q4_K_M** — heavy CPU offload (~40 GB → mostly RAM), ~3–5 t/s. Quality leader for instruction-following polish on long English texts.
- **GLM-4.5 (355B MoE, 32B active)** — only viable at IQ2/IQ3 quantization; borderline fit even in 128 GB RAM, single-digit t/s. Mention because it tops BFCL, but realistically not worth running on this box.
- **Mistral Medium 3.5 (128B dense)** *(new release)* — Apr 29, 2026. 256k ctx, 77.6% on SWE-Bench. Modified MIT (revenue gates above a threshold). 128B dense ≈ 75 GB at Q4_K_M — heavy RAM offload and 1–2 t/s. **Dropped on hardware grounds, not quality.**

### Models considered and dropped (dominated)

- **Llama 3.2 3B** — dominated by Qwen 3.5 4B on agentic and Gemma 3 4B on multilingual.
- **Phi-4 Mini** — strong on English reasoning, but multilingual gap kills it for the ES/CA workload.
- **Mistral Small 3.2 (~22B dense)** — dominated by Gemma 4 26B MoE on speed *and* by Qwen 3.6 35B-A3B on quality at similar VRAM.
- **Gemma 3 27B (dense)** — dominated by Gemma 4 26B MoE: same family, better quality, ~6× faster on this GPU thanks to MoE bandwidth savings.
- **Qwen 3.6 27B (dense)** — released alongside the 35B-A3B MoE. 31 t/s, 65k usable ctx on the 5060 Ti. Same VRAM as the MoE sibling but ~3× slower and slightly lower composite. **MoE wins outright in the same family on this hardware.**
- **Qwen3.5-35B-A3B** — superseded by the 3.6 update on speed and quality at similar VRAM. Demote.
- **Qwen3 8B / Qwen3.5 9B** — kept on the chart but borderline. A useful "ultra-context" pick but for typical transcript/document work, the 26B MoE class wins.

---

## 6. Concurrency plan

Four practical recipes. All assume the hub's role-based slot model: one fast-lane model, one balanced workhorse, optional batch quality model.

### Recipe 1 — "Two lanes" (most common)

| Slot | Model | Where | VRAM | RAM | Notes |
|------|-------|-------|------|-----|-------|
| Fast lane | Qwen 3.5 4B Q4 | GPU | ~3 GB | — | OpenClaw routing, classification |
| Deep lane | Gemma 4 26B MoE | GPU | ~14 GB | — | Transcript polish, documents, deep agentic, EN↔ES↔CA |

Both fit fully in VRAM. ~17 GB total — minor shared-memory spill which Windows handles gracefully on Blackwell. Both run at near-peak speed.

### Recipe 2 — "Qwen 3.6 stack" (Apache 2.0 only)

| Slot | Model | Where | VRAM | RAM | Notes |
|------|-------|-------|------|-----|-------|
| Fast lane | Qwen 3.5 4B Q4 | GPU | ~3 GB | — | OpenClaw routing |
| Deep lane | Qwen 3.6 35B-A3B Q4 | GPU | ~13.5 GB | — | All-Apache stack — license clarity for Macaya Bank work |

Speed parity with Recipe 1. Use when license traceability beats Catalan track record.

### Recipe 3 — "Quality run" (batch / overnight)

| Slot | Model | Where | VRAM | RAM | Notes |
|------|-------|-------|------|-----|-------|
| Fast lane | Qwen 3.5 4B Q4 | GPU | ~3 GB | — | Stays hot for routing |
| Quality | Qwen3 32B Q4 | GPU + CPU | ~13 GB | ~6 GB | Long-form polish, hard reasoning |

Speed: fast lane unaffected, quality lane at ~10 t/s. Good for end-of-day transcript reprocessing, document reformatting, or a translation job where quality matters more than time.

### Recipe 4 — "Three concurrent" (heavy day)

| Slot | Model | Where | VRAM | RAM | Notes |
|------|-------|-------|------|-----|-------|
| Fast lane | Qwen 3.5 4B Q4 | GPU | ~3 GB | — | OpenClaw routing |
| Agentic deep | GPT-OSS 20B MXFP4 | GPU | ~12 GB | — | Tool-use heavy tasks |
| Translation/style | Gemma 3 4B Q4 | CPU | — | ~3 GB | EN↔ES↔CA translation, runs at ~10 t/s on the 7800X3D |

Total ~15 GB VRAM, ~3 GB RAM. The 7800X3D's 96 MB L3 makes Gemma 3 4B on CPU unusually responsive — useful as a Catalan specialist that doesn't compete for GPU.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 The landscape in May 2026

Whisper is no longer the WER leader on English benchmarks, but with the workload narrowed to **English + Spanish**, more options open up. The current accuracy leaders — NVIDIA Canary-Qwen 2.5B (5.63% avg WER), IBM Granite Speech 3.3 8B (5.85%), and Microsoft Phi-4-Multimodal — all become eligible *if* their Spanish coverage holds up.

Two updates this run:
- **Granite Speech 3.3 multilingual** confirmed on the model card for EN/FR/DE/ES/PT, with X↔EN speech translation for those pairs plus EN→{JA, ZH}. Granite is now back in scope as both a transcription *and* an audio-translation candidate.
- **NVIDIA Parakeet TDT v3** went multilingual with 25 languages including Spanish (HF Open ASR Leaderboard 2026-Q1 update). Worth a smoke test on real ES recordings — if quality holds, it's a 2000+ × RTFx contender.

The honest comparison is now between **Whisper Turbo (your current default)**, **faster-whisper Turbo** (same model, different runtime), **Whisper Large v3** (higher accuracy, slower, supports translate), **Granite Speech 3.3 8B** (top accuracy on EN, real ES support, supports speech translation), **Parakeet TDT v3** (speed king, Spanish quality unverified), and **Qwen3-ASR 1.7B** (still on the watch list).

### 7.2 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (5060 Ti, est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| **Whisper Large v3 Turbo** (current) | 809M | ~1.6 GB | 60–80× | ✅ | ✅ | ❌ | Your existing default. Solid. |
| **★ faster-whisper Turbo** | 809M (CT2) | ~1.0 GB INT8 / ~1.6 GB FP16 | 100–150× | ✅ | ✅ | ❌ | Same Turbo weights via CTranslate2. ~2× faster, lower VRAM. **Drop-in upgrade.** |
| **Whisper Large v3** (faster-whisper) | 1.55B | ~2 GB | 30–50× | ✅✅ | ✅✅ | ✅ | Higher accuracy, supports translate. Use for ES → EN single-pass. |
| **Granite Speech 3.3 8B** | 8B | ~5 GB FP16 | 15–30× | ✅✅✅ | ✅✅ | ✅ (X↔EN for FR/DE/ES/PT) | Top accuracy on EN (5.85% WER). ES coverage real, plus speech translation in the box. Heavier (~5 GB, ~3× the latency). |
| **Parakeet TDT v3** | 1.1B | ~2 GB | 2000+× | ✅✅✅ | ✅ | ❌ | Now multilingual (25 languages incl. ES). Speed king if ES quality holds — needs validation on real recordings. |
| **Qwen3-ASR 1.7B** | 1.7B | ~1.5 GB | TBD | ✅ | ✅ | ❌ | 52 languages incl. ES. Per-language ES WER not yet in published benchmarks — watch. |
| **Whisper Large v3 (translate task)** | 1.55B | ~2 GB | 30× | ✅✅ | ✅✅ | ✅ | Single-model audio→EN. Use as a fallback architecture for workload G. |

### 7.3 Recommendation: workload F — transcribe EN/ES

**Direct answer to "is there better than Whisper Turbo?":** Yes — but only as a runtime upgrade, not a model upgrade.

**★ Primary: faster-whisper Turbo (INT8)** — same Whisper Large v3 Turbo weights you're already using, but reimplemented in CTranslate2. You get roughly ~2× higher RTFx and lower VRAM at the same accuracy. No quality regression.

```
pip install faster-whisper
```

```python
from faster_whisper import WhisperModel
model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
```

Use `compute_type="int8_float16"` for the best speed/quality balance on the 5060 Ti. Drop to `"int8"` only if you need to free more VRAM for a concurrent LLM.

**☆ Accuracy upgrade (if you want it): Granite Speech 3.3 8B.** Better English WER, real Spanish + Portuguese support, Apache 2.0, speech translation included. Costs you ~5 GB VRAM and ~3× the latency. Only worth it if Whisper Turbo is making errors that matter on your specific recordings — otherwise the speed/footprint tradeoff favors Turbo.

**☆ Speed upgrade (if it lands): Parakeet TDT v3.** RTFx 2000+ on the multilingual track — by far the fastest option if Spanish quality holds. Currently a watch item until per-language ES WER is validated.

**Verdict:** Switch the runtime (faster-whisper), keep the model (Turbo). Re-evaluate Granite or Parakeet if you start hitting accuracy ceilings or batch-throughput ceilings.

### 7.4 Recommendation: workload G — ES audio → English

Two architectures, both viable. Choose by what else you need from the pass:

#### Architecture (i) — Single-model translation

faster-whisper **Large v3** (not Turbo — Turbo doesn't translate) with `task="translate"`. ~2 GB VRAM, ~30× realtime. Output is direct English text from Spanish audio.

- ✅ Simplest pipeline, lowest latency, lowest VRAM.
- ❌ Output is literal/transactional English. No style polish, no summarization, no light editing for conciseness.
- ❌ Whisper's translation quality for ES→EN is good but not great.

Granite Speech 3.3 8B is a quality-tier alternative here (it does ES→EN speech translation natively) but at ~5 GB and ~3× the latency.

#### Architecture (ii) — Two-stage ASR → LLM

faster-whisper Turbo transcribes in Spanish (~1 GB VRAM), then Gemma 4 26B MoE *(or Qwen 3.6 35B-A3B if you're on the all-Apache stack)* translates and polishes (~14 GB VRAM, ~99 t/s).

- ✅ Higher final translation quality — the LLM understands context, idiom, and your style preferences.
- ✅ Translation + polishing + disfluency removal in one LLM call.
- ✅ Catalan extension comes free if you ever add ES↔CA (Gemma stack only — Qwen 3.6 Catalan is less benchmarked).
- ❌ ~15 GB VRAM total — tight with the fast-lane model in slot 1.

**Verdict:** Default to architecture (ii). The quality gain on translation polish is real, your existing workflow already passes transcripts through an LLM for polishing anyway, and bundling translate+polish+disfluency into one LLM call is genuinely cheaper than running three components.

### 7.5 Workload H — disfluency / filler-word removal

You're currently using a dedicated post-processing model to strip filler words. The question: is there a better specialized model, or should you fold it into the LLM pass?

**Verdict: fold it into the LLM polishing pass.**

The honest landscape:
- Specialized disfluency-removal models (DisfluencyFixer, sequence-tagging models trained on Switchboard, the DISCO multilingual corpus) exist but are research-grade and primarily English / Hindi / German / French. **Spanish disfluency-correction models are sparse.**
- Tier-B LLMs (Gemma 4 26B MoE, Qwen 3.6 35B-A3B) handle disfluency removal extremely well as part of an instruction-following pass. The same model doing your transcript polish is already doing 80% of this work.
- Adding a separate component for filler-word removal means: another model to load, another VRAM slot, another point of failure, and worse multilingual coverage than the LLM you're already running.

**Recommended approach:** Add disfluency removal as an explicit instruction in your transcript-polishing prompt. Example:

```
Clean up this transcript:
- Remove filler words (um, uh, eh, este, like, you know)
- Remove false starts and self-corrections, keep the corrected version
- Tighten conciseness without losing meaning or voice
- Preserve technical terms and proper nouns exactly
- Keep the output in [Spanish/English] — do not translate

Transcript:
{transcript}
```

This subsumes your current dedicated disfluency-removal step into the polish pass, frees a component, and gives better Spanish coverage than any specialized open-source model in May 2026.

If you find the LLM occasionally over-edits (e.g. removes idiomatic discourse markers that aren't really fillers), the fix is a tighter prompt with examples, not a different model.

### 7.6 Concurrency footprint

Adding the ASR stage to the existing recipes:

- **Recipe 1 (two lanes) + ASR:** add faster-whisper Turbo INT8 (~1 GB VRAM) to slot 0. Fits comfortably with Qwen 3.5 4B + Gemma 4 26B MoE. Total ~18 GB — slight shared-memory spill but functional.
- **Recipe 5 (audio batch):** faster-whisper Turbo (~1 GB) + Gemma 4 26B MoE (~14 GB) for transcribe → polish/translate/disfluency in one LLM call. ~15 GB VRAM. Fast lane drops out for the batch.
- **CPU fallback:** faster-whisper Turbo runs at ~5–10× realtime on the 7800X3D's 8 cores. Useful if GPU is fully claimed.

### 7.7 Models considered and dropped

- **NVIDIA Canary-Qwen 2.5B** — accuracy leader but English-only. Out.
- **NVIDIA Parakeet TDT v1/v2** — fastest English ASR (RTFx 2000+) but English-only. v3 is back in scope (see §7.1).
- **Distil-Whisper** — fast and accurate but English-only. Out.
- **Microsoft Phi-4-Multimodal** — top accuracy tier, multilingual capable, but the speech variant has a heavier footprint and weaker community tooling than Whisper. Eligible but not currently best-in-class for this stack.
- **Seamless M4T v2** — capable single-model speech-to-text-translation but heavier (2.3B+) and the community tooling is less mature than Whisper's. Not currently better than two-stage ASR + LLM.
- **Specialized disfluency-removal models (DisfluencyFixer etc.)** — outperformed by an LLM polishing prompt for this language mix.

---

## 8. How to refresh this analysis (next quarter)

Anchored to the prompt file `docs/frontier/RESEARCH_PROMPT.md`. Quick checklist:

- [ ] Update the run date (use `/frontier-refresh`).
- [ ] Re-survey the families in §3.1 of the prompt for releases in the last 90 days. The fast-moving ones in 2026 are Qwen, Gemma, GLM, Mistral, and Kimi. Llama has been quieter.
- [ ] Check r/LocalLLaMA top-of-month and Hugging Face trending-30-day.
- [ ] Re-pull BFCL (now v4) and FLORES standings.
- [ ] **Re-test Qwen3-ASR on ES** — could displace Whisper if 2026 community benchmarks land.
- [ ] **Validate Parakeet TDT v3 ES quality** — speed king if it holds.
- [ ] Re-check Granite Speech 3.3 ES per-language WER if IBM publishes it.
- [ ] Recompute the memory math only if a major new quantization format ships (e.g., NVFP4 going mainstream, post-training Q3 schemes improving).
- [ ] Diff against this report — note promotions, demotions, new entrants on the frontier.
- [ ] Regenerate the artifact with new data, archive the old as `…-2026-05-10.html`.

What rarely changes between runs:

- The memory math (it's deterministic given the model and quant).
- The role of MoE in dominating dense models on consumer GPUs.
- The dominance of CPU offload as the failure mode for >20 GB models on 16 GB VRAM.

What usually changes:

- Which specific model wins each tier (this run: Qwen 3.6 35B-A3B tied Gemma 4 26B at Tier B; Qwen 3.5 4B replaced Qwen3 4B at Tier A).
- BFCL leaderboard top spots (3-month half-life).
- Default quantization recommendations from Ollama / LM Studio.

---

## 9. Open questions / uncertainty

- **Catalan translation quality** — FLORES-200 has Catalan but the public per-model breakdowns for newer 2026 models are sparse, especially Qwen 3.6. Recommend a manual smoke test before swapping the all-Apache stack in for any Catalan-heavy work.
- **Qwen 3.6 35B-A3B vs. Gemma 4 26B MoE** — composite tie within ~1%. Catalan is the unverified delta. If Catalan smoke tests show parity, flip to Qwen 3.6 for cleaner license; otherwise keep Gemma.
- **GPT-OSS 20B multilingual** — published agentic numbers are excellent, but I couldn't find systematic FLORES results for ES/CA. Test before relying on it for translation.
- **Qwen 3.6 35B-A3B at long context with concurrent fast-lane model** — single-model 98 t/s at 262k is well-documented; behaviour with a second model competing for VRAM and the shared GPU memory pool on Windows is less so.
- **GLM-4.5 / GLM-5.1 at IQ2 on 128 GB RAM** — theoretically viable, practically painful. Would need a dedicated benchmarking session before recommending.
- **Memory-bandwidth ceiling for MoE models** — 448 GB/s on the 5060 Ti caps decode speed at ~110 t/s for 3-4B-active MoEs. Both Tier B picks sit near this ceiling.
- **Granite Speech 3.3 8B Spanish quality** — top-tier on EN benchmarks (5.85% WER) and the model card now confirms ES + speech translation, but per-language ES WER is not as deeply benchmarked as English. Run a smoke test if you're considering switching off Whisper Turbo for accuracy reasons.
- **Parakeet TDT v3 Spanish quality** — multilingual support newly announced; per-language ES WER not in the May 2026 public numbers. Smoke-test before any switch.
- **Qwen3-ASR for Spanish** — promising 52-language coverage and a small footprint, but per-language ES WER not yet in published benchmarks.
- **faster-whisper INT8 quality on accented ES** — INT8 quantization saves ~30% VRAM but quality on accented or noisy audio is rarely benchmarked. Validate before relying on it.

---

## 10. Current decisions (live, edited by `/swap-model`)

The decisions below mirror `config/models.yaml` → `roles:` at the time
this section was last updated. `/swap-model` rewrites both this section
and the yaml together, so the two stay in sync.

| Role | Model | Decided | Why |
|---|---|---|---|
| **agentic_light** | `gemma4_e4b` (gemma4-e4b-it) | 2026-05-10 | Incumbent. Working in production for OpenClaw routing/classification. Strict frontier reading prefers Qwen 3.5 4B (Tier A top pick), but no measured ceiling on the incumbent yet — keep until proven worse. |
| **agentic_heavy** | `gemma4_26b` (gemma4-26b-a4b-it) | 2026-05-10 | Tier B top pick on the frontier. 99 t/s, 256k ctx, strong multilingual including Catalan. Now tied with Qwen 3.6 35B-A3B (Apache 2.0) — Gemma stays default on Catalan track record. |
| **audio_transcribe** | `whisper` (whisper-large-v3-turbo) | 2026-05-10 | Runtime upgrade to faster-whisper Turbo is the strict-frontier pick (~2× RTFx, lower VRAM, same quality). **Engine code change pending** — manual work in `src/run_backend.py` + `src/backend_process.py`. Until then, stay on whisper.cpp Turbo. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Strict frontier reading recommends `watch` — report §7.4 makes the two-stage path (Turbo → Gemma 4 26B) the default, leaving this slot as a fallback only. Keep defined and lazy-loaded; no active maintenance. |

---

*Generated as a companion to `docs/frontier/RESEARCH_PROMPT.md`. The prompt is the reusable source-of-truth; this report is the May 2026 snapshot (refreshed).*
