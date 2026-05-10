# Local LLM Efficient Frontier — Research Brief

**Last updated:** 2026-05-10
**Run cadence:** quarterly, or whenever a notable open-weights model launches
**Owner:** Roberto

---

## 1. Goal

Produce the **efficient frontier (Pareto front)** of currently-available open-weights LLMs for **local inference on the system below**, optimized for *my* workloads — explicitly **no coding**.

Two deliverables:

1. **Interactive HTML/React artifact** plotting the frontier, filterable by use-case tier and VRAM budget.
2. **Didactic markdown report** explaining objectives, methodology, results, and how to repeat the analysis.

---

## 2. System

- **GPU:** NVIDIA RTX 5060 Ti, **16 GB VRAM** (Blackwell, FP4-capable)
- **CPU:** AMD Ryzen 7 7800X3D (8c / 16t, 96 MB L3 — strong CPU-inference candidate thanks to large cache)
- **RAM:** **128 GB DDR5** (huge CPU-offload / spill headroom; running at 3600 MT/s on a 6400 kit)
- **Storage:** 2 TB NVMe (WD_BLACK SN850X) + 11 TB HDD across two SATA drives
- **OS:** Windows 11 Pro (build 26200)
- **Likely runtimes:** Ollama, LM Studio, llama.cpp / GGUF, ExLlamaV2, vLLM under WSL2

---

## 3. Workloads (priority order)

### Text workloads (LLM tier)

| # | Use case | Latency tolerance | Quality bar |
|---|----------|------------------|-------------|
| A | OpenClaw **fast lane** — easy agentic steps, routing, simple tool calls, classification | very low | medium |
| B | OpenClaw **deep lane** — hard agentic reasoning, multi-step tool use, planning | medium | high |
| C | Transcript cleanup & conciseness polishing (post-ASR, EN/ES/CA) | medium | high (style) |
| D | Document processing — extraction, summarization, restructuring | medium | high |
| E | Translation — primarily EN ↔ ES, EN ↔ CA | low–medium | high |

### Audio workloads (ASR tier)

| # | Use case | Latency tolerance | Quality bar |
|---|----------|------------------|-------------|
| F | Transcription of **EN, ES** audio → text in source language | medium (batch OK) | high (low WER, accent-robust) |
| G | Audio translation: **ES → EN** | medium | high |
| H | Transcript polishing — remove filler words / disfluencies, tighten conciseness | medium | medium (not critical) |

For workload G, two architectures are possible and the analysis must evaluate both:
- **(i) Single-model speech-to-text-translation** — Whisper-class model with the `translate` task does both steps end-to-end.
- **(ii) Two-stage ASR → LLM** — transcribe in the source language, then a tier-B LLM translates the transcript. More controllable, lets the LLM also do polishing in the same pass.

For workload H, the analysis should briefly assess whether dedicated disfluency-removal models (DisfluencyFixer, sequence-tagging models, HF disfluency classifiers) outperform doing it inside the LLM polishing pass. This is "nice to know" — the LLM-based approach is the working baseline and this is not a blocking decision.

**Note on workload context:** I am currently using Whisper Turbo for transcription. The analysis should explicitly tell me whether a better option exists for my EN/ES workload, not just list candidates.

**Explicitly out of scope:** code generation, code editing, code reasoning. Do **not** weight coding benchmarks (HumanEval, MBPP, SWE-bench, LiveCodeBench, BigCodeBench, etc.) in the quality composite.

---

## 4. Concurrency assumption

I typically run **2–3 models simultaneously**. CPU offload is acceptable thanks to 128 GB RAM. Plan budgets accordingly:

- **Slot 1 (always-hot, GPU-resident):** tier-A fast-lane model
- **Slot 2 (GPU-resident or partial offload):** tier-B/C/D model
- **Slot 3 (CPU + spillover):** occasional larger tier-C quality model for batch runs

---

## 5. Research methodology

Execute these steps in order. Show your work in the markdown report.

1. **Survey current SOTA open-weights models** as of the run date. Cover at least these families and note any newer entrants:
   Qwen, Llama, Mistral / Magistral / Ministral, Gemma, Phi, DeepSeek (V-series and non-coder variants only), Command (Cohere), Hermes / Nous, Yi, Mixtral, GLM, InternLM, Falcon, OLMo, plus any frontier instruct/reasoning models released in the last 90 days.
2. **Per model, capture:**
   - Parameter count, architecture (dense vs. MoE — for MoE record both total and active params)
   - License (note non-commercial / AUP restrictions)
   - Native context window
   - Recommended quantizations: GGUF Q4_K_M, Q5_K_M, Q6_K, Q8_0; AWQ; ExLlamaV2 EXL2; MLX (irrelevant here, Windows); FP8 / FP4 if Blackwell-supported
   - Release date
3. **Memory budget for this 16 GB GPU + 128 GB RAM box:**
   - VRAM cost at each common quantization
   - KV-cache cost at 8k / 32k / native context
   - Decision per model: *fully in VRAM* / *partial offload* / *CPU-only*
   - Show the math at least once in the report (e.g. "Q4_K_M of a 32B ≈ 19 GB → spills ~3 GB to RAM at 8k ctx")
4. **Speed expectations (tok/s)** on RTX 5060 Ti 16 GB. If 5060 Ti numbers are scarce, triangulate from RTX 4070 and 4060 Ti 16 GB published benchmarks. Mark estimates clearly.
5. **Quality signal for *my* workloads** (no coding):
   - Instruction following: **IFEval**, MT-Bench, Arena-Hard
   - Agentic / tool use: **BFCL v3+**, τ-bench, agent leaderboards (LangSmith, Galileo, HF agent leaderboard)
   - Summarization & writing: Arena writing/longform, recent human-eval reports, RewardBench
   - Translation: **FLORES-200** for EN↔ES; FLORES + community reports for **EN↔CA** specifically (Catalan coverage is uneven — flag it)
   - **Skip all coding benchmarks**
   Build a composite quality score weighted by my workload mix; document the weights.
