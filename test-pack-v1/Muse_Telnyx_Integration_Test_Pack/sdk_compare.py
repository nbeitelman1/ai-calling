"""Optional cross-check against one pinned, inspected official SDK helper file."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from signatures import verify
from fixtures_support import PUBLIC, RAW, NOW, signed

PINNED_SHA = '2c5135b23b772c86af79c9ab621f17b67f55228a40e33bc520db2c2c2d5e4bed'
COMMIT = '269609c283e4ecd9774127a1686c41f695654708'


def accepted(fn):
    try:
        fn()
        return True
    except Exception:
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk-file', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    source = Path(a.sdk_file).read_bytes()
    if hashlib.sha256(source).hexdigest() != PINNED_SHA:
        raise ValueError('SDK file hash differs from reviewed source; refusing import')
    spec = importlib.util.spec_from_file_location('pinned_telnyx_verifier', a.sdk_file)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)
    cases = [('valid', RAW, signed(RAW), PUBLIC, True),
             ('whitespace_preserved', b' '+RAW, signed(b' '+RAW), PUBLIC, True),
             ('body_modified', RAW+b' ', signed(RAW), PUBLIC, False),
             ('old_timestamp', RAW, signed(RAW, NOW-301), PUBLIC, False),
             ('future_timestamp', RAW, signed(RAW, NOW+301), PUBLIC, False),
             ('missing_headers', RAW, {}, PUBLIC, False),
             ('bad_public_key', RAW, signed(RAW), '%%%%', False)]
    rows = []
    with patch.object(sdk.time, 'time', return_value=NOW):
        for name, body, headers, key, expected in cases:
            ours = accepted(lambda: verify(body, headers, key, now=NOW))
            theirs = accepted(lambda: sdk.verify_webhook_signature(body, headers, key))
            rows.append({'case': name, 'expected_accept': expected,
                         'cryptography_accept': ours, 'sdk_pynacl_accept': theirs,
                         'pass': ours == theirs == expected})
    from importlib.metadata import version
    result = {'scope': 'OFFLINE_SDK_SIGNATURE_PARITY_ONLY', 'sdk_commit': COMMIT,
              'sdk_file_sha256': PINNED_SHA,
              'our_verifier_sha256': hashlib.sha256(Path(__file__).with_name('signatures.py').read_bytes()).hexdigest(),
              'cryptography_version': version('cryptography'), 'pynacl_version': version('PyNaCl'),
              'cases': rows,
              'all_pass': all(x['pass'] for x in rows), 'provider_calls': 0}
    with open(a.output, 'x', encoding='utf-8') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps(result, indent=2))
    return 0 if result['all_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
