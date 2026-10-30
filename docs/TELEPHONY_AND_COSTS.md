# Telephony + cost setup — Twilio vs Telnyx for Nathan's self-hosted AI calling system

Date researched: Oct 27, 2026. Method: public web search + page text only.
No accounts created, no numbers bought, no keys entered, no payments made.

## Recommendation

**Use Telnyx as the primary carrier, with Twilio as the fallback if Telnyx's
streaming setup proves fiddly in the first test.**

Why Telnyx wins for *this* system (single concurrent call, personal use,
prepaid "nothing serious" wallet):
- Telephony is roughly half the price of Twilio on the published rates below,
and the monthly number is $1.00 vs $1.15.
- It is prepaid by design: you add funds, set a threshold + recharge amount,
and you can also set a daily spend limit on the outbound voice profile —
that is the closest thing either carrier offers to a hard wallet cap.
- Telnyx's own support docs say bank-account (ACH) auto-recharge is possible
(support-gated, and ACH settlement can be delayed, so a card is recommended
as backup). That matches Nathan's "hook it up to a wallet" request better
than Twilio, which is card-centric.
- Pipecat has an official Telnyx WebSocket integration (TeXML + media
streaming), so this is not an unsupported hack — it is a documented path.

Why Twilio is still a credible fallback:
- Pipecat's Twilio dial-out path is the simplest and best-documented:
server calls Twilio REST with TwiML, Twilio dials, then opens a WebSocket
media stream to the Pipecat server. More examples, more tutorials, and a
dev runner that handles the webhook for local testing.
- Twilio offers a $15 trial credit, which is useful for a first test call —
but trial accounts have restrictions, so do not assume a trial number can
call arbitrary business numbers until the account is upgraded and verified
(check this in the account; it was not verified from a primary source here).

The honest trade-off: Telnyx saves ~$0.008/min on telephony, which is
~$0.04 on a 5-minute call and ~$0.08 on a 10-minute call. That is real but
small at personal volume. If Telnyx's two-step streaming start or its
custom-parameter handling costs an evening of debugging, Twilio's simpler
path is worth the extra cents. Do not fight Telnyx for more than one test
evening before switching.

## Head-to-head

| Item | Twilio | Telnyx |
|---|---|---|
| US local number | $1.15/mo | $1.00/mo |
| Outbound US voice (PSTN leg) | $0.0140/min | ~$0.007/min total for Voice API ($0.002/min control + ~$0.005/min SIP termination; see note below) |
| Media streaming add-on | $0.0044/min (Media Streams) | $0.0035/min (media streaming, WebSockets) |
| Telephony + streaming, per minute | $0.0184/min | $0.0105/min (if the $0.007 and $0.0035 stack — see "Unverified" below) |
| Inbound local voice | $0.0085/min | From $0.0032/min (SIP inbound) |
| Recording (if used) | $0.0025/min + $0.0005/min/mo storage | $0.002/min |
| Billing model | Pay-as-you-go balance, drawn down by usage + monthly charges | Prepaid portal balance, pay-as-you-go, no minimum plan |
| Minimum payment / top-up | $20 upgrade balance is commonly reported; auto-recharge trigger as low as $10 | Minimum payment $10 reported in Telnyx support docs |
| Auto-recharge | Yes — configurable threshold + amount; reported default-on after upgrade in third-party write-ups, and a $5 low-balance email notification trigger | Yes — threshold + recharge, but only after a first manual payment; limited to 10 recharges in 24h; the charge brings the balance up to threshold + recharge, so it can exceed the nominal recharge amount if the balance fell far below threshold |
| Bank account (ACH) auto-recharge | Not the normal personal path; card is the standard method | Yes, per Telnyx support, but ACH access is granted by support, settlement can be delayed, and Telnyx recommends a card/PayPal on file as failover |
| Daily spend cap | Usage triggers are alerts only unless you wire a suspend webhook (third-party write-up) | Daily spend limit setting is reported on outbound voice profiles — verify in the portal, as this was a secondary source |
| Trial / free credit | $15 trial credit reported | Free signup; $25 is suggested as a starter top-up in a Telnyx onboarding guide (not a free credit) |
| Pipecat path | TwiML Bin / REST + `<Connect><Stream>` WebSocket; official Pipecat Twilio WebSocket docs; dial-out sequence documented | TeXML application + WebSocket media streaming (`bidirectionalMode="rtp"`); official Pipecat Telnyx WebSocket docs; dial-out via Call Control `answer` then `streaming_start` (two commands) |

