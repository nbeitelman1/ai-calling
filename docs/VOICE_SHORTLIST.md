# Voice shortlist — Nathan's self-hosted AI calling system

Date researched: Oct 27, 2026
Method: public web search + page text only. No sign-ups, no purchases, no accounts created.
Brief: feminine, warm, slightly sultry in vibe. **NOT a clone of Megan Fox, Madison Beer, or any real person. No celebrity clones were searched for or included.**

## How to use this list

1. After the TTS account is created (Nathan's step, via Secure Vault — never in chat), audition the candidates below with the **same test script** (see bottom).
2. Pick by ear on a test call to Nathan's own phone. Do not pick by name or by a written description alone.
3. Lock the winner's voice ID into `~/workspace/ai-calling/server/voice_config.json`.
4. Keep the disclosure line from `ARCHITECTURE.md` unchanged: "Hi, this is Nathan's assistant calling on his behalf…" — the voice is tuned for warmth, not for impersonating a human or a real person.

## Pipecat compatibility (verified from Pipecat's service list)

Pipecat lists Text-to-Speech services including Cartesia, Deepgram, and ElevenLabs, with install extras such as `pipecat-ai[cartesia]`.
Source: https://github.com/jianjungki/pipecat

That means all three providers below can be used as the TTS service in the Pipecat pipeline, with the provider-specific voice parameter:
- Cartesia: `voice_id` (UUID)
- ElevenLabs: `voice_id`
- Deepgram Aura: `voice` / model string such as `aura-2-stella-en`

## User-suggested candidate (Nathan, Oct 27, 2026 — check the page, then audition)

### ElevenLabs — Natasha — "Sultry Velvety Magnetic Versatile"
- **Voice ID (verified from the official ElevenLabs page Nathan sent):** `kCx3Qoh3lfILbbTZftSq`
- **Page:** https://elevenlabs.io/voices/kCx3Qoh3lfILbbTZftSq
- **Description on the page:** captivating female American voice, good for versatile use — lectures, audiobooks, storytelling, meditation, voice acting, commercial. Category: Narrative Story. Tags: Sultry, Velvety, Magnetic, Versatile.
- **Type:** Community Voice Library voice (public shared voice, not marked as stock or as a user clone on the page). Usage shown on the page: 5.9K users, 7.8M characters generated. Languages: English, Norwegian, plus 16 more (18 total).
- **Why it fits:** this is the most on-brief tagline found so far — sultry + velvety + magnetic — and it's a voice Nathan himself picked, so it goes on the audition list ahead of the mirror-sourced candidates.
- **Cautions before use:** (1) Category is Narrative Story, so test it on the business-call script, not a narration sample — a storytelling voice can feel too performative on a phone call. (2) Confirm the community voice's licence/commercial-use terms in the official ElevenLabs library at account creation before using it in the calling system. (3) Preview is available on the page (play button + generate-your-own-speech section) — listen there first, then decide if it makes the test-call shortlist.
- **Confidence:** HIGH for name, ID, tags, and community-voice type (official page, read Oct 27, 2026, read-only, no sign-in/purchase). Licence terms: UNVERIFIED — check at account creation.

## Top 5 — audition first

### 1. Cartesia — Skylar — FIRST CHOICE TO TEST
- **Voice ID (verified in Cartesia's own docs example):** `db6b0ed5-d5d3-463d-ae85-518a07d3c2b4`
- **Official description:** "Approachable American female ideal for customer care and support." Tagline: "Friendly Guide." Gender: feminine. Language: en, en-US native.
- **Why it fits:** Cartesia itself lists Skylar among its top stable English female voices for voice agents (with Gemma, Jacqueline, Emma, Lauren), and stable/realistic voices are what Cartesia recommends for production agents over studio-emotive voices. American accent, customer-care framing, and a "friendly guide" tagline are the closest stock match to warm + confident without drifting into character-voice territory.
- **Tuning for the sultry direction:** try slightly slower pacing and a lower, warmer delivery in the Pipecat/Cartesia voice settings; do not push it into breathy character territory on business calls.
- **Pricing note (third-party summary, verify on Cartesia's pricing page at account creation):** Sonic billed at 1 credit per character; reported overage roughly $0.038–$0.065 per 1,000 characters depending on plan; plans reported as Pro $5/mo, Startup $49/mo, Scale $299/mo. Treat these as planning figures only.
- **Confidence:** HIGH for the ID and description (Cartesia docs example). Pricing: MEDIUM (third-party summary, not the official pricing page).

### 2. ElevenLabs — Ivanna — CLOSEST "SULTRY" DESCRIPTION
- **Voice ID (from a third-party mirror of the ElevenLabs library — verify in the official ElevenLabs Voice Library before use):** `YgzytRZyVmEux6PCtJYB`
- **Description found:** "A smooth, sultry and captivating voice that conveys confidence, allure, and sophistication. Perfect for roles requiring a sultry, mysterious, hypnotic or elegant vibe." English, American, female, middle-aged, best for conversational. Published Jan 2025; reported 54.1M characters generated in the last year and 27k users adding it.
- **Why it fits:** This is the most on-brief written description found for warm + sultry + confident, and it is tagged conversational rather than narration-only, which matters for phone calls.
- **Caution:** "Sultry / hypnotic / elegant" on a business call can tip into too much. Audition it with the actual business-call script, not a narration sample, and judge whether it stays professional.
- **Pricing note (third-party summary, verify on ElevenLabs pricing at account creation):** API rates reported as $0.08 per 1,000 characters for Eleven v4 / v3, $0.04 per 1,000 for v4 Turbo / Flash / v3 Conversational. Plans reported as Starter $6/mo, Creator $22/mo, Pro $99/mo. Free plan is non-commercial — do not use it for this system.
- **Confidence:** MEDIUM for the ID (mirror, not elevenlabs.io). Description and pricing: MEDIUM (third-party summaries).

### 3. ElevenLabs — Sloane — SULTRY BUT CALMER
- **Voice ID (third-party mirror — verify in the official ElevenLabs Voice Library before use):** `BAMYoBHLZM7lJgJAmFz0`
- **Description found:** "Sultry, confident Canadian voice with a smooth, seductive tone and modern elegance… refined, warm, and unforgettable." English, Canadian, female, middle-aged, calm tone, best for conversational. Published May 2025.
- **Why it fits:** Same sultry direction as Ivanna but described as calm rather than mysterious/hypnotic, which may land better with a business recipient. Canadian accent is a mild difference from the US voices above — audition for accent preference.
- **Pricing note:** Same ElevenLabs API rates as Ivanna above.
- **Confidence:** MEDIUM (mirror source, verify ID in the official library).

### 4. ElevenLabs — Kara — WARM, INTIMATE, UNHURRIED
- **Voice ID (third-party mirror — verify in the official ElevenLabs Voice Library before use):** `e5GiMv0F86WAieIslrRJ`
- **Description found:** "A warm, intimate feminine voice characterized by its deep resonance and breathy, soulful undertones… an 'unhurried' quality that invites the listener to lean in." English, American, female, middle-aged, best for narration. Published Jan 2026.
- **Why it fits:** Warm + deep + breathy is the other half of the brief (warmth, not just sultriness). It may be the best fallback if Ivanna/Sloane feel too "performance" on a business call.
- **Caution:** Tagged narration-first, so test pacing carefully — an unhurried narration voice can feel slow on a phone call. Try a slightly faster delivery setting if it drags.
- **Pricing note:** Same ElevenLabs API rates as above.
- **Confidence:** MEDIUM (mirror source, verify ID in the official library).

### 5. Cartesia — Jacqueline — STABLE AGENT VOICE, ID TO CONFIRM IN-ACCOUNT
- **Voice ID:** NOT verified from a public official source in this research. Do not use a guessed ID.
- **Why it is on the list anyway:** Cartesia lists Jacqueline among its top stable English female voices for voice agents, and a separate voice listing tags a Cartesia voice named Jacqueline as "Reassuring Agent." That combination — stable + reassuring — is exactly what a business call needs, and it may beat Skylar on warmth once heard.
- **How to get the ID:** after the Cartesia account exists, call `GET https://api.cartesia.ai/voices` with the account key and filter by name "Jacqueline" (Cartesia's List Voices API returns id, name, tagline, description, gender, language, accents). That is the authoritative step; do not copy an ID from a blog.
- **Pricing note:** Same Cartesia Sonic figures as Skylar above.
- **Confidence:** MEDIUM for the name/recommendation (Cartesia docs). ID: UNVERIFIED — in-account lookup required.

## Fallbacks — Deepgram Aura (if Cartesia/ElevenLabs don't land)

Deepgram Aura is less expressive by design — it optimises for clarity and low latency in enterprise/conversational use — so these are fallbacks, not first picks for a sultry direction. All are feminine, English, and usable in Pipecat as Deepgram TTS voices.

- **aura-2-stella-en (Stella)** — described in third-party summaries as "Warm, friendly" (also "energetic" in one summary). Best Aura fallback for warmth.
- **aura-2-luna-en (Luna)** — described as "Calm, soothing" / "Polite, soft." Best if the winner needs to be dialled back to calm/professional.
- **aura-2-athena-en (Athena)** — described as "Smooth, professional female." Pipecat example projects use this as a Deepgram default. Best if clarity beats warmth on a given call type.
- **Pricing note (third-party summary, verify on Deepgram pricing):** Aura-2 reported at $0.030 per 1,000 characters pay-as-you-go ($0.027 on Growth); Aura-1 at $0.015 per 1,000. Deepgram is also the STT provider in the planned stack, so using Aura would mean one vendor for both directions.
- **Confidence:** MEDIUM for the model strings (multiple third-party summaries agree); style words are third-party, not Deepgram's own voice library copy.

## Also worth auditioning after the account exists (not top 5)

- **Cartesia — Katie** — en-US female, tagline "Friendly Fixer" in a voice listing; ID `f786b574-daa5-4673-aa0c-cbe3e8534c02` appears only in a third-party showcase, not in Cartesia's own docs found here — verify via the in-account voice list before use.
- **Cartesia — Gemma** — en-GB female, Cartesia stable-agent list; ID `62ae83ad-4f6a-430b-af41-a9bede9286ca` appears only in a third-party showcase — verify in-account. British accent may or may not be wanted.
- **Cartesia — Sophie** — ID `95856005-0332-41b0-935f-352e296aa0df`, described as "Warm, friendly" female, appears only in a third-party voice-agent guide — verify in-account.
- **ElevenLabs — Eden** — ID `M8391pS6bItq6N4ptCRa` (third-party mirror, verify in official library). Described as "warm, deep, and slightly raspy… smooth, expressive, and cinematic." Good if Nathan wants raspier/warmer over sultry.

## Audition test script (use the same text for every candidate)

Read this aloud through the pipeline, then on a test call to Nathan's own phone:

> "Hi, this is Nathan's assistant calling on his behalf about his GAP claim status. Is now an okay time to talk through that? I wanted to check whether the packet is complete, and whether there's a confirmation number I should write down. My number for a callback is — I'll give that at the end if you need it."

Score each voice 1–5 on: warmth, professionalism on a business call, intelligibility of numbers/names, pacing (too fast / too slow), and whether it sounds like an assistant rather than a character performance. The winner is the highest professionalism + warmth combined score, not the sultry score alone.

## What could NOT be verified without an account

1. **Full Cartesia voice catalog and IDs for Jacqueline, Katie, Gemma, Sophie.** Cartesia's public docs confirm the voice-list API and give Skylar as a worked example, but the full 500+ voice library is behind an account. The authoritative lookup is `GET https://api.cartesia.ai/voices` after the account/key exists.
2. **ElevenLabs Voice Library IDs, audio samples, and per-voice commercial terms.** The IDs above come from a third-party mirror (json2video) of the ElevenLabs library, not from elevenlabs.io pages fetched in this research. Verify each ID, listen to the sample, and confirm the voice's sharing/licence terms in the official ElevenLabs Voice Library before using it in the calling system.
3. **Exact current pricing.** All pricing figures above are third-party summaries from Oct 2026 pages. Providers change list prices and plan quotas. Confirm Cartesia, ElevenLabs, and Deepgram pricing on their official pricing pages at account creation, and record the confirmed rate in `voice_config.json` alongside the chosen voice.
4. **How each voice actually sounds on a phone line.** No public description substitutes for hearing it through the Pipecat pipeline at 8 kHz telephony audio, with the disclosure line and a real business-call script. That test call is the deciding step, and it requires the account + number + server steps that are still Nathan's to do.
5. **Latency and cost per real call.** Per-character TTS rates do not include telephony minutes, STT, or LLM tokens. The local wallet ledger in `call_job.py` is the place to log the measured all-in cost per call once test calls run.

## Sources

- Cartesia — Choosing a Voice (stable vs emotive lists, 500+ voices): https://docs.cartesia.ai/build-with-cartesia/capability-guides/choosing-a-voice
- Cartesia — List Voices API (Skylar worked example, id + description + gender): https://docs.cartesia.ai/api-reference/voices/list
- Cartesia voice showcase (Katie / Gemma IDs — third-party, unverified): https://www.anygen.io/showcase/cartesia/index.html
- Cartesia voice-agent guide (Sophie ID + warm/friendly — third-party, unverified): https://edesy.in/docs/voice-agent/tts/cartesia
- Pipecat services list (Cartesia / Deepgram / ElevenLabs TTS): https://github.com/jianjungki/pipecat
- ElevenLabs library mirror — Ivanna: https://json2video.com/ai-voices/elevenlabs/voices/YgzytRZyVmEux6PCtJYB/
- ElevenLabs library mirror — Sloane: https://json2video.com/ai-voices/elevenlabs/voices/BAMYoBHLZM7lJgJAmFz0/
- ElevenLabs library mirror — Kara: https://json2video.com/ai-voices/elevenlabs/voices/e5GiMv0F86WAieIslrRJ/
- ElevenLabs library mirror — Eden: https://json2video.com/ai-voices/elevenlabs/voices/M8391pS6bItq6N4ptCRa/
- ElevenLabs pricing summary (third-party, Oct 2026): https://devtoollab.com/blog/elevenlabs-alternatives
- Cartesia pricing summary (third-party, Oct 2026): https://www.forasoft.com/blog/article/real-time-voice-cloning-technology
- Deepgram Aura voice lists (third-party summaries): https://deepwiki.com/deepgram-devs/deepgram-demos-rust/3.3.4-voice-models and https://dev.to/gaatif/elevenlabs-deepgram-aura-text-to-speech-implementation-guide-in-net-8-15ji
- Deepgram Aura pricing summary (third-party, Oct 2026): https://omidsaffari.com/blog/deepgram-pricing
