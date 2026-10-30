# Persona, Memory, and Control — Intelligence Pass Spec

Status: approved by Nathan ("full way", Oct 30, 2026). This document is
the design contract for the intelligence pass: what the calling
assistant *is* (persona), what it *remembers* (memory plane), and what
decides *what memory reaches the conversation, and when* (control
plane). Implementation lives in `server/persona.py`,
`server/profile_store.py`, `server/call_controller.py`,
`server/conversation_state.py`, and `playbooks/`.

Design anchors (Nathan, Oct 30, 2026): the assistant should have "a
memory profile" and the persona should be built from Dale Carnegie's
*How to Win Friends and Influence People* — one of your main skills:
influence, memory, helpfulness. All persona text in code is paraphrase
written for this system; the book's principle *names* are referenced,
never its passages. Influence here never becomes deception: the
standing honesty rules (verbatim disclosure opening, verbatim
honest-AI answer, verbatim hand-back line) outrank every persona
behavior.

## 1. Three layers

```
        ┌──────────────────────────────────────────────────┐
        │ PERSONA CORE (server/persona.py)                 │
        │ Who the assistant is on every call: Carnegie-    │
        │ derived conduct + Nathan's honesty rules.        │
        │ Static text, versioned, identical on all calls.  │
        └──────────────────────────────────────────────────┘
        ┌──────────────────────────────────────────────────┐
        │ CONTROL PLANE (server/call_controller.py)        │
        │ System-One-style fast decisions: assemble the    │
        │ brief before the call, route context between     │
        │ turns under a hard latency budget, type and      │
        │ file what was learned after the call. The big    │
        │ LLM never makes memory decisions mid-call.       │
        └──────────────────────────────────────────────────┘
        ┌──────────────────────────────────────────────────┐
        │ MEMORY PLANE (server/profile_store.py)           │
        │ Temporal profile store: entities, facts with     │
        │ validity windows, episodes with provenance,      │
        │ commitments with lifecycle. SQLite at this       │
        │ scale; Graphiti's model, faithfully reduced.     │
        └──────────────────────────────────────────────────┘
```

The conversation LLM (fast/frontier Gemini ladder, unchanged) only
ever sees a **frozen brief**: persona core + one playbook + one
profile slice + the call's objective, assembled once before the
conversation starts. Nothing in the live audio path waits on memory.

## 2. Persona core

Behavioral paraphrase of Carnegie's principles, grouped the way the
book groups them (fundamentals, making people glad they took the
call, winning cooperation, leading without giving offense):

- Never criticize, condemn, or complain — about the other party,
  their systems, or their colleagues.
- Offer honest appreciation early and specifically, never flattery.
- Frame every request in terms of what the other person gets out of
  it; help them *want* to help.
- Be genuinely interested: ask about them, then let them do most of
  the talking. Listening is the job, not a pause between speeches.
- Use the person's name once you know it; it is the fastest way to
  make someone feel singled out in a good way.
- Avoid arguments entirely. If the other person is mistaken on
  something that matters, circle back with a question instead of a
  correction. Never tell them they are wrong.
- When the assistant is wrong or missing something, admit it
  immediately and plainly, then say what happens next.
- Begin refusals and difficult asks in a friendly way; get small
  agreements early ("yes" momentum) before the real ask.
- Let the other person feel the workable idea was theirs when they
  offer one — adopt it and credit it.
- See it from their chair: state their constraints back to them
  before asking them to bend one.
- Appeal to the better motive (helping a patient, a customer, a
  neighbor) rather than to rules or pressure.
- When guiding someone to fix something, open with what is already
  right, raise the problem indirectly, ask questions instead of
  issuing orders, let them save face, and make the fix feel easy.
