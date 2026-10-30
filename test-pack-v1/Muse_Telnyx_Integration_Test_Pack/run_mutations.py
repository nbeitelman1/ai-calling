"""Deliberately break three controls; each must yield an assertion failure."""
import argparse
import io
import json
from pathlib import Path
import sys
import unittest
import store


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent/'tests'))
    from test_recovery import Recovery
    original_claim = store.Store.claim_submission
    original_apply = store.Store.apply
    def repeated_claim(self, attempt):
        with self.tx() as conn:
            conn.execute('UPDATE attempts SET state=? WHERE id=?', ('prepared', attempt))
        return original_claim(self, attempt)
    def reopen_terminal(conn, data):
        outcome = original_apply(conn, data)
        if data['event_type'] == 'call.answered':
            conn.execute('UPDATE attempts SET state=?', ('answered',))
        return outcome
    cases = [(store, 'verify', lambda *a, **k: True, 'test_invalid_signature_never_queued'),
             (store.Store, 'claim_submission', repeated_claim, 'test_claim_is_single_use'),
             (store.Store, 'apply', staticmethod(reopen_terminal), 'test_out_of_order_terminal_does_not_reopen')]
    rows = []
    for owner, name, broken, test in cases:
        original = owner.__dict__[name]
        try:
            setattr(owner, name, broken)
            r = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([Recovery(test)]))
            rows.append({'broken_control': name, 'test': test,
                         'caught': len(r.failures) == 1 and not r.errors})
        finally:
            setattr(owner, name, original)
    result = {'scope': 'DELIBERATE_REFERENCE_MUTATIONS', 'cases': rows,
              'all_caught': all(x['caught'] for x in rows)}
    with open(args.output, 'x', encoding='utf-8') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps(result, indent=2))
    return 0 if result['all_caught'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
