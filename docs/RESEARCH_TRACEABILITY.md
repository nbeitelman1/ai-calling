# Research-to-code traceability

**Research date:** October 30, 2026. Official documentation corroborated source inspection; the installed hash-locked package is the authority for runtime compatibility. No public search contained private code, credentials, phone numbers or user data.

## Pipecat lifecycle and audio

1. **Custom FrameProcessor / Pipeline processing**  
   https://docs.pipecat.ai/pipecat/fundamentals/custom-frame-processor  
   https://docs.pipecat.ai/pipecat/learn/pipeline  
   Official examples require the parent `process_frame()` call and forwarding relevant frames. Installed `pipecat/processors/frame_processor.py` shows the StartFrame lifecycle/data-queue machinery. This explains why a start signal can pass while a custom processor's normal data queue does not drain. Applied in five processors; verified with real worker/queue tests and a broken-lifecycle mutant.

2. **TTS and native standalone speech**  
   https://docs.pipecat.ai/pipecat/learn/text-to-speech  
   `TTSSpeakFrame` is the native standalone speech path. Installed `services/tts_service.py` and `services/elevenlabs/tts_base.py` govern context creation, sequenced text, completion and silence insertion. The ZIP's context patch was checked against that implementation, not accepted blindly. The candidate's duplicate text insertion was independently detected and removed.

3. **Frame semantics and idle detection**  
   https://docs.pipecat.ai/api-reference/server/frames/system-frames  
   https://docs.pipecat.ai/api-reference/server/frames/control-frames  
   https://docs.pipecat.ai/pipecat/fundamentals/detecting-user-idle  
   Transport bot-speaking frames are distinct from TTS generation frames. Applied to activity/silence handling. Neither frame type proves a human heard the audio; a carrier-aware playout tracker remains backlog.

4. **Telnyx serializer / FastAPI WebSocket transport**  
   https://docs.pipecat.ai/api-reference/server/services/serializers/telnyx  
   https://docs.pipecat.ai/api-reference/server/services/transport/fastapi-websocket  
   https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets  
   Checked serializer configuration and 8kHz telephony / 16kHz processing boundaries against the installed code. Full synthetic testing exercised the real serializer and transport. Do not confuse encoded PCMU on the wire with linear PCM inside the pipeline.

5. **Usage metrics**  
   https://docs.pipecat.ai/pipecat/fundamentals/metrics  
   https://docs.pipecat.ai/api-reference/server/utilities/observers/service-metrics-observer  
   Typed usage includes LLM tokens, STT audio seconds and TTS characters. Usage collection is enabled, but a complete model/provider-specific billing ledger is not implemented by this candidate. This is explicitly a next-wave requirement, not a claimed completed integration.

## Telnyx protocol and trust boundaries

6. **Media Streaming over WebSockets**  
   https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming  
   https://developers.telnyx.com/api-reference/websockets/stream-call-media-over-websocket  
   Official media upgrade signature contract covers `{telnyx-timestamp}|{stream_url}`, including the exact query string. The candidate verifies this with the existing Ed25519 verifier, freshness and duplicate-header controls. The canonical public host is configuration, not a forwarded-header assertion. The document also describes media, mark and clear behavior; mark/clear-aware playout confirmation is intentionally not claimed as implemented.

7. **Voice API webhooks**  
   https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-webhooks  
   Distinguishes call hangup, media streaming and playback events and documents retry/response behavior. The candidate retries idempotent processing on duplicate delivery and avoids treating a media disconnect or command acknowledgment as a final bill/terminal event. Persistent recovery and storage-failure response policy still need work.

## Provider configuration, latency and accounting

8. **Gemini model catalog**  
   https://ai.google.dev/api/models  
   Model listing is paginated and exposes supported generation methods. Applied pagination, repeated-token protection and generation-capability filtering. The selected fallback is carried through a matching fresh preflight receipt instead of silently reverting to the first configured name at call time. No model name was newly hard-pinned by the audit.