- Give people a good reputation to live up to ("you folks have been
  great about this") and praise the slightest improvement.

Layered over Nathan's standing rules, verbatim and non-negotiable:
the disclosure opening, the honest-AI identity answer, the hand-back
line ("Nathan will need to take this over. I'll end the call now."),
the forbidden topics, and the allowed-decisions boundary. One fix
from the Oct 30 call: being *asked whether it is AI* is an identity
question — it gets the identity answer and the conversation
CONTINUES; it is not by itself a hand-back trigger. Asking for a
human, refusing an assistant, or demanding a forbidden action is.

## 3. Memory plane — temporal profile store

SQLite database (default `AI_CALLING_PROFILE_DB`, else
`AI_CALLING_LOG_DIR/profile.db`), deliberately modeled on Graphiti's
temporal knowledge graph, reduced to what a single-assistant,
single-user system needs:

- **entities** — people, businesses, places, topics, and Nathan
  himself. Each has a kind, a canonical name, and aliases (phone
  numbers are aliases, which is how a dialed number resolves to a
  known counterparty).
- **facts** — (entity, predicate, value) with `valid_from` /
  `valid_to` and a `superseded_by` link. Storing a new value for an
  existing (entity, predicate) closes the old row's window instead
  of deleting it: the store always knows what is true *now* and can
  still answer what was true *then*. Every fact carries provenance
  (who/what supplied it) and the episode it came from.
- **episodes** — one row per meaningful event (a call, a seeding
  batch): reference id (attempt id for calls), counterparty, a short
  factual summary, a pointer to the transcript record, when it
  happened, provenance. Episodes are the audit trail behind facts.
- **commitments** — promises with an owner (`owed_by`:
  assistant / counterparty / nathan), the counterparty they bind,
  the promise text, a due string as spoken (dates stay as stated
  until a human normalizes them), and a lifecycle state:
  open → done / dropped / superseded. Open commitments are the
  first thing a follow-up call's brief surfaces.

API used by the controller and pipeline:

- `get_profile_slice(counterparty, objective, budget_chars)` —
  a prompt-ready text block, priority-ordered and budget-capped:
  open commitments first, then the counterparty's current facts,
  then a compact Nathan core, then recent episode summaries
  (newest first; episodes are what gets truncated when the budget
  bites). Everything in the slice is labeled historical background
  that may be stale — the assistant must never present profile
  contents as something the other party just said.
- `record_episode(ref, counterparty, summary, transcript_ref)` —
  idempotent on ref.
- `extract_and_store(items, episode_id)` — items are typed dicts
  (`fact` / `preference` / `commitment` / `counterparty_note`),
  each stored with the episode as provenance; facts supersede,
  commitments open, preferences store as facts under a
  `preference:` predicate.
- `open_commitments(counterparty=None)` — lifecycle queries.

## 4. Control plane — System-One-style controller

The pattern is Jev-Mem's (arXiv 2609.23986): a fast control plane
makes the memory decisions; the expensive reasoner (System Two —
here, the Gemini ladder) is invoked only to talk. Three contracts:

- **Pre-call — `build_brief(job, store, playbooks)`** runs once,
  before any conversation (in the media-claim path server-side, and
  in `place_call.py --dry-run` for preview). It selects the
  playbook, pulls the profile slice, and freezes a `CallBrief`
  (persona reference, playbook, slice, end conditions). The
  pipeline consumes only the frozen brief; a brief failure fails
  open to today's behavior (objective + business only).
- **In-call — `TurnRouter`** makes between-turn decisions in
  microseconds from the frozen brief and the conversation tracker:
  which playbook slot is open, whether an assistant ask is a
  near-duplicate, whether a terminal line was spoken. Hard rule:
  rules and cached brief data only — no network, no database, no
  model call on the audio path. Fail-open is structural: any
  router error degrades to the pre-pass pipeline behavior.
- **Post-call — extraction + `writeback(record, store)`** runs in
  the media handler's teardown (fail-open; a memory failure never
  breaks a call or its record). A rule-based typer scans the final
  transcript for commitments (either side), counterparty-stated
  facts, and preferences, stores them under the call's episode,
  and the returned summary lands in the call record as
  `memory_writeback` — so every write is visible in the record
  that produced it.

Decider interface: all judgment calls route through
`ControllerDecider` (`server/call_controller.py`). The shipped
implementation is `RuleBasedDecider` (deterministic patterns +
similarity). A `HostedJevDecider` class documents the hosted
contract and refuses with provisioning instructions until Nathan
supplies a key (see §6) — it is never constructed by default and
performs no network I/O in this build.

## 5. Conversation mechanics (pipeline changes)

- **Conversation tracker** (`server/conversation_state.py`):
  records assistant asks and whether the next user turn answered
  them (playbook slot filling by keyword/question similarity), and
  user/assistant turn history for the guard below.
- **Repetition guard**: an outgoing assistant question whose
  normalized token similarity to an already-sent ask is ≥ 0.75 is a
  repeat. First repeat of a question → the gate substitutes the
  next unasked playbook question (or a progression line when the
  playbook is exhausted). A further repeat → the wrap-up line is
  spoken and the call ends via the terminal path. Repeats are
  counted in the record's `conversation_state` summary. (Oct 30:
  one question went out 4×; under this guard it goes out once.)
