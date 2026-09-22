# TypeSafe Jev call-site survey (issue #614)

Fleet-wide survey of decision-shaped LLM call sites — classification, yes/no gating, choice-among-options, or rubric scoring, as opposed to open-ended generation — checked against whether they could be routed to TypeSafe's Jev decision model via local-llm-hub#611's `POST /v1/systemone` route. Jev's three question types: **Noul** (yes/no → probability), **Choice** (one of ≤255 options → choice + per-option probabilities + confidence), **Score** (ordered rubric levels → score + per-level probabilities + confidence).

content-management#285's body asserted this scan had already happened ("Stage A was picked as the pilot from a fleet-wide scan of every decision-shaped LLM call site... most other candidate sites were ruled out by privacy or by the model's documented limits") without recording the results anywhere. This doc is that record, redone from scratch across all 44 of the user's active repos (`gh repo list ferraroroberto`), each surveyed on disk under `E:/automation/<repo>`.

The privacy read in every row is about the **content itself** leaving the machine to a US-hosted third-party cloud vendor — not about hub routing, which is already the fleet's standard path for LLM access ([[access-model-loopback-tailscale-only]]).

## Repos with no LLM usage at all

`algo-trading`, `automation` (docs mention `openai-whisper` only, no code calls it), `closed-company-accounting`, `email-archiver`, `facilitation-shuffle`, `family-accounting`, `github-copilot-usage`, `gitlab-to-github-migrator`, `illustration-color-edit`, `mathgamesforkids`, `mcp-personal-onedrive` (one incidental docstring mention of "Anthropic" in an OAuth RFC comment), `notion-automation-files` (mentions only in personal data files, not code), `pvgis`, `social-media-analytics`, `team-os`, `tools`, `vibe-coding-workshop`, `website`, `website-analytics`.

## Repos with LLM usage but no decision-shaped call sites

