# AI Calling — Debug Handoff Package

**For the debugging AI: start here.** This package contains everything needed to debug one specific, reproducible failure in a self-hosted AI phone-calling system. The full source is included as markdown (files 04–06), the architecture in 02, the environment in 03, hard evidence in 07, and the complete project history in 09.

## The system
A self-hosted outbound AI caller: Telnyx (telephony + media streaming) → a Python server (FastAPI + Pipecat 1.8.0) → Groq Whisper (STT) → Google Gemini (dialogue, fast/frontier tiers) → ElevenLabs (TTS, voice "Jessica"). Code repo: https://github.com/nbeitelman1/ai-calling

## THE BUG (one paragraph)
Outbound calls connect. The Telnyx media WebSocket connects (`streaming.start` returns 200, and on the last call it stayed up for a clean 46-second call). **But the bot has never spoken on any live call — 5 out of 5 qualification calls, the callee hears silence.** No transcript/cost record has ever been produced by a live call. Yet a no-dial probe that builds the pipeline exactly the way production does (`build_live_pipeline`) and queues the opening line produced real synthesized audio frames (89,560 bytes of PCM, TTFB 0.28s). So: speech synthesis works in isolation; on a live media session the bot stays mute and no conversation turn is ever recorded. **Find where the live media-session path diverges from the probe path and why the opening line never becomes audio on the wire.**

## What "fixed" looks like
1. On a live (or faithfully simulated) media session, the bot emits its opening — "Hi, this is Nathan's assistant calling on his behalf." — as outbound audio frames on the media WebSocket.
2. Inbound speech produces a full turn: STT transcript → LLM reply → TTS audio out.
3. A transcript/cost record is written for the call (the dialer looks for it in `logs/qualification-call-2026-10-29.jsonl` and has never found one).

## Reading order
00 this file → 01 problem timeline → 02 architecture → 03 environment (do not skip: this VM's egress proxy rewrites WebSocket handshakes; it has already caused one production bug) → 04–06 source → 07 evidence → 08 suspects/open questions → 09 full history.

## Ground rules
- Every claim in 01/07 is from logs, call records, or the callee's own report — treat them as facts. Anything labeled SUSPECT in 08 is a hypothesis, not a finding.
- The test suite passes 129/129. The bug survives the suite because no test drives a real media session end-to-end.
- Live-session logging is itself broken (see 03): during live calls the server journal shows nothing for the media session. Any diagnosis method that depends on those logs will starve; prefer adding observability or bench reproduction.
