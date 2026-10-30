"""Unit tests for tools/place_call.py's pure gate logic.

No sockets, no carrier, no credentials: destination validation, the
approval environment gate, preflight-receipt freshness (epoch and ISO
shapes), the one-call gate query and the monthly budget sum run
against synthetic temp databases.
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

_spec = importlib.util.spec_from_file_location(
    "place_call", ROOT / "tools" / "place_call.py")
place_call = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(place_call)

TO = "+13218315278"


class TestDestination(unittest.TestCase):
    def test_allowlisted_destination_passes(self):
        self.assertEqual(
            place_call.validate_destination(TO, (TO,)), TO)

    def test_unlisted_destination_refused(self):
        with self.assertRaises(ValueError):
            place_call.validate_destination("+12025550199", (TO,))

    def test_malformed_destinations_refused(self):
        for bad in ("3218315278", "+1-321-831-5278", "+abc", "", "+1"):
            with self.assertRaises(ValueError, msg=bad):
                place_call.validate_destination(bad, (TO,))

    def test_env_extends_allowlist(self):
        env = {"AI_CALLING_AUTHORIZED_DESTINATIONS": " +12025550199 ,"}
        allowlist = place_call.allowlist_from_env(env)
        self.assertIn(TO, allowlist)
        self.assertIn("+12025550199", allowlist)


class TestApprovalGate(unittest.TestCase):
    def test_exact_value_required(self):
        self.assertTrue(place_call.approval_present(
            {"AI_CALLING_DIAL_APPROVED": "YES_THIS_TEST_CALL"}))
        for value in ("", "yes", "YES", "YES_THIS_TEST_CALL ", None):
            env = {} if value is None else {
                "AI_CALLING_DIAL_APPROVED": value}
            self.assertFalse(place_call.approval_present(env), msg=value)


class TestReceiptStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "provider-preflight.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_missing_receipt(self):
        ok, detail = place_call.receipt_status(self.path, now=1000.0)
        self.assertFalse(ok)
        self.assertIn("no preflight receipt", detail)

    def test_fresh_epoch_receipt(self):
        self.write({"ready": True, "checked_at": 900.0})
        ok, _ = place_call.receipt_status(self.path, now=1000.0)
        self.assertTrue(ok)

    def test_stale_epoch_receipt(self):
        self.write({"ready": True, "checked_at": 100.0})
        ok, detail = place_call.receipt_status(self.path, now=1000.0 + 7200)
        self.assertFalse(ok)
        self.assertIn("TTL", detail)

    def test_fresh_iso_receipt(self):
        stamp = datetime.fromtimestamp(950.0, timezone.utc).isoformat()
        self.write({"ready": True, "checked_at": stamp})
        ok, _ = place_call.receipt_status(self.path, now=1000.0)
        self.assertTrue(ok)

    def test_not_ready_receipt(self):
        self.write({"ready": False, "checked_at": 999.0})
        ok, _ = place_call.receipt_status(self.path, now=1000.0)
        self.assertFalse(ok)


def _make_db(path, attempts=(), jobs=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE attempts(attempt_id TEXT, state TEXT)")
    conn.execute("CREATE TABLE jobs(job_id TEXT, reserved_cents INTEGER, "
                 "billing_month TEXT)")
    conn.executemany("INSERT INTO attempts VALUES(?,?)", attempts)
    conn.executemany("INSERT INTO jobs VALUES(?,?,?)", jobs)
    conn.commit()
    conn.close()


class TestGateAndBudgetQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "state.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unresolved_attempts_listed(self):
        _make_db(self.db, attempts=[
            ("a-1", "hangup"), ("a-2", "answered"),
            ("a-3", "closed_unsubmitted"), ("a-4", "submit_unknown")])
        found = place_call.unresolved_attempts(self.db)
        self.assertEqual([u["attempt_id"] for u in found], ["a-2", "a-4"])

    def test_gate_clear_when_all_terminal(self):
        _make_db(self.db, attempts=[("a-1", "hangup"),
                                    ("a-2", "closed_unsubmitted")])
        self.assertEqual(place_call.unresolved_attempts(self.db), [])

    def test_monthly_reserved_sums_current_month_only(self):
        month = time.strftime("%Y-%m", time.gmtime())
        _make_db(self.db, jobs=[("j-1", 100, month), ("j-2", 137, month),
                                ("j-3", 500, "1999-01")])
        self.assertEqual(place_call.monthly_reserved_cents(self.db), 237)
        self.assertEqual(
            place_call.monthly_reserved_cents(self.db, month="1999-01"), 500)


class TestArgs(unittest.TestCase):
    def test_defaults(self):
        args = place_call.parse_args(["--to", TO])
        self.assertEqual(args.max_minutes, 10)
        self.assertEqual(args.cap_cents, 100)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.auto_restore)
        self.assertEqual(args.objective, "controlled live qualification")

    def test_to_is_required(self):
        with self.assertRaises(SystemExit):
            place_call.parse_args([])


if __name__ == "__main__":
    unittest.main()
