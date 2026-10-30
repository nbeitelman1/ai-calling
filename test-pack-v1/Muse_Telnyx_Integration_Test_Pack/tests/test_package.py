import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from verify_manifest import verify

class PackageIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / 'example.py'
        self.file.write_bytes(b'pass\n')
        self.manifest = {'schema': 'muse-pack-manifest/v1', 'files': {
            'example.py': {'bytes': 5, 'sha256': hashlib.sha256(b'pass\n').hexdigest()}}}
        self.save()

    def save(self):
        (self.root / 'MANIFEST.json').write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_bytes(self):
        self.assertTrue(verify(self.root)['pass'])

    def test_changed_bytes_detected(self):
        self.file.write_bytes(b'fail\n')
        self.assertFalse(verify(self.root)['pass'])

    def test_missing_file_detected(self):
        self.file.unlink()
        self.assertFalse(verify(self.root)['pass'])

    def test_unlisted_code_detected(self):
        (self.root / 'unlisted.py').write_bytes(b'pass\n')
        self.assertFalse(verify(self.root)['pass'])

    def test_escape_refused(self):
        self.manifest['files']['../outside.py'] = self.manifest['files']['example.py']
        self.save()
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_empty_manifest_refused(self):
        self.manifest['files'] = {}
        self.save()
        with self.assertRaises(ValueError):
            verify(self.root)
