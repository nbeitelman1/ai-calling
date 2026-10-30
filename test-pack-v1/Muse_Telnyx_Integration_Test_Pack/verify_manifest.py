"""Check packaged bytes against MANIFEST.json; integrity, not origin attestation."""
import hashlib
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    errors = []
    rows = manifest['files']
    if manifest.get('schema') != 'muse-pack-manifest/v1' or not rows:
        raise ValueError('Invalid or empty manifest')
    for name, expected in rows.items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe manifest path')
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Symlink or path escape refused')
        if not path.is_file():
            errors.append(name + ': missing')
            continue
        data = path.read_bytes()
        if len(data) != expected['bytes']:
            errors.append(name + ': size mismatch')
        if hashlib.sha256(data).hexdigest() != expected['sha256']:
            errors.append(name + ': hash mismatch')
    extra = [str(p.relative_to(root)) for p in root.rglob('*.py')
             if '__pycache__' not in p.parts and str(p.relative_to(root)) not in rows]
    return {'scope': 'BYTE_INTEGRITY_NOT_AUTHENTICITY', 'files_checked': len(rows),
            'errors': errors, 'unlisted_python_files': extra,
            'pass': not errors and not extra}


def main():
    result = verify(Path(__file__).resolve().parent)
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