6. **Build the Pareto frontier** for each tier:
   - Tier A (fast): x = tok/s, y = quality, drop dominated points
   - Tier B (balanced): same, with a minimum quality floor
   - Tier C (quality): same, with a minimum context-window floor
7. **Sanity-check** against community consensus: recent r/LocalLLaMA threads, Hugging Face top-trending of the last 30 days, llama.cpp recent issues for perf regressions/wins, latest Ollama default model bumps.

8. **Survey ASR (audio) models** for workloads F and G. Cover at least:
   - **Whisper family** — Large v3, Large v3 Turbo, Distil-Whisper, faster-whisper (CTranslate2 reimplementation), WhisperX
   - **NVIDIA NeMo family** — Canary-Qwen, Canary, Parakeet TDT
   - **IBM Granite Speech 3.3 8B** — supports EN/FR/DE/ES, accuracy-tier model
   - **Microsoft Phi-4-Multimodal** — top-tier ASR per HF leaderboard, multilingual
   - **Newer entrants** — Qwen3-ASR, Moonshine
   - **Speech-to-translation specialists** — Seamless M4T v2, anything else with built-in translate
9. **Per ASR model, capture:**
   - Parameters, VRAM at FP16 / INT8
   - RTFx (realtime factor) on consumer GPU — Whisper Large v3 baseline ≈ 10–20×, Turbo ≈ 60×, Parakeet ≈ 2000× (English)
   - **Languages supported** — explicitly check EN, ES coverage and quality
   - **Translation capability** — does it support `task=translate`? Which language pairs?
   - License
10. **Recommend the audio stack** by walking the decision tree:
    - For workload F (transcribe EN/ES): which model balances WER, RTFx, and VRAM footprint when running alongside an LLM in slot 1? **Compare explicitly against Whisper Turbo** (the current default) and answer: is there a strict upgrade?
    - For workload G (ES → EN): single-model (Whisper translate task) vs. two-stage (Whisper transcribe + tier-B LLM translate)? Recommend by quality, latency, and concurrency footprint.
    - For workload H (disfluency / filler removal): briefly compare specialized models (DisfluencyFixer-style sequence taggers) vs. doing it inside the tier-B LLM's polishing pass. State whether it's worth the extra component.

---

## 6. Deliverable 1 — Interactive artifact

Single-file React or HTML artifact with:

- **Scatter plot:** x = estimated tok/s on RTX 5060 Ti, y = composite quality score
- **Bubble size:** VRAM at recommended quantization
- **Color:** tier (A fast / B balanced / C quality)
- **Filter toggle:** "fits fully in 16 GB VRAM" vs. "allow CPU offload"
- **Hover card:** model name, params (total/active for MoE), quant, VRAM, ctx, license, primary use case, sources
- **Shortlist panel:** top 1–2 picks per tier with one-line rationale
- **Audio stack panel:** comparison cards for ASR models covering EN/IT/ES, with WER, RTFx, VRAM, translation capability flag, and a clear primary recommendation for both transcription (workload F) and audio translation (workload G)
- **Date stamp** prominent at top
- All data inline, **no external API calls**, no `localStorage`, works offline

---

## 7. Deliverable 2 — Markdown report (didactic)

Sections, in this order:

1. **Objective** — what an efficient frontier means here, why it's both hardware- and workload-specific.
2. **System & workloads** — restate the box and the workloads (text + audio).
3. **Methodology** — the steps in §5, written so a colleague could reproduce them.
4. **How to read the chart** — explain Pareto dominance, tiers, and quantization tradeoffs in plain language. Include one worked memory-budget example.
5. **Results** — shortlist per tier, plus a "dominated models" appendix with *why* each was dropped.
6. **Concurrency plan** — concrete 2-model and 3-model recipes (which goes on GPU, which spills to RAM, expected tok/s, when to use which combo).
7. **Audio (ASR) annex** — workload F and G analysis: shortlist of Whisper-class and competing models, decision tree for single-model vs. two-stage translation, VRAM footprint when running concurrently with the LLM stack.
8. **How to refresh this analysis** — checklist for the next quarterly run; flag what almost always changes (top model in each tier) vs. what rarely changes (memory math, framework defaults).
9. **Open questions / things I couldn't verify** — explicit uncertainty (e.g. Catalan translation quality, untested quantizations, missing Blackwell-specific numbers).

Tone: didactic, no hand-waving, show the memory math at least once.

---

## 8. Honesty rules

- **Date-stamp** every claim that depends on "current SOTA".
- If a benchmark number isn't published for a specific quantization, mark it "estimated" and show reasoning.
- Don't recommend a model you can't justify against an alternative on the frontier.
- Surface licenses — some "open" models forbid commercial use or carry AUP restrictions that matter for Macaya Bank work.
- If two models are within ~3% on the composite score, call it a tie rather than picking one.

---

## 9. Refresh checklist (for future runs)

- [ ] Update the date at the top.
- [ ] Re-run the §5.1 survey for any model family released or majorly updated in the last 90 days.
- [ ] Re-check Hugging Face trending and r/LocalLLaMA's last 30 days.
- [ ] Verify llama.cpp / Ollama / LM Studio default versions haven't shifted memory math.
- [ ] Re-pull benchmark snapshots (IFEval, BFCL, FLORES, Arena).
- [ ] Diff against the previous quarter's shortlist — note promotions, demotions, new entrants.
- [ ] Regenerate the artifact and report; archive the old ones with a date suffix.
