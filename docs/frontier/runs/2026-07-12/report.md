# Local LLM + ASR Efficient Frontier — Results

**Run date:** 2026-07-12
**Hardware:** RTX 5060 Ti 16 GB · Ryzen 7 7800X3D · 128 GB DDR5
**Workloads:** OpenClaw agentic (fast + deep lanes), transcript polishing, document processing, EN↔ES↔CA translation, **audio transcription EN/ES, audio translation ES → EN, transcript disfluency removal**. No coding.

---

## 0. Verdict

| role | incumbent | verdict | best alternative | gap | reason |
|------|-----------|---------|-------------------|-----|--------|
| `agentic_light` | `qwen35_4b` (qwen3.5-4b) | keep | Gemma 3 4B (Catalan niche) | — | No new 4B-class entrant Feb–Jul 2026; incumbent still the Tier A Pareto pick (~110 t/s, 262k ctx, Apache 2.0) |
| `agentic_heavy` | `gemma4_26b` (gemma4-26b-a4b-it) | keep | Qwen 3.6 35B-A3B | tie (≤3%) | Tie persists; GLM-5.2 and MiniMax M3 don't fit this box; Gemma 4 12B Unified is dominated by the 26B MoE |
| `audio_transcribe` | `whisper` (large-v3-turbo) | runtime_upgrade | same weights on faster-whisper (CT2) | — | ~2× RTFx, lower VRAM, same quality; engine change still pending since 2026-05-10 |
| `audio_translate` | `whisper_translate` (whisper-medium) | watch | two-stage: Turbo → Gemma 4 26B MoE | — | Two-stage stays the ES→EN default; keep the slot lazy-loaded as fallback |

**Diff vs. previous run (2026-05-10):** No frontier movement at the top — all four verdicts unchanged in substance; `agentic_light` moves `upgrade` → `keep` because the recommended swap to Qwen 3.5 4B was applied on 2026-05-10 itself, and the only genuinely new frontier-relevant release is Gemma 4 12B Unified (Jun 3), which does not displace any incumbent.

*Why this is the core artifact:* everything below exists to justify these six columns. If you read nothing else, this table plus the diff line is the run.

---

## 1. Objective

The "efficient frontier" of local LLMs is the set of models where, for a given level of quality, no other model is faster (or, for a given speed, no other model is more accurate). Everything off the frontier is **dominated** — a strictly better choice exists on at least one axis without giving up the other.

The frontier is **always hardware- and workload-specific**: a 70B that dominates on a 5090 falls off the 5060 Ti's frontier into CPU-offload territory, and a coding-specialist that wins SWE-bench is irrelevant here because coding carries 0% weight. This report identifies the frontier for *this* box and *these* workloads, as of July 2026.

