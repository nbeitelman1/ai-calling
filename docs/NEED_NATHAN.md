# What Nathan actually needs to do — and nothing else

This is the complete list of Nathan-only steps for the self-hosted
AI calling build. Everything not listed here is Muse's work, or work
already done in the scaffold. No accounts, numbers, or keys have been
created yet, and none will be created by Muse on Nathan's behalf.

If a step below is not done, the build stops at the point noted —
it does not silently work around it.

## 1. Pick the carrier: Twilio or Telnyx

- Why only you: it's your account, your billing relationship, and
  your choice of carrier. Muse can implement either; it cannot pick
  your vendor for you.
- What to do: say "Twilio" or "Telnyx" in chat.
- If not done: the server transport cannot be implemented, and no
  number can be bought.

## 2. Pick the server: VPS or home server (prototype tunnel is fine for testing)

- Why only you: it's your hardware / hosting bill and your network.
  A laptop that sleeps will drop calls, so this has to be an
  always-on host with a stable public endpoint.
- What to do: say "VPS" (and which one, if you have a preference) or
  "home server" — or "prototype on a tunnel first, decide later"
  for the test call only.
- If not done: the server has nowhere to run for real calls. The
  test call can still be prototyped on a tunnel once you say so.

## 3. Create the accounts (carrier + STT + LLM + TTS)

- Why only you: account creation, identity/phone verification,
  and accepting terms are yours. Muse never creates accounts in
  your name.
- What to do: create accounts with the chosen carrier, Deepgram
  (STT, scaffold default), a fast streaming LLM provider, and
  Cartesia (TTS, scaffold default) — or tell Muse if you want
  different providers first.
- If not done: no keys can be issued, and no call can run.

## 4. Supply API keys — via the Secure Vault link only, never in chat

- Why only you: only you can paste a key into the secure capture
  page. Muse will issue the link when the server is ready for it.
- What to do: when Muse sends the Secure Vault link, open it and
  paste each provider key there. Do not paste any key, token, or
  secret in chat, email, or a file.
- If not done: the server cannot authenticate to any provider, and
  the test call cannot run.

## 5. Buy the dedicated AI number

- Why only you: it's a purchase on your carrier account.
- What to do: in the carrier dashboard, buy one new dedicated number
  for the AI caller. Do NOT port your personal number — the build
  default is a new number, so your personal line stays clean.
- If not done: there is no caller ID to dial from, and no number
  for callbacks to reach.

## 6. Confirm the monthly cap ($20 to start)

- Why only you: it's your money and your risk tolerance.
- What to do: confirm "$20/mo is fine" or name a different cap.
  The scaffold default is $20/mo, with a $5/call soft cap and an
  alert at 80%.
- If not done: the wallet ledger stays at the scaffold default and
  no real call will be dialled until you confirm — the cap is a
  safety gate, not a detail to assume.

## 7. Pick the voice by ear, on the test call

- Why only you: voice taste is yours, and the final voice is chosen
  by listening, not by a spec sheet.
- What to do: on the test call(s), listen to 2–3 stock feminine
  voices in the direction already approved (warm, confident,
  slightly sultry, slightly slower pacing) and say which one to lock
  in. No clones, no real-person voices — stock voices only.
- If not done: the voice stays unpicked, and no real call runs with
  an unapproved voice.

## 8. Approve the test call (exact job, before dialling)

- Why only you: the first dial, even to your own phone, needs your
  explicit go-ahead on the exact job.
- What to do: when Muse shows the test-call job (your own number,
  objective, bounds), approve that exact job in chat, be available
  to answer, to ask for a human, to decline a loop-in, and to let a
  call go to voicemail on cue (see `TEST_PLAN.md`, Phase A).
- If not done: no test call is placed, and Phase B cannot start.

## 9. Approve each first real business call (exact job, before dialling)

- Why only you: first real calls are per-call approval, not covered
  by a general "go ahead."
- What to do: for each of the first real calls, approve the exact
  job Muse shows you (number, business, objective, allowed
  decisions, max minutes) in chat, then review the transcript
  afterwards.
- If not done: that call is not placed. Looser, pre-approved job
  types only become possible later, after 2–3 real calls pass and
  you explicitly say so.

---

That's it. Nine items. No other Nathan action is required for this
build as specified — no bank-account connection (the wallet is a
local prepaid ledger with manual card top-ups only), no porting of
your personal number, no voice-clone upload, and no per-call
approval forever — only for the first real calls, until you loosen it.