### Sources for the table

- Twilio US local number $1.15/mo, outbound $0.0140/min, inbound local
$0.0085/min, SIP/client $0.0040/min: Cartesia's Twilio pricing summary,
reading Twilio's voice pricing page as current Aug 2026
and the same rates restated in a second
summary.
- Twilio Media Streams $0.0044/min: Twilio's own US voice pricing page,
as returned in search.
- Twilio $15 trial credit, no ongoing free plan: third-party summary
.
- Twilio balance behaviour (email at $5, suspension at $0, in-progress call
can complete into a negative balance): Twilio help centre
. Auto-recharge configuration and the
$10 lowest trigger: Twilio support
.
- Telnyx outbound voice ~$0.007/min ($0.002 control + SIP): Telnyx pricing
summaries and
; a second comparison gives the same
$0.007/min outbound figure.
- Telnyx number $1.00/mo, inbound from $0.0032/min, media streaming
$0.0035/min, recording $0.002/min: Telnyx's own voice-AI pricing table
.
- Telnyx prepaid + auto-recharge after first manual payment, and the
"threshold + recharge" charging behaviour, 10 recharges/24h limit:
Telnyx support and
. Minimum payment $10: Telnyx support
.
- Telnyx ACH auto-recharge (support-gated, delayed settlement, card
failover recommended): Telnyx support
and.
- Telnyx daily spend limit on outbound voice profiles: reported in a
Telnyx integration guide — secondary
source, verify in portal.
- Pipecat Twilio dial-out (REST + TwiML -> WebSocket -> bot):
Pipecat docs and the dial-out server
sequence.
- Pipecat Telnyx dial-in (TeXML -> WebSocket, `bidirectionalMode="rtp"`):
Pipecat docs. Telnyx Call Control
two-step streaming (`answer`, then `streaming_start`): third-party
Pipecat SDK notes — note the same notes
flag that Pipecat's `parse_telephony_websocket` drops Telnyx
`custom_parameters` from `call_data`, so the raw start frame must be
passed through if job metadata is needed on the stream.

## How a call flows into Pipecat (both carriers)

1. Muse writes and Nathan approves a call job (number, objective, bounds).
2. The self-hosted server asks the carrier to dial the target number.
3. The carrier answers the call setup and opens a WebSocket media stream
(8 kHz mu-law audio, the normal phone codec — not HD audio) to the
Pipecat server.
4. Pipecat runs: audio in -> Deepgram STT -> fast LLM -> Cartesia TTS ->
audio out, with voice-activity detection / interruption handling.
5. The server logs the transcript and estimated cost locally; Muse polls
or receives a webhook and reports back.
6. Loop-in is a *separate* outbound leg to Nathan's phone, bridged only
on his accept — it is not a native "transfer" on the WebSocket media
path. That is a design constraint to confirm in code, not a free
carrier feature: Pipecat's WebSocket telephony notes flag that the
WebSocket path lacks advanced call-centre features such as transfers
or reconnects (secondary source, Evalgent).

## Per-minute cost model (telephony + Deepgram + fast LLM + Cartesia)

