# Improving a Self-Hosted AI Voice-Calling Assistant — Pipecat / Telnyx / Groq Whisper / Gemini / ElevenLabs

**Scope:** Pipecat (Python) pipeline, Telnyx Call Control, Groq `whisper-large-v3-turbo` STT, Google Gemini flash-lite-class LLM with a failover chain, ElevenLabs `eleven_flash_v2_5` (premade voice), SQLite temporal memory (facts with validity windows, episodes, commitments), rule-based in-call controller, per-objective playbooks. Outbound business calls (booking, information gathering), first live calls completed, single developer, self-hosted, late 2026.
**Effort key:** S = hours–2 days · M = 3 days–2 weeks · L = >2 weeks / new infrastructure. Costs are list-price / secondary-source figures checked Oct 2026 — verify in vendor portals before budgeting; several were in flux.
**Confidence:** Pipecat framework claims are from Pipecat's own current docs (fetched 2026-10-31, docs updated 2026-10-19/29). Provider latency/price figures are a mix of vendor claims and third-party benchmarks and are flagged as such — the single most repeated finding across sources is *measure on your own phone chain*.

## Summary — the five things that matter most

1. **You are probably leaving Pipecat 1.x turn machinery on the table.** Current Pipecat defaults already include Smart Turn v3 as the turn-stop strategy, and the framework ships — commonly missed — turn-completeness strategies, `MinWords` anti-backchannel interruption, Krisp VIVA interruption prediction, a ready-made Backchannel package, context summarization (off by default), latency/turn observers, speculative (eager-EOT) generation, and a full evals framework. Auditing your pipeline against current docs is the highest impact-per-effort move available.
2. **Your STT is architecturally the latency bottleneck, not your LLM or TTS.** Groq Whisper is *batch*, not streaming: no partials, no eager end-of-turn, and the final transcript only starts after the utterance is packaged and sent. A streaming STT built for telephony/turn-detection (Deepgram Nova-3, or Flux which folds end-of-turn into the model) is the one provider swap most likely to cut both latency and entity errors on 8 kHz audio — at ~7–10× the STT unit cost, which is still only ~$0.005/min.
3. **A Gemini-only failover chain does not protect you from a Gemini outage.** Independent 30-day probing (Sep–Oct 2026) found flash-lite's *median* TTFT competitive but its p95 at 5.6–6.1 s with 8–10% timeouts/5xx (free-tier key, so treat as directional). In-call you cannot afford serial 2 s→4 s→8 s retries: you need a *deadline-aware, cross-provider* fallback (e.g. Groq/Cerebras-hosted open models via LiteLLM or an in-process circuit breaker) that fires *before first token*, plus a spoken filler while it fires.
4. **Do not adopt Graphiti/Zep/Letta yet — steal their patterns instead.** Your SQLite store already implements Graphiti's core idea (validity windows, invalidation-not-overwrite). The recall problem is upstream: rule-based extraction. Replace it with LLM extraction using Gemini structured output + a Pydantic validate-and-repair loop, add `sqlite-vec` embeddings in the *same database file* for semantic dedup/repetition, and copy mem0's ADD/UPDATE/DELETE/NOOP merge decision. That is days of work; Graphiti is a graph database + a second extraction system to operate.
5. **Test like the serious teams do, in layers you mostly already own:** Pipecat Evals (scripted + simulated, text mode in CI, audio mode pre-release) → scripted *real Telnyx calls* to your own numbers → the same task-completion/latency/interruption scorecard applied to production recordings. Gate launches on task completion, never on WER.

---

## Ranked recommendations (impact ÷ effort, single developer)

| # | Recommendation | Section | Effort | Cost delta |
|---|---|---|---|---|
| 1 | Audit/upgrade to Pipecat 1.x turn stack: Smart Turn v3 default, VAD on the user aggregator, `MinWords` start strategy, turn-completeness stop strategy | §1, §8 | S | $0 |
| 2 | Single-owner closing state machine in your controller (kills double-goodbye) | §1 | S | $0 |
| 3 | Instrument before optimising: `UserBotLatencyObserver`/`TurnTrackingObserver`/`ServiceMetricsObserver` + per-stage p50/p95 dashboard + dual-channel recordings | §2, §5 | S | ~$0.002/min recording |
| 4 | Deadline-aware **cross-provider** LLM failover + circuit breaker + pre-first-token-only fallback; keep serial retries for post-call work only | §3 | S–M | Hedged/speculative turns only |
| 5 | LLM post-call extraction: Gemini structured output + Pydantic validate→repair + evidence spans + review queue | §4 | M | Fractions of a cent/call |
| 6 | Pre-synthesised filler + backchannel clips (Pipecat Backchannel package; cache greetings/confirmations/goodbyes) | §1, §2, §7 | S | One-off TTS chars |
| 7 | Pipecat Evals suite (scripted text in CI + audio/simulated pre-release) with task-completion gate | §5 | S–M | Local judge = $0 |
| 8 | Telnyx Premium AMD + greeting-ended voicemail-drop handling (+ iOS call-screening mode) | §6 | S | $0.0065/answered call (Premium) |
| 9 | TTS phone-chain tuning: `ulaw_8000`, `style=0`, speaker-boost off, short first sentence, judge voices through 8 kHz only | §7 | S | $0 |
| 10 | Context hygiene: Pipecat auto-summarisation on, Gemini prompt caching, playbook-scoped prompts/tools (Flows pattern) | §2, §3, §8 | S | Saves LLM cost |
| 11 | `sqlite-vec` semantic layer in the existing DB: paraphrase dedup, in-conversation repetition detection, hybrid (FTS5+vector) recall | §4 | M | $0 local embeddings |
| 12 | Commitment ledger as a state machine + due/overdue job + next-call surfacing + verification against tool logs, not transcripts | §4 | M | $0 |
| 13 | Benchmark streaming STT (Deepgram Nova-3 / Flux) against Groq Whisper on *your* recorded calls; adopt if entity-WER/latency wins | §2 | M | STT $0.0007 → ~$0.005/min |
| 14 | Speculative generation on eager EOT (Pipecat `EagerUserTurnStopStrategy` + SpeculationGate; requires an eager-EOT STT such as Flux) | §2 | M | +50–70% LLM calls on speculated turns |
| 15 | Real-telephony test layer: scripted Telnyx calls to own numbers, incl. voicemail/AMD, barge-in, silence, wrong-number personas | §5, §6 | M | Cents per test call |
| 16 | A/B Cartesia Sonic-3 / Deepgram Aura-2 (and Eleven v4 Turbo) through the phone chain | §7 | M | Similar or lower per-char |
| 17 | Dual-channel `record-from-answer` + Telnyx `transcription_start` as an independent transcript for extraction cross-checks and eval corpus | §6 | S | ~$0.002/min + transcription |
| — | **Defer:** Graphiti/Zep, Letta, speech-to-speech (Gemini Live) rewrite, native-SIP/LiveKit transport migration | §4, §6 | L | New infra to operate |

