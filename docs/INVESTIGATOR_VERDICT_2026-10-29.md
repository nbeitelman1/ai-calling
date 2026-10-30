# Investigator verdict — Self-Hosted AI Calling (Oct 29, 2026)

**Ingested Oct 29, 2026, 4:53pm EDT via the WhatsApp side chat, at Nathan's
direction ("ingest and save to files, no response necessary"). No call was
placed, no service was changed, and no live work was done on the basis of
this file.**

## Provenance

- Original: `~/workspace/user/files/Self_Hosted_AI_Calling_Investigation_Handoff_2026-10-29.pdf`
  (117 KB, 9 pages, PDF 1.3)
- Model on the export: azure/gpt-6-astra; created/exported Oct 29, 2026;
  investigator turn timestamped 4:48pm.
- Full text extraction (pdftotext, layout preserved):
  `INVESTIGATOR_VERDICT_2026-10-29.txt` in this same directory.
- Companion document (Muse's own handoff, unchanged):
  `INVESTIGATOR_HANDOFF_2026-10-29.md` — the 4:46pm measurement + inbound-filter
  restart described there is the state the investigator audited.
- **Investigator's own scope limit, stated in the verdict:** it reviewed
  provider documentation, downloaded Pipecat 1.8.0, inspected its source,
  and ran credential-free tests. It did **not** inspect the deployed files,
  access the VM, change anything, or place a call. Deployment-specific
  conclusions below are therefore conditional until checked against the
  deployed code.

## Headline verdict (investigator's words, condensed)

**Pause the next measurement call until the inbound filter is checked.**
The handoff contains a likely track-name mistake that could discard all
caller audio, and the investigator reproduced an additional VAD issue in
published Pipecat 1.8.0 that makes detection depend on incoming buffer
boundaries. Measurement-first remains the right approach, but the
measurement path is not yet trustworthy.

## Findings at a glance

| Finding | Investigator assessment |
|---|---|
| New filter accepts `inbound_track` | Likely blocking defect: Telnyx documents `media.track` as `inbound` |
| Pipecat VAD depends on buffer boundaries | Reproduced in published 1.8.0 state machine |
| Earlier sample-rate pinning diagnosis | Supported by the release source |
| Narrowband speech needs lower thresholds | Still a hypothesis — not established |
| Empty transcript proves OpenAI received no usable audio | Incorrect inference: audio might never have been committed |
| Broken application-side hangup is minor | Should be elevated before another call |

## 1. Highest priority: track-name mismatch (introduced AFTER call 9)

Two different Telnyx fields use different values:

| Location | Caller-audio value |
|---|---|
| Call Control request: `stream_track` | `inbound_track` |
| Incoming WebSocket event: `media.track` | `inbound` |

The handoff says the newly deployed filter "drops media events whose track
is not None/`inbound_track`". If that describes the actual code, and no
earlier wrapper normalizes the field, valid `inbound` events will all be
dropped. Offline evaluation of that exact rule: `inbound` → No,
`outbound` → No, `inbound_track` → Yes, missing/None → Yes.

This potential regression was introduced after call 9. It could break the
next call, but it cannot explain the earlier failures.

Before dialing, per the verdict: inspect the raw-event-to-filter path
(not just the filter in isolation); a raw event with `media.track ==
"inbound"` must reach the decoder; `outbound` must not reach the inbound
resampler or VAD; missing/unknown tracks need an explicit, logged policy
(do not silently treat them as caller audio on a dual-track stream);
count events before and after filtering; verify tests use Telnyx's actual
wire-format values, not only internally invented fixtures.

Note from the verdict: receiving only the inbound track does not
inherently disable bot playback — track selection controls what Telnyx
sends to the application; bidirectional streaming separately enables
application-to-Telnyx media.

## 2. Pipecat 1.8.0 VAD is sensitive to buffer boundaries (new finding)

In the inspected 1.8.0 `VADAnalyzer._run_analyzer`: process every complete
32 ms window in the incoming buffer, update tentative counters, and only
**after the loop finishes** check whether to confirm SPEAKING or QUIET.
A qualifying speech run can therefore reach six windows, then be reset by
a failing window within the same incoming buffer, before speech is
confirmed.

Offline reproduction (state-machine tests, not speech-recognition tests):
six passing windows followed by two failing windows, one window per call
→ SPEAKING observed; the same sequence in 3,032-byte input buffers → no
SPEAKING; sustained passing confidence in 3,032-byte buffers → SPEAKING.

The handoff's reported 3,032-byte frame is 94.75 ms of mono PCM16 at
16 kHz — almost three VAD windows — so this is relevant to this pipeline.
Consequence: "six consecutive passing chunks are sufficient" is
incomplete for this release; where chunks fall relative to input-buffer
boundaries also matters.

Before reducing thresholds, per the verdict: add a packetization-invariance
regression test (identical audio, different input chunk sizes); observe
internal window scores, counters, and state, not only the state returned
after an entire buffer; evaluate bounded re-chunking or a narrowly
reviewed state-machine correction in the test harness first; do not
immediately edit the installed library or broadly upgrade Pipecat.
Caveat: 1.8.0 resets Silero's recurrent state on a five-second wall-clock
schedule, so fast offline replay and real-time streaming can exercise
different reset behavior — record reset timing or control it in the
diagnostic harness.

## 3. Evidence does not yet isolate "narrowband VAD confidence"

The sample-rate correction is supported (Pipecat preserves a
constructor-provided sample rate when `set_sample_rate()` runs; removing
the incorrect 8 kHz pin is a valid correction for a 16 kHz stream).

What the counters establish: WebSocket media messages reached the
application; decoded frames reached the pipeline; no confirmed speech
event was observed; no final transcript was recorded.

What they do not establish: frames contained intelligible caller speech;
the correct track survived filtering; codec, channel count, and actual
sample rate matched the labels; audio ordering and duration were
preserved; audio reached OpenAI; OpenAI received a commit; the
recording/transcript tap captured every relevant downstream event.

Telnyx notes (per the verdict): the `start.media_format` event identifies
encoding, rate, and channels; `media` contains raw codec payloads without
RTP headers; media ordering is not guaranteed.

Better diagnostic controls before blaming thresholds: a real-speech
positive control through the same decoder, resampler, and VAD; a
silence/noise negative control; correct duration accounting across each
stage; an intelligibility check on a consented, protected diagnostic
sample, if audio capture is approved. Measure total samples, not just
frames. Instrumentation must record the confidence already computed —
do not invoke Silero a second time merely to log it, as it is stateful.

## 4. OpenAI commit boundary

Pipecat's local-VAD mode sends audio to OpenAI, then commits the buffer
when it receives `VADUserStoppedSpeakingFrame`. Without that event,
buffered audio may never produce a finalized transcription. "Connected
and ready" is not a transcription-path test.

Boundaries to trace: session configuration (accepted model, audio format,
turn-detection settings from `session.updated`); audio transmission
(append count, decoded bytes, send failures); turn completion (local stop
event and commit request); server acceptance
(`input_audio_buffer.committed`); transcription (delta, completed,
failed, API error events, correlated by `item_id`); application delivery
(`TranscriptionFrame`, transcript tap, aggregator receipt).

Controlled diagnostic (needs separate approval for any provider usage):
send a known speech fixture through the same STT session and issue one
explicit commit, without dialing anyone. If transcription succeeds,
session configuration, audio submission, commit, and transcript handling
work for that fixture; if it fails, local VAD is not the only unresolved
issue. This is a diagnostic, not a recommendation to deploy timer-based
commits that might split people mid-sentence.

Server VAD is a documented alternative: OpenAI's transcription-session
reference lists both `gpt-4o-mini-transcribe` and `server_vad` — a
configuration to validate, not a reason to assume a model change is
necessary. Acceptance by the exact live session remains untested; the
older session-creation endpoint is deprecated, so preserve the current GA
session format. If approved: use server VAD as the turn-boundary
authority, remove the competing local VAD processor from that
configuration, verify Pipecat's external turn-strategy integration, and
keep the text LLM and Jessica TTS unchanged. Pipecat documents this mode
and warns against retaining a separate VAD processor alongside it. No
switch to a speech-to-speech assistant is needed to try server-side
transcription VAD.

## 5. Hangup and duration enforcement — elevate before another test

- Telnyx dial supports `time_limit_secs` (measured after answer, range
  30–14,400 s); Telnyx terminates the call at the limit and emits
  `call.hangup` with cause `time_limit`. For a short diagnostic, use an
  explicitly approved short limit within the existing ten-minute ceiling.
  This protects against an application stall but does not replace
  provider-session cleanup.
- Proxy failure explanation: Pipecat 1.8.0's serializer creates
  `aiohttp.ClientSession()` without proxy configuration; aiohttp does
  not read proxy environment variables by default (`trust_env=True` or
  explicit proxy configuration is needed). Route hangup through the
  approved proxy path, use bounded timeouts, and verify termination —
  not merely that a hangup request was attempted.
- A duration limit is not a $1 spending guarantee: the budget must
  include telephony, STT, LLM, and TTS usage, with bounded generation and
  shutdown behavior. Balance deltas and estimated cents do not
  demonstrate an enforced cap.
- Consent before measurement, not only before business calls: for any
  approved audio capture or transcription test, confirm Lauren's informed
  consent beforehand. Florida's general consent provision refers to prior
  consent from all parties; the opening "Nathan's assistant" sentence
  alone does not establish consent to transcription or recording.
  Separately, the FCC treats AI-generated human voices as artificial
  voices under the TCPA, with applicable consent requirements and
  exemptions; using a stock voice does not remove those obligations.
  This is a compliance checkpoint, not a conclusion that the earlier
  tests were unlawful.

## Revised next-step order (investigator's)

1. Inspect the deployed filter and add raw Telnyx wire-format tests.
   Resolve `inbound` vs `inbound_track` before any dial.
2. Validate the VAD test harness: real speech, silence, buffer-size
   invariance, controlled reset behavior.
3. Add instrumentation through the STT commit and completion boundaries.
   Log metadata and counts without exposing credentials or raw audio.
4. Verify termination and budget safeguards: fix the hangup path and
   include a carrier-side duration limit.
5. Only then request approval for one bounded measurement call.
   Recheck the existing tunnel, use a fresh attempt, ensure media
   registration is current. Treat the handoff's 4:46pm restart as its
   latest reported state; do not restart again reflexively.
6. Choose the next change from the earliest failing boundary:

| Observed failure | Next focus |
|---|---|
| Raw inbound events arrive, accepted frames do not | Track filter |
| Accepted audio is distorted, silent, or time-expanded | Decode, ordering, resampling, track separation |
| Intelligible audio reaches VAD, but speech events fail | State-machine behavior, then thresholds |
| Audio is appended, but no commit occurs | Turn-completion path |
| Commit is acknowledged, but transcription fails | STT configuration and provider errors |
| Transcript exists, but no response follows | Aggregator, LLM, and response delivery |

## Bottom line (investigator's)

Measurement-first remains the right approach, but the measurement path is
not yet trustworthy. Check the likely track-filter regression, account
for the reproduced Pipecat buffer-boundary behavior, and trace OpenAI
commits before spending another call on threshold tuning.

---

Full verdict text: `INVESTIGATOR_VERDICT_2026-10-29.txt`
Original PDF: `~/workspace/user/files/Self_Hosted_AI_Calling_Investigation_Handoff_2026-10-29.pdf`
Prior handoff (unchanged): `INVESTIGATOR_HANDOFF_2026-10-29.md`
