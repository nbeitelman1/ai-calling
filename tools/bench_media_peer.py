"""Bench harness: emulate a Telnyx media-streaming peer against the
RUNNING live server on 127.0.0.1:8002. No Telnyx API call is made:
the adapter attempt is bound with fabricated provider ids via
record_response (local DB only), exactly as the dialer binds real ids
after its dial. The websocket peer then speaks the documented Telnyx
media protocol (connected -> start -> media) and records everything
the server sends back.
"""
import asyncio
import base64
import json
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, "/home/hatch/workspace/ai-calling/server")

CONNECTION_ID = "3081546725312169326"
FROM = "+13214978649"
TO = "+13218315278"
DB_PATH = "/tmp/bench.db"
LOG_NAME = "bench-2026-10-30-inst.jsonl"
ROOT = "/home/hatch/workspace/ai-calling"


def _file_value(path, prefix):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(prefix):
                return line.split("=", 1)[1]
    raise RuntimeError(f"{prefix} not found in {path}")


def http_json(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (bench harness)")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode("utf-8", "replace")[:500]}


def stamp(t0):
    return f"{time.monotonic() - t0:7.3f}s"


def main():
    attempt_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    print(f"ATTEMPT {attempt_id}\nJOB {job_id}", flush=True)

    token = _file_value(f"{ROOT}/.internal-token", "INTERNAL_TOKEN=")
    reg_body = {
        "attempt_id": attempt_id,
        "job": {"to": TO, "business": "Nathan qualification call",
                "objective": "bench diagnosis (no dial)", "test_call": True,
                "opening": "Hi, this is Nathan's assistant calling on his behalf."},
        "refs": {"elevenlabs_voice_id": "cgSgspJ2msm6clMCkdW9"},
        "expected_route": [CONNECTION_ID, FROM, TO],
        "log_path": LOG_NAME,
    }
    st, reg = http_json("POST", "http://127.0.0.1:8002/internal/register-media-job",
                        reg_body, {"X-Internal-Token": token})
    print(f"REGISTER -> {st} {reg}", flush=True)
    if st != 200 or not reg.get("registered"):
        print("ABORT: registration failed")
        return 2
    del token

    from telnyx_adapter import AdapterConfig, TelnyxAdapter
    from telnyx_adapter.config import Approval
    pubkey = _file_value(f"{ROOT}/.webhook-public-key", "TELNYX_WEBHOOK_PUBLIC_KEY=")
    adapter = TelnyxAdapter(DB_PATH, pubkey,
                            AdapterConfig(authorized_destinations=(TO,)))
    approval = Approval(job_id=job_id, destination=TO, max_minutes=10,
                        estimated_cost_cents=100, approver_ref="bench")
    adapter.prepare(job_id=job_id, connection_id=CONNECTION_ID,
                    from_number=FROM, to_number=TO,
                    attempt_id=attempt_id, approval=approval, max_minutes=10)
    adapter.claim_submission(attempt_id)
    ccid = "bench-cc-" + uuid.uuid4().hex
    adapter.record_response(attempt_id, {"data": {
        "call_control_id": ccid,
        "call_leg_id": "bench-leg-" + uuid.uuid4().hex,
        "call_session_id": "bench-sess-" + uuid.uuid4().hex}})
    print(f"BOUND control_id={ccid} state="
          f"{adapter.get_attempt(attempt_id).get('state')}", flush=True)

    # Inbound speech: a cached bot phrase replayed as the "caller" —
    # real intelligible speech for VAD/STT without any provider call.
    from live_pipeline import FixedPhraseCache
    from telnyx_media import pcm16_to_pcmu
    cache = FixedPhraseCache(
        f"{ROOT}/logs/phrase-cache",
        namespace="cgSgspJ2msm6clMCkdW9/eleven_flash_v2_5/pcm_16000",
        sample_rate=16000)
    pcm16 = cache.load("Are you still there?")
    assert pcm16, "speech phrase not in cache"
    try:
        import audioop
        pcm8, _ = audioop.ratecv(pcm16, 2, 1, 16000, 8000, None)
    except ImportError:
        import struct
        samples = struct.unpack(f"<{len(pcm16)//2}h", pcm16)
        pcm8 = b"".join(samples[i].to_bytes(2, "little", signed=True)
                        for i in range(0, len(samples), 2))
    speech_ulaw = pcm16_to_pcmu(pcm8)
    silence_ulaw = b"\xff" * 160  # 20ms of mu-law silence
    print(f"SPEECH prepared: {len(pcm8)//2} samples @8k "
          f"({len(pcm8)/16000:.2f}s), {len(speech_ulaw)} ulaw bytes", flush=True)

    stream_id = "bench-stream-" + uuid.uuid4().hex
    result = asyncio.run(run_session(attempt_id, ccid, stream_id, speech_ulaw,
                                     silence_ulaw))
    print("SESSION RESULT:", json.dumps(result, indent=1), flush=True)
    return 0


async def run_session(attempt_id, ccid, stream_id, speech_ulaw, silence_ulaw):
    import websockets

    t0 = time.monotonic()
    out = {"media_msgs": 0, "media_bytes": 0, "events": [], "other": [],
           "opening_bytes_before_speech": None, "close": None}
    seq = 0

    def note(kind, detail):
        out["events"].append((round(time.monotonic() - t0, 3), kind, detail))

    try:
        async with websockets.connect(
                f"ws://127.0.0.1:8002/telnyx/media/{attempt_id}",
                max_size=None) as ws:
            note("ws", "connected")
            await ws.send(json.dumps({
                "event": "connected", "version": "1.0.0", "seq_number": "0",
                "call_control_id": ccid, "call_session_id": "bench"}))
            await ws.send(json.dumps({
                "event": "start", "seq_number": "1", "stream_id": stream_id,
                "start": {"call_control_id": ccid, "call_session_id": "bench",
                          "media_format": {"encoding": "PCMU",
                                           "sample_rate": 8000, "channels": 1},
                          "from": FROM, "to": TO}}))
            note("ws", "connected+start sent")

            async def recv_loop():
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            out["other"].append(
                                (round(time.monotonic() - t0, 3), "nonjson",
                                 str(raw)[:120]))
                            continue
                        ev = msg.get("event")
                        if ev == "media":
                            out["media_msgs"] += 1
                            n = len(base64.b64decode(msg["media"]["payload"]))
                            out["media_bytes"] += n
                            if out["media_msgs"] <= 3 or out["media_msgs"] % 100 == 0:
                                note("media", f"#{out['media_msgs']} +{n}B "
                                              f"total={out['media_bytes']}B")
                        else:
                            out["other"].append(
                                (round(time.monotonic() - t0, 3), ev,
                                 str(msg)[:160]))
                except Exception as exc:
                    out["close"] = f"{type(exc).__name__}: {exc}"

            rt = asyncio.create_task(recv_loop())

            # Phase A: up to 18s waiting for the opening.
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline and out["media_bytes"] < 8000:
                if rt.done():
                    break
                await asyncio.sleep(0.1)
            out["opening_bytes_before_speech"] = out["media_bytes"]
            note("phaseA", f"media_bytes={out['media_bytes']} "
                           f"msgs={out['media_msgs']} rt_done={rt.done()}")

            # Phase B: send the speech (20ms chunks, ~real-time), then
            # 1.5s of silence so VAD stop + STT segment commit can fire.
            if not rt.done():
                base_msgs, base_bytes = out["media_msgs"], out["media_bytes"]
                for off in range(0, len(speech_ulaw), 160):
                    seq += 1
                    await ws.send(json.dumps({
                        "event": "media", "sequence_number": str(seq),
                        "stream_id": stream_id,
                        "media": {"track": "inbound", "chunk": str(seq),
                                  "timestamp": str(seq * 20),
                                  "payload": base64.b64encode(
                                      speech_ulaw[off:off + 160]).decode()}}))
                    await asyncio.sleep(0.02)
                for _ in range(75):
                    seq += 1
                    await ws.send(json.dumps({
                        "event": "media", "sequence_number": str(seq),
                        "stream_id": stream_id,
                        "media": {"track": "inbound", "chunk": str(seq),
                                  "timestamp": str(seq * 20),
                                  "payload": base64.b64encode(
                                      silence_ulaw).decode()}}))
                    await asyncio.sleep(0.02)
                note("phaseB", f"speech sent ({seq} chunks); "
                               f"reply baseline msgs={base_msgs} bytes={base_bytes}")
                out["reply_baseline"] = (base_msgs, base_bytes)

                # Phase C: up to 75s for a reply turn (LLM streaming
                # through the egress proxy is slow); feed 1s of silence
                # every ~6s so the peer looks like a quiet caller.
                # Baseline is re-snapshotted HERE: the opening audio is
                # still streaming in during phase B, so the phase-B
                # baseline would mistake its tail for a reply.
                base_msgs, base_bytes = out["media_msgs"], out["media_bytes"]
                note("phaseC", f"reply watch baseline msgs={base_msgs} "
                               f"bytes={base_bytes}")
                deadline = time.monotonic() + 75
                last_feed = time.monotonic()
                while time.monotonic() < deadline:
                    if rt.done():
                        break
                    if out["media_bytes"] > base_bytes + 4000:
                        break
                    if time.monotonic() - last_feed >= 6:
                        last_feed = time.monotonic()
                        for _ in range(50):
                            seq += 1
                            await ws.send(json.dumps({
                                "event": "media", "sequence_number": str(seq),
                                "stream_id": stream_id,
                                "media": {"track": "inbound", "chunk": str(seq),
                                          "timestamp": str(seq * 20),
                                          "payload": base64.b64encode(
                                              silence_ulaw).decode()}}))
                            await asyncio.sleep(0.02)
                    await asyncio.sleep(0.25)
                note("phaseC", f"final msgs={out['media_msgs']} "
                               f"bytes={out['media_bytes']} rt_done={rt.done()}")
            try:
                await ws.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(rt, timeout=5)
            except Exception:
                rt.cancel()
    except Exception as exc:
        out["close"] = f"session: {type(exc).__name__}: {exc}"
    out["elapsed"] = round(time.monotonic() - t0, 2)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