---

## Findings

### 1. Conversational naturalness — turn-taking, barge-in, end-of-turn, filler/backchannel, double-goodbye

**1a. Make Smart Turn v3 your (only) silence arbiter, and check where your VAD lives. [S, $0]**
What: Current Pipecat's default turn-stop is `TurnAnalyzerUserTurnStopStrategy` with `LocalSmartTurnAnalyzerV3` — an ~8M-parameter audio model (Whisper-Tiny-based) that judges prosody/phrasing over up to the last 8 s of the turn, runs on CPU in <100 ms (docs say as low as ~10–12 ms), and falls back to a ~3 s silence timeout if it keeps saying "incomplete." VAD is now configured **on the user context aggregator** (`LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)))`), not on the transport as in older Pipecat — code carried over from 0.0.x examples commonly misses this and ends up with stacked/duplicated endpointing timers, the classic "two defaults added up to 800 ms" failure.
Why here: endpointing is ~35% of a worked 800 ms turn budget (see §2) and the single biggest *deliberate* wait; a fixed silence timeout both cuts off people reading out dates/times and adds dead air after decisive speakers.
Sources: https://docs.pipecat.ai/pipecat/learn/speech-input · https://www.evalgent.com/blog/voice-agent-stack-guide

**1b. Add a turn-*completeness* stop strategy for content, not just prosody. [S, $0]**
What: `FilterIncompleteUserTurnStrategies` (the conversation LLM marks the turn complete/incomplete) or `ClassifierUserTurnStrategies` (a small classifier, for LLMs that follow markers poorly) run *after* Smart Turn; the turn ends only when both say "done."
Why here: your worst cut-offs will be semantic — "I'd like Tuesday… no wait, Thursday morning" — which an audio model cannot catch. With a flash-lite-class LLM the filter variant costs one small extra inference on ambiguous turns; the classifier variant avoids taxing your main model. Watch the latency trade-off and only enable it for objectives with long, number-heavy turns (booking) if it costs too much on short ones.
Source: https://docs.pipecat.ai/pipecat/learn/speech-input

**1c. Stop backchannels from barging in: `MinWords` + (optionally) Krisp VIVA interruption prediction. [S, $0–paid]**
What: Pipecat interruptions are on by default and an `InterruptionFrame` cancels LLM+TTS instantly — which is why a caller's "uh-huh / yeah / okay" kills your sentence. `MinWordsUserTurnStartStrategy(min_words=2–3, use_interim=True)` requires real content before a turn start interrupts; the Krisp VIVA Interruption Prediction start strategy goes further and classifies genuine interruptions vs acknowledgements from audio (Krisp licensing/cost not verified — check before adopting).
Why here: this is the standard fix and it is a parameter, not a project. Note the deliberate exception: protect genuinely uninterruptible lines (identity disclosure, recording consent) with `FirstSpeechUserMuteStrategy` / user-input muting instead — mute *discards* over-talk, so use it only where the caller must hear every word.
Also configure `empty_user_turn` on `LLMUserAggregatorParams`: when a cough/noise interrupts but nothing transcribes, the default behaviour runs the LLM once to recover ("sorry, say that again?" / resume); set it deliberately rather than inheriting it.
Sources: https://docs.pipecat.ai/pipecat/fundamentals/interruptions · https://docs.pipecat.ai/pipecat/learn/speech-input