- **Terminal discipline**: if the assistant's own reply contains
  the hand-back line, that reply is the last inference of the
  call. The gate releases it, the handler waits for it to finish
  playing (bounded), and the call hangs up. No new inference
  starts after it: later user speech is not answered, later LLM
  responses are dropped at the gate.
- **Prompt**: `build_system_prompt` = persona core + honesty lines
  (verbatim) + objective/business/allowed decisions + frozen brief
  (playbook flow in order, facts to collect, end conditions;
  profile slice labeled as background) + conversation discipline
  (one question at a time, never re-ask an answered question,
  acknowledge answers before moving on, wrap up when the end
  conditions are met).

## 6. Decision brief — hosted Jev vs the local controller

**Question.** Should the control plane's judgment seat be filled by
TypeSafe AI's hosted **Jev** API, or by a local controller?

**What Jev is.** A hosted "System One" model: it accepts typed
questions about a candidate (store it? retrieve it? evidence
enough?) and returns probabilistic assessments instead of prose.
Jev-Mem pairs it with a structured memory plane and invokes the
LLM only for final reasoning, reporting strong long-memory
benchmark results (LoCoMo 0.777 judge score, ~0.93 s median query
latency, memory construction ~6.6× faster than the strongest
baseline). It is an API and a research architecture — not a memory
store, and not a plug-and-play library.

**Hosted Jev as our controller — for:** purpose-built typed
decisions on exactly our three contracts (brief selection support,
retrieval judgments, post-call typing); sub-second assessments
without managing a local model; the typing quality on messy,
real-world transcripts is likely better than hand-written patterns.
**Against:** (1) *Privacy* — every assessment ships Nathan's
profile facts and call content to a third-party service; this
profile is personal (family, doctors, finances-adjacent threads),
and Nathan chose a self-hosted, end-to-end build precisely to keep
that data on his own machine. (2) *Dependency* — a young vendor's
hosted API becomes a live dependency of a phone call; an outage or
pricing change degrades the assistant. (3) *Fit* — our hot path
(repetition, terminal discipline, brief assembly) is deterministic
rules; Jev's value concentrates in post-call typing, the one place
latency does not matter and a local fast model can also work.
(4) *Provisioning* — it needs a new account and API key that only
Nathan should create.

**Local controller — for:** zero new accounts, keys, or data
egress; deterministic and unit-testable; zero added in-call
latency; behind the same `ControllerDecider` interface, so the
seat can be re-filled later without touching the pipeline.
**Against:** rule-based typing will miss paraphrased commitments a
trained assessor would catch; improving it means maintaining
patterns (or later, calling the existing fast Gemini tier
post-call, which stays inside the current provider set).

**Recommendation.** Ship the local controller now (this pass).
Revisit hosted Jev only if, on real calls, post-call extraction
measurably under-files commitments/facts — and then only for the
post-call typing seat, never the in-call path, and only with
Nathan's explicit acceptance of profile data leaving his machine.

**A hosted-Jev switch would require from Nathan, exactly:**
1. He creates the TypeSafe/Jev account himself.
2. He creates an API key and stores it through the Secure Vault /
   credential-file flow like the other providers — never in chat.
3. He explicitly approves sending profile slices and call
   transcripts to that hosted API for assessment.
Nothing else changes: the adapter implements `ControllerDecider`,
configuration names the provider, and the pipeline is untouched.

## 7. Playbooks

Data files in `playbooks/` (JSON): `appointment_scheduling`,
`information_gathering`, `follow_up_status`. Each declares a goal
shape, the facts to collect (slot, question, match keywords), the
question flow (collection order), success conditions, end
conditions, allowed decisions, and hand-back triggers specific to
the playbook (e.g. an office requiring Nathan's date of birth to
book is a hand-back trigger — that value is never guessed or taken
from the profile by the assistant). Selection: `--playbook` on
`place_call.py`, else keyword inference from the objective, else
no playbook (brief = persona + profile slice + objective).

## 8. Evaluation

Offline, synthetic providers, production handler (the
`test_offline_media_session.py` pattern): (a) a scripted LLM that
repeats one question must be progressed by the guard and never
send it twice; (b) a scripted hand-back line must appear exactly
once and end the call with no further assistant turns; (c) two
sequential synthetic calls to the same counterparty — the second
call's system prompt must contain a fact and an open commitment
written back from the first; (d) extraction unit tests over
synthetic transcripts file facts/preferences/commitments with
episode provenance. Controller latencies (brief assembly, per-turn
routing, extraction) are measured on the real Oct 30 transcripts
by `tools/measure_controller_latency.py`.
