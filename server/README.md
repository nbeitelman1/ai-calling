# Live Server

`live_server.py` hosts Telnyx webhooks and the media WebSocket. The production
pipeline is Telnyx PCMU -> Silero VAD -> Groq Whisper -> Gemini -> ElevenLabs.

Install from `requirements.txt`, provision `GROQ_API_KEY`, `GEMINI_API_KEY`,
`ELEVENLABS_API_KEY`, and `TELNYX_API_KEY` through the approved credential
bridge or the deployment-local `.provider-credentials` EnvironmentFile, then
install the hardened systemd units. The server never dials; a
separately approved parent registers one expiring media job and submits the
canonical adapter's one-shot Telnyx request.

No call is authorized by installation alone. `tools/dial_measure_20261029.py`
requires a freshly verified `AI_CALLING_PUBLIC_HOST` and the per-call approval
sentinel `AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL`.

`provider_preflight.py` authenticates with provider model/voice listing APIs
without generating audio or text and writes the receipt consumed by `/readyz`.
`tools/deploy_verify.ps1` runs the full offline suite, provider preflight, and
local health/readiness checks. It never dials.

`tools/deploy_linux.sh` builds a replacement venv from the hash-locked
`requirements.lock`, runs tests before activation, rotates the old venv for
rollback, installs the systemd masters, and verifies health/readiness. It never
dials and refuses to run without all deployment-local environment files.

Copy `provider-credentials.example` to the deployment-local
`.provider-credentials`, fill it outside version control, and set mode 0600.
The public hostname must be fixed; `.lhr.life` and URL-shaped values fail
readiness. Quota defaults in the systemd master are conservative placeholders
and must be compared with the current provider consoles before deployment.

The service prestart authenticates, lists models/voice without generation,
prewarms missing fixed phrases once as raw `pcm_16000`, and prunes JSONL call
records older than 30 days. `/readyz` is observational and requires a recent
preflight receipt, all five valid cached phrases, configured quota ceilings, a
fixed non-`.lhr.life` hostname, writable storage, and every credential.