**1d. Backchannels and fillers: pre-recorded clips, gated, never through the LLM. [S, one-off TTS cost]**
What: Three distinct behaviours, three mechanisms:
- *Listener backchannels* — install the community **Pipecat Backchannel** package (documented in Pipecat's own docs, needs pipecat ≥1.6): it plays cached "mhm / yeah / right" clips, in your own TTS voice, on pauses Smart Turn reads as *incomplete*. It takes no turn, writes **nothing to LLM context** (important — fillers in history teach the LLM to parrot them), costs no network round-trip at playback, and is tuned by `cooldown_s=2.5`, `fire_probability=0.8`, volume 0.6. Call `prewarm()` at startup so the first caller doesn't pay clip recording.
- *Latency-masking fillers* — for tool calls / slow turns only: play a pre-synthesised "let me check that for you" *in parallel* with the tool/LLM track (speculative-tool-calling pattern), cancelled when the real answer starts. Trigger on predicted wait (e.g. >600–800 ms), not unconditionally — a filler before a 200 ms answer sounds scripted and *adds* perceived latency. Rotate 3–4 variants and track repeat rate.
- *Tiny utterances generally* ("okay", "got it", "one moment") — serve from an audio-clip cache rather than live TTS: synthesisers measurably degrade/hallucinate on 1–2 word inputs, and a clip is ~0 ms.
Why here: your pipeline is half-duplex — nothing in the default cascade "listens" mid-utterance — so clips-on-incomplete-pauses is the only backchannel a cascaded Pipecat bot can do safely; pushing live TTS while the caller talks flips agent state to *speaking* and makes the caller's continuation look like an interruption.
Sources: https://docs.pipecat.ai/api-reference/server/extensions/pipecat-backchannel · https://www.evalgent.com/blog/backchanneling-proactivity-voice-agents · https://getstream.io/blog/speculative-tool-calling-voice/

**1e. Double-goodbye: make closing a controller state, not an LLM behaviour. [S, $0]**
What: Add an explicit `CLOSING` state to your rule-based controller (you already have the right owner — the LLM must never own hangup):
1. Objective complete (or caller goodbye intent) → controller sets `CLOSING`, injects *one* canned/scripted closing line (cached audio, §7), and arms hangup on **bot-stopped-speaking / playback-ended**, not on text generation.
2. In `CLOSING`, the LLM is no longer run for new turns; a caller interjection either (a) cancels the hangup and reopens the conversation *once* (Retell/Bolna pattern: interruption of the goodbye discards the pending hangup), or (b) is ignored if it's just "bye / thanks" acknowledgements — classify with a cheap keyword/intent check, not a full LLM turn.
3. Hangup itself is idempotent: one `EndFrame` → Telnyx serializer's `auto_hang_up=True` calls Telnyx hangup. Add safety nets independent of the LLM: user-silence hangup (Pipecat 1.x idle handling lives in `LLMUserAggregatorParams(user_idle_timeout=…)` → `on_user_turn_idle`; 5–10 s, 1–2 re-engagement prompts, then end) and a hard max-call-duration.
Why here: two polite reactive agents (or one agent + a polite human + an idle prompt) will keep agreeing to stop and never stop; every vendor that ships an `end_call` tool converged on "speak the closing, *then* hang up, cancellable by interruption" for exactly this reason. Your controller/playbook architecture makes this a small change with outsized polish value — double-goodbyes are the most *noticeable* flaw on otherwise successful calls.
Sources: https://docs.retellai.com/integrate-llm/integrate-function-calling · https://www.bolna.ai/docs/outbound/hangup-calls · https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets (auto_hang_up)

**1f. Tune VAD last, and on your own recordings.** Defaults (`confidence=0.7, start_secs=0.2, stop_secs=0.2, min_volume=0.6`) are sane; the failure modes are clipped short answers ("yes") at high `start_secs` and cough/keyboard false starts at low ones. Track false-start rate (VAD fired, no words) and missed-short-answer rate from production recordings before touching anything. [S]
Source: https://www.evalgent.com/blog/voice-agent-stack-guide

### 2. Reply latency end-to-end on telephony audio

**Where the time actually goes.** A worked 800 ms budget for a cascaded phone agent (Evalgent, list prices/assumptions labelled, Oct 2026): transport tax ~120 ms (mostly irreducible), **deciding the user is done ~280 ms (35%)**, STT flush + LLM TTFT + first speakable chunk + TTS first audio ~400 ms. Pipecat's own illustrative cascade totals 1.044 s: endpointing 200 ms, final transcript 125 ms, LLM first token 336 ms, TTS first audio 359 ms. Two consequences: (i) endpointing and TTS sentence-chunking beat LLM-shopping for wins; (ii) budgets only hold at p50 — a 300 ms LLM tail spike + 200 ms TTS spike = a 1.3 s turn, so **manage p95 voice-to-voice, measured from dual-channel recordings at the carrier edge**, not from server-side per-service metrics alone (Pipecat `enable_metrics` TTFB misses the phone legs).
Sources: https://www.evalgent.com/blog/voice-agent-stack-guide · https://www.marktechpost.com/2026/08/30/lowest-latency-inference-apis-for-voice-and-realtime-agents-a-time-to-first-token-ttft-first-benchmark/

**2a. The Groq Whisper problem — and the one swap worth benchmarking. [M; STT ~$0.0007/min → ~$0.005/min]**
Groq's Whisper is *very fast batch* inference (216–228× realtime, ~$0.04/hour of audio) but it is **not streaming**: no interim transcripts (so no `MinWords`-on-interim, no early intent, no speculation), and turn latency includes packaging the whole utterance + a round trip after end-of-speech. Purpose-built streaming STT also beats it where it hurts you: one 2026 comparison puts Deepgram Nova-3 telephony WER at ~9.4% vs Whisper ~12.8% on phone audio (directional, vendor-adjacent figures — verify on your calls). Benchmark, in order:
- **Deepgram Nova-3 streaming** — sub-300 ms streaming, interim + final, keyword/entity boosting for names, dates, business-specific terms. ~$0.0048/min promo / $0.0077 regular.
- **Deepgram Flux** — Nova-class STT with turn detection *inside* the model: `eot_threshold` (default 0.7), `eager_eot_threshold` emitting `EagerEndOfTurn` typically 150–250 ms before final EOT, ~260 ms p50 EOT detection (vendor). This is the variant that unlocks §2b.
- Keep Groq Whisper as the **offline** transcriber for recordings/extraction cross-checks, where batch is a virtue (cheap, fast, independent second opinion).
Method: run both over 30–50 of your real recorded calls; score *entity* error rate (names/dates/times/numbers) and end-of-speech→final-transcript latency, not headline WER.
Sources: https://dev.to/pietrus914/speech-to-text-api-comparison-whisper-api-options-in-2026-400h · https://llmbook.icsgen-ai.org/part-8-conversational-ai-with-llms/module-39-voice-realtime-multimodal/section-39.6.html

**2b. Speculative generation on eager end-of-turn. [M; +50–70% LLM calls on speculated turns — vendor figure]**
Pipecat ≥1.9 (per current reference docs) ships `EagerUserTurnStopStrategy` + a `SpeculationGate`: the LLM starts on a *provisional* EOT, its output (and crucially its **tool calls / side effects**) is held, then released if the committed transcript matches (`NormalizedMatch` default) or discarded via `UserTurnResponseCancelFrame` if the caller resumes, the transcript differs, or a 5 s speculation timeout passes. This moves LLM TTFT off the critical path on correct predictions. It requires an STT that emits eager EOT (Flux; Groq Whisper cannot). Rule from production write-ups: *speculative tokens are fine, speculative side effects are not* — never let a booking write fire from a speculated turn.
Sources: https://reference-server.pipecat.ai/en/latest/api/pipecat.turns.user_stop.eager_user_turn_stop_strategy.html · https://github.com/ombharatiya/ai-system-design-guide/blob/HEAD/18-voice-and-audio-agents/01-realtime-voice-agents.md

**2c. Cheap latency wins that need no new vendor. [S each, $0]**
- **Short first sentence.** TTS can't start until the first *speakable chunk*; prompt/playbook rule: first sentence ≤ ~8 words, no lists/markdown, numbers spelled the way they should be spoken. Sentence-level streaming on, always.
- **Deterministic replies where the controller already knows the answer** (confirmations, slot read-backs, closings): skip the LLM entirely — template + cached TTS audio. Custom builds commonly report most fixed phrases repeat across calls; even at 30–40% of turns this is your fastest, cheapest, most reliable "model."
- **Persistent connections everywhere:** pre-open/reuse the ElevenLabs WebSocket, Gemini and (if adopted) Deepgram sessions at call start — or at worker warm-up — never per turn. Prewarm the whole pipeline at dial time (during ringing/AMD, §6, you have several free seconds: load memory, prefetch objective data, warm connections, synthesise the greeting).
- **Co-locate:** host in the region nearest your Telnyx PoP / majority of callees; region mismatch is 50–150 ms of round-trip you can never tune away.
- **Shrink the prompt:** long system prompts + full history raise TTFT *every* turn and cost grows ~quadratically with turns without caching — see §8 context summarisation + Gemini prompt caching (cached input $0.01/M vs $0.10/M).
- **Don't double-buffer:** audit every silence timer in the chain (VAD stop, Smart Turn fallback, STT endpointing, your controller's own waits) — timers in series are the #1 self-inflicted latency bug in cascaded stacks.

### 3. LLM reliability — 503s, failover, retry, routing

**Diagnosis first:** Gemini `503 UNAVAILABLE` = that *model* is overloaded (capacity, not your key/quota — that's `429 RESOURCE_EXHAUSTED`, counted per *project*, not per API key, so key rotation doesn't fix quota). Overload is model-specific, so falling back *within* Gemini often works — until the outage is Google-wide, which is precisely when a same-provider chain all fails together. And flash-lite's weakness is the tail: one 30-day, 3-region probe (12,992 requests, Sep 27–Oct 26 2026) measured `gemini-flash-lite-latest` TTFT medians of 1.2–1.7 s with **p95 of 5.6–6.1 s and 8–10% timeout/5xx** — on a free-tier key, so paid-tier tails may be better, but the shape (fine median, brutal tail) matches widely reported 503 behaviour. Artificial Analysis puts first-party flash-lite TTFT at ~0.31 s under benchmark conditions; both can be true — benchmarks measure the median, callers experience the tail.
Sources: https://dev.to/max_bob/fastest-ai-inference-api-in-2026-30-days-of-latency-data-for-groq-cerebras-sambanova-and-43-386k · https://artificialanalysis.ai/models/gemini-2-5-flash-lite · https://blog.laozhang.ai/en/posts/gemini-high-demand-error

