"""Conversation-state tracker — the repetition guard's memory.

The Oct 30, 2026 qualification call asked one qualifying question
four times because nothing in the pipeline remembered what had
already been asked. This tracker is that memory: a pure,
deterministic record of assistant questions (normalized, with
answered flags), of which playbook slots have been addressed, and
of near-duplicate detection between a candidate question and
everything already asked.

No I/O, no models, no clocks: same inputs, same answers, every
time — which is what makes the guard testable in the offline
harness and safe to run in the response path.
"""
from __future__ import annotations

import re

_FILLER = frozenset({
    "please", "just", "really", "actually", "basically", "um", "uh",
    "so", "well", "okay", "ok", "right", "now",
})

# Similarity at or above this between a candidate question and an
# already-asked question counts as a repeat.
REPEAT_THRESHOLD = 0.75

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    lowered = _NON_WORD.sub(" ", (text or "").lower())
    return " ".join(w for w in lowered.split() if w not in _FILLER)


# Interrogative scaffolding: the words that frame a question
# without carrying its topic. Similarity is computed on content
# tokens only, so "could you tell me about the kind of work you
# do" and "what kind of work do you do" compare on {kind, work} —
# while "what is your name" vs "what is your number" compare on
# {name} vs {number} and correctly differ.
_SCAFFOLD = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "am", "be", "been",
    "do", "does", "did", "have", "has", "had", "can", "could",
    "would", "will", "shall", "should", "may", "might", "you",
    "your", "yours", "i", "me", "my", "we", "our", "it", "its",
    "this", "that", "what", "when", "where", "who", "whom", "how",
    "why", "tell", "say", "of", "to", "for", "and", "or",
})


def tokens(text: str) -> frozenset[str]:
    return frozenset(normalize(text).split())


def content_tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in tokens(text) if w not in _SCAFFOLD)


def similarity(a: str, b: str) -> float:
    """Content-token similarity with a containment boost: a short
    question fully contained in a longer one is the same question
    for guard purposes even though Jaccard alone says otherwise.
    Single-content-word questions match only when identical —
    "parking?" and "is parking free?" are different questions."""
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta or not tb:
        return 0.0
    if len(ta) == 1 or len(tb) == 1:
        return 1.0 if ta == tb else 0.0
    overlap = len(ta & tb)
    if not overlap:
        return 0.0
    jaccard = overlap / len(ta | tb)
    containment = overlap / min(len(ta), len(tb))
    return max(jaccard, 0.9 * containment)


def extract_questions(text: str) -> list[str]:
    """Sentences in `text` that are questions."""
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip())
            if s.strip().endswith("?")]


class AskedQuestion:
    __slots__ = ("text", "normalized", "answered", "count")

    def __init__(self, text: str):
        self.text = text
        self.normalized = normalize(text)
        self.answered = False
        self.count = 1


class ConversationTracker:
    """Asked/answered state, playbook progress, guard decisions."""

    def __init__(self, playbook: dict | None = None):
        self.playbook = playbook
        self.asked: list[AskedQuestion] = []
        self.slots_filled: set[str] = set()
        self.substitutions = 0

    # -- observation ---------------------------------------------------

    def observe_user(self, text: str) -> None:
        """A user turn answers the most recent unanswered question
        and fills any playbook slot whose keywords it matches."""
        if self.asked:
            for entry in reversed(self.asked):
                if not entry.answered:
                    entry.answered = True
                    break
        lowered = (text or "").lower()
        for item in (self.playbook or {}).get("collect", []):
            if item["slot"] in self.slots_filled:
                continue
            if any(kw in lowered for kw in item["keywords"]):
                self.slots_filled.add(item["slot"])

    def observe_assistant(self, text: str) -> None:
        for question in extract_questions(text):
            self._record(question)

    def _record(self, question: str) -> AskedQuestion:
        for entry in self.asked:
            if similarity(entry.text, question) >= REPEAT_THRESHOLD:
                entry.count += 1
                return entry
        entry = AskedQuestion(question)
        self.asked.append(entry)
        return entry

    # -- guard decisions -------------------------------------------------

    def check(self, candidate_text: str) -> dict:
        """Evaluate a candidate assistant response before it speaks.

        Returns {"action": "pass"|"substitute"|"wrap_up",
                 "repeated": [question texts], "max_count": int}.
        - pass: no near-duplicate question in the candidate.
        - substitute: a question is asked for the 2nd time overall —
          the caller should replace just that sentence.
        - wrap_up: a question would be asked a 3rd time — replace
          with a closing line and end the call.
        """
        repeated: list[str] = []
        worst = 0
        for question in extract_questions(candidate_text):
            for entry in self.asked:
                if similarity(entry.text, question) >= REPEAT_THRESHOLD:
                    repeated.append(entry.text)
                    worst = max(worst, entry.count + 1)
                    break
        if not repeated:
            return {"action": "pass", "repeated": [], "max_count": 0}
        action = "wrap_up" if worst >= 3 else "substitute"
        return {"action": action, "repeated": repeated, "max_count": worst}

    def note_repeat_attempts(self, candidate_text: str) -> None:
        """Record that the model TRIED to re-ask (the gate blocked
        it). Without this, a model stuck on one question substitutes
        forever; with it, the third attempt escalates to wrap_up."""
        for question in extract_questions(candidate_text):
            for entry in self.asked:
                if similarity(entry.text, question) >= REPEAT_THRESHOLD:
                    entry.count += 1
                    break

    def next_unasked_question(self) -> str | None:
        """The playbook's next question whose slot is unfilled and
        whose text has not already been asked — the progression
        target when the guard substitutes a repeat."""
        for item in (self.playbook or {}).get("collect", []):
            if item["slot"] in self.slots_filled:
                continue
            candidate = item["question"]
            if all(similarity(entry.text, candidate) < REPEAT_THRESHOLD
                   for entry in self.asked):
                return candidate
        # Fall back to any unasked playbook question, slot or not.
        for item in (self.playbook or {}).get("collect", []):
            candidate = item["question"]
            if all(similarity(entry.text, candidate) < REPEAT_THRESHOLD
                   for entry in self.asked):
                return candidate
        return None

    def summary(self) -> dict:
        return {
            "questions_asked": len(self.asked),
            "repeats": sum(e.count - 1 for e in self.asked),
            "slots_filled": sorted(self.slots_filled),
            "substitutions": self.substitutions,
        }

def substitute_questions(text: str, tracker: ConversationTracker,
                         replacement: str) -> str:
    """Rewrite a response whose question(s) repeat earlier asks:
    keep the non-question sentences, drop the repeated questions,
    and end with the progression question instead."""
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split((text or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if sentence.endswith("?") and any(
                similarity(entry.text, sentence) >= REPEAT_THRESHOLD
                for entry in tracker.asked):
            continue
        kept.append(sentence)
    if replacement and not any(
            similarity(s, replacement) >= REPEAT_THRESHOLD for s in kept):
        kept.append(replacement)
    return " ".join(kept).strip()
