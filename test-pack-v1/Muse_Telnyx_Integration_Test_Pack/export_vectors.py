"""Export deterministic synthetic signed vectors for cross-language tests."""
import argparse
import hashlib
import json
from fixtures_support import PUBLIC, RAW, NOW, signed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    result = {'scope': 'SYNTHETIC_KEY_NOT_TELNYX_ACCOUNT_KEY',
              'public_key_base64': PUBLIC, 'fixed_test_clock': NOW,
              'body_utf8': RAW.decode(), 'body_sha256': hashlib.sha256(RAW).hexdigest(),
              'headers': signed(RAW), 'expected_signature_accept': True,
              'note': 'Inject fixed_test_clock for offline replay; real current-time validation rejects old vectors.'}
    with open(a.output, 'x', encoding='utf-8') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print('Wrote one deterministic fixture, not a captured Telnyx delivery.')


if __name__ == '__main__':
    main()