**3a. In-call policy: deadline-aware, fail over before first token, never serial-retry. [S–M]**
- Set a **per-turn deadline** (e.g. 1,000–1,200 ms to first audio). Primary Gemini attempt gets a TTFT budget (~500–700 ms); on timeout/503, *cancel and switch provider* — a retry of an overloaded model spends the caller's patience on the thing that just failed.
- Fallback must be **cross-provider and OpenAI-compatible-shaped** so it's a config change, not a rewrite: Groq-hosted open models (you already have a Groq relationship; GPT-OSS / Llama-class, ~$0.075/$0.30 per M for GPT-OSS 20B) or Cerebras are the natural second leg for this stack; keep a Gemini Flash (non-lite) as leg 2 if you want same-provider first. Order by *measured p95 TTFT from your region*, not headline speed.
- Add a **circuit breaker**: after N consecutive 503s/timeouts (e.g. 3 in 60 s), route around Gemini for a cooldown (60–300 s) with half-open probes — otherwise every call pays the failure tax during an outage. Log `served_by` + fallback rate per call; alert when fallback rate > a few %.
- While the fallback fires, the §1d filler clip covers the gap. If *all* legs fail: one scripted apology + offer of a callback / retry-later commitment (which your commitment system then tracks, §4) — never dead air, never a hallucinated answer.
- **Hedging** (fire primary + fallback in parallel, take first token, cancel loser) is the aggressive version: justified only on turns where the deadline is already blown, since it doubles cost on those turns.
Implementation choice: **LiteLLM** (self-hosted proxy or in-process Router) gives you exactly this vocabulary out of the box — `fallbacks`, `num_retries`, `allowed_fails` + `cooldown_time`, latency-based routing, background health checks that drain dead deployments before a caller hits them — and Pipecat can point at its OpenAI-compatible endpoint. For a single developer, configuring LiteLLM (S) beats hand-rolling tenacity+pybreaker logic you then have to test (M). Caveat from field reports: verify your fallback config actually engages (a common failure is providers bundled under one model name with no `fallbacks` set, silently running as 1 provider), and never fall back on safety/policy refusals (400/content blocks are *not* retryable — retrying them elsewhere is a jailbreak amplifier).
Sources: https://medium.com/@sathishkraju/what-is-a-model-router-and-which-one-should-you-actually-use-in-2026-0beec49a5c8f · https://dev.to/orca_forge/litellmdemarutipurobaidawohuo-yong-suruhuorubatukushe-ding-nobesutopurakuteisu-1c52 (fallback-misconfiguration field report, via search index)

**3b. Post-call policy is the opposite: patient and cheap.** Extraction, summarisation and eval judging are not latency-bound — there, bounded exponential backoff + jitter (1 s→2 s→4 s, ≤3–4 tries), then next model in chain, then a dead-letter queue for morning review is correct. Also consider Gemini **Batch** pricing (50% off) for nightly re-extraction/eval sweeps. Don't let one code path serve both regimes.
Source: https://blog.laozhang.ai/en/posts/nano-banana-pro-errors-troubleshooting-hub (error-class → retry/fix/rewrite taxonomy)

**3c. Model routing by job, not one model for everything.** Flash-lite is fine for in-call turns with a tight prompt and controller guardrails; use a *stronger* model where errors compound silently: post-call extraction (§4), turn-completeness judging if flash-lite follows markers poorly, and eval judging. Galileo/Artificial-Analysis-style profiles consistently rate 2.5 Flash-Lite's tool-selection/action-completion well below its speed/cost scores — respectable for conversation, shaky as your only extractor/judge. The cost difference at your scale is fractions of a cent per call.
Source: https://galileo.ai/model-hub/gemini-2-5-flash-lite-overview

### 4. Memory quality — extraction recall, semantic repetition, commitments, and the Graphiti/Letta/mem0 question

**Verdict up front: keep SQLite. Your schema is already the right shape.** Graphiti's celebrated innovation — facts as relationships with *validity windows*, contradicted facts *invalidated rather than overwritten*, episodes as first-class inputs — is a description of the store you already built. What Graphiti adds on top (entity graph traversal, hybrid semantic+BM25+graph retrieval, automatic episode→fact consolidation) costs you a graph database (Neo4j/FalkorDB/Neptune) to operate, a second LLM-extraction system, and Cypher-flavoured debugging — for multi-hop relational questions ("who else at this business have we spoken to about X?") that a small outbound-calling deployment rarely asks yet. **Zep** (the hosted Graphiti) removes the ops but is cloud/BYOC-only — against your self-hosted premise — at ~104 ms claimed p50 retrieval. **Letta** is not a memory store at all: it's an agent *framework/OS* where the agent pages its own tiered memory via tool calls; adopting it means replacing your controller+Pipecat architecture, not upgrading your DB. **mem0** is the only one that's a drop-in *layer* (vector store + ADD/UPDATE/DELETE/NOOP extraction, Apache-2.0, self-hostable; Pipecat even documents a `Mem0MemoryService`) — but its classic weakness is exactly yours: no native temporal reasoning, contradictions resolved by the reader model. **Adoption triggers to revisit:** >~10–50k facts per callee population, genuine multi-entity/multi-hop questions, or multiple agents sharing memory. Until then, steal the patterns below.
Sources: https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026 · http://dev.to/plur9/mem0-vs-letta-vs-zep-which-should-you-use-for-agent-memory-1n8m · https://atlan.com/know/episodic-memory-ai-agents/

