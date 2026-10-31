"""WP-2 isolated controller negative controls; no runtime/phone claims."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('duplicate-line', 'if self.phase != ClosingPhase.CLOSING or self._line_claimed:',
     'if self.phase != ClosingPhase.CLOSING:', 'test_duplicate_triggers_claim_exactly_one_line'),
    ('second-reopen', 'self._can_reopen = reversible and self.reopens == 0',
     'self._can_reopen = reversible', 'test_second_closing_cannot_reopen'),
    ('early-hangup', 'if not self._current(token) or not self._stopped or self._interrupted:',
     'if not self._current(token) or self._interrupted:', 'test_generation_alone_cannot_arm_hangup'),
    ('stale-generation', 'return self.phase == ClosingPhase.CLOSING and token == self.generation',
     'return self.phase == ClosingPhase.CLOSING', 'test_stale_stop_cannot_complete_new_closing'),
    ('ack-reopens', 'or is_acknowledgement(text):', 'or False:',
     'test_acknowledgements_never_cancel_closing'),
    ('clear-is-stop', 'if not self._current(token) or not self._started or self._stopped or self._interrupted:',
     'if not self._current(token) or not self._started or self._stopped:',
     'test_clear_or_interruption_is_not_playback_completion'),
]


def worker(case, output):
    name, old, new, test = case
    started = time.monotonic()
    original = (ROOT / 'server/closing_state.py').read_bytes()
    with tempfile.TemporaryDirectory(prefix='wp02-control-') as temporary:
        copy = Path(temporary) / 'repo'
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns('.git', 'logs', '__pycache__', '*.pyc'))
        path = copy / 'server/closing_state.py'
        text = path.read_text()
        assert text.count(old) == 1, name
        path.write_text(text.replace(old, new))
        receipt = output / (name + '.json')
        log_path = output / (name + '.log')
        with log_path.open('w') as log:
            proc = subprocess.run([sys.executable, 'tools/offline_verify.py',
                '--pattern', 'test_closing_state.py', '--output', str(receipt)],
                cwd=copy, stdout=log, stderr=subprocess.STDOUT, timeout=30)
        result = json.loads(receipt.read_text())
        log_text = log_path.read_text()
        # unittest subTest failures are represented in the log, even when the
        # existing runner does not append a separate row in its JSON test list.
        intended = 'FAIL: ' + test in log_text
        caught = (proc.returncode == 1 and result['failures'] > 0 and result['errors'] == 0
                  and intended and result['socket_guard']['blocked_external_connects'] == 0)
    unchanged = (ROOT / 'server/closing_state.py').read_bytes() == original
    return {'control': name, 'intended_test': test, 'caught': caught and unchanged,
            'intended_assertion_failure_seen': intended, 'harness_errors': result['errors'],
            'working_source_unchanged': unchanged, 'seconds': time.monotonic() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('use a new output directory to retain earlier evidence')
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker, case, output) for case in CASES]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result), flush=True)
    wall = time.monotonic() - started
    summary = {'scope': 'PURE_CONTROLLER_NEGATIVE_CONTROLS_NOT_MEDIA_ACCEPTANCE',
               'cases': sorted(results, key=lambda x: x['control']),
               'all_caught': all(row['caught'] for row in results),
               'wall_seconds': wall, 'worker_seconds': sum(row['seconds'] for row in results),
               'parallelism_multiple': sum(row['seconds'] for row in results) / wall,
               'source_sha256': hashlib.sha256((ROOT / 'server/closing_state.py').read_bytes()).hexdigest()}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary['all_caught'] else 1)


if __name__ == '__main__':
    main()
