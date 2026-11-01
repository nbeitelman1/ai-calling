# Problem Timeline — Oct 29–30, 2026 (all times EDT)

## The five qualification calls (all to the owner's own number, authorized test calls)
- **Call #1 (Oct 29, ~11:12pm)** — dial connected; media WebSocket FAILED (Telnyx error 90046) through the old tunnel (tunl). Root cause found later at the tunnel layer: tunl's response-capture middleware cannot hijack connections, so WS upgrades die at its edge. Transport replaced with Pinggy Pro (WS upgrade through it verified: HTTP 101).
- **Call #2 (Oct 30, 10:34am)** — media WebSocket CONNECTED through Pinggy (streaming.start 200). Callee heard silence; call 28.6s. Server log: the ElevenLabs TTS service's outbound WebSocket handshake died — "invalid Connection header: close". Wire-proven cause: this VM's egress proxy TLS-intercepts and re-originates the 101 response with `Connection: close`; the websockets library (17.3) strictly rejects it. FIXED in `server/live_pipeline.py`: `_CloseHeaderTolerantConnection` normalizes the handshake response for the TTS service only. Verified post-fix by a production-Service probe: HANDSHAKE OK + ping/pong OK.
- **Call #3 (Oct 30, 10:45am)** — media connected; callee heard silence again; 21.4s. New cause found in logs: Pipecat's TTS sentence aggregation needs NLTK `punkt_tab` data, which was absent; `sent_tokenize` raised LookupError before synthesis → zero audio frames. FIXED by installing punkt_tab into `~/workspace/ai-calling/nltk_data` and setting `NLTK_DATA` in the systemd unit. Verified without dialing: production-path probe queued the opening the production way → "Generating TTS", TTFB 0.281s, AUDIO_FRAMES=2, AUDIO_BYTES=89,560, zero error frames; suite 129/129.
- **Call #4 (Oct 30, 11:55am)** — dial OK; the call rolled to voicemail; streaming started then STOPPED after ~3.8s; callee joined from voicemail, heard silence, pressed keys (DTMF logged 11:56:16). ~40s later the entire VM was replaced by the runtime (systemd units and journal wiped) — the reason streaming stopped at 3.8s is UNRECOVERABLE from logs. Stack rebuilt (deploy + units reinstalled, readyz green local and public).
- **Call #5 (Oct 30, 12:00pm)** — dial OK; answered LIVE in 7.0s; streaming.start 200; clean 46.4s call ending in a normal hangup (nothing crashed). **Callee: "No speaking" — silence for the entire call.** Dialer again reported "no transcript/cost record found". Server journal contained NO entries at all for the call window (only service startup lines) — the live media session's logging is not landing in the journal or in `logs/`.

## What is proven working (do not re-litigate)
- Telnyx account, API auth, outbound dial, webhooks (initiated/answered/hangup all delivered and correlated), trial verified-number calling.
- Media transport through Pinggy: WebSocket upgrade succeeds externally; streaming.start 200 on calls #2–#5.
- Provider preflight: Groq/Gemini/ElevenLabs keys validate; Jessica voice resolves; suite 129/129 including adapter reference tests 57/57.
- TTS synthesis via the production pipeline builder in a no-dial probe (after the two fixes above).

## What has never worked
- Bot speech on a live call (0 for 5).
- Any STT→LLM→TTS turn on a live call.
- Any transcript/cost record from a live call.
- Live-session observability in the journal.