**4a. Replace rule-based extraction with LLM extraction + a validation cage. [M — the single biggest memory win; fractions of a cent/call]**
Why rules under-file: real callees hedge, correct themselves mid-sentence ("Tuesday — no, Thursday"), imply facts, and bury commitments in small talk. No keyword/regex set survives that; this is a recall problem, and recall is what LLMs are for. Pattern:
1. **Schema-first:** one Pydantic model per playbook objective — `facts[]` (subject, predicate, value, valid_from/valid_until, confidence, **evidence_span** = the verbatim quote supporting it), `episode_summary`, `commitments[]` (who owes whom what, by when), `outcome`. Require the evidence span: it makes hallucinated facts auditable and lets you spot-check precision cheaply.
2. **Provider-enforced structure:** Gemini structured output (`responseMimeType: application/json` + schema / current `responseFormat` in newer SDKs) constrains shape at decode time at no surcharge — *and still validate client-side*, because truncation/refusals return HTTP 200 with schema-violating payloads.
3. **Validate→repair loop:** on `ValidationError`, feed the validator's message back and re-ask, ≤2 retries (one repair attempt resolves the vast majority in published pipelines), then park the call in a **review queue** instead of dropping it — a dropped extraction is invisible; a queued one is a TODO.
4. **Two passes for recall:** Pass 1 extracts per-objective slots (high precision). Pass 2 is an adversarial "what did you miss?" sweep over the transcript with the Pass-1 output in context, asked *only* for facts/commitments not already listed. This is the cheapest recall lever there is.
5. **AUDN merge, copied from mem0/memX:** for each candidate fact, retrieve nearest existing facts (§4b) and let a cheap LLM call classify **A**dd / **U**pdate (invalidate old fact's window, insert new) / **D**elete / **N**oop. This is what turns extraction into a *temporal* store instead of an append-only pile — and it's ~50 lines around your existing tables.
6. **Use a stronger extractor than your in-call model** (§3c) and run it on the *best* transcript you have (dual transcripts, §6, disagreements flagged for review).
Sources: https://omidsaffari.com/blog/structured-outputs · http://dev.to/aifrontierpost/structured-outputs-getting-json-you-can-trust-from-llms-54m0 · https://deepwiki.com/toby-bridges/memx-memory

**4b. Semantic (not token) repetition + dedup: add `sqlite-vec` to the same file. [M, $0 with local embeddings]**
What: `sqlite-vec` adds vector search *inside SQLite* — no new service. Embed (a) stored facts, (b) candidate facts, (c) the agent's own recent utterances per call. Embeddings: local `bge-small-en` / Qwen3-Embedding-0.6B (CPU, ~50 ms) keeps it self-hosted and free; Gemini embeddings are the zero-ops alternative.
Uses:
- *Extraction dedup:* cosine ≳0.85 to an existing fact → route to the AUDN judge instead of inserting a paraphrase duplicate. (Embeddings alone can't tell "loves X" from "hates X" — they embed near-identically — which is why the LLM judge makes the final call on candidates; embeddings are the cheap bulk filter.)
- *In-call repetition detection:* embed each agent utterance; if cosine to any of the last ~6 agent utterances ≳0.85 *and* it isn't a deliberate read-back/confirmation, flag it to the controller: force a playbook advance, a rephrase, or escalation. This catches the "agent re-asks for the date it already got, in different words" loop that token-overlap checks structurally cannot.
- *Recall at call start:* hybrid retrieval for the pre-call briefing — FTS5 (exact names/numbers) + vector (paraphrased needs) fused, instead of either alone.
Source: https://agent-memory.bruegs.com/ (hybrid embeddings-filter + LLM-judge pipeline, cosine thresholds 0.60 novel / 0.85 duplicate, from a production memory architecture write-up)

**4c. Commitment follow-through: commitments are a ledger, not a fact type. [M, $0]**
What: Promote commitments to their own state machine — `OPEN → DUE → FULFILLED | VIOLATED | EXPIRED | CANCELLED` — with `owner` (agent/business/callee), `due_at`, `source_episode_id`, `fulfilment_evidence`. Then:
- A **nightly/on-boot job** (you're single-process friendly: a cron'd Python script over the same SQLite file) lists OPEN commitments past due → they become (i) the top of the *next outbound call's* objective list to that callee, and (ii) an operator-visible overdue list. A commitment nobody re-reads is a diary entry, not a commitment.
- **Verify fulfilment against tool/system logs, not transcripts:** "the agent said it booked the appointment" ≠ booked. If the booking tool returned an ID, the commitment is fulfilled by *that record*; transcript claims are evidence, not proof. (Eval methodology sources make the same point for testing: check downstream state, not the transcript.)
- At call start, inject open commitments into the prompt as *obligations with dates*, and require the playbook to open by acknowledging overdue ones — this is the behaviour callees actually judge repeat callers by.
Source (principle): https://futureagi.com/blog/evaluating-voice-ai-agents-2026/ (downstream-state verification)

**4d. Extraction quality loop.** Keep a golden set: 20–30 real calls, hand-labelled for facts/commitments. Score every extraction change on fact recall/precision + commitment recall against it (this plugs straight into §5). Without it, "extraction feels better" is unfalsifiable — and messy-call recall is precisely where vibes lie.

### 5. Evaluation — how serious teams test before (and after) live calls

**The layered model** (Pipecat's own eval lifecycle doc states it plainly: local evals are layer 1; deployed-path testing and production scoring are separate layers you add, not replacements):

| Layer | Tooling | What it catches | When |
|---|---|---|---|
| 1. Scripted, text mode | **Pipecat Evals** `turns:` scenarios — assert `text_contains`, `function_call`+args, `within_ms` latency budget, semantic `eval:` | Prompt/controller/tool regressions | Every commit (CI); runs in seconds, $0 audio cost |
| 2. Simulated callers, text mode | Pipecat Evals `persona:`+`goal:`+`success:` — an LLM callee (rushed, confused, hostile-from-turn-3, rambling) + LLM judge on the whole conversation | Whole-objective completion across phrasings you didn't script | Every prompt/model/playbook change |
| 3. Audio mode | Same scenarios through real STT/TTS (local Kokoro+Moonshine/Whisper defaults = no keys/cost) | Turn-taking, barge-in (`send_after`), name/number transcription, VAD timing | Pre-release |
| 4. Real telephony | Scripted Telnyx calls to your own numbers / a second Pipecat bot as callee over PSTN | AMD behaviour, codec/STT reality, Telnyx webhook edge cases, true turn-gap | Pre-release + after any telephony change |
| 5. Production scoring | Same scorecard on recorded live calls (LLM judge + deterministic checks) | Drift (providers change models silently), new failure classes → feed back as new Layer-1 scenarios | Nightly/weekly |

Sources: https://docs.pipecat.ai/pipecat/evals/overview · https://docs.pipecat.ai/pipecat/evals/lifecycle · https://www.testmuai.com/blog/pipecat-agent-testing/

**Metrics & gates.** Gate on the *task*, diagnose by *stage* — the methodology point worth internalising: WER is an ASR diagnostic, **not** a launch gate (a 5%-WER stack can still fail 32% of tasks via wrong-slot entities, dropped negations, codec damage, or downstream LLM/TTS failures).
- **Launch gate:** task completion (booked correctly / information obtained, verified in downstream state) — Hamming's production targets: **>85%** (critical <70%).
- **Latency:** voice-to-voice / time-to-first-audio **p50 and p95 per stage and end-to-end** (target TTFA <1.7 s, critical >5 s in Hamming's table; serious cascaded builds aim p50 ≤800 ms–1 s). Pipecat Evals expresses this as `within_ms` per turn.
- **Interruption:** barge-in recovery **>90%** (critical <75%); plus barge-in *stop time* (caller starts → agent silent; measure from audio, target a few hundred ms) and false-interruption rate (backchannel killed a sentence, §1c).
- **Conversation health:** repetition incidents/call (§4b detector doubles as the metric), double-goodbye count (should be 0), idle-prompt false-fire rate, filler trigger/penalty rate.
- **STT diagnostics:** WER <10% (critical >15%) **and entity error rate reported separately** — a wrong digit in a phone number is not 1/20th of a failed call, it *is* the failed call.
- **Reliability:** provider error/fallback rate per call (§3), extraction recall/precision vs the §4d golden set.
- **Method:** run each scenario **multiple times** (agents are non-deterministic; a single pass proves nothing — τ-bench-style repetition), and keep the suite at 50–100 curated conversations before growing it.
Sources: https://hamming.ai/resources/voice-agent-testing-guide · https://futureagi.com/blog/evaluating-voice-ai-agents-2026/ · https://dev.to/gjusev/making-voice-agent-failures-reproducible-with-python-55ao (open-source `voice-evals`: replay + scripted-probe, jiwer WER, e2e p50/p95/p99, barge-in stop time, CI exit-code gates — a useful complement if you outgrow Pipecat Evals' reporting)

**Cheap wins inside this section:** Pipecat Evals' default judge/persona is a *local Ollama model* — simulations cost nothing but your in-call LLM's tokens. And every production failure should graduate into a Layer-1 scenario the same day; that flywheel, not any platform purchase (Hamming/Coval/etc. exist if you outgrow DIY), is what separates tested agents from lucky ones.

### 6. Telephony — Telnyx-specific improvements

**6a. Turn on Premium AMD and handle the greeting, not just the verdict. [S config; Premium $0.0065/answered call, Standard $0.002/call — secondary pricing guides, verify in portal]**
What: `answering_machine_detection` on the Dial call. Premium returns `human_residence | human_business | machine | silence | fax_detected` via `call.machine.premium.detection.ended`, plus — the part builds miss — `call.machine.premium.greeting.ended` with `beep_detected`. Correct flow: on `machine`, **wait for greeting-ended/beep**, then either hang up (and log a voicemail episode + schedule retry — AMD outcomes belong in memory as episode facts) or play a *pre-recorded* voicemail drop (never live-TTS your greeting into a beep). On `silence`, treat as a likely pocket/accidental answer: one "hello?" then hang up. New in current Telnyx: `premium_ios_call_screening_detection` handles Apple's call-screening ("state your name and reason") — increasingly the actual "person" answering your outbound calls in late 2026; the screened flow re-runs Premium AMD after the screening prompt ends. Gate the extra $0.0045/call: Premium for campaigns where voicemail-drop timing matters, Standard where a hangup suffices.
Why it matters beyond cost: without AMD your bot delivers its opening line to voicemail greetings, pollutes episodes with machine transcripts, and burns LLM/TTS spend talking to a recording.
Sources: https://developers.telnyx.com/api-reference/call-commands/dial · https://www.cloudtalk.io/telnyx-pricing/

**6b. Use the ringing/AMD window as free compute time.** While Telnyx rings and AMD runs, your worker should already: open the Pipecat pipeline + all provider WebSockets, load the callee briefing (§4b), prefetch objective data, and pre-synthesise the greeting. Ring-to-first-word time is a scored metric in production stacks; silence on pickup reads as a robocall and triggers hangups (and, with iOS screening, worse).
Source: https://www.evalgent.com/blog/voice-agent-stack-guide (session-runtime layer)

**6c. Recording + Telnyx transcription as infrastructure, not compliance garnish. [S; recording ~$0.002/min]**
- `record: record-from-answer` with **dual** channels: caller/agent separated recordings are what make §2's true turn-gap measurement, §5's production scoring, and honest TTS listening tests possible. (Pipecat-side recording of the mixed stream is not a substitute.)
- `transcription_start` (Telnyx/Google engines) gives you an **independent second transcript** per call. Uses: cross-check against your STT transcript for extraction (disagreement on a name/date → review queue), a cheap always-on transcript if your pipeline dies mid-call, and eval corpus building without re-running STT.
- Call Control actions available on the same leg and worth wiring: `streaming_start` (bidirectional mode for Pipecat), `fork_start` (fork audio to a second destination — e.g. a supervisor/eval recorder — without touching the bot path), `transfer`/`bridge` for human handoff (remember: set Telnyx serializer `auto_hang_up=False` on any leg that must survive the bot).
Source: https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-commands-and-resources

**6d. SIP vs WebSocket streaming — stay on WebSocket; know what you're trading.** Telnyx media streaming to Pipecat is 8 kHz mono over WebSocket (TCP): head-of-line blocking under loss and no native wideband are real, but for PSTN callees the *call itself* is 8 kHz G.711 — there is no wideband to recover, and your bottleneck is elsewhere (§2). Native-SIP termination (LiveKit SIP, or Daily+Telnyx SIP) buys codec control, transfers/reconnects and an SFU's jitter handling at the cost of a second media stack to operate — an **L**-effort migration justified only when you need warm transfers/conference/multi-party or WebSocket loss becomes measurable in your turn-gap data. One nuance: if you ever fork/stream via RTP instead, Telnyx's fork codec list includes G.722/Opus — useful for *non-PSTN* (SIP-to-SIP) legs where wideband actually exists.
Sources: https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets · https://www.evalgent.com/blog/voice-agent-stack-guide (Architecture A discussion: "Pipecat does not terminate SIP itself… WebSocket path lacks advanced call-center features")

**6e. Webhook hygiene.** Configure the Call Control app's **failover webhook URL** (Telnyx retries to it when the primary fails), make every command idempotent (`command_id`), and carry per-call state in `client_state` rather than assuming your process that placed the call is the one that gets the webhook — this is what makes a mid-call worker restart survivable.
Source: https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-webhooks

### 7. TTS quality — ElevenLabs on phone audio, and what to benchmark against it

**Reality check first:** everything above ~4 kHz in your beautiful 24 kHz ElevenLabs demo is discarded by the 8 kHz phone chain. **All voice/settings decisions must be made by listening through a real Telnyx call recording** (§6c), never in a browser. Within that constraint:

**7a. Settings for `eleven_flash_v2_5` on telephony. [S, $0]**
- **Output format `ulaw_8000`** (native G.711 μ-law): removes an MP3/PCM decode + resample stage between ElevenLabs and Telnyx and matches the wire format exactly. If your Pipecat ElevenLabs service is synthesising 44.1 kHz MP3 by default, this alone can shave tens of ms and a class of resampling artefacts.
- **`style: 0`** — any style exaggeration costs compute/latency; **`use_speaker_boost: false`** for the same reason (it's a similarity feature you don't need on a premade voice at 8 kHz).
- **`stability` ~0.5, `similarity_boost` ~0.75** as the starting point (the widely used production defaults); raise stability toward 0.6–0.7 if the voice wanders on numbers/dates, lower it if it sounds flat on confirmations. `speed` 1.0 (up to ~1.05 if your callees are business-receptionist-paced — test, don't assume).
- **Text hygiene beats settings:** your LLM/playbook output is the TTS's script — spell out dates/times/currency as spoken, no markdown/URLs/lists, short first sentence (§2c). Add a pronunciation pass for your domain's names/terms (phonetic respelling in a pre-TTS processor is a small Pipecat `FrameProcessor`).
- Premade-voice caveat: if one voice consistently mangles your domain entities at 8 kHz, switching *voices* (or a cloned/designed voice recorded clean) usually beats any slider. Test 3–4 voices on the same entity-heavy script through the phone.
Sources: https://eliteai.tools/agent-skills/elevenlabs-performance-tuning · https://www.promptfoo.dev/docs/providers/elevenlabs/ (ulaw_8000 note)

**7b. Flash is still the right ElevenLabs model for calls — but price/quality moved.** Flash v2.5: ~75 ms *model* latency (vendor); ~135 ms measured TTFB in one 2026 benchmark; Turbo v2.5 is now deprecated in ElevenLabs' own listings in favour of Flash. New since your stack was chosen: **Eleven v4 Turbo** (~150 ms vendor median-to-first-speech, $40/M chars — same as Flash's current $40/M per Oct-2026 pricing checks; other trackers list Flash at $25–50/M, so verify your plan) topping independent TTS arenas. It's worth one A/B slot, not a migration.
Sources: https://voiceaibench.com/ · https://www.forasoft.com/blog/article/elevenlabs-alternatives · https://invideo.io/blog/elevenlabs-ai-voice-models/

**7c. Alternatives worth one benchmarking afternoon each. [M per candidate]**
| Candidate | Claimed / measured TTFA | Price (per 1M chars) | Why benchmark it |
|---|---|---|---|
| Cartesia Sonic-3 / 3.5 / 3.6 | ~85 ms claimed; **166–190 ms measured median** in production benchmarks | ~$20–37 | Built for voice agents; the latency leader on vendor numbers, less clearly in the wild — exactly why you measure on your chain |
| Deepgram Aura-2 | ~115 ms claimed | ~$15 | Cheapest credible option; pairs with a Deepgram STT move (§2a) for one-vendor simplicity |
| ElevenLabs Flash v2.5 (incumbent) | ~75 ms model / ~135 ms measured | ~$40 | Best realism/accents per benchmarks; your baseline |
Score each on: p50/p95 TTFA *through Telnyx*, blind MOS over the phone recording on your real scripts (incl. names/dates/numbers), and cost/call at your actual agent-talk ratio (TTS is per-character: at ~360 agent chars/min, Flash ≈ $0.014/min — your dominant AI cost line, ahead of LLM ~$0.001–0.004/min and Groq STT ~$0.0007/min).
Sources: https://voiceaibench.com/ · https://dev.to/mrzitoun/benchmarking-real-time-voice-ai-apis-cartesia-vs-deepgram-vs-elevenlabs-2026-2n8c

**7d. Cache ruthlessly.** Greetings, fillers (§1d), confirmations, closings: synthesise once, play forever. Beyond latency, cached audio is *identical* every call — which removes TTS variance from your eval results (§5) and from callee experience.

### 8. Pipecat features a custom build commonly misses — checklist

Verified against current Pipecat docs (Oct 2026). If your code predates Pipecat 1.x patterns, several of these are *replacements* for things you hand-built:

- [ ] **Smart Turn v3 as default stop strategy** + VAD configured on the **user aggregator**, not the transport (§1a). Also: Pipecat 1.8's turn arbitration (one arbiter decides among VAD/STT/turn-model proposals) fixed a hidden flat ~0.5 s per-turn delay present in earlier composition patterns — upgrading can be a latency fix by itself. (Secondary source: https://github.com/ombharatiya/ai-system-design-guide/blob/HEAD/18-voice-and-audio-agents/01-realtime-voice-agents.md)
- [ ] **Turn-completeness strategies** (`FilterIncomplete…` / `Classifier…`) (§1b) and **`EagerUserTurnStopStrategy` + SpeculationGate** (§2b).
- [ ] **Interruption controls:** `MinWordsUserTurnStartStrategy`, Krisp VIVA interruption prediction, mute strategies, `empty_user_turn` config; plus the guarantee that **only spoken-so-far text enters context** on interruption — if your custom code appends full LLM generations to history, you're teaching the model a conversation the callee never heard. (§1c) Source: https://docs.pipecat.ai/pipecat/fundamentals/interruptions
- [ ] **Backchannel package** (§1d) and **idle handling** in `LLMUserAggregatorParams` (`user_idle_timeout` → `on_user_turn_idle`; the old `UserIdleProcessor` was removed in 1.0).
- [ ] **Context summarisation:** `enable_auto_context_summarization=True` on the assistant aggregator — **off by default**, triggers at 8,000 est. tokens / 20 messages, preserves system + recent + incomplete function-call pairs, can run on a *dedicated cheap LLM* so it never interrupts the conversation, and can be fired on demand via `LLMSummarizeContextFrame`. For 10+-minute calls this is a TTFT and cost fix, not just a context-window fix. Source: https://docs.pipecat.ai/pipecat/fundamentals/context-summarization
- [ ] **Observers instead of print-debugging:** `UserBotLatencyObserver` (user→bot latency), `TurnTrackingObserver`, `ServiceMetricsObserver`, `StartupTimingObserver`, plus built-in metrics (TTFB per service) and OpenTelemetry tracing. These are your §2/§5 instrumentation for free. Source: https://docs.pipecat.ai/api-reference/server/utilities/observers/observer-pattern
- [ ] **Pipecat Evals** (§5) — scripted + simulated scenarios against the *same* bot code, `pipecat eval` CLI, library API for pytest/CI.
- [ ] **Pipecat Flows** as the reference design for your playbooks: conversations as nodes, each node exposing *only* its task's prompt + tools, transitions changing context/tools, shared `flow_manager.state`. Even if you keep your own controller (reasonable — it owns telephony/commitment state Flows doesn't), borrow the per-node prompt/tool scoping: it shrinks prompts (latency, §2c), improves tool-arg accuracy on small models (§3c), and makes per-objective evals (§5) natural. Flows now ships inside `pipecat-ai` (the separate `pipecat-ai-flows` package is legacy). Sources: https://discuss.huggingface.co/t/voice-ai-agent-construction/177750 · https://deepwiki.com/pipecat-ai/pipecat-flows/4.1-environment-setup
- [ ] **A documented long-term-memory service exists** (`Mem0MemoryService`, scoped by user/agent/run id, per Pipecat's memory docs as reported by integrators) — the sanctioned trial path if you ever A/B mem0 against your SQLite store without rearchitecting (§4). Source: https://www.maximem.ai/blog/pipecat-memory-synap-integration
- [ ] **Telnyx serializer specifics:** `auto_hang_up` semantics (§1e/§6c), DTMF handling in the serializer, and `resampler_clear_after_secs` for barge-in audio hygiene. Source: https://reference-server.pipecat.ai/en/latest/api/pipecat.serializers.telnyx.html

---

## Could not verify / open questions

- **Your current Pipecat version is unknown**, and it changes the advice materially: Smart Turn v3-by-default, aggregator-side VAD, speculation, and Evals are 1.x-era (docs current Oct 2026; secondary sources cite 1.12.0). If you're on 0.0.x, item #1 becomes "upgrade," effort M, and several S items become available only after it. No source conflict — just an unspecified parameter.
- **Prices in flux:** ElevenLabs per-character pricing ($25 vs $40 vs $50 per M chars across Oct-2026 trackers), Telnyx AMD rates (traced to a Dec-2022 rate card in one source, corroborated at $0.0065/$0.002 by a 2026 pricing guide), and Deepgram promo vs regular rates all need a portal check before budgeting. Direction (TTS ≫ LLM > STT in your per-minute cost) is robust to these ranges.
- **Latency figures are not comparable across sources:** vendor "model latency" (ElevenLabs 75 ms, Cartesia ~40–90 ms) vs measured end-to-end TTFB (135 ms / 166–190 ms) differ by network and buffering; the Gemini tail data (§3) comes from a free-tier key. All are used here only to rank *what to measure*, not as predictions for your deployment.
- **Krisp VIVA** interruption prediction / VAD licensing and pricing, and whether Deepgram Flux is available in Pipecat's current Deepgram service with eager-EOT wired to `EagerUserTurnStopStrategy` in your version, were not verified on primary pricing/API pages — check before committing to §1c/§2b.
- **Call-recording/consent and AI-disclosure law** for outbound calls (jurisdiction-dependent, and changing in 2026) is out of scope of this mission and was not researched; the mute-strategy mechanism for uninterruptible disclosures is noted in §1c only as a technical capability.

## Sources

Primary / framework docs (fetched 2026-10-31):
- Pipecat — Speech Input & Turn Detection: https://docs.pipecat.ai/pipecat/learn/speech-input
- Pipecat — Interruptions: https://docs.pipecat.ai/pipecat/fundamentals/interruptions
- Pipecat — Context Summarization: https://docs.pipecat.ai/pipecat/fundamentals/context-summarization
- Pipecat — Evals overview & lifecycle: https://docs.pipecat.ai/pipecat/evals/overview · https://docs.pipecat.ai/pipecat/evals/lifecycle
- Pipecat — Backchannel extension: https://docs.pipecat.ai/api-reference/server/extensions/pipecat-backchannel
- Pipecat — Observer pattern: https://docs.pipecat.ai/api-reference/server/utilities/observers/observer-pattern
- Pipecat — Telnyx WebSocket integration: https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets
- Pipecat — Telnyx serializer reference: https://reference-server.pipecat.ai/en/latest/api/pipecat.serializers.telnyx.html
- Pipecat — Eager turn stop / SpeculationGate reference: https://reference-server.pipecat.ai/en/latest/api/pipecat.turns.user_stop.eager_user_turn_stop_strategy.html
- Telnyx — Dial (AMD, premium/iOS screening, beep webhooks): https://developers.telnyx.com/api-reference/call-commands/dial
- Telnyx — Voice API commands (record/stream/fork/transcription): https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-commands-and-resources
- Telnyx — Voice API webhooks (failover URL): https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-webhooks

Analysis, benchmarks & field reports (read via web index, Oct 2026):
- Evalgent — Voice Agent Stack: Every Layer, Latency and Cost (2026) [opened in full]: https://www.evalgent.com/blog/voice-agent-stack-guide
- Evalgent — Backchanneling, Fillers, Idle Prompts: https://www.evalgent.com/blog/backchanneling-proactivity-voice-agents
- GetStream — Speculative Tool Calling: https://getstream.io/blog/speculative-tool-calling-voice/
- 30 days of inference latency data (Groq/Gemini flash-lite tail): https://dev.to/max_bob/fastest-ai-inference-api-in-2026-30-days-of-latency-data-for-groq-cerebras-sambanova-and-43-386k
- Artificial Analysis — Gemini 2.5 Flash-Lite: https://artificialanalysis.ai/models/gemini-2-5-flash-lite
- Gemini high-demand / 503 route board: https://blog.laozhang.ai/en/posts/gemini-high-demand-error
- LiteLLM routing/fallbacks explainer: https://medium.com/@sathishkraju/what-is-a-model-router-and-which-one-should-you-actually-use-in-2026-0beec49a5c8f
- STT comparison (Groq batch vs Deepgram telephony): https://dev.to/pietrus914/speech-to-text-api-comparison-whisper-api-options-in-2026-400h
- Voice AI latency / Flux eager EOT: https://www.marktechpost.com/2026/08/30/lowest-latency-inference-apis-for-voice-and-realtime-agents-a-time-to-first-token-ttft-first-benchmark/
- TTS benchmarks: https://voiceaibench.com/ · https://www.forasoft.com/blog/article/elevenlabs-alternatives · https://dev.to/mrzitoun/benchmarking-real-time-voice-ai-apis-cartesia-vs-deepgram-vs-elevenlabs-2026-2n8c
- ElevenLabs tuning (formats/settings): https://eliteai.tools/agent-skills/elevenlabs-performance-tuning
- Memory frameworks compared (2026): https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026 · http://dev.to/plur9/mem0-vs-letta-vs-zep-which-should-you-use-for-agent-memory-1n8m · https://atlan.com/know/episodic-memory-ai-agents/
- Structured outputs (Gemini schema + validate/repair): https://omidsaffari.com/blog/structured-outputs · http://dev.to/aifrontierpost/structured-outputs-getting-json-you-can-trust-from-llms-54m0
- memX (AUDN + sqlite-vec hybrid memory): https://deepwiki.com/toby-bridges/memx-memory · Agent memory architecture (cosine thresholds): https://agent-memory.bruegs.com/
- Voice-agent testing (Hamming targets): https://hamming.ai/resources/voice-agent-testing-guide · FutureAGI eval methodology: https://futureagi.com/blog/evaluating-voice-ai-agents-2026/ · Pipecat agent testing: https://www.testmuai.com/blog/pipecat-agent-testing/ · voice-evals OSS: https://dev.to/gjusev/making-voice-agent-failures-reproducible-with-python-55ao
- End-of-call patterns: https://docs.retellai.com/integrate-llm/integrate-function-calling · https://www.bolna.ai/docs/outbound/hangup-calls
- Telnyx pricing guide (AMD/recording/streaming rates): https://www.cloudtalk.io/telnyx-pricing/
- Pipecat memory services (Mem0MemoryService): https://www.maximem.ai/blog/pipecat-memory-synap-integration
- Real-time voice agents system-design guide (turn arbitration, speculation): https://github.com/ombharatiya/ai-system-design-guide/blob/HEAD/18-voice-and-audio-agents/01-realtime-voice-agents.md
