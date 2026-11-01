# Suspects and Open Questions (hypotheses — none of these are findings)

1. **Session startup seam.** The dialer registers a media job and later Telnyx opens `wss://…/telnyx/media/<attempt_id>`. Trace exactly what the server does on that WebSocket connect: does it claim the job, build the pipeline, and START the pipeline task? Compare against the probe, which builds and runs the pipeline directly. The divergence is most likely here: registration → claim → transport creation → pipeline task lifecycle (created? awaited? cancelled when the WS handler returns?).
2. **Opening delivery path.** Production queues the opening as an aggregated sentence frame. In call #3 it reached TTS aggregation (punkt error). In call #5 nothing was observed — did the opening frame get queued at all? Is it queued before the transport/output is ready and silently dropped? Is the phrase-cache path used for the opening in live sessions, and does the cache lookup succeed under the live namespace?
3. **Outbound audio write path.** When TTS produces audio frames in a live session, does the transport serialize them onto the Telnyx media WebSocket in the format Telnyx expects (PCMU payloads in `media` messages with the right stream/call identifiers)? A format or identifier mismatch could make Telnyx discard audio while keeping the stream up (consistent with call #5: stream healthy for 46s, callee hears nothing, no error anywhere).
4. **Inbound path never exercised?** No transcript record has ever been written. Is the record written only at pipeline teardown, and is teardown reached? Is the STT service receiving inbound frames (frame counts in/out of the media handler)?
5. **Worker registration.** Check how pipeline processors/workers are registered and started in the live path (the probe path may register implicitly). If a processor in the chain never starts, frames may queue indefinitely without error.
6. **Observability first.** Before another live dial: make the live session log somewhere durable (file under `logs/`, not only the journal), including frame counters (media frames in, TTS frames out, WS messages sent) and pipeline task lifecycle events. Two of five failures were undiagnosable for lack of this.
7. **Bench before phone.** A faithful local reproduction — a fake Telnyx media peer speaking the media-streaming protocol to `ws://127.0.0.1:8001/telnyx/media/<attempt>` after registering a job the way the dialer does — should be able to demonstrate the failure and verify a fix without burning live calls.

## Constraints for any fix
- Keep the suite green (129 tests; adapter reference suite 57/57; mutation checks 3/3).
- Do not change the dialer's authorization model (per-call approval flag) or the adapter's single-claim semantics.
- Any new outbound WebSocket client must tolerate this VM's egress proxy (see 03).
- The fix must be verified by a live or bench media session that produces: opening audio out, one full inbound-speech turn, and a transcript/cost record.
