"""Operator-attested resolution of attempts that never became calls.

Thin CLI over TelnyxAdapter.resolve_attempt_unsubmitted. Every guard
lives in the adapter; this tool only collects the attestation inputs
(operator, resolution basis, evidence reference, attestation text) and
prints the recorded receipt per attempt. It never dials, never ingests
or processes events, and never touches an attempt the adapter refuses.
Exit status is nonzero if any named attempt was refused.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from telnyx_adapter import AdapterConfig, TelnyxAdapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--attempt", action="append", required=True,
                        dest="attempts", help="attempt id; repeatable")
    parser.add_argument("--basis", required=True,
                        choices=("carrier_refused", "not_a_carrier_attempt"))
    parser.add_argument("--operator", required=True,
                        help="who attests, as a reference, not a secret")
    parser.add_argument("--evidence-ref", required=True,
                        help="pointer to the evidence behind the attestation")
    parser.add_argument("--attestation", required=True,
                        help="attestation text, stored verbatim with the attempt")
    args = parser.parse_args()
    adapter = TelnyxAdapter(
        args.db, os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY", ""), AdapterConfig())
    exit_code = 0
    for attempt_id in args.attempts:
        try:
            receipt = adapter.resolve_attempt_unsubmitted(
                attempt_id, basis=args.basis, operator=args.operator,
                evidence_ref=args.evidence_ref, attestation=args.attestation)
            print(json.dumps({"resolved": True, **receipt}, sort_keys=True))
        except Exception as exc:
            exit_code = 1
            print(json.dumps(
                {"resolved": False, "attempt_id": attempt_id,
                 "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
