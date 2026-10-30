"""Run local Telnyx-contract tests with a Python socket egress guard."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
import cryptography

class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test); self.rows.append({'test': test.id(), 'status': 'PASS'})

    def addFailure(self, test, err):
        super().addFailure(test, err); self.rows.append({'test': test.id(), 'status': 'FAIL'})

    def addError(self, test, err):
        super().addError(test, err); self.rows.append({'test': test.id(), 'status': 'ERROR'})

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.rows.append({'test': test.id(), 'status': 'SKIP'})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    observed = {'loopback_connects': 0, 'blocked_external_connects': 0}
    def guard(event, args):
        if event == 'socket.connect':
            addr = args[1]
            if not isinstance(addr, tuple) or addr[0] not in ('127.0.0.1', '::1'):
                observed['blocked_external_connects'] += 1
                raise PermissionError('External socket refused by test runner')
            observed['loopback_connects'] += 1
    sys.addaudithook(guard)
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root/'tests'))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    code = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob('*.py')) if '__pycache__' not in f.parts}
    passed = result.wasSuccessful() and not result.skipped and result.testsRun > 0
    receipt = {'schema': 'telnyx-integration-tests/v1', 'utc_epoch': int(time.time()),
               'scope': 'REFERENCE_CONTRACT_AND_LOOPBACK_HTTP_NOT_TELNYX_SERVICE',
               'tests_run': result.testsRun, 'all_pass': passed, 'tests': result.rows,
               'python_version': sys.version.split()[0],
               'cryptography_version': cryptography.__version__, 'socket_guard': observed,
               'source_sha256': code, 'real_provider_calls': 0,
               'muse_runtime_tested': False, 'audio_pipeline_tested': False}
    with open(a.output, 'x', encoding='utf-8') as f:
        json.dump(receipt, f, indent=2); f.write('\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
