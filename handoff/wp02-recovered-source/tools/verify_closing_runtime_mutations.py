"""Negative controls for owned closing wiring; copies only, no provider network."""
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
    ('serializer-double-owner', 'server/live_pipeline.py',
     'auto_hang_up=not owned_closing', 'auto_hang_up=True',
     'test_closing_media.TestClosingMedia.test_double_goodbye_one_cached_line_and_one_hangup'),
    ('missing-clear-barrier', 'server/closing_runtime.py',
     'await asyncio.wait_for(frame.closing_ack.wait(), timeout=2.0)', 'await asyncio.sleep(0)',
     'test_closing_runtime.TestClosingRuntime.test_clear_barrier_waits_for_transport_receipt'),
    ('repeated-hangup', 'server/closing_runtime.py',
     'if self.hangup_dispatches:', 'if False:',
     'test_closing_runtime.TestClosingRuntime.test_concurrent_hangup_claims_dispatch_once'),
    ('context-leak', 'server/closing_runtime.py',
     "            if self.owner.closing:\n                self.owner.suppressed_contexts += 1",
     "            if False:\n                self.owner.suppressed_contexts += 1",
     'test_closing_runtime.TestClosingRuntime.test_context_queued_before_close_is_blocked_before_llm'),
    ('stale-llm-reply', 'server/live_pipeline.py',
     "if closing and (closing.closing or getattr(self, '_response_epoch', -1) != closing.epoch):",
     'if closing and closing.closing:',
     'test_closing_runtime.TestClosingRuntime.test_old_llm_response_is_not_released_after_reopening'),
    ('ack-clears-queue', 'server/closing_runtime.py',
     "        if (direction == FrameDirection.DOWNSTREAM and self.owner.closing\n                and isinstance(frame, InterruptionFrame)\n                and not getattr(frame, 'closing_control', False)):\n            return\n",
     '', 'test_closing_runtime.TestClosingRuntime.test_ack_interruption_does_not_clear_output_processor_queue'),
]

CASES += [
    ('runner-exit-leaves-call', 'server/live_pipeline.py',
     "            if closing:\n                # With serializer auto-hangup disabled, even an unexpected\n                # normal runner exit must close the billable leg exactly once.\n                hangup_acknowledged = await dispatch_hangup(\n                    adapter, media_token, _resolve_ref(refs.telnyx_api_key_ref), outcome)\n",
     '', 'test_closing_media.TestClosingMedia.test_unexpected_pipeline_end_still_hangs_up_once'),
    ('cache-charged-as-llm', 'server/live_pipeline.py',
     '"gemini_requests": closing.llm_requests if closing else sum(',
     '"gemini_requests": sum(',
     'test_closing_media.TestClosingMedia.test_double_goodbye_one_cached_line_and_one_hangup'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('use a fresh evidence directory')
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name, relative, old, new, test in CASES:
        started = time.monotonic()
        original = (ROOT / relative).read_bytes()
        with tempfile.TemporaryDirectory(prefix='wp02-runtime-mutation-') as temporary:
            copy = Path(temporary) / 'repo'
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns('.git', 'logs', '__pycache__', '*.pyc'))
            path = copy / relative
            text = path.read_text()
            assert text.count(old) == 1, name
            path.write_text(text.replace(old, new))
            receipt = output / (name + '.json')
            with (output / (name + '.log')).open('w') as log:
                proc = subprocess.run([sys.executable, 'tools/offline_verify.py', '--test', test,
                    '--output', str(receipt)], cwd=copy, stdout=log, stderr=subprocess.STDOUT, timeout=45)
            data = json.loads(receipt.read_text())
            rows = [row for row in data['tests'] if row['test'].endswith(test)]
            intended = bool(rows and rows[0]['status'] == 'FAIL' and 'AssertionError' in rows[0].get('detail', ''))
            caught = (proc.returncode == 1 and data['tests_run'] == 1 and data['failures'] == 1
                      and data['errors'] == 0 and intended
                      and data['socket_guard']['blocked_external_connects'] == 0)
        assert (ROOT / relative).read_bytes() == original
        row = {'control': name, 'test': test, 'caught': caught, 'harness_errors': data['errors'],
               'seconds': time.monotonic() - started,
               'source_sha256': hashlib.sha256(original).hexdigest()}
        results.append(row)
        print(json.dumps(row), flush=True)
    result = {'scope': 'WIRING_NEGATIVE_CONTROLS_NOT_LIVE_CARRIER', 'cases': results,
              'all_caught': all(row['caught'] for row in results)}
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['all_caught'] else 1)


if __name__ == '__main__':
    main()
