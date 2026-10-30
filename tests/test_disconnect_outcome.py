"""Session-record outcome split: a media socket that closed because
the call ended (a signed call.hangup is on record) is labelled
"hangup"; only a socket that closed with no signed hangup on record
keeps "media_disconnected".

Both Oct 30, 2026 live calls were callee hangups whose records read
media_disconnected, because the watcher sees the socket close before
the signed hangup is reconciled. _disconnect_record_outcome applies
the reconciliation evidence at record time. The end-to-end shapes are
covered by tests/test_offline_media_session.py (genuine drop) and its
hangup variant.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from live_pipeline import _disconnect_record_outcome


class TestDisconnectRecordOutcome(unittest.TestCase):
    def test_signed_hangup_relabels_to_hangup(self):
        self.assertEqual(
            _disconnect_record_outcome(
                "media_disconnected",
                {"answered_event": True, "hangup_event": True,
                 "hangup_source": "callee"}),
            "hangup")

    def test_genuine_drop_keeps_media_disconnected(self):
        self.assertEqual(
            _disconnect_record_outcome(
                "media_disconnected",
                {"answered_event": True, "hangup_event": False,
                 "hangup_source": None}),
            "media_disconnected")

    def test_missing_evidence_keeps_media_disconnected(self):
        self.assertEqual(
            _disconnect_record_outcome("media_disconnected", {}),
            "media_disconnected")
        self.assertEqual(
            _disconnect_record_outcome(
                "media_disconnected", {"answered_event": False}),
            "media_disconnected")

    def test_other_outcomes_are_untouched(self):
        for outcome in ("hangup", "completed", "max_minutes",
                        "loop_in_requested", "silence_timeout",
                        "cancelled", "error:RuntimeError"):
            self.assertEqual(
                _disconnect_record_outcome(
                    outcome, {"hangup_event": True}),
                outcome)


if __name__ == "__main__":
    unittest.main()
