"""Prepare a new immutable-path venv and verify code; never deploy or dial.

Run with Python 3.12. --install-approved authorizes package-registry downloads,
not telephony or provider API access. Never move the resulting virtualenv.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def render_service_override(project, venv):
    import re
    project, venv = str(project), str(venv)
    if not all(re.fullmatch(r'/[A-Za-z0-9_./-]+', value) for value in (project, venv)):
        raise ValueError('service paths must be absolute and contain only letters, digits, /, _, ., -')
    return ('# Candidate only: review before installing. No activation performed.\n'
            '[Service]\n'
            f'WorkingDirectory={project}\n'
            'ExecStartPre=\n'
            f'ExecStartPre={venv}/bin/python {project}/server/prestart.py\n'
            'ExecStart=\n'
            f'ExecStart={venv}/bin/python {project}/server/start_live_server.py\n'
            f'Environment=NLTK_DATA={project}/nltk_data\n'
            'TimeoutStopSec=25\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv',type=Path,required=True)
    parser.add_argument('--install-approved',action='store_true')
    args=parser.parse_args()
    if not args.install_approved:
        parser.error('explicit --install-approved is required for dependency downloads')
    if sys.version_info[:2] != (3,12):
        parser.error('use Python 3.12 to reproduce the tested runtime')
    project=Path(__file__).resolve().parents[1]
    target=args.venv.expanduser().resolve()
    if target.exists():
        parser.error('venv destination already exists; select a new path, do not overwrite a deployment')
    if not (project/'nltk_data/tokenizers/punkt_tab/english/ortho_context.tab').is_file():
        parser.error('bundled English tokenizer data is missing')
    os.umask(0o077)
    subprocess.run([sys.executable,'-m','venv',str(target)],check=True)
    python=target/'bin/python'
    subprocess.run([str(python),'-m','pip','install','--require-hashes',
                    '-r',str(project/'server/requirements.lock')],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    receipt=target/'candidate-qa.json'
    subprocess.run([str(python),str(project/'tools/offline_verify.py'),
                    '--output',str(receipt)],check=True)
    summary={'prepared_at':time.time(),'status':'PREPARED_NOT_DEPLOYED',
             'project':str(project),'python':str(python),'qa_receipt':str(receipt),
             'live_calls_placed':0,'provider_preflight_run':False,
             'instructions':'Keep this venv at its current path. Follow docs/MUSE_TEST_HANDOFF.md.'}
    override=target/'candidate-systemd.override.conf'
    override.write_text(render_service_override(project,target))
    summary['systemd_override_for_review_only']=str(override)
    (target/'candidate-preparation.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
