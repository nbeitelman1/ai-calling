# Evidence

## Call #5 (Oct 30, 2026, 12:00pm EDT) — dialer stdout, verbatim
```
HEALTH http://127.0.0.1:8001/healthz -> 200 {'status': 'ok', 'mode': 'telnyx-key'}
HEALTH https://aqlmfmjrsk.a.pinggy.link/healthz -> 200 {'status': 'ok', 'mode': 'telnyx-key'}
ATTEMPT 6cf59482-84bf-463f-9645-de6c34dc01b1
JOB 3af1eb8f-af05-47ee-a98a-04332c8b6981
REGISTER -> 200 {'registered': True, 'attempt_id': '6cf59482-84bf-463f-9645-de6c34dc01b1'}
CLAIM ok: client_state_encoded=True
DIAL -> 200
CALL_CONTROL_ID v3:O7LKzMPHxe28iLJ3rRb2BZhGyusvmxLh994XrPKVOFJBhJa9H7ZwOA
ANSWERED=True state=answered after 7.0s
STREAMING_START -> 200 {'data': {'result': 'ok'}}
MONITOR: hangup observed at 35.0s
FINAL_STATE hangup DURATION_S 46.4
RECORD: no transcript/cost record found in log path
```
Callee report: answered personally, heard **no speech at all** during the 46s. Attempts table: state=hangup. Job: spent_cents=0, billing_reconciled=0 (no cost record was ever settled for any qualification call).

## Call #4 (Oct 30, 2026, 11:55am EDT) — webhook event timeline (from logs/webhook.db)
- 11:55:02 call.initiated
- 11:55:20 call.answered (voicemail system; callee did not pick up the ring)
- 11:55:23 streaming.started
- 11:55:27 streaming.stopped  (~3.8 seconds of media)
- 11:56:16 call.dtmf.received (callee, having joined from voicemail, pressing keys into silence)
- No call.hangup recorded (the VM was replaced mid-incident; the server died before/without processing it)

## The working probe (Oct 30, ~10:52am EDT) — no dial
Built the TTS path exactly as `build_live_pipeline` does (fresh phrase cache, real synthesis), queued the opening line the production way. Result: "Generating TTS" logged, TTFB 0.281s, AUDIO_FRAMES=2, AUDIO_BYTES=89,560 (~2.8s of 16kHz PCM), zero error frames. This probe is the control experiment: the synthesis chain works when driven this way. The live media session differs from the probe somewhere between the WebSocket connect and the pipeline run.

## Call #2/#3 log signatures (for pattern-matching)
- Call #2: `Service#0 exception (websockets/client.py): invalid Connection header: close` — the TTS service handshake (fixed).
- Call #3: NLTK `punkt_tab` LookupError raised from Pipecat's `match_endofsentence` during TTS aggregation of the FIRST bot turn (the opening) — i.e., in call #3 the opening WAS being processed by the pipeline and died in aggregation. Compare call #5, where no error and no speech were observed at all, and no journal entries exist for the window.

## Journal check for call #5's window
`journalctl -u ai-calling-live --since 12:00:40 --until 12:02:20` → "-- No entries --". The unit's journal has the 11:59 service startup (Pipecat banner, provider keys loaded) and then nothing for the call. The media session evidently logs somewhere else, or not at all, on this instance.
