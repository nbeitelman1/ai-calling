"""Read-only Muse feedback bundle: no credentials, transcripts or network calls."""
import argparse
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote


def inspect_state(path):
    path=Path(path).expanduser().resolve()
    if not path.is_file():
        return {'available':False,'reason':'database_not_found'}
    connection=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro',uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        names={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={'available':True,'attempt_states':{},'unresolved_attempts':[]}
        if 'attempts' in names:
            result['attempt_states']=dict(connection.execute('SELECT state,COUNT(*) FROM attempts GROUP BY state'))
            result['unresolved_attempts']=[{'attempt_id':row[0],'state':row[1],'stop_requested':bool(row[2]),
                                           'has_control_id':bool(row[3]),'hangup_acknowledged':bool(row[4])}
                for row in connection.execute("SELECT attempt_id,state,stop,control_id,hangup_ack FROM attempts "
                    "WHERE state IN ('submit_unknown','initiated','answered') ORDER BY attempt_id LIMIT 100")]
            result['unresolved_list_cap']=100
        if 'events' in names:
            result['event_outcomes']=dict(connection.execute('SELECT COALESCE(outcome,\'pending\'),COUNT(*) FROM events GROUP BY outcome'))
        if 'media_jobs' in names:
            result['media_job_counts']=[{'claimed':row[0],'settled':row[1],'count':row[2]} for row in connection.execute(
                'SELECT (claimed_at IS NOT NULL),quota_settled,COUNT(*) FROM media_jobs GROUP BY (claimed_at IS NOT NULL),quota_settled')]
        return result
    finally:
        connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--record',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    report={'scope':'read_only_no_network_no_transcripts_no_credentials',
            'python':platform.python_version(),'packages':{},
            'configuration_present':{key:bool(os.environ.get(key)) for key in (
                'TELNYX_WEBHOOK_PUBLIC_KEY','AI_CALLING_PUBLIC_HOST','GEMINI_FAST_MODELS',
                'GEMINI_FRONTIER_MODELS','GROQ_STT_MODELS','ELEVENLABS_TTS_MODELS')},
            'state':inspect_state(args.db)}
    for package in ['pipecat-ai','fastapi','websockets','cryptography','onnxruntime']:
        try:
            report['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report['packages'][package]='not_installed'
    result=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True)
    report['revision']=result.stdout.strip() if result.returncode==0 else 'not_a_checkout'
    if args.record:
        safe_fields=('pipeline_started','cleanup_pending_tasks','media_payload_bytes',
            'outbound_payload_bytes','outbound_media_frames','audio_evidence_scope',
            'hangup_acknowledged','hangup_required','carrier_reconciliation','tts_characters',
            'billing_reconciled','cost_scope')
        lines=[line for line in args.record.read_text().splitlines() if line.strip()]
        if lines:
            record=json.loads(lines[-1])
            report['last_record']={key:record[key] for key in safe_fields if key in record}
            report['last_record']['outcome_type']=':'.join(str(record.get('outcome','')).split(':')[:2])
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