9. **Groq speech-to-text**  
   https://console.groq.com/docs/speech-to-text  
   Groq documents 16kHz mono preprocessing and a **10-second minimum billed length per request**. A segmented VAD/STT pipeline can incur materially more billed duration than summed speech or call-wall duration. This is why current dollar estimates are labeled incomplete and why a per-request usage/billing reconciliation is a P0 production item. Public list prices do not replace the account's actual plan/invoice.

10. **ElevenLabs multi-context WebSocket**  
    https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/multi-context-web-socket  
    One connection per session, context lifecycle, flushing and interruption cleanup are relevant to the cache/live boundary. REST voice/model existence does not prove the real Muse WebSocket connection succeeds or that the voice sounds correct. Real provider testing remains an explicit acceptance gate.

## Python and deployment

11. **Cancellation semantics**  
    https://docs.python.org/3.12/library/asyncio-task.html  
    `wait_for()` waits for cancellation to settle and can exceed its nominal timeout. The candidate uses bounded `asyncio.wait` for owned-task cleanup and reports leftovers. This does not kill a cancellation-resistant coroutine; process supervision and isolation remain the final boundary.

12. **Virtualenv portability**  
    https://docs.python.org/3.12/library/venv.html  
    Virtualenv entry-point shebangs contain absolute paths. The legacy deployment's rename-based venv swap is therefore not a reliable general deployment pattern. The supplied preparation tool creates a new environment at its final path and generates a review-only service override. A fully automated atomic deployment/rollback was not tested.

13. **WebSocket proxies and upgrade headers**  
    https://websockets.readthedocs.io/en/16.0/deploy/nginx.html  
    https://websockets.readthedocs.io/en/17.1/topics/proxies.html  
    Proxy preservation of HTTP/1.1 Upgrade/Connection semantics matters. The source's blanket normalization was narrowed to one explicitly enabled known defect. Fixing the proxy is preferable to maintaining a compatibility exception. Current proxy docs may describe a later version; behavior was checked against the pinned installed client.

14. **Tokenizer provenance**  
    https://github.com/nltk/nltk_data/tree/550b6625bcef1f2abff2ff770a5a0d272c9c6b2a  
    Archive: `packages/tokenizers/punkt_tab.zip`  
    Archive SHA-256: `e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`  
    Only the English tabular model files and upstream README were copied. No pickle is loaded. Individual hashes are in `evidence/tokenizer-provenance.json`; the upstream README retains attribution and training-source information. Review third-party redistribution/licensing under your normal process.

## Calling compliance boundary — engineering guidance, not legal clearance

15. **FCC Declaratory Ruling FCC 24-17**  
    https://docs.fcc.gov/public/attachments/FCC-24-17A1.txt  
    https://www.fcc.gov/document/fcc-confirms-tcpa-applies-ai-technologies-generate-human-voices  
    AI-generated voices fall within the TCPA artificial/prerecorded-voice framework. Applicable consent requirements, exemptions and circumstances need review. A caller-side approval flag is not recipient consent, and disclosure is not a universal substitute for it.

16. **FTC guidance and 2024 B2B anti-fraud changes**  
    https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule  
    https://www.ftc.gov/news-events/news/press-releases/2024/03/ftc-implements-new-protections-businesses-against-telemarketing-fraud-affirms-protections-against-ai  
    https://www.ftc.gov/business-guidance/resources/qa-telemarketers-sellers-about-dnc-provisions-tsr-0  
    Telemarketing, purely informational calls and business-to-business calls have different treatment. Older/general B2B guidance should not be read as a blanket exemption after later rule changes. TCPA, TSR, state rules and recording/transcription consent are distinct checks. This audit does not decide legal eligibility for any particular planned call.

## Decisions deliberately NOT made from research alone

- No provider change, model benchmark claim, or new automatic dial/transfer was introduced.
- No production API was queried to infer account entitlements, remaining quota or pricing.
- No guess about the exact live silent-call root cause was promoted to proof without a local reproduction or a clearly labeled hypothesis.
- No claim that all defects, vulnerabilities or legal obligations have been found.
- No claim that a green offline suite proves speech quality, carrier playback, runtime proxy behavior or invoice-level budget compliance.