`app-launcher` (drive-mode reply + failure-tail summarizers, both open-ended prose), `voice-transcriber` (filler-word transcript cleanup), `mass-html-to-markdown` (feature-text summarization), `photo-ocr` (OCR transcription — high privacy sensitivity as a call site, but not decision-shaped), `oracle-to-gcp` (SQL translation + free-text optimization suggestions), `home-automation/app/webapp/routers/pc_fleet.py` (hub used as a non-LLM status API), `minecraft-bedrock-bot/src/errors.js` (deterministic string parsing, no LLM call), `life-os` (a full agentic `claude -p` recap session, not a discrete decision call), `project-scaffolding` (only calls the hub's `/admin/api/version` for build-identity checks).

`suna` is vendored third-party code (Kortix AI's open-source agent, Apache-2.0) — out of scope, not surveyed further.

## Decision-shaped call sites

| repo | file:line | decision | Jev type | privacy read | verdict |
|---|---|---|---|---|---|
| content-management | `engagement/classify/llm_fallback.py:190` `classify_one` | AI-generated vs. human social-media comment, calibrated 0–1 confidence | Noul (near-literal match) | low — public social-media comment text + already-public commenter name/URL | **candidate** — best structural match in the whole survey |
| fleet-config | `hooks/codex_attention.py:67` `classify_stop` | Codex session final response: `awaiting_input` / `finished` / `uncertain` + confidence | Choice (3) | low-moderate — ≤70-word secret-redacted excerpt of the agent's own message | **candidate** — tight scope, already emits `{verdict, confidence}`, low volume, fail-open |
| content-management | `newsletter/triage/score.py:302` `score_content` (Stage B) | topic (1 of 3), relevance 0–5, "star" 0–5, news/promo/paywall flags | mixed: Choice + Score + Noul | low — public article title/author/excerpt | **candidate** — sibling of the already-piloted Stage A (#285), decomposes cleanly |
| content-management | `newsletter/classifier.py:38` | article → 1 of 3 fixed archive topics | Choice | low — public article title + snippet | **candidate** |
| home-automation | `scripts/voice_bench/schema.py` + `hub.py` | spoken utterance → 1 of 11 home-automation intents + typed slots | Choice (11, closed enum) | low — HVAC/plug/alarm/media commands; disarm code kept out-of-band | **candidate**, with caveat — confirmed to be a benchmark harness (issue #234); needs-more-info on whether Tier-2 routing is live in production |
| task-os | `src/archive_rank.py:319` `rank()` | which archive folder (of ≤6 candidates, incl. "none fit") an email belongs in + confidence | Choice | **sensitive** — real email subject/sender/recipients + 400-char body preview | **candidate**, flag privacy — best structural fit in the survey, but real email content |
| whatsapp-radar | `src/analysis/classifier.py:356` `HubClassifier.classify_traced` | `action_required` (bool) + `priority` (low/med/high) per message, bundled with free text | Noul + Score, bundled | **sensitive** — real personal/family WhatsApp/Gmail text, incl. a children-hint block (#215) | needs more info — decomposition is straightforward; family-content sensitivity should gate go/no-go first |
| home-automation | `src/alarm_scene.py:279` `_call_vision`/`_parse_verdict` | camera-trip verdict: `real` / `false` / `uncertain` | Choice (3) | **high** — home-interior security camera imagery, already sent to a cloud vision backend today | candidate on shape, but flag explicitly — adding a second cloud vendor to an already-sensitive image path deserves its own go/no-go |
| arboldelossuenos | `src/python/llm/text_analysis.py:67` | ~11 compliance checks on a child's Christmas-wish letter + overall pass/fail | Noul (bundle) | **sensitive** — minor's first name + age (prompt strips address/school/illness) | needs more info — clean shape, charity handles children's data; confirm data-handling posture first |
| arboldelossuenos | `src/python/llm/color_analysis.py:23`, `drawing_analysis.py:28` | is the drawing in color / is a clear hand-drawing present | Noul | **sensitive** — image of a child's submitted drawing | needs more info — same minors'-data caution |
| copilot-studio-transcripts | `src/classifier.py:64` `classify_turn` | bot-transcript turn → 1 of caller-supplied categories + confidence | Choice | needs more info — depends on the deployed bot's domain (could be a support bot with customer PII) | needs more info |
| copilot-studio-transcripts | `src/judge.py:154` `judge_one` | conversation vs. agent instructions: correct / partial / incorrect | Score (3-level, cleanest Score match found) | needs more info — same transcript caveat | needs more info |
| copilot-studio-transcripts | `src/evaluator.py:124` `compare_meaning` | continuous 0–1 meaning-similarity, thresholded | doesn't cleanly map | needs more info | needs more info — continuous score, not a discrete rubric; would need bucketing to fit Score |
| pdf-to-markdown | `src/vertexai_backend.py:47` (schema) / `src/hub_gemini_backend.py` equivalent | refinement-loop verdict `NEEDS ANOTHER PASS` / `CLEAN` + per-correction severity/category, riding inside a bulk document-rewrite response | doesn't cleanly map | can't tell — arbitrary uploaded PDF content | ruled out today — decision fields are inseparable from the dominant free-text `corrected_markdown` payload; would need a prompt/pipeline restructure |
| minecraft-bedrock-bot | `src/model-reply.js:26` + `src/model-client.js:58` | agent turn status `continue`/`done`/`give_up`, bundled with free-text reasoning + game actions | doesn't cleanly map | low — game-world state only | ruled out today — full agentic tool-use turn, not an isolable decision call |
| accounting-quarterly | `src/invoice_ocr.py:266` | expense category (1 of 14), one field inside a 25+-field document extraction | doesn't cleanly map | **sensitive** — real invoices, Spanish tax IDs, addresses, amounts | ruled out — inseparable from a large multi-field extraction call, and privacy-heavy regardless |
| externalrisk | `src/python/llm/gemini_criteria.py:126`, `:162` | does a bank-risk document confirm a client holds a competitor product / express intent to move it | Noul (clean shape) | **sensitive** — live client financial-risk data, production bank pipeline | **ruled out** — clean shape but real client financial data; no third-party cloud vendor without compliance sign-off first |
| task-os | `src/ai/triage.py:251` `generate_suggestions` | priority + parent project + person + free-text reason, one bundled call | doesn't cleanly map | needs more info — personal task content | needs more info — would need decomposing into separate Choice/Score calls first |
| task-os | `src/enrich.py:265` | spoken sentence → task title/description + date phrases | doesn't map | needs more info | ruled out — open-ended extraction |
| whatsapp-radar | `src/analysis/gmail_survey.py:152`, `summarize.py:100` | invents a new triage taxonomy / summarizes messages | doesn't map | sensitive — real mailbox samples | ruled out — open-ended generation |
| grocery-shopping-automation | `src/inventory_extract.py:228`, `src/voice_command.py:151` | batched variable-cardinality item/quantity extraction against a household inventory | doesn't cleanly map | low | ruled out — shape mismatch, and the LLM is deliberately kept out of the one true decision (which operation to apply) |
| inspiration-system | `src/enrichment.py:136` | `tone` (7) / `abstraction_level` (3) fields inside an open-ended metaphor/theme-generation call | doesn't cleanly map | low | ruled out — minor fields riding on a dominant free-text call |
| oracle-to-gcp | `unit_test/query_cost_audit.py`, `query_optimization_loop.py` | free-text SQL-rewrite suggestions | doesn't map | needs more info | ruled out — open-ended generation |
| local-llm-hub | `src/dictionary_miner.py:198` `llm_cluster_replacements` | open-set clustering of mis-heard STT terms | doesn't map | needs more info — dictation content | ruled out — open-cardinality clustering, not a fixed decision |

## Top picks for a follow-up Jev pilot

Ranked by shape fit × content sensitivity, independent of #285 (already piloted):

1. **`content-management/engagement/classify/llm_fallback.py:190`** — literal Noul match (binary + calibrated confidence) over public social-media content. Cleanest candidate found.
2. **`content-management/newsletter/triage/score.py:302` (Stage B)** — direct sibling of the already-piloted Stage A, same low-sensitivity criteria brief, decomposes cleanly into Choice+Score+Noul.
3. **`fleet-config/hooks/codex_attention.py:67`** — already emits exactly `{verdict, confidence}`, tiny call volume, fail-open by design, low-sensitivity content.

**Flagged for a privacy conversation before piloting, independent of Jev's merits** — `task-os/archive_rank.py` (real email content), `whatsapp-radar/classifier.py` (family/personal messages), `home-automation/alarm_scene.py` (security-camera imagery), `arboldelossuenos` (minors' data). All have clean decision shapes; none should go to a third-party cloud vendor without an explicit go/no-go first.

**Ruled out on privacy alone regardless of shape** — `externalrisk` (live client financial-risk data in a production bank pipeline) and `accounting-quarterly` (real invoices with Spanish tax IDs).

## Local/open-source alternative spike

Investigated whether a local or open-weight technique can give Jev's shape — typed output *plus calibrated confidence* — as an option for the sites flagged sensitive above.

**This hub's own infrastructure isn't there yet.** `grep -ri "logprobs\|log_prob\|top_logprobs" src/` returns zero matches — `ChatCompletionRequest` (`src/server.py:717`) has no `logprobs`/`top_logprobs` field, and since Pydantic silently drops undeclared fields, a caller sending `logprobs: true` through the hub today gets nothing, even though the underlying llama-server backends support it natively on their own ports. `response_format` (JSON-schema-constrained output) is forwarded 1:1 to llama-server (`_build_openai_extra`, `src/server.py:748`) and works, subject to the known `<think>`-tag/grammar-stack limitation already documented in `README.md:2253`. So: typed output works via passthrough; there's no logprobs path to build a confidence signal on top of it.

**The underlying technique — forced single-token choice + logprobs + temperature/Platt scaling — is real and well-documented**, with equally well-documented failure modes: raw logprobs are systematically overconfident, RLHF training worsens this, and the aggregation method for multi-token answers matters more than logprob-list truncation depth (swings of 0.15–0.29 ECE observed in the literature depending on aggregation choice alone). A fitted (not default) temperature/Platt scale on labeled data is what turns "rescaled" into "calibrated."

**Mature general tooling exists** — [LM-Polygraph](https://github.com/IINemo/lm-polygraph) (EMNLP 2023, 40+ methods) and [UQLM](https://arxiv.org/html/2507.06196v2) — but as research toolkits for uncertainty quantification, not packaged typed-decision products; the `Choice`/`Score`/`Noul` wrapper and abstention layer would need to be built on top.

**A purpose-built candidate exists, but is too new to trust**: [zhangcy122/OpenJev](https://github.com/zhangcy122/OpenJev) (created 2026-09-20, 16 stars) implements exactly this shape — forced single-token answer, real logprobs, temperature-scaled softmax — with source-verified `client.py`/`calibrator.py`. Gaps: single-author, 2 days old, unaudited external benchmark claims, a hardcoded (not fit) temperature constant, and its Ollama fallback path silently substitutes self-reported verbalized confidence for real logprobs — the exact anti-pattern this spike was checking for. [JonathanHHenson/open-cricket](https://github.com/JonathanHHenson/open-cricket) is a more honest sibling (same primitives, real logprobs) but makes no calibration claim at all.

**Verdict:** nothing turnkey and independently verified does what Jev claims today. Building this locally would mean adding a logprobs surface to the hub's `/v1/chat/completions` path (or a new single-token-choice endpoint), then layering a temperature/Platt scale fit on labeled decisions — using OpenJev's source and LM-Polygraph's methods as reference, not as a dependency to adopt as-is.

## Recommendation

- **Pursue #285's Stage B and the `llm_fallback.py`/`codex_attention.py` sites as follow-up Jev pilots** — clean shapes, low-sensitivity content, direct reuse of the #611 route. Each should be its own small pilot issue rather than folding into this one.
- **Do not pilot the flagged-sensitive sites** (email content, family messages, security-camera images, minors' data, bank financial data) without a separate, explicit privacy/compliance decision first — that decision is orthogonal to Jev's own merits and shouldn't be made implicitly by routing a pilot.
- **A local-alternative spike is worth its own issue, but not urgently** — nothing available today is both mature and calibrated. If a flagged-sensitive site's owner later wants typed-decision-with-confidence without third-party egress, `OpenJev`'s source plus LM-Polygraph's calibration methods are the concrete starting point, layered on a hub logprobs feature that doesn't exist yet.