Assumptions, stated so they can be corrected with real bills:
- One concurrent call, US numbers, outbound only. Server/VPS cost is
EXCLUDED (budget it separately; a small VPS is typically a few dollars
a month, but that was not sourced for this build).
- STT is billed on call minutes (the caller's speech plus silence/hold
while the stream is open), not only on seconds the caller is talking.
- LLM is the variable: it depends on how much transcript context is
re-sent each turn. The range below ($0.005–$0.02/min) covers a short,
tightly-scoped job on a cheap fast model up to a longer, context-heavy
job. It is an estimate, not a meter reading.
- TTS is billed on characters the agent speaks. Cartesia's own guidance
is 750–800 credits per minute of speech, at 1 credit per character.
The model below assumes the agent speaks ~50% of the call (the other
half is the other person, hold music, or silence), i.e. ~400 chars/min.
If the agent dominates the call, TTS rises toward $0.03/min.
- Taxes, carrier surcharges, and any regulatory fees are excluded.
Twilio rounds each leg up to the whole minute; Telnyx's per-second vs
per-minute rounding for this exact path was not confirmed from a
primary source (see "Unverified").

| Component | Low | Central | High | Basis |
|---|---|---|---|---|
| Telephony + streaming — Telnyx | $0.0105 | $0.0105 | $0.0105 | $0.007 + $0.0035 (see table) |
| Telephony + streaming — Twilio | $0.0184 | $0.0184 | $0.0184 | $0.0140 + $0.0044 |
| Deepgram Nova-3 streaming STT | $0.0048 | $0.0077 | $0.0077 | Regular $0.0077/min; promo $0.0048/min reported Oct 2026 — budget at regular |
| Fast LLM (e.g. gpt-4o-mini class) | $0.005 | $0.010 | $0.020 | $0.15/1M input, $0.60/1M output for gpt-4o-mini; token count per minute is the estimate |
| Cartesia TTS | $0.015 | $0.020 | $0.030 | $49/1M chars normalised (~$0.049/1k); ~400 chars/min at 50% agent talk time = ~$0.020/min |
| **Total — Telnyx stack** | **$0.035/min** | **$0.048/min** | **$0.068/min** | Sum of the column |
| **Total — Twilio stack** | **$0.043/min** | **$0.056/min** | **$0.076/min** | Sum of the column |

Component sources:
- Deepgram Nova-3 streaming $0.0077/min regular, $0.0048/min promo,
batch $0.0043/min: and
. Note one source warns Deepgram
pricing "moved several times in 2026" and to re-verify before
budgeting a year out.
- gpt-4o-mini $0.15/1M input, $0.60/1M output:
and.
Caution: OpenAI's GPT-4o / GPT-4.1 family is reported as leaving the
API on Jan 14, 2027, with a cheaper successor (gpt-6-luna, $0.10/1M
input, $0.50/1M output) — do not hard-code gpt-4o-mini as a permanent
choice; the pipeline must keep the LLM swappable.
.
- Cartesia 1 credit per character; 750–800 credits per minute of
speech; Pro $5/mo for 100,000 credits:.
Normalised $49/1M characters for Sonic 3.6:
. The $5/100k-credit plan implies
~$50/1M characters, consistent with that figure.
- Cross-check: an independent Telnyx + OpenAI Realtime build reports
~$0.07–$0.17/min all-in for a realtime model, and a Deepgram ->
fast-LLM -> Cartesia cascade at ~$0.03–$0.045/min
. Our central Telnyx figure ($0.048/min)
sits just above that cascade range because it uses the regular (not
promo) Deepgram rate and a fuller TTS allowance — that is deliberate
for budgeting.

### What a call costs (central estimate, telephony + AI only)

| Call length | Telnyx stack | Twilio stack |
|---|---|---|
| 5 minutes | ~$0.24 (range $0.18–$0.34) | ~$0.28 (range $0.22–$0.38) |
| 10 minutes | ~$0.48 (range $0.35–$0.68) | ~$0.56 (range $0.43–$0.76) |

