"""Reproduce the unmodified runtime's double-goodbye at provider CLIENT seams.

Uses real Pipecat services, synthetic speech, scripted Google responses and a
synthetic peer. This diagnoses a baseline defect, not a passing WP-2 acceptance
case, live model quality, or carrier playout. No production pipeline patches.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.client_seam_bench import BenchSession, ProviderRig, types


class GoodbyeClientRig(ProviderRig):
    async def generate(self, model=None, contents=None, config=None, **kwargs):
        users = [' '.join(getattr(part, 'text', '') or '' for part in content.parts or [])
                 for content in contents or [] if getattr(content, 'role', None) == 'user']
        self.llm_calls.append({'t': time.monotonic(), 'model': model,
                              'user_text': users[-1] if users else '', 'reply': 'Goodbye.'})
        await asyncio.sleep(self.client_delay)
        async def chunks():
            yield types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role='model', parts=[types.Part(text='Goodbye.')]),
                finish_reason=types.FinishReason.STOP)])
        return chunks()


async def reproduce():
    with tempfile.TemporaryDirectory() as temporary:
        with patch('tools.client_seam_bench.ProviderRig', GoodbyeClientRig):
            session = BenchSession(temporary, mode='legacy', silence_seconds=60)
        async with session as bench:
            await bench.wait(lambda: len(bench.trace.bot_stops) >= 1)
            await bench.play('goodbye', 'Goodbye.', 'first_goodbye')
            await bench.wait(lambda: len(bench.trace.bot_stops) >= 2)
            await asyncio.sleep(.25)
            await bench.play('goodbye', 'Goodbye.', 'second_goodbye')
            await bench.wait(lambda: len(bench.trace.bot_stops) >= 3)
            await asyncio.sleep(.3)
            natural_end = bench.running.done()
            terminal_reason = bench.ladder.terminal_reason
        result = session.results()
        result.update({
            'scenario': 'reviewed_base_double_goodbye',
            'runtime_source_sha256': hashlib.sha256((ROOT / 'server/live_pipeline.py').read_bytes()).hexdigest(),
            'natural_end_before_forced_cleanup': natural_end,
            'terminal_reason_before_cleanup': terminal_reason,
            'session_outcome_after_forced_peer_disconnect': session.record.get('outcome') if session.record else None,
            'wp02_pipeline_acceptance_passed': False,
            'baseline_defect_reproduced': (len(result['llm_calls']) == 2
                                         and [row['text'] for row in result['tts_calls']] == ['Goodbye.', 'Goodbye.']
                                         and not natural_end and terminal_reason is None),
        })
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    network = {'blocked_attempts': 0}
    def deny(event, details):
        if event in ('socket.connect', 'socket.sendto'):
            network['blocked_attempts'] += 1
            raise PermissionError('Offline closing bench forbids network')
    sys.addaudithook(deny)
    os.environ['NLTK_DATA'] = str(ROOT / 'nltk_data')
    import nltk.data
    nltk.data.path.insert(0, str(ROOT / 'nltk_data'))
    result = asyncio.run(reproduce())
    result['socket_guard'] = network
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in (
        'scenario', 'baseline_defect_reproduced', 'natural_end_before_forced_cleanup',
        'terminal_reason_before_cleanup', 'service_classes', 'cleanup_pending_tasks', 'socket_guard')}, indent=2))
    if result['fixture_errors'] or result['unexpected_stt_calls'] or result['cleanup_pending_tasks'] or network['blocked_attempts']:
        raise SystemExit('Invalid baseline bench evidence')
    raise SystemExit(0 if result['baseline_defect_reproduced'] else 1)


if __name__ == '__main__':
    main()
