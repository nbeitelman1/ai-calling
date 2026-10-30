import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


class TestMediaRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        env = {
            "TELNYX_WEBHOOK_PUBLIC_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "TELNYX_WEBHOOK_DB": str(Path(cls.tmp.name) / "webhook.sqlite"),
            "AI_CALLING_LOG_DIR": str(Path(cls.tmp.name) / "logs"),
        }
        cls.env = patch.dict(os.environ, env)
        cls.env.start()
        sys.modules.pop("live_server", None)
        cls.server = importlib.import_module("live_server")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.server.JOB_REGISTRY.clear()

    def _job_refs(self):
        from live_pipeline import LiveCallJob, LiveCredentialRefs
        return (LiveCallJob(to="+12025550191", business="Test",
                            objective="test"), LiveCredentialRefs())

    def test_registry_is_single_use(self):
        job, refs = self._job_refs()
        self.server.register_media_job("attempt-single", job, refs,
                                       ("connection", "from", "to"), "call.jsonl")
        self.assertIsNotNone(self.server.claim_media_job("attempt-single"))
        self.assertIsNone(self.server.claim_media_job("attempt-single"))

    def test_duplicate_registration_is_rejected(self):
        job, refs = self._job_refs()
        self.server.register_media_job("attempt-duplicate", job, refs,
                                       ("connection", "from", "to"), "call.jsonl")
        with self.assertRaises(ValueError):
            self.server.register_media_job("attempt-duplicate", job, refs,
                                           ("connection", "from", "to"), "call.jsonl")

    def test_log_name_cannot_escape_log_directory(self):
        job, refs = self._job_refs()
        with self.assertRaises(ValueError):
            self.server.register_media_job("attempt", job, refs,
                                           ("connection", "from", "to"), "../bad.jsonl")

    def test_launcher_accepts_preprovisioned_credentials_without_vault_module(self):
        sys.modules.pop("start_live_server", None)
        launcher = importlib.import_module("start_live_server")
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "already-set"}):
            launcher._provision("TEST_PROVIDER_KEY", "custom.unused")
            self.assertEqual(os.environ["TEST_PROVIDER_KEY"], "already-set")

    def test_readiness_reports_missing_stable_host_and_preflight(self):
        with patch.dict(os.environ, {}, clear=True):
            status = self.server.readiness_status()
        self.assertFalse(status["ready"])
        self.assertIn("public_host", status["failures"])
        self.assertIn("provider_preflight", status["failures"])