Add the monthly number on top: $1.00 (Telnyx) or $1.15 (Twilio).
So a month with, say, ten 5-minute calls on Telnyx is roughly
$1.00 + 10 x $0.24 = ~$3.40 in carrier+AI costs, before server and taxes.
At the $20/mo local cap already set in `call_job.py`, the Telnyx stack
buys roughly 400 minutes (~80 five-minute calls) before the number fee —
the cap is generous for personal use and is not the binding constraint;
the per-call approval and job bounds are.

Do NOT use Telnyx's bundled Voice AI Agent ($0.05/min) or Twilio's
ConversationRelay ($0.07/min) as a shortcut in this build: both would
replace the self-hosted Pipecat pipeline Nathan asked for, and both are
priced on top of telephony in the Twilio case. The whole point of the
self-hosted stack is that the $0.048/min figure above *is* the pipeline.

## Prepaid / wallet setup — recommended settings

For Telnyx (primary):
1. Add funds manually first ($10 minimum payment reported; $25 is a
sensible first top-up — it covers the $1 number plus ~500 minutes at
the central rate, with headroom).
2. Only after that first payment, set auto-recharge: e.g. threshold $10,
recharge $20. Remember the Telnyx rule: the charge brings the balance
to threshold + recharge, so a deep dip can charge more than the
nominal recharge amount.
3. Set a daily spend limit on the outbound voice profile (verify the
exact control in the portal).
4. Turn on low-balance notifications to Nathan's email.
5. If ACH auto-recharge is wanted, ask Telnyx support to enable it, and
keep a card on file as failover — ACH settlement delay means a
card-only fallback is safer for a low-volume account.
6. Keep the local ledger in `call_job.py` as the second guardrail:
$20/mo local cap, $5/call soft cap, alert at 80%. The carrier balance
and the local ledger will not match to the cent (rounding, delayed
billing, taxes) — reconcile monthly, and trust the carrier bill over
the local estimate.

For Twilio (fallback, if used):
1. Expect auto-recharge to be offered/defaulted on after upgrade —
set the trigger deliberately (lowest reported trigger is $10) and do
not leave it at an unexamined default.
2. Usage triggers are alerts, not stops, unless a suspend webhook is
wired — so the local ledger cap is the real stop, not Twilio's.
3. Budget with per-minute rounding: a 2-minute-10-second call bills as
3 minutes per leg.

