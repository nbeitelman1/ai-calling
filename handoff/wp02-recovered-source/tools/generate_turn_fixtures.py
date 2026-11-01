"""Generate synthetic, non-personal speech fixtures locally with FFmpeg/libflite.

Not a field corpus or a voice-quality benchmark. FFmpeg is needed only to
regenerate the checked-in WAVs, not to run the test suite.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import wave

PHRASES = {
    'disclosure': "Hi, this is Nathan's assistant calling on his behalf.",
    'hello': 'Hello there I can hear you.',
    'yeah': 'Yeah.',
    'interject': 'Please wait.',
    'tuesday': 'I can do Tuesday.',
    'tuesday_unmodified': 'I can do Tuesday.',
    'thursday': 'No wait Thursday afternoon.',
    'reply': 'I can help with that and will check the options for you.',
    'thursday_reply': 'Thursday afternoon.',
    'tuesday_reply': 'Tuesday.',
    'idle_prompt': 'Are you still there?',
    'goodbye': 'Goodbye.',
    'goodbye_reply': 'Goodbye.',
    'closing_line': 'Thank you for your time. Goodbye.',
    'real_question': 'Opening hours?',
    'confirmation': 'Confirmation.',
    'handback': "Nathan will need to take this over. I'll end the call now.",
    'silence_close': "I haven't heard anything, so I'll end the call now. Goodbye.",
    'human_request': 'Please let me talk to a human.',
    'objective_complete': 'Office appointment available time bring confirmation.',
}


def main():
    root=Path(__file__).resolve().parents[1]
    dest=root/'tests/fixtures/turn_speech'
    dest.mkdir(parents=True,exist_ok=True)
    version=subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]
    records=[]
    for name,text in PHRASES.items():
        # textfile avoids punctuation/quote interpretation by the filter parser.
        text_path=dest/(name+'.txt')
        text_path.write_text(text)
        rate=16000 if name in ('disclosure','reply','thursday_reply','tuesday_reply','idle_prompt','goodbye_reply','closing_line','handback','silence_close') else 8000
        path=dest/(name+'.wav')
        # Reach the unchanged 200ms VAD threshold robustly in a controlled
        # synthetic fixture; this is not a speech-recognition accuracy corpus.
        # The unmodified Tuesday waveform remains as a diagnostic fixture.
        tempo={'yeah':'0.75','tuesday':'0.65','goodbye':'0.75'}.get(name)
        filters=['-af','atempo='+tempo] if tempo else []
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                        f'flite=textfile={text_path}:voice=slt',*filters,
                        '-ar',str(rate),'-ac','1','-c:a','pcm_s16le',str(path)],check=True)
        text_path.unlink()
        with wave.open(str(path),'rb') as wav:
            frames=wav.getnframes()
            assert wav.getnchannels()==1 and wav.getsampwidth()==2
        records.append({'file':path.name,'text':text,'filter':'atempo='+tempo if tempo else None,
                        'sample_rate':rate,'samples':frames,
                        'seconds':frames/rate,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (dest/'manifest.json').write_text(json.dumps({
        'scope':'SYNTHETIC_LOCAL_SPEECH_NOT_RECORDED_CALLEE_AUDIO',
        'generator':version,'voice':'Flite slt','files':records},indent=2)+'\n')
    print(json.dumps(records,indent=2))


if __name__=='__main__':
    main()
