"""Read-only WP-2 source/native inventory and small pure-controller timing sample."""
import argparse
import ast
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def deny(event, details):
        if event in ('socket.connect', 'socket.sendto'):
            raise PermissionError('Read-only inventory forbids network')
    sys.addaudithook(deny)
    import call_controller as cc
    from conversation_state import ConversationTracker
    from closing_state import ClosingState
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams, LLMUserAggregator
    from pipecat.serializers.telnyx import TelnyxFrameSerializer
    from pipecat.turns.user_idle_controller import UserIdleController
    source = ROOT / 'server/live_pipeline.py'
    tree = ast.parse(source.read_text())
    routes = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
              and ast.unparse(node.func).endswith('TurnRouter')]
    end_frames = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == 'EndFrame']
    receipts = []
    for obj in (LLMUserAggregatorParams, LLMUserAggregator._on_user_turn_idle,
                UserIdleController, TelnyxFrameSerializer.InputParams,
                TelnyxFrameSerializer.serialize):
        path = Path(inspect.getsourcefile(obj))
        receipts.append({'symbol': obj.__module__ + '.' + obj.__qualname__,
                         'signature': str(inspect.signature(obj)),
                         'source_file': str(path),
                         'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'source_start_line': inspect.getsourcelines(obj)[1]})
    brief = cc.CallBrief(persona_core='', hand_back_rules='', playbook=None,
                         profile_text='', end_conditions=())
    samples = []
    for _ in range(5000):
        router = cc.TurnRouter(brief, ConversationTracker())
        start = time.perf_counter_ns()
        result = router.after_user_turn('Goodbye.')
        samples.append((time.perf_counter_ns() - start) / 1000)
        assert result.action == 'wrap_up'
    sorted_samples = sorted(samples)
    content = {
        'scope': 'WP02_CONTROLLER_CHECKPOINT_NOT_RUNTIME_CLOSING_ACCEPTANCE',
        'pipecat_version': importlib.metadata.version('pipecat-ai'),
        'reviewed_base': 'ffaf4ccf4450359d3a87728906521316f8e562d4',
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        'runtime_router_construction_lines': routes,
        'runtime_end_frame_sites': end_frames,
        'runtime_router_instantiated': bool(routes),
        'integration_claim': 'AST construction evidence alone cannot establish runtime acceptance',
        'native_default_user_idle_timeout': LLMUserAggregatorParams().user_idle_timeout,
        'native_default_auto_hang_up': TelnyxFrameSerializer.InputParams().auto_hang_up,
        'native_source_receipts': receipts,
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (source, ROOT / 'server/call_controller.py', ROOT / 'server/closing_state.py')},
        'pure_router_microbenchmark': {'n': len(samples), 'p50_microseconds': statistics.median(samples),
                                     'p95_nearest_rank_microseconds': sorted_samples[int(.95 * len(samples)) - 1],
                                     'max_microseconds': max(samples),
                                     'definition': 'one synthetic goodbye route; construction excluded; not conversation latency'},
        'live_calls': 0, 'provider_api_calls': 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(content, indent=2) + '\n')
    print(json.dumps(content, indent=2))


if __name__ == '__main__':
    main()
