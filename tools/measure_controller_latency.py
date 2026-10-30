#!/usr/bin/env python3
"""Measure the control plane's offline latencies on a real transcript.

Reads a session JSONL (default: the Oct 30, 2026 qualification
records), then times, per record: brief assembly against a seeded
throwaway profile store, per-turn routing + repetition-guard
checks replayed over the real transcript, and the post-call
extraction pass. Everything is local and deterministic — the point
is proving the controller cannot add dead air: in-call decisions
are pure rules over in-memory state.
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import call_controller as cc  # noqa: E402
import conversation_state as cs  # noqa: E402
import live_pipeline as live  # noqa: E402
import profile_seed  # noqa: E402
import profile_store as ps  # noqa: E402

DEFAULT_LOG = ("/home/hatch/workspace/ai-calling/logs/"
               "qualification-call-2026-10-29.jsonl")


def timed(fn, repeats=200):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {"mean_ms": round(statistics.mean(samples), 4),
            "p95_ms": round(sorted(samples)[int(0.95 * len(samples))], 4),
            "max_ms": round(max(samples), 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=DEFAULT_LOG)
    args = parser.parse_args()
    records = [json.loads(line) for line in
               Path(args.log).read_text().splitlines() if line.strip()]
    with tempfile.TemporaryDirectory() as tmp:
        store = ps.ProfileStore(Path(tmp) / "profile.db")
        profile_seed.seed_starter_profile(store)
        for i, record in enumerate(records, 1):
            transcript = record.get("transcript") or []
            job = live.LiveCallJob(
                to="+13218315278", business="Nathan qualification call",
                objective="controlled live qualification",
                counterparty="Nathan E. Beitelman")
            brief = timed(lambda: cc.build_brief(job, store), repeats=50)
            brief_once = cc.build_brief(job, store)
            tracker = cs.ConversationTracker(brief_once.playbook)
            router = cc.TurnRouter(brief_once, tracker)
            per_turn = []
            for turn in transcript:
                start = time.perf_counter()
                if turn["role"] == "user":
                    router.after_user_turn(turn["text"])
                else:
                    tracker.check(turn["text"])
                    tracker.observe_assistant(turn["text"])
                per_turn.append((time.perf_counter() - start) * 1000.0)
            extraction = timed(
                lambda: cc.extract_items(transcript,
                                         counterparty="Nathan E. Beitelman"),
                repeats=50)
            print(json.dumps({
                "record": i,
                "outcome": record.get("outcome"),
                "turns": len(transcript),
                "brief_assembly": brief,
                "per_turn_routing": {
                    "mean_ms": round(statistics.mean(per_turn), 4)
                    if per_turn else 0.0,
                    "max_ms": round(max(per_turn), 4) if per_turn else 0.0},
                "extraction_pass": extraction,
                "guard_summary_on_real_transcript": tracker.summary(),
            }))
        store.close()


if __name__ == "__main__":
    main()
