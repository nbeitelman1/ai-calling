"""Production media handler, transport, serializer and TTS cache; fake providers.

This proves local wire output, not carrier receipt or intelligible speech.
"""
import asyncio
import base64
import json
import math
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import live_pipeline as live
from pipecat.frames.frames import (InputAudioRawFrame, TranscriptionFrame,
    LLMContextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    LLMTextFrame, TTSStartedFrame, TTSStoppedFrame, TTSAudioRawFrame, TTSTextFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.utils.text.base_text_aggregator import AggregationType
from starlette.websockets import WebSocket


def tone(rate=16000, duration=.25):
    return b''.join(struct.pack('<h',int(5000*math.sin(2*math.pi*440*i/rate)))
                    for i in range(int(rate*duration)))


class OfflineSTT(FrameProcessor):
    Settings=GroqSTTService.Settings
    def __init__(self, **kwargs):
        super().__init__()
        self.audio_frames=0
    async def process_frame(self, frame, direction):
        await super().process_frame(frame,direction)
        if isinstance(frame,InputAudioRawFrame) and direction==FrameDirection.DOWNSTREAM:
            self.audio_frames+=1
        await self.push_frame(frame,direction)


class OfflineLLM(FrameProcessor):
    Settings=GoogleLLMService.Settings
    def __init__(self, **kwargs):
        super().__init__()
        self.context_requests=0
    async def process_frame(self, frame, direction):
        await super().process_frame(frame,direction)
        if isinstance(frame,LLMContextFrame) and direction==FrameDirection.DOWNSTREAM:
            self.context_requests+=1
            self.last_messages=json.loads(json.dumps(frame.context.messages))
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame('Offline response.'))
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame,direction)


class OfflineElevenLabs(ElevenLabsTTSService):
    async def _connect(self):
        pass
    async def _disconnect(self):
        pass
    async def run_tts(self,text,context_id):
        if not self.audio_context_available(context_id):
            await self.create_audio_context(context_id)
            yield TTSStartedFrame(context_id=context_id)
        yield TTSAudioRawFrame(tone(),self.sample_rate,1,context_id=context_id)
        # Match the production service's sequencer-owned text fallback.
        yield TTSStoppedFrame(context_id=context_id)


class Adapter:
    def __init__(self, hangup_event=False, hangup_source=None):
        self.claimed=False
        self.settled=None
        self._hangup_event=hangup_event
        self._hangup_source=hangup_source
    def get_attempt(self,_):
        return {'state':'answered','control_id':'control-offline','hangup_ack':False,
                'payload_json':json.dumps({'connection_id':'conn','from':'+12025550100','to':'+12025550101'})}
    def claim_media_job(self,_,now):
        if self.claimed:
            return None
        self.claimed=True
        return {'registered':True}
    def settle_media_job_quotas(self,_,actual):
        self.settled=actual
    def reconcile_call_record(self,_):
        return {'answered_event':True,'hangup_event':self._hangup_event,
                'hangup_source':self._hangup_source}


