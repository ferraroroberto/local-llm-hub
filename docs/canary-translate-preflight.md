# Canary-1b-v2 speech translation: `audio_translate` pre-flight

NVIDIA's Canary-1b-v2 translates speech directly (Spanish audio in, English text out) on the Mac Mini's Apple Neural Engine through [FluidAudio](https://github.com/FluidInference/FluidAudio) 0.18. This page records how it compares with the two ways the hub translates Spanish audio today. It is a measurement and a recommendation: `config/models.yaml` is untouched, and swapping a role is `/swap-model`'s job.

## Verdict

**Swap candidate: yes, against `whisper_translate`; no, against the two-stage default.**

- Against the role's current model (`whisper_translate`, whisper-medium on CPU) Canary is about 7x faster, never answered in the wrong language (`whisper_translate` returned Spanish on 2 of 16 clips), and scores about 4 BLEU higher. The score gap is inside the noise of 16 clips; the speed and reliability gaps are not.
- Against the two-stage path (whisper transcribes, `gemma4_26b` translates and polishes) Canary scores lower (25.4 vs 32.6 BLEU, a gap that is probably real but not firmly established at this sample size), and the two-stage path is fast once its models are loaded. Canary is not a quality upgrade over it. It is the cheaper path: no tower GPU memory and about 2.2 GB on the Mac.
- A swap needs more than a `model_id` edit. Nothing in the hub can serve Canary yet; see [What a swap needs](#what-a-swap-needs).
- Spontaneous dictation is not covered. See [Open questions](#open-questions).

## Method

Run `python scripts/canary_preflight.py` (`prepare`, `hub`, `report`; its docstring has the exact calls).

- **Audio:** 16 Spanish clips from the public FLEURS test set (`es_419`, CC-BY-4.0): 12 of at most 15 s and 4 longer (16.6 to 30.3 s), picked with a fixed seed, one speaker per sentence. Canary's decode window is 15 s, so the long clips exercise its VAD pause-splitting path.
- **Reference:** FLEURS' own English text for the same sentence id. One human reference per clip.
- **Paths:**
  1. Canary-1b-v2, `assets/deploy/mac/parakeet-worker/Sources/CanaryWorker/CanaryWorker.swift`, int4 on the ANE, Spanish to English, run in a scratch build beside the live Parakeet worker. Latency is the second of two passes over the clips.
  2. `whisper_translate` through the hub's `POST /v1/audio/translations`, unmodified.
  3. Two-stage: `whisper` with `language=es`, then `gemma4_26b` with a one-sentence translate-and-polish prompt, thinking off, temperature 0.
- **Score:** corpus BLEU with 13a tokenisation (close to sacreBLEU's default, not byte-identical) and a unigram F1 per clip. Intervals are a 2,000-sample paired bootstrap over clips.

## Results

| Path | BLEU (all 16) | BLEU short / long | Median latency | Median RTFx | Failures |
| --- | --- | --- | --- | --- | --- |
| Canary-1b-v2 (Mac ANE) | 25.4 | 18.3 / 33.8 | 1.2 s | 8.3 | 0 |
| `whisper_translate` (CPU) | 21.5 | 17.7 / 26.3 | 8.7 s | 1.3 | 0 |
| Two-stage (`whisper` + `gemma4_26b`) | 32.6 | 26.8 / 39.7 | 3.0 s | 3.7 | 0 |

Paired differences in BLEU, with 95% intervals:

| Comparison | Difference | Interval |
| --- | --- | --- |
| Canary minus `whisper_translate` | +3.9 | -2.1 to +9.9 |
| Two-stage minus Canary | +7.1 | -1.0 to +14.6 |
| Two-stage minus `whisper_translate` | +11.0 | +5.9 to +16.4 |

Only the last gap excludes zero. With the two clips where `whisper_translate` answered in Spanish removed, the scores are Canary 26.5, `whisper_translate` 23.0, two-stage 31.7.

### Latency and footprint

- **Canary latency** is the warm figure on a Mac Mini M4 that was also serving live Parakeet traffic: about 1 to 1.9 s for clips up to 22 s, and 3.7 to 4.7 s for the two 30 s clips.
- **Canary memory:** 0.6 GB resident after load, 2.19 GB at the peak across all clips. Loading took about 25 s. The Canary and Silero VAD models add to FluidAudio's shared model cache on the Mac.
- **`whisper_translate` latency** includes a CPU-only whisper-medium on the satellite, so its figure moves with that machine's load.
- **Two-stage latency** is warm GPU for both models. `gemma4_26b` is on-demand: a cold start adds tens of seconds, and about 13.4 GB of tower VRAM while loaded.
- **Thinking matters for the two-stage path.** With the stage-2 model's thinking left on, the same 16 clips took a median 32 s each, one took 140 s, and capped replies came back as the model's reasoning instead of a translation (3.5 BLEU). `scripts/canary_preflight.py hub` turns thinking off unless given `--think`.

### What the outputs look like

| Reference | Canary | `whisper_translate` | Two-stage |
| --- | --- | --- | --- |
| Parisians have a reputation for being egocentric, rude and arrogant. | Parisians are reputed to be self-centered, rude, and cunning. | *(Spanish: "Los parisinos tienen reputación de egocéntricos...")* | Parisians have a reputation for being egocentric, rude, and conceited. |
| The city is in stark contrast to the rest of the country's cities... | The city is very similar to the rest of the country... | The city contrasts a lot with the rest of the country... | The city contrasts much with the rest of the country... |
| Rip currents are the returning flow from waves breaking off the beach... | Returning currents are the flow that retreats from the waves... | The return currents are the flow that recedes the waves... | Rip currents are the flow that retreats from the waves breaking on the shore... |

## Findings

- **Canary's failure modes are real but few.** One clip's meaning flipped ("contrasta mucho" became "very similar"). One 22 s clip lost the middle of a sentence ("Additionally, he participated in. The most recent examples..."). Two of the four long clips show stray punctuation where the VAD joined pieces ("ground., Glenn").
- **`whisper_translate` fails differently.** It ignored the translate task on 2 of 16 clips and returned the Spanish transcript, with a double space and broken word ("descort eses") in places. A role that sometimes answers in the wrong language is a worse default than one with an occasional mistranslation.
- **Some misses come from the data, not the paths.** FLEURS' Spanish sentence says "fotografías" and "80.000" where its English reference says "broadsides" and "800,000". All three paths follow the Spanish, so those lower every score equally.
- **The two-stage path scores best largely because it paraphrases toward natural English** ("apex predator", "rip currents"), which a BLEU reference rewards. That is a stylistic gain; this test cannot say it is a fidelity gain.
- **Canary's output on read speech is cased, punctuated English.** The only cleanup it visibly needs is the stray punctuation at VAD joins.

## Open questions

- **Is the polish pass still needed on top of Canary?** FLEURS is read, fluent speech with no fillers or false starts, so this test cannot show either way. For spontaneous dictation, treat the polish pass as still required until Canary runs on dictation-style audio. The owner chose public clips only for this run, so that comparison is a separate step on audio the owner approves.
- **Sample size.** 16 clips cannot settle a 4-point BLEU gap. A run over the full ES test set (about 350 sentences) would.
- **Whether two-stage's quality edge matters for the role.** The role serves short Spanish dictation to English text. If that use tolerates `whisper_translate`-level quality today, Canary's speed and reliability win. If it needs the two-stage quality, Canary is not the answer.

## What a swap needs

- **A hub-side server for Canary.** `assets/deploy/mac/parakeet-worker/Sources/CanaryWorker/CanaryWorker.swift` speaks the same line protocol as the Parakeet worker, but no Python wrapper, backend row or `POST /v1/audio/translations` route handler reaches it. `src/parakeet_server.py` is the pattern to follow. Today the translate route rewrites to a whisper-server call, so Canary needs its own branch there.
- **A host decision.** The Mac has 16 GB shared with Parakeet and the other models placed there; Canary's 2.2 GB peak is small but real.
- **The package bump.** `Package.swift` now asks for FluidAudio 0.18.0 or later, for both targets. The scratch build compiled `ParakeetWorker` against it without changes, but the live Parakeet worker has not been run on 0.18, so rebuild and check `audio_transcribe` before relying on it. Plain `swift build -c release` now builds both binaries; `src/parakeet_server.py` only reads the Parakeet one.