In both cases: use a low-limit card for provider billing, never a
primary bank account with no cap, and never put provider API keys in
chat, files, or logs — they go through Muse's Secure Vault connection
flow when the account is created (Nathan's step, not this research).

## Gotchas for a single-concurrent-call personal system

1. **Public endpoint required.** Both carriers need a public HTTPS/WSS
URL for webhooks and media streams. A tunnel (ngrok/tunl) is fine
for a prototype test call to Nathan's own phone; a sleeping laptop
or an unstable tunnel will drop real calls. Production needs a
small always-on server with a stable domain.
2. **8 kHz phone audio.** Expect mu-law telephony quality, not
studio audio. Voice choice should be tested over an actual phone
call, not over laptop speakers — a voice that sounds sultry on
headphones can sound muffled or hissy at 8 kHz.
3. **Hold music and phone trees eat minutes.** STT, telephony, and
streaming meters run while the agent waits on hold. A 10-minute
call that is 7 minutes of hold still costs ~10 minutes. Set a
max-hold / max-duration rule in the job.
4. **Voicemail: hang up, don't leave details.** Both the Twilio and
Telnyx patterns detect voicemail poorly on a raw media stream
unless answering-machine detection is added (Twilio charges
$0.0075 per AMD use). The safe default in this build is: if it is
clearly voicemail, end the call and report back — do not leave an
AI voicemail with account details.
5. **Loop-in is a second leg, not a transfer.** On the WebSocket media
path, bridging Nathan in means dialling him separately and bridging
audio in the server. Budget a second telephony leg for loop-in
minutes, and test the bridge before relying on it for a real call.
6. **Spam / STIR-SHAKEN labelling.** A new number calling businesses
repeatedly, especially with an AI voice, can be flagged. Keep volume
low, one call at a time, only to numbers Nathan directed, and use
the approved disclosure opening — it also protects the number's
reputation.
7. **SMS is a separate compliance track.** If the AI number ever sends
texts, US local numbers need A2P 10DLC registration (Twilio) or the
Telnyx equivalent. Voice-only calling avoids that track; do not
assume the voice number can text without registration.
8. **Recording is not free and not automatic.** The transcript in this
build comes from the live STT stream, logged locally — that is not
the same as a carrier call recording, and it avoids Twilio's
$0.0025/min recording fee. But recording/transcribing still needs
the disclosure Nathan approved ("Nathan's assistant calling on his
behalf"), and Florida is an all-party-consent state for recording.
9. **Delayed billing.** Both carriers warn, in effect, that charges can
land after the call. Do not treat the local ledger as a real-time
bank balance; treat it as an estimate with a monthly reconciliation.
10. **Provider pricing moves.** Deepgram's rates in particular were
reported as changing through 2026. Re-check Deepgram, Cartesia,
and the carrier rate card on the day the account is created, and
update this file with the actual billed rates after the first
test call.

## Unverified without an account (do not treat as settled)

- Whether Telnyx's $0.007/min outbound figure and $0.0035/min media
streaming fee stack additively on the same call, or whether the
Voice API fee already includes streaming in some configurations.
The file budgets them as additive (conservative). The first Telnyx
invoice will settle this.
- Telnyx per-second vs per-minute rounding on the Voice API + SIP +
streaming path, and the exact behaviour of its daily spend limit
control (secondary sources only).
- Whether Telnyx charges a 3% fee on card/PayPal payments generally, or
only for customers with ACH access — the support article states a 3%
fee in the ACH context; the scope was not confirmed.
- Twilio trial-account restrictions (which numbers a trial number may
call) and the exact Twilio upgrade minimum in the current console.
- Cartesia's effective per-character cost under the plan Nathan would
actually buy (credit buckets vs pay-as-you-go), and whether the
chosen stock voice carries any premium-voice surcharge.
- The fast LLM's true token burn per minute for Nathan's actual jobs
(system prompt length, transcript re-send policy, and turn count all
move this). The $0.005–$0.02/min range is a planning band, not a quote.
- Server/VPS cost, domain cost, and tunnel cost for the production
endpoint — excluded from the per-minute figures above.
- Whether a personal cell number could be used as Telnyx/Twilio caller
ID without porting. The build assumes a new dedicated number; do not
promise personal-number caller ID until a carrier confirms it in
the account.

## Sources (full URLs, verbatim from search results)

- https://www.cartesia.ai/learn/twilio-pricing
- https://www.twilio.com/en-us/voice/pricing/us
- https://help.twilio.com/articles/223135487-How-Twilio-billing-works
- https://support.twilio.com/hc/en-us/articles/223135607-How-to-Configure-and-Remove-Auto-Recharge-Triggers-on-Twilio
- https://telnyx.com/pricing/voice-ai-agents
- https://support.telnyx.com/en/articles/4280500-billing-setup-billing-groups
- https://support.telnyx.com/en/articles/7045419-ach-direct-debit-payment-method
- https://docs.pipecat.ai/pipecat/telephony/twilio-websockets
- https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets
- https://www.retellai.com/blog/best-speech-to-text-models
- https://www.forasoft.com/blog/article/7-best-ai-tools-to-elevate-audio-apps
- https://tech-insider.org/ca/elevenlabs-vs-openai-vs-google-gemini-tts-2026/
- https://aihustleworld.com/2026/08/elevenlabs-vs-alternatives.html