class TestOfflineMediaSession(unittest.IsolatedAsyncioTestCase):
    async def test_cached_disclosure_reaches_pcmu_wire_and_session_records(self):
        incoming=asyncio.Queue()
        outputs=[]
        audio_seen=asyncio.Event()
        async def send(message):
            outputs.append(message)
            if message['type']=='websocket.send':
                payload=json.loads(message.get('text','{}'))
                if payload.get('event')=='media':
                    audio_seen.set()
        websocket=WebSocket({'type':'websocket','path':'/telnyx/media/offline',
                            'headers':[],'query_string':b''},incoming.get,send)
        await incoming.put({'type':'websocket.connect'})
        await websocket.accept()
        for message in [{'event':'connected','version':'1.0.0'},
                        {'event':'start','sequence_number':'1','stream_id':'stream-offline',
                         'start':{'call_control_id':'control-offline',
                                  'media_format':{'encoding':'PCMU','sample_rate':8000,'channels':1}}}]:
            await incoming.put({'type':'websocket.receive','text':json.dumps(message)})
        with tempfile.TemporaryDirectory() as tmp:
            env={'GROQ_API_KEY':'offline','GEMINI_API_KEY':'offline',
                 'ELEVENLABS_API_KEY':'offline','TELNYX_API_KEY':'offline',
                 'AI_CALLING_PHRASE_CACHE':tmp,'ELEVENLABS_TTS_MODELS':'offline',
                 'ELEVENLABS_SAMPLE_RATE':'16000','AI_CALLING_START_TIMEOUT_SECONDS':'5'}
            refs=live.LiveCredentialRefs(elevenlabs_voice_id='offline')
            job=live.LiveCallJob(to='+12025550101',business='Offline',objective='Offline',max_minutes=1)
            cache=live.FixedPhraseCache(tmp,namespace='offline/offline/pcm_16000')
            cache.store(job.opening,tone())
            adapter=Adapter()
            original_build=live.build_live_pipeline
            captured={}
            def build(*args,**kwargs):
                result=original_build(*args,**kwargs)
                captured['worker'],captured['ladder'],captured['services']=result
                return result
            with patch.dict(os.environ,env), \
                 patch('pipecat.services.groq.stt.GroqSTTService',OfflineSTT), \
                 patch('pipecat.services.google.llm.GoogleLLMService',OfflineLLM), \
                 patch('pipecat.services.elevenlabs.tts.ElevenLabsTTSService',OfflineElevenLabs), \
                 patch.object(live,'build_live_pipeline',side_effect=build), \
                 patch.object(live,'_hangup_via_adapter',new=AsyncMock(return_value=True)) as hangup:
                running=asyncio.create_task(live.handle_media_websocket(
                    websocket,'offline',job,refs,adapter,
                    ('conn','+12025550100','+12025550101'),str(Path(tmp)/'session.jsonl'),
                    models={'fast':'offline','frontier':'offline','stt':'offline'}))
                try:
                    await asyncio.wait_for(audio_seen.wait(),5)
                    # Feed real Telnyx-shaped PCMU packets through the production decoder/VAD.
                    for i in range(1,21):
                        await incoming.put({'type':'websocket.receive','text':json.dumps({
                            'event':'media','stream_id':'stream-offline','sequence_number':str(i+1),
                            'media':{'track':'inbound','payload':base64.b64encode(bytes([255])*160).decode()}})})
                    await asyncio.sleep(.6)
                    self.assertGreater(captured['services']['stt'].audio_frames,0)
                    # Drive a synthetic completed user turn through real context aggregation.
                    user_tap=captured['services']['stt']
                    await user_tap.push_frame(VADUserStartedSpeakingFrame())
                    await user_tap.push_frame(TranscriptionFrame(
                        'Hello there.','offline-user',str(time.time()),finalized=True))
                    await user_tap.push_frame(VADUserStoppedSpeakingFrame())
                    deadline=time.monotonic()+3
                    while not captured['services']['llm'].context_requests and time.monotonic()<deadline:
                        await asyncio.sleep(.05)
                    self.assertGreater(captured['services']['llm'].context_requests,0)
                    deadline=time.monotonic()+3
                    while captured['services']['activity'].bot_cycles < 2 and time.monotonic()<deadline:
                        await asyncio.sleep(.01)
                    self.assertGreaterEqual(captured['services']['activity'].bot_cycles,2)
                    await incoming.put({'type':'websocket.disconnect','code':1000})
                    record=await asyncio.wait_for(asyncio.shield(running),8)
                    self.assertEqual(record['outcome'],'media_disconnected')
                    self.assertTrue(record['pipeline_started'])
                    self.assertEqual(record['cleanup_pending_tasks'],0)
                    self.assertFalse(record['hangup_required'])
                    self.assertGreater(record['media_payload_bytes'],0)
                    self.assertTrue(any(t['text']=='Offline response.' for t in record['transcript']))
                    self.assertIsNotNone(adapter.settled)
                    self.assertTrue((Path(tmp)/'session.jsonl').is_file())
                    self.assertTrue(hangup.await_count>=1)
                    media=[json.loads(m['text']) for m in outputs if m['type']=='websocket.send' and 'text' in m]
                    pcmu=b''.join(base64.b64decode(m['media']['payload']) for m in media if m.get('event')=='media')
                    self.assertGreaterEqual(len(pcmu),4000,
                        'Two 250ms tones at 8kHz must not be stranded in the streaming resampler')
                    self.assertGreater(len(set(pcmu)),1,'Non-silent synthetic audio must reach wire')
                    prior_assistant=[m for m in captured['services']['llm'].last_messages if m.get('role')=='assistant']
                    self.assertEqual(str(prior_assistant).count(live.DISCLOSURE_OPENING),1)
                    final_assistant=[m for m in captured['services']['context'].messages if m.get('role')=='assistant']
                    self.assertEqual(str(final_assistant).count('Offline response.'),1)
                    self.assertFalse(captured['services']['tts']._audio_contexts)
                    self.assertEqual(record['tts_characters'],len('Offline response.'))
                    print('WIRE_EVIDENCE '+json.dumps({
                        'scope':'synthetic_providers_real_local_transport',
                        'input_pcmu_bytes':record['media_payload_bytes'],
                        'output_pcmu_bytes':len(pcmu),
                        'output_frames':record['outbound_media_frames'],
                        'llm_requests':captured['services']['llm'].context_requests,
                        'cleanup_pending_tasks':record['cleanup_pending_tasks'],
                        'carrier_playback_verified':False}))
                finally:
                    if not running.done():
                        running.cancel()
                    await asyncio.gather(running,return_exceptions=True)

    async def test_socket_close_after_signed_hangup_records_hangup(self):
        # Same wire flow as the genuine-drop test above, but the
        # adapter's reconciliation reports a signed call.hangup from
        # the callee: the record must say "hangup", not
        # "media_disconnected" (the Oct 30 live-call label defect).
        incoming=asyncio.Queue()
        outputs=[]
        audio_seen=asyncio.Event()
        async def send(message):
            outputs.append(message)
            if message['type']=='websocket.send':
                payload=json.loads(message.get('text','{}'))
                if payload.get('event')=='media':
                    audio_seen.set()
        websocket=WebSocket({'type':'websocket','path':'/telnyx/media/offline',
                            'headers':[],'query_string':b''},incoming.get,send)
        await incoming.put({'type':'websocket.connect'})
        await websocket.accept()
        for message in [{'event':'connected','version':'1.0.0'},
                        {'event':'start','sequence_number':'1','stream_id':'stream-offline',
                         'start':{'call_control_id':'control-offline',
                                  'media_format':{'encoding':'PCMU','sample_rate':8000,'channels':1}}}]:
            await incoming.put({'type':'websocket.receive','text':json.dumps(message)})
        with tempfile.TemporaryDirectory() as tmp:
            env={'GROQ_API_KEY':'offline','GEMINI_API_KEY':'offline',
                 'ELEVENLABS_API_KEY':'offline','TELNYX_API_KEY':'offline',
                 'AI_CALLING_PHRASE_CACHE':tmp,'ELEVENLABS_TTS_MODELS':'offline',
                 'ELEVENLABS_SAMPLE_RATE':'16000','AI_CALLING_START_TIMEOUT_SECONDS':'5'}
            refs=live.LiveCredentialRefs(elevenlabs_voice_id='offline')
            job=live.LiveCallJob(to='+12025550101',business='Offline',objective='Offline',max_minutes=1)
            cache=live.FixedPhraseCache(tmp,namespace='offline/offline/pcm_16000')
            cache.store(job.opening,tone())
            adapter=Adapter(hangup_event=True, hangup_source='callee')
            with patch.dict(os.environ,env), \
                 patch('pipecat.services.groq.stt.GroqSTTService',OfflineSTT), \
                 patch('pipecat.services.google.llm.GoogleLLMService',OfflineLLM), \
                 patch('pipecat.services.elevenlabs.tts.ElevenLabsTTSService',OfflineElevenLabs), \
                 patch.object(live,'_hangup_via_adapter',new=AsyncMock(return_value=True)):
                running=asyncio.create_task(live.handle_media_websocket(
                    websocket,'offline',job,refs,adapter,
                    ('conn','+12025550100','+12025550101'),str(Path(tmp)/'session.jsonl'),
                    models={'fast':'offline','frontier':'offline','stt':'offline'}))
                try:
                    await asyncio.wait_for(audio_seen.wait(),5)
                    await incoming.put({'type':'websocket.disconnect','code':1000})
                    record=await asyncio.wait_for(asyncio.shield(running),8)
                    self.assertEqual(record['outcome'],'hangup')
                    self.assertEqual(
                        record['carrier_reconciliation']['hangup_source'],'callee')
                    self.assertFalse(record['hangup_required'])
                    self.assertTrue(record['pipeline_started'])
                    self.assertIsNotNone(adapter.settled)
                finally:
                    if not running.done():
                        running.cancel()
                    await asyncio.gather(running,return_exceptions=True)
