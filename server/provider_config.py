"""One configuration identity shared by preflight, prewarm, readiness and calls."""
import hashlib
import json
import math
import os
import time
from pathlib import Path
from model_select import parse_pool

DEFAULT_VOICE='cgSgspJ2msm6clMCkdW9'


def tts_configuration():
    pool=parse_pool(os.environ.get('ELEVENLABS_TTS_MODELS','eleven_flash_v2_5'))
    if not pool:
        raise ValueError('ElevenLabs model pool is empty')
    voice=os.environ.get('ELEVENLABS_VOICE_ID',DEFAULT_VOICE).strip()
    if not voice or '/' in voice:
        raise ValueError('invalid ElevenLabs voice ID')
    rate=int(os.environ.get('ELEVENLABS_SAMPLE_RATE','16000'))
    if rate not in (8000,16000,22050,24000,44100):
        raise ValueError('unsupported ElevenLabs PCM sample rate')
    return {'model':pool[0],'voice':voice,'sample_rate':rate}


def configuration_fingerprint():
    config={'tts':tts_configuration(), 'pools':{
        key:parse_pool(os.environ.get(key)) for key in (
            'GROQ_STT_MODELS','GEMINI_FAST_MODELS','GEMINI_FRONTIER_MODELS')}}
    return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()


def receipt_path():
    return Path(os.environ.get('AI_CALLING_PREFLIGHT_RECEIPT',
        str(Path(os.environ.get('AI_CALLING_LOG_DIR',
            str(Path(__file__).resolve().parent.parent/'logs')))/'provider-preflight.json')))


def load_preflight_receipt(*,now=None):
    data=json.loads(receipt_path().read_text(encoding='utf-8'))
    age=(time.time() if now is None else now)-float(data['checked_at'])
    if (data.get('ready') is not True or not math.isfinite(age) or not 0<=age<=3600
            or data.get('configuration_fingerprint')!=configuration_fingerprint()):
        raise ValueError('preflight receipt is stale or does not match configuration')
    models=data.get('models',{})
    for role,pool_name in [('fast','GEMINI_FAST_MODELS'),
                           ('frontier','GEMINI_FRONTIER_MODELS'),('stt','GROQ_STT_MODELS')]:
        if models.get(role) not in parse_pool(os.environ.get(pool_name)):
            raise ValueError('preflight resolved model does not belong to configured pool')
    return data
