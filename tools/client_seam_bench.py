"""Offline raw-audio bench with REAL Pipecat STT/LLM/TTS service classes.

Only provider clients are doubled: google.genai.Client, the OpenAI-compatible
Whisper client, and the ElevenLabs websocket connect function. Speech is
synthetic Flite audio. Scripted STT/LLM responses do NOT measure recognition,
model intelligence, network latency, voice quality or carrier playback.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import deque
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'server'))
import live_pipeline as live
from google.genai import types
from openai.types.audio import Transcription
from pipecat.frames.frames import (
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame, ErrorFrame,
    InterruptionFrame, InputAudioRawFrame, LLMContextFrame, TranscriptionFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from starlette.websockets import WebSocket
from telnyx_media import pcm16_to_pcmu
from websockets.protocol import State

FIXTURES = ROOT/'tests/fixtures/turn_speech'
REPLY = 'I can help with that and will check the options for you.'


def read_pcm(name: str, *, trim: bool = False) -> tuple[bytes, int]:
    import array
    with wave.open(str(FIXTURES/(name+'.wav')), 'rb') as wav:
        assert wav.getnchannels()==1 and wav.getsampwidth()==2
        rate=wav.getframerate()
        data=wav.readframes(wav.getnframes())
    if trim:
        samples=array.array('h', data)
        if sys.byteorder != 'little':
            samples.byteswap()
        voiced=[i for i,v in enumerate(samples) if abs(v)>=150]
        if voiced:
            margin=int(rate*.03)
            lo=max(0,voiced[0]-margin)
            hi=min(len(samples),voiced[-1]+1+margin)
            data=data[lo*2:hi*2]
    return data, rate


def pcm_energy(data):
    import numpy as np
    samples=np.frombuffer(data,dtype='<i2').astype('int64')
    return int((samples*samples).sum(dtype='int64'))


class Trace(BaseObserver):
    def __init__(self):
        super().__init__()
        self.services={}
        self.events=[]
        self.bot_starts=[]
        self.bot_stops=[]
        self.input_energy=0.0

    async def on_process_frame(self, data):
        now=time.monotonic()
        frame=data.frame
        processor=data.processor
        if (processor is self.services.get('stt') and isinstance(frame,InputAudioRawFrame)
                and data.direction==FrameDirection.DOWNSTREAM):
            self.input_energy+=pcm_energy(frame.audio)
        if (processor.__class__.__name__=='BotActivityTap'
                and data.direction==FrameDirection.UPSTREAM):
            if isinstance(frame,BotStartedSpeakingFrame):
                self.bot_starts.append(now)
                self.events.append({'event':'bot_start','t':now})
            elif isinstance(frame,BotStoppedSpeakingFrame):
                self.bot_stops.append(now)
                self.events.append({'event':'bot_stop','t':now})
        if processor is self.services.get('stt') and isinstance(
                frame,(VADUserStartedSpeakingFrame,VADUserStoppedSpeakingFrame)):
            self.events.append({'event':type(frame).__name__,'t':now,
                                'direction':data.direction.name})
        if processor.__class__.__name__=='TranscriptTap' and isinstance(frame,TranscriptionFrame):
            self.events.append({'event':'transcript_at_tap','t':now,
                'text':frame.text,'finalized':frame.finalized,'direction':data.direction.name})
        if processor is self.services.get('llm') and isinstance(frame,LLMContextFrame):
            self.events.append({'event':'llm_context','t':now})
        if processor.__class__.__name__=='ResponseGate' and isinstance(frame,ErrorFrame):
            self.events.append({'event':'error','t':now,'fatal':frame.fatal,
                                'error':str(frame.error)[:200]})


class ProviderRig:
    def __init__(self):
        self.stt_script=deque()
        self.stt_calls=[]
        self.llm_calls=[]
        self.tts_calls=[]
        self.sockets=[]
        self.unexpected_stt_calls=0
        self.errors=[]
        self.client_delay=.02

    def openai_client(self, **kwargs):
        return SimpleNamespace(audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=self.transcribe)))

    async def transcribe(self, **kwargs):
        audio=kwargs['file'][1]
        with wave.open(io.BytesIO(audio),'rb') as wav:
            rate,channels,frames=wav.getframerate(),wav.getnchannels(),wav.getnframes()
            energy=pcm_energy(wav.readframes(frames))
        if self.stt_script:
            text=self.stt_script.popleft()
        else:
            text=''
            self.unexpected_stt_calls+=1
        self.stt_calls.append({'t':time.monotonic(),'text':text,
            'wav_rate':rate,'channels':channels,'samples':frames,'pcm_energy':energy,
            'model':kwargs.get('model')})
        await asyncio.sleep(self.client_delay)
        return Transcription(text=text)

    def genai_client(self, **kwargs):
        return SimpleNamespace(aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=self.generate)))

    async def generate(self, model=None, contents=None, config=None, **kwargs):
        user_messages=[]
        for content in contents or []:
            if getattr(content,'role',None)=='user':
                user_messages.append(' '.join(getattr(part,'text','') or ''
                    for part in getattr(content,'parts',[]) or []))
        latest=user_messages[-1] if user_messages else ''
        markers='◐' in str(getattr(config,'system_instruction',''))
        if 'thursday' in latest.lower():
            text=('●' if markers else '')+'Thursday afternoon.'
        elif 'tuesday' in latest.lower():
            text='◐' if markers else 'Tuesday.'
        elif latest.strip().lower() in ('yeah','yeah.','okay','okay.'):
            text=('●' if markers else '')+'Thursday afternoon.'
        else:
            text=('●' if markers else '')+REPLY
        self.llm_calls.append({'t':time.monotonic(),'model':model,'user_text':latest,
                               'reply':text,'completion_instructions':markers})
        await asyncio.sleep(self.client_delay)
        async def chunks():
            yield types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role='model',parts=[types.Part(text=text)]),
                finish_reason=types.FinishReason.STOP)],
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=10,candidates_token_count=10,total_token_count=20))
        return chunks()

    async def websocket_connect(self, uri, **kwargs):
        assert uri.startswith('wss://api.elevenlabs.io/'), 'Unexpected provider connection'
        websocket=ScriptedElevenLabsSocket(self)
        self.sockets.append(websocket)
        return websocket

    async def cleanup(self):
        for websocket in self.sockets:
            await websocket.close()


class ScriptedElevenLabsSocket:
    """Client boundary: emit the pinned multi-context wire response protocol."""
    def __init__(self, rig):
        self.rig=rig
        self.state=State.OPEN
        self.incoming=asyncio.Queue()
        self.closed=asyncio.Event()
        self.tasks=set()
        self.audio_tasks={}
        self.finalized=set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value=await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    def spawn(self, coro):
        task=asyncio.create_task(coro)
        self.tasks.add(task)
        def done(finished):
            self.tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                self.rig.errors.append(type(finished.exception()).__name__+': '+str(finished.exception()))
        task.add_done_callback(done)
        return task

    async def send(self, raw):
        message=json.loads(raw)
        if message.get('close_socket'):
            await self.close()
            return
        context=message.get('context_id')
        if message.get('close_context'):
            if context not in self.finalized:
                self.finalized.add(context)
                self.spawn(self.finish(context))
            return
        text=message.get('text','')
        if context and text.strip():
            task=self.spawn(self.emit(context,text))
            self.audio_tasks.setdefault(context,[]).append(task)

    async def emit(self, context, text):
        fixture={REPLY:'reply','Goodbye.':'goodbye_reply',
                 'Thank you for your time. Goodbye.':'closing_line',
                 'Thursday afternoon.':'thursday_reply',
                 'Tuesday.':'tuesday_reply','Are you still there?':'idle_prompt',
                 live.DISCLOSURE_OPENING:'disclosure'}.get(text.strip())
        if fixture is None:
            raise AssertionError('Unexpected text reached fixture TTS: '+repr(text))
        pcm,rate=read_pcm(fixture)
        assert rate==16000
        self.rig.tts_calls.append({'context_id':context,'text':text,'t':time.monotonic(),
                                   'pcm_bytes':len(pcm)})
        await asyncio.sleep(self.rig.client_delay)
        chars=list(text)
        duration_ms=len(pcm)/2/rate*1000
        alignment={'chars':chars,
                   'charStartTimesMs':[int(i*duration_ms/max(1,len(chars))) for i in range(len(chars))],
                   'charDurationsMs':[int(duration_ms/max(1,len(chars)))]*len(chars)}
        await self.incoming.put(json.dumps({'contextId':context,
            'audio':base64.b64encode(pcm).decode(),'alignment':alignment}))

    async def finish(self, context):
        pending=self.audio_tasks.get(context,[])
        if pending:
            await asyncio.gather(*pending)
        await self.incoming.put(json.dumps({'contextId':context,'isFinal':True}))

    async def wait_closed(self):
        await self.closed.wait()

    async def close(self, **kwargs):
        if self.closed.is_set():
            return
        self.state=State.CLOSED
        self.closed.set()
        pending=[t for t in self.tasks if t is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)
        await self.incoming.put(None)


class SyntheticAdapter:
    """Local call-state fixture; no network/dial capability."""
    def __init__(self):
        self.claimed=False
        self.ended=False
        self.settled=None
    def get_attempt(self,_):
        return {'state':'hangup' if self.ended else 'answered',
                'control_id':'control-offline','hangup_ack':False,
                'payload_json':json.dumps({'connection_id':'conn',
                    'from':'+12025550100','to':'+12025550101'})}
    def claim_media_job(self,_,now):
        if self.claimed:return None
        self.claimed=True
        return {'registered':True}
    def settle_media_job_quotas(self,_,actual):self.settled=actual
    def reconcile_call_record(self,_):
        return {'answered_event':True,'hangup_event':self.ended,
                'hangup_source':'callee' if self.ended else None}
    def request_stop(self,attempt_id=None,reason=None):pass
    def hangup_command(self,*args,**kwargs):return None


class BenchSession:
    def __init__(self, tmp, *, mode='legacy', completeness=False, playbook='', silence_seconds=60):
        self.tmp=Path(tmp)
        self.mode,self.completeness,self.playbook=mode,completeness,playbook
        self.silence_seconds=silence_seconds
        self.rig=ProviderRig()
        self.trace=Trace()
        self.incoming=asyncio.Queue()
        self.audio_queue=asyncio.Queue()
        self.wire=[]
        self.labels={}
        self.adapter=SyntheticAdapter()
        self.stack=ExitStack()
        self.pump_task=None
        self.running=None
        self.worker=None
        self.services=None
        self.ladder=None
        self.record=None
        self.sequence=2

    async def wait(self, predicate, timeout=10):
        deadline=time.monotonic()+timeout
        while not predicate():
            if self.rig.errors:
                raise AssertionError('Invalid provider fixture: '+self.rig.errors[0])
            if self.running and self.running.done():
                error=self.running.exception()
                raise AssertionError('Media session finished before condition: '+str(error))
            if time.monotonic()>=deadline:
                raise TimeoutError('Bench condition deadline exceeded')
            await asyncio.sleep(.01)

    async def send(self,message):
        if message['type']=='websocket.send':
            payload=json.loads(message.get('text','{}'))
            event={'event':payload.get('event'),'t':time.monotonic()}
            if payload.get('event')=='media':
                event['bytes']=len(base64.b64decode(payload['media']['payload']))
            self.wire.append(event)

    async def pump(self):
        due=time.monotonic()
        while True:
            try:chunk,label,last,done=self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:chunk,label,last,done=bytes([255])*160,None,False,None
            await self.incoming.put({'type':'websocket.receive','text':json.dumps({
                'event':'media','stream_id':'stream-offline','sequence_number':str(self.sequence),
                'media':{'track':'inbound','payload':base64.b64encode(chunk).decode()}})})
            self.sequence+=1
            if last:
                self.labels[label]=time.monotonic()+len(chunk)/8000
                asyncio.get_running_loop().call_later(len(chunk)/8000,done.set)
            due+=.02
            await asyncio.sleep(max(0,due-time.monotonic()))

    async def play(self, fixture, transcript, label):
        pcm,rate=read_pcm(fixture,trim=True)
        assert rate==8000
        encoded=pcm16_to_pcmu(pcm)
        done=asyncio.Event()
        self.rig.stt_script.append(transcript)
        chunks=[encoded[i:i+160] for i in range(0,len(encoded),160)]
        for i,chunk in enumerate(chunks):
            await self.audio_queue.put((chunk.ljust(160,b'\xff'),label,i==len(chunks)-1,done))
        await asyncio.wait_for(done.wait(),8)
        return self.labels[label]

    async def __aenter__(self):
        self.started=time.monotonic()
        env={'GROQ_API_KEY':'offline','GEMINI_API_KEY':'offline',
             'ELEVENLABS_API_KEY':'offline','TELNYX_API_KEY':'offline',
             'ELEVENLABS_TTS_MODELS':'offline','ELEVENLABS_SAMPLE_RATE':'16000',
             'AI_CALLING_PHRASE_CACHE':str(self.tmp),'AI_CALLING_PROFILE_DB':str(self.tmp/'profile.db'),
             'AI_CALLING_SILENCE_SECONDS':str(self.silence_seconds),'AI_CALLING_START_TIMEOUT_SECONDS':'8',
             'GEMINI_FAST_MODELS':'bench-model','GEMINI_FRONTIER_MODELS':'bench-model',
             'AI_CALLING_TURN_STACK':self.mode,
             'AI_CALLING_FILTER_INCOMPLETE_TURNS':'1' if self.completeness else '0'}
        self.stack.enter_context(patch.dict(os.environ,env))
        self.stack.enter_context(patch('google.genai.Client',self.rig.genai_client))
        self.stack.enter_context(patch('pipecat.services.whisper.base_stt.AsyncOpenAI',self.rig.openai_client))
        self.stack.enter_context(patch('pipecat.services.websocket_service.websocket_connect',self.rig.websocket_connect))
        original=live.build_live_pipeline
        def capture(*args,**kwargs):
            result=original(*args,**kwargs)
            self.worker,self.ladder,self.services=result
            assert isinstance(self.services['stt'],GroqSTTService)
            assert isinstance(self.services['llm'],GoogleLLMService)
            assert isinstance(self.services['tts'],ElevenLabsTTSService)
            self.trace.services=self.services
            activity=self.services['activity']
            self.user_start_spy=self.stack.enter_context(patch.object(
                activity,'user_started',wraps=activity.user_started))
            self.user_stop_spy=self.stack.enter_context(patch.object(
                activity,'user_stopped',wraps=activity.user_stopped))
            self.worker.add_observer(self.trace)
            return result
        self.stack.enter_context(patch.object(live,'build_live_pipeline',side_effect=capture))
        self.job=live.LiveCallJob(to='+12025550101',business='Offline bench',
            objective='Compare a scripted conversation',max_minutes=1,playbook=self.playbook)
        cache=live.FixedPhraseCache(self.tmp,namespace='offline/offline/pcm_16000')
        cache.store(self.job.opening,read_pcm('disclosure')[0])
        websocket=WebSocket({'type':'websocket','path':'/telnyx/media/client-seam',
            'headers':[],'query_string':b''},self.incoming.get,self.send)
        await self.incoming.put({'type':'websocket.connect'})
        await websocket.accept()
        for item in ({'event':'connected','version':'1.0.0'},
                     {'event':'start','sequence_number':'1','stream_id':'stream-offline',
                      'start':{'call_control_id':'control-offline','media_format':{
                          'encoding':'PCMU','sample_rate':8000,'channels':1}}}):
            await self.incoming.put({'type':'websocket.receive','text':json.dumps(item)})
        self.running=asyncio.create_task(live.handle_media_websocket(websocket,'client-seam',
            self.job,live.LiveCredentialRefs(elevenlabs_voice_id='offline'),self.adapter,
            ('conn','+12025550100','+12025550101'),str(self.tmp/'session.jsonl'),
            models={'fast':'bench-model','frontier':'bench-model','stt':'bench-whisper'}))
        self.pump_task=asyncio.create_task(self.pump())
        try:
            await self.wait(lambda:bool(self.trace.bot_starts),8)
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(self,exc_type,exc,tb):
        try:
            self.adapter.ended=True
            await self.incoming.put({'type':'websocket.disconnect','code':1000})
            if self.running:
                try:self.record=await asyncio.wait_for(asyncio.shield(self.running),8)
                except (Exception,asyncio.CancelledError):
                    self.running.cancel()
                    await asyncio.gather(self.running,return_exceptions=True)
            if self.pump_task:
                self.pump_task.cancel()
                await asyncio.gather(self.pump_task,return_exceptions=True)
            await self.rig.cleanup()
        finally:
            self.stack.close()

    def results(self):
        offset=lambda row:{**row,'t':round(row['t']-self.started,6)}
        return {'scope':'SYNTHETIC_SPEECH_REAL_SERVICES_FAKE_PROVIDER_CLIENTS',
            'mode':self.mode,'playbook':self.playbook,'completeness_requested':self.completeness,
            'stt_calls':[offset(r) for r in self.rig.stt_calls],
            'llm_calls':[offset(r) for r in self.rig.llm_calls],
            'tts_calls':[offset(r) for r in self.rig.tts_calls],
            'events':[offset(r) for r in self.trace.events],
            'wire':[offset(r) for r in self.wire],
            'user_ends':{k:round(v-self.started,6) for k,v in self.labels.items()},
            'service_classes':{k:type(self.services[k]).__module__+'.'+type(self.services[k]).__name__
                               for k in ('stt','llm','tts')},
            'service_mro':{k:[c.__module__+'.'+c.__name__ for c in type(self.services[k]).__mro__]
                           for k in ('stt','llm','tts')},
            'effective_turn_policy':self.services.get('turn_policy',{'mode':'legacy'}),
            'activity_callbacks':{'user_started':self.user_start_spy.call_count,
                                  'user_stopped':self.user_stop_spy.call_count},
            'unexpected_stt_calls':self.rig.unexpected_stt_calls,
            'fixture_errors':self.rig.errors,
            'stt_input_pcm_energy':self.trace.input_energy,
            'cleanup_pending_tasks':self.record.get('cleanup_pending_tasks') if self.record else None,
            'provider_network_calls':0,'carrier_playback_verified':False}


async def run_scenario(name, *, mode='legacy', completeness=False, correction_pause=.45):
    with tempfile.TemporaryDirectory() as tmp:
        session=BenchSession(tmp,mode=mode,completeness=completeness,
                             playbook='appointment_scheduling' if name=='date_correction' else '',
                             silence_seconds=2 if name=='idle_empty_reprompt' else 60)
        async with session as bench:
            if name in ('opening_overtalk','late_opening_overtalk'):
                await asyncio.sleep(2.0 if name=='late_opening_overtalk' else .25)
                await bench.play('hello','Hello there I can hear you.','overtalk')
                await bench.wait(lambda:bool(bench.trace.bot_stops),8)
                await asyncio.sleep(.2)
            elif name in ('normal','backchannel','interjection','empty_turn'):
                await bench.wait(lambda:len(bench.trace.bot_stops)>=1,8)
                await bench.play('hello','Hello there I can hear you.','hello')
                await bench.wait(lambda:len(bench.trace.bot_starts)>=2,10)
                if name in ('backchannel','interjection','empty_turn'):
                    await asyncio.sleep(.15)
                    fixture='yeah' if name!='interjection' else 'interject'
                    text={'backchannel':'yeah','interjection':'please wait','empty_turn':''}[name]
                    await bench.play(fixture,text,name)
                await bench.wait(lambda:len(bench.trace.bot_stops)>=2,8)
                await asyncio.sleep(.35)
            elif name=='idle_empty_reprompt':
                await bench.wait(lambda:len(bench.trace.bot_stops)>=1,8)
                await bench.play('interject','','empty_idle')
                await bench.wait(lambda:any(r['text']=='Are you still there?' for r in bench.rig.tts_calls),8)
                await bench.wait(lambda:len(bench.trace.bot_stops)>=2,5)
            elif name=='date_correction':
                await bench.wait(lambda:len(bench.trace.bot_stops)>=1,8)
                await bench.play('tuesday','I can do Tuesday','tuesday')
                await asyncio.sleep(correction_pause)
                await bench.play('thursday','no wait Thursday afternoon','thursday')
                await bench.wait(lambda:any('Thursday' in r['text'] for r in bench.rig.tts_calls),10)
                await asyncio.sleep(2)
            else:
                raise ValueError('Unknown scenario')
        result=session.results()
        result['scenario']=name
        result['correction_pause_seconds']=correction_pause if name=='date_correction' else None
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',choices=['normal','opening_overtalk','late_opening_overtalk','backchannel','interjection','empty_turn','idle_empty_reprompt','date_correction'],required=True)
    parser.add_argument('--mode',choices=['legacy','explicit'],default='legacy')
    parser.add_argument('--completeness',action='store_true')
    parser.add_argument('--correction-pause',type=float,default=.45)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    def deny_network(event,details):
        if event in ('socket.connect','socket.sendto'):
            raise PermissionError('Client-seam bench forbids network access')
    sys.addaudithook(deny_network)
    os.environ['NLTK_DATA']=str(ROOT/'nltk_data')
    import nltk.data
    nltk.data.path.insert(0,str(ROOT/'nltk_data'))
    if not 0 <= args.correction_pause <= 5:
        parser.error('correction-pause must be 0..5 seconds')
    result=asyncio.run(run_scenario(args.scenario,mode=args.mode,
        completeness=args.completeness,correction_pause=args.correction_pause))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('scope','scenario','mode','service_classes',
          'unexpected_stt_calls','cleanup_pending_tasks')},indent=2))


if __name__=='__main__':
    main()