**What changed since 2026-05-10:** the May–July release wave was loud but mostly off-frontier for this box. **GLM-5.2** (Jun 13, 744B MoE) got a dedicated local evaluation and a NO-GO — no quant fits 144 GB combined (see `docs/glm-5.2-evaluation.md`, issue #141). **MiniMax M3** (Jun 1, ~428B MoE) is borderline-loadable at 2-bit but coding-first and untested here. **Kimi K2.7 Code** (mid-June) is ~1T-class and coding-focused — out on both size and scope. The genuinely interesting release is **Gemma 4 12B Unified** (Jun 3): encoder-free multimodal with *native audio input*, 256k ctx, 140 languages, ~7 GB at Q4 — dominated by the 26B MoE for today's roles, but a watch item for a future combined ASR+LLM lane. No new Qwen shipped that runs locally; through July, Qwen 3.6 (April) remains the newest.

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

1. Diffed against the 2026-05-10 run: the survey focused on models released or majorly updated 2026-05-10 → 2026-07-12, on top of the standing landscape.
2. Surveyed via web search: open-weights release coverage (Qwen, Gemma, Mistral, GLM, MiniMax, Kimi, Granite, Ministral families), r/LocalLLaMA and aggregator consensus for 16 GB VRAM, the HF Open ASR Leaderboard's multilingual track, and llama.cpp release notes for runtime changes.
3. Reused the local first-party evidence where it exists: `docs/glm-5.2-evaluation.md` (GLM-5.2 fit math, NO-GO), njannasch.dev's 5060 Ti benchmarks for the Qwen 3.6 / Gemma 4 26B numbers carried from the previous run.
4. Computed VRAM with the standing rule of thumb **Q4_K_M ≈ 4.5 bits/param** plus KV-cache; new-model speed/quality figures without published 5060 Ti numbers are **estimated** and flagged in the data.
5. Applied the honesty rules: date-stamped claims, ≤3% composite = tie (the Gemma 4 26B / Qwen 3.6 35B-A3B tie stands), licenses surfaced (Gemma 4 12B Unified's reported Apache 2.0 flagged for verification).

---

## 4. How to read the chart

- **X axis** — estimated single-stream tokens/second on the 5060 Ti at the recommended quant.
- **Y axis** — composite quality score for *these* workloads (0–100, normalized).
- **Bubble size** — VRAM at recommended quant. **Color** — tier (A fast / B balanced / C quality).
- **Filled border** — on the Pareto frontier. **Hollow** — dominated.
- **Toggle** — show only models that fit fully in 16 GB VRAM, or include CPU-offload models.

### Worked memory example (so the math isn't a black box)

For **GLM-5.2 at its smallest useful quant** — this run's cautionary tale:

```
smallest quality-retaining quant (UD-IQ2_M) ≈ 239 GB on disk
RAM+VRAM needed to run ≈ ~245 GB
this box: 16 GB VRAM + 128 GB RAM ≈ 144 GB total
shortfall ≈ ~100 GB → does not load at any quality worth using
```

Compare **Gemma 4 26B MoE** (the incumbent): ~14 GB at native W4, 4B active params during decode → bandwidth pressure set by 4B, not 26B → 99 t/s fully GPU-resident. **MoE with a small active set remains the single biggest reason the frontier looks the way it does on consumer GPUs** — and "total parameters must physically fit somewhere" remains the hard gate that keeps the 400B+ wave off this box entirely.

---

## 5. Results — shortlist by tier

### Tier A — Fast lane (OpenClaw routing, classification, simple tool calls)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen 3.5 4B *(incumbent)* | 4B hybrid MoE | Q4_K_M | ~3 GB | ~110 | 65 | 262k | Apache 2.0 | yes |
| ☆ Gemma 3 4B | 4B dense | Q4_K_M | ~3 GB | ~100 | 60 | 128k | Gemma | yes |
| Phi-4 Mini | 3.8B dense | Q4_K_M | ~2.5 GB | ~120 | 49 | 16k | MIT | yes (speed end) |
| Granite 4.1 8B *(new check)* | 8B dense | Q4_K_M | ~5 GB | ~60 est | 64 est | 128k | Apache 2.0 | no |
| Llama 3.2 3B | 3B dense | Q4_K_M | ~2 GB | ~120 | 43 | 128k | Llama 3.2 | no |

The incumbent keeps the tier. Granite 4.1 8B (late Apr) headlines on tool-calling and code, but its wins are coding-weighted — on this stack's no-coding composite it lands below Qwen 3.5 4B at twice the footprint. Gemma 3 4B stays relevant purely as the Catalan-strongest small model.

### Tier B — Balanced (the workhorse) — **TIED, unchanged**

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★★ Gemma 4 26B MoE *(incumbent)* | 26B / 4B active | native W4 | ~14 GB | 99 | 83 | 256k | Gemma | yes |
| ★★ Qwen 3.6 35B-A3B | 35B / 3B active | Q4_K_M | ~13.5 GB | 98 | 84 | 262k | Apache 2.0 | yes |
| ☆ GPT-OSS 20B | 21B / 3.6B active | MXFP4 | ~12 GB | ~100 | 72 | 131k | Apache 2.0 | yes |
| Gemma 4 12B Unified *(new)* | 12B dense | Q4_K_M | ~7 GB | ~45 est | 76 est | 256k | Apache 2.0 (verify) | no |
| Ministral 3 14B *(new check)* | 14B dense | Q4_K_M | ~8.5 GB | ~35 est | 70 est | 256k | Apache 2.0 | no |
| Mistral Small 3.2 | ~22B dense | Q4_K_M | ~13 GB | ~30 | 74 | 128k | Apache 2.0 | no |

The tie at the top persists — nothing released May–July even threatens it. **Gemma 4 12B Unified** deserves its own sentence: released Jun 3, encoder-free multimodal (text, image, **native audio** straight into the backbone), 140 languages, benchmarks "nearing the 26B". On this chart it's dominated — the 26B MoE is both faster and better — but it's the first plausible building block for a future *single-model audio→polished-text lane*, so it goes on the watch list rather than the discard pile.

### Tier C — Quality (slow, CPU-offload, batch / non-interactive)

| model | params | quant | VRAM | tok/s | quality | ctx | license | on frontier |
|-------|--------|-------|------|-------|---------|-----|---------|-------------|
| ★ Qwen3 32B dense | 32B | Q4_K_M | ~19.5 GB (spill) | ~11 | 84 | 128k | Apache 2.0 | yes |
| Llama 3.3 70B | 70B | Q4_K_M | ~40 GB (offload) | ~4 | 82 | 128k | Llama 3.3 | no |
| Mistral Medium 3.5 | 128B dense | Q4_K_M | ~75 GB (offload) | ~2 | 86 | 256k | Modified MIT | no |

### Models considered and dropped this run

- **GLM-5.2 (744B / 40B active, Jun 13)** — first-party NO-GO: smallest useful quant needs ~245 GB combined; the box has ~144 GB. Right model, wrong size (`docs/glm-5.2-evaluation.md`, #141). Revisit if a GLM-5.2-Air/Flash (~80–120B) ships.
- **MiniMax M3 (~428B / ~23B active, Jun 1)** — 2-bit dynamic GGUF reportedly runs in ~138 GB, which technically squeaks under 144 GB, but: 2-bit quality, expert-offload on DDR5 (single-digit t/s expected), and a coding-first profile this stack doesn't weight. Not worth a slot; not chart-plotted because it isn't a realistic operating point.
- **Kimi K2.7 Code (mid-June)** — ~1T-class, coding-focused. Out on size and scope.
- **Mistral's July MoE ("fat but sparse")** — early access announced, weights not open yet. Watch next run.
- Standing drops carried from May: Qwen 3.6 27B dense, Gemma 3 27B, Qwen3.5-35B-A3B, Mistral Small 3.2, Llama 3.2 3B, Phi-4 Mini (for ES/CA), Qwen3 8B/9B class for the deep lane.

---

## 6. Concurrency plan

Unchanged from the previous run — the four recipes still describe the practical envelope:

1. **Two lanes (default):** Qwen 3.5 4B (GPU ~3 GB) + Gemma 4 26B MoE (GPU ~14 GB). Both near-peak; ~17 GB with graceful shared-memory spill.
2. **Qwen 3.6 stack (all-Apache):** Qwen 3.5 4B + Qwen 3.6 35B-A3B (~13.5 GB). Speed parity; license clarity. llama.cpp's May MTP merge adds a small single-stream tailwind here.
3. **Quality batch:** Qwen 3.5 4B + Qwen3 32B dense (~3.5 GB CPU spill, ~10 t/s) for overnight reprocessing.
4. **Three concurrent:** Qwen 3.5 4B + GPT-OSS 20B (GPU) + Gemma 3 4B (CPU, ~10 t/s on the 7800X3D) as a Catalan specialist.

Runtime note for this period: llama.cpp merged **Qwen 3.6 MTP speculative decoding** (May 2026) and CUDA kernel-fusion improvements (published single-stream gains up to ~24% on some paths). Neither changes a verdict; both slightly sweeten the Qwen 3.6 alternative and batch throughput generally.

---

## 7. Audio (ASR) annex — workloads F, G, H

### 7.1 The landscape in July 2026

Quiet quarter. The May run's three open threads — Qwen3-ASR per-language ES numbers, Parakeet TDT v3 ES quality, Granite Speech ES depth — all remain open: no new comparative Spanish WER landed on the public leaderboard cut between May and July. The Qwen3-ASR **technical report** (arXiv 2601.21337, published February) documents multilingual evals on Common Voice / FLEURS / MLS, but a head-to-head vs Whisper Turbo on ES still has to be assembled locally to be trusted.

### 7.2 ASR candidate comparison (EN + ES)

| Variant | Params | VRAM | RTFx (5060 Ti, est.) | EN | ES | Translates → EN? | Notes |
|---------|--------|------|----------------------|----|----|-------------------|-------|
| **Whisper Large v3 Turbo** (current) | 809M | ~1.6 GB | 60–80× | ✅ | ✅ | ❌ | The incumbent. Solid. |
| **★ faster-whisper Turbo** | 809M (CT2) | ~1.0 GB INT8 | 100–150× | ✅ | ✅ | ❌ | Same weights, ~2× faster. **Still the pending runtime upgrade.** |
| **Whisper Large v3** (faster-whisper) | 1.55B | ~2 GB | 30–50× | ✅✅ | ✅✅ | ✅ | Higher accuracy, supports translate. Workload-G fallback. |
| **Granite Speech 3.3 8B** | 8B | ~5 GB | 15–30× | ✅✅✅ | ✅✅ | ✅ (X↔EN) | Accuracy tier; only if Turbo errors start to matter. |
| **Parakeet TDT v3** | 1.1B | ~2 GB | 2000+× | ✅✅✅ | ✅ | ❌ | Speed king if ES holds — still unvalidated. |
| **Qwen3-ASR 1.7B** | 1.7B | ~1.5 GB | TBD | ✅ | ✅ | ❌ | Tech report out; comparative ES still thin. Watch. |

### 7.3 Workload F — transcribe EN/ES

**Verdict unchanged: runtime_upgrade.** Switch the runtime (faster-whisper, `compute_type="int8_float16"`), keep the model (Turbo). The change is manual engine work in `src/run_backend.py` + `src/backend_process.py` and has been pending since 2026-05-10 — it stays the single highest-value, lowest-risk item on this board. Granite (accuracy) and Parakeet v3 (speed) remain conditional alternatives behind local smoke tests.

### 7.4 Workload G — ES audio → English

**Verdict unchanged: two-stage default.** faster-whisper Turbo transcribes ES, Gemma 4 26B MoE translates + polishes + de-disfluences in one call (~15 GB total). Single-model faster-whisper Large v3 `task=translate` stays the fallback when the LLM slot is busy — which is exactly what the `whisper_translate` role slot (whisper-medium, lazy CPU) implements today, hence its `watch` verdict rather than retirement.

### 7.5 Workload H — disfluency / filler removal

**Verdict unchanged: folded into the LLM polishing pass.** Specialized disfluency models remain research-grade with sparse Spanish coverage; the tier-B LLM already does this work in the polish prompt (the canonical prompt lives in `frontier.json` → `disfluency_verdict.prompt`).

### 7.6 Concurrency footprint

Unchanged: Turbo INT8 (~1 GB) coexists with both two-lane recipes; the audio-batch recipe (Turbo + 26B MoE, ~15 GB) drops the fast lane during batches; CPU fallback runs Turbo at ~5–10× realtime on the 7800X3D.

### 7.7 Dropped

Carried from May: Canary-Qwen (EN-only), Parakeet v1/v2 (EN-only), Distil-Whisper (EN-only), Phi-4-Multimodal (footprint/tooling), Seamless M4T v2 (heavier, weaker tooling than two-stage), specialized disfluency models (worse ES coverage than the LLM pass). New this run: nothing entered, nothing exited.

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

Reading the progression: the May run's one actionable verdict (`agentic_light` upgrade) was executed same-day via `/swap-model` and the stack has been stable since. The `runtime_upgrade` on transcribe is now two runs old without being acted on — either schedule the faster-whisper engine work or consciously demote it to `watch`.

---

## 9. Open questions / uncertainty

- **Gemma 4 12B Unified license** — launch coverage says Apache 2.0, which would be a first for Gemma-branded weights. Verify on the model card before building anything on it.
- **Gemma 4 12B Unified native-audio quality** — if its built-in ASR approaches Whisper-Turbo quality for EN/ES, a single-model audio→polished-text lane becomes possible (~7 GB replacing Turbo+polish for casual use). Nobody has published ES WER for it yet.
- **Catalan on Qwen 3.6 35B-A3B** — still the only meaningful delta vs Gemma 4 26B; still needs a local smoke test before the all-Apache stack could take the deep lane.
- **MiniMax M3 at UD-Q2 on this box** — technically ~138 GB, i.e. loadable with ~6 GB headroom. Expected single-digit t/s and 2-bit quality loss make it academic, but it's the first 400B-class model that isn't strictly impossible here.
- **Qwen3-ASR / Parakeet v3 Spanish** — both still waiting on comparative per-language ES WER; unchanged from May.
- **faster-whisper INT8 on accented ES** — validate before relying on it (unchanged from May).
- **Mistral's July "fat but sparse" MoE** — in early access; if the open weights land at an 80–120B/small-active shape, it's a genuine deep-lane candidate for the next run.

---

## 10. Current decisions (live, edited by `/swap-model`)

The decisions below mirror `config/models.yaml` → `roles:` at the time
this section was last updated. `/swap-model` rewrites both this section
and the yaml together, so the two stay in sync.

| Role | Model | Decided | Why |
|---|---|---|---|
| **agentic_light** | `qwen35_4b` (qwen3.5-4b) | 2026-05-10 | Upgraded from gemma4_e4b via `/swap-model`. Tier A top pick — hybrid Gated DeltaNet + sparse MoE on a 4B base, Q4_K_M ~3 GB, 262k native ctx, 201 languages, Apache 2.0. gemma4_e4b retained in `enabled:` for ad-hoc fallback. |
| **agentic_heavy** | `gemma4_26b` (gemma4-26b-a4b-it) | 2026-05-10 | Tier B top pick. 99 t/s, 256k ctx, strong multilingual including Catalan. Tied with Qwen 3.6 35B-A3B (Apache 2.0) — Gemma stays default on Catalan track record. |
| **audio_transcribe** | `whisper` (whisper-large-v3-turbo) | 2026-05-10 | Runtime upgrade to faster-whisper Turbo is the strict-frontier pick (~2× RTFx, lower VRAM, same quality). **Engine code change pending** — manual work in `src/run_backend.py` + `src/backend_process.py`. Until then, stay on whisper.cpp Turbo. |
| **audio_translate** | `whisper_translate` (whisper-medium, lazy CPU) | 2026-05-10 | Strict frontier reading recommends `watch` — the two-stage path (Turbo → Gemma 4 26B) is the default, leaving this slot as a fallback only. Keep defined and lazy-loaded; no active maintenance. |

---

*Generated by the `/frontier-refresh` skill (`.claude/skills/frontier-refresh/SKILL.md`), which owns the research brief and this report's output contract. This is the July 2026 snapshot.*

---

## Addendum (2026-07-12, post-run) — §0/§7.3 `runtime_upgrade` disproven locally

The faster-whisper (CTranslate2) runtime swap this run carried as `runtime_upgrade` for `audio_transcribe` was built and A/B-measured the same day (#274) and **disproven**: measured speedup **1.0×** (aggregate RTFx 33.4 vs 33.6 int8_float16, 32.6 vs 33.3 fp16, over 556 s of real dictation audio — whisper.cpp v1.8.6 cuBLAS already runs turbo ~33× real-time on this GPU), WER parity-to-worse, and the leading "Claude Code" wake phrase dropped **0/2 vs 2/2** across an 18-attempt decode sweep. Full method and per-clip numbers: [#274 closing comment](https://github.com/ferraroroberto/local-llm-hub/issues/274#issuecomment-4949098008).

Effective verdict for the next run: `watch`, per the local-findings override — see `docs/frontier/local-findings.md` (#277), which now carries this finding and its re-open trigger deterministically. §0 and §8 above are left as written (history is never rewritten).
