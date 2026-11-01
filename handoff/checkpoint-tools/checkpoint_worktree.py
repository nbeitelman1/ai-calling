"""Offline recovery snapshot: Git history plus exact current working files.

Does not commit, push, merge, deploy, or certify tests. Untracked files must be
explicitly included. Secrets, symlinks, and oversized snapshots are refused.
The Git index, hooks, configuration, ignored files, and stashes are not restored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

PATTERNS = {
    'github-token': rb'(?:github_pat_|gh[opusr]_)[A-Za-z0-9_]{20,}',
    'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'google-key': rb'AIza[A-Za-z0-9_-]{30,}',
    'aws-key': rb'AKIA[A-Z0-9]{16}',
}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '.git' in path.parts or '\\' in name:
        raise ValueError('Unsafe checkpoint path')
    if not path.parts or str(path) != name:
        raise ValueError('Checkpoint paths must be normalized relative paths')
    return path


def collect(root, include):
    tracked = set(git(root, 'ls-files', '-z').decode().strip('\0').split('\0')) - {''}
    untracked = set(git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().strip('\0').split('\0')) - {''}
    include = set(include)
    for name in include:
        safe_relative(name)
        if name not in untracked and name not in tracked:
            raise ValueError('Explicit include must be a non-ignored repository file: ' + name)
    omitted = untracked - include
    if omitted:
        raise ValueError('Untracked files require explicit --include: ' + ', '.join(sorted(omitted)))
    data, deleted = {}, []
    for name in sorted(tracked | include):
        relative = safe_relative(name)
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
            raise ValueError('Symlinks are not included: ' + name)
        if not path.exists():
            deleted.append(name)
            continue
        if not path.is_file():
            raise ValueError('Expected a regular file: ' + name)
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('File exceeds checkpoint limit: ' + name)
        if path.name == '.env' or path.suffix.lower() in {'.pem', '.key', '.pfx', '.p12'}:
            raise ValueError('Credential-like file refused: ' + name)
        content = path.read_bytes()
        for rule, expression in PATTERNS.items():
            if re.search(expression, content):
                raise ValueError('Secret pattern ' + rule + ' in ' + name)
        data[name] = content
    if sum(map(len, data.values())) > 100 * 1024 * 1024:
        raise ValueError('Snapshot exceeds 100 MB limit')
    return data, deleted


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive CRC failure')
        manifest = json.loads(archive.read('MANIFEST.json'))
        expected_names = {'MANIFEST.json', *manifest['sha256']}
        if set(archive.namelist()) != expected_names or len(archive.namelist()) != len(expected_names):
            raise ValueError('Unexpected or duplicate archive entries')
        for name, digest in manifest['sha256'].items():
            safe_relative(name)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('Hash mismatch: ' + name)
    return manifest


def save_checkpoint(root, output, include=()):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or root in output.parents:
        raise ValueError('Checkpoint must be written outside the repository')
    if output.exists():
        raise FileExistsError(output)
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    branch = git(root, 'symbolic-ref', '--short', 'HEAD').decode().strip()
    status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    files, deleted = collect(root, include)
    with tempfile.TemporaryDirectory(prefix='durable-checkpoint-') as scratch:
        bundle = Path(scratch) / 'repository.bundle'
        git(root, 'bundle', 'create', str(bundle), branch)
        git(root, 'bundle', 'verify', str(bundle))
        second_files, second_deleted = collect(root, include)
        if files != second_files or deleted != second_deleted or git(root, 'rev-parse', 'HEAD').decode().strip() != head:
            raise RuntimeError('Working tree changed while snapshot was being captured')
        if git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all') != status:
            raise RuntimeError('Index/status changed while snapshot was being captured')
        payload = {'repository.bundle': bundle.read_bytes(), 'git-status.porcelain': status}
        payload.update({'working-tree/' + name: content for name, content in files.items()})
        manifest = {
            'schema': 'working-tree-recovery/v1', 'head': head, 'branch': branch,
            'dirty': bool(status), 'files_captured': len(files), 'deleted_tracked_files': deleted,
            'explicit_untracked_files': sorted(include),
            'verification': 'archive integrity only; tests must be bound to this snapshot separately',
            'limitations': ['does not recreate index staging or permission changes', 'ignores ignored files',
                            'no hooks/configuration/stash backup', 'pattern scan is not exhaustive DLP'],
            'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix='checkpoint-', suffix='.partial', dir=output.parent, delete=False)
        temporary = Path(handle.name)
        handle.close()
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, content in payload.items():
                    archive.writestr(name, content)
                archive.writestr('MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
            verify_archive(temporary)
            if output.exists():
                raise FileExistsError(output)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include', action='append', default=[])
    args = parser.parse_args()
    manifest = save_checkpoint(args.repo, args.output, args.include)
    print(json.dumps({k: manifest[k] for k in ('head', 'branch', 'dirty', 'files_captured', 'verification')}, indent=2))


if __name__ == '__main__':
    main()
