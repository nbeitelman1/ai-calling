"""Qualification test-call dialer, Oct 29, 2026 — derived from
tools/dial_measure_20261029.py (same Telnyx adapter source and media module),
repointed to Nathan's own verified number under his fresh per-call
authorization for the controlled live qualification (return handoff §13).

One call only, to Nathan's verified number: max 10 minutes, $1.00 hard cap,
disclosure first. Prints NO credential, token, or surrogate value anywhere.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, "/home/hatch/workspace/ai-calling/server")
from telnyx_adapter import AdapterConfig, TelnyxAdapter
from telnyx_adapter.config import Approval
from telnyx_media import streaming_start_request

CONNECTION_ID = "3081546725312169326"
FROM = "+13214978649"
TO = "+13218315278"
HOST = os.environ.get("AI_CALLING_PUBLIC_HOST", "").strip()
DB_PATH = "/home/hatch/workspace/ai-calling/logs/webhook.db"
LOG_PATH = "/home/hatch/workspace/ai-calling/logs/qualification-call-2026-10-29.jsonl"
MAX_SECONDS = 600
ANSWER_TIMEOUT_SECONDS = int(os.environ.get("AI_CALLING_ANSWER_TIMEOUT_SECONDS", "45"))
CAP_DOLLARS = 1.00
RATE_PER_MIN = 0.11


def log(msg):
    print(msg, flush=True)


def http_json(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    # tunl's Cloudflare edge bans the default Python-urllib signature
    # (error 1010, proven Oct 29 ~10:54pm); present a normal UA instead.
    req.add_header("User-Agent", "Mozilla/5.0 (ai-calling qualification dialer)")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode("utf-8", "replace")[:500]}


def telnyx_post(path, body):
    url = "https://api.telnyx.com" + path
    key = os.environ.get("TELNYX_API_KEY", "").strip()
    if not key:
        sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
        from dynamic_credentials import dynamic_credential_entry
        entry = dynamic_credential_entry("custom.telnyx")
        key = str(entry["surrogate"]).strip()
    if not key:
        raise RuntimeError("Telnyx credential is not provisioned")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (ai-calling qualification dialer)")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode("utf-8", "replace")[:500]}


def read_pubkey():
    value = os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY", "").strip()
    if value:
        return value
    env_file = "/home/hatch/workspace/ai-calling/.webhook-public-key"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELNYX_WEBHOOK_PUBLIC_KEY="):
                    return line.split("=", 1)[1]
    with open("/etc/systemd/system/ai-calling-live.service") as f:
        for line in f:
            if line.startswith("Environment=TELNYX_WEBHOOK_PUBLIC_KEY="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("public key not found in unit file")


def read_internal_token():
    with open("/home/hatch/workspace/ai-calling/.internal-token") as f:
        for line in f:
            line = line.strip()
            if line.startswith("INTERNAL_TOKEN="):
                return line.split("=", 1)[1]
    raise RuntimeError("internal token not found")


def hangup(control_id, adapter, attempt_id):
    try:
        adapter.request_stop(attempt_id=attempt_id, reason="dialer stop")
        cmd = adapter.hangup_command(attempt_id)
        if cmd is not None:
            st, response = telnyx_post(cmd["path"], cmd["json"])
            if st == 200:
                adapter.acknowledge_hangup(attempt_id, response)
            log(f"HANGUP via adapter command: status={st}")
            return st == 200
    except Exception as exc:
        log(f"HANGUP adapter command failed: {type(exc).__name__}")
    log("HANGUP not sent: canonical command unavailable; operator reconciliation required")
    return False


def health_get(url):
    """Health probe. The public (https) check goes through curl: Pinggy's
    HTTP/1.1 response path drops response bodies for urllib clients
    (IncompleteRead, 0 of 35 bytes, proven Oct 30, 2026) while curl
    receives the full body through the same path. The gate still
    requires HTTP 200 AND the server's real health JSON; the local
    check keeps using http_json."""
    if url.startswith("https://"):
        proc = subprocess.run(
            ["curl", "-sS", "-m", "20", "-w", "\n%{http_code}", url],
            capture_output=True, text=True, timeout=30)
        out = proc.stdout.rstrip("\n")
        raw, _, code = out.rpartition("\n")
        st = int(code) if code.isdigit() else 0
        body = json.loads(raw) if raw else {}
        return st, body
    return http_json("GET", url)


def main():
    if os.environ.get("AI_CALLING_DIAL_APPROVED", "") != "YES_THIS_TEST_CALL":
        log("ABORT: fresh per-call approval is not present")
        return 2
    if not HOST or "/" in HOST:
        log("ABORT: set AI_CALLING_PUBLIC_HOST to the freshly verified host")
        return 2
    for url in ("http://127.0.0.1:8001/healthz", f"https://{HOST}/healthz"):
        ok = False
        for attempt_no in range(3):
            try:
                st, body = health_get(url)
            except Exception as exc:
                # Transient egress/tunnel hiccup (RemoteDisconnected seen
                # Oct 29 ~10:58pm while the tunnel itself stayed up).
                log(f"HEALTH {url} attempt {attempt_no + 1} error: "
                    f"{type(exc).__name__}")
                time.sleep(3)
                continue
            log(f"HEALTH {url} -> {st} {body}")
            if st == 200:
                ok = True
                break
            time.sleep(3)
        if not ok:
            log("ABORT: health check failed before dial")
            return 2

    attempt_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    token = read_internal_token()
    log(f"ATTEMPT {attempt_id}")
    log(f"JOB {job_id}")

    reg_body = {
        "attempt_id": attempt_id,
        "job": {"to": TO, "business": "Nathan qualification call",
                "objective": "controlled live qualification", "test_call": True,
                "opening": "Hi, this is Nathan's assistant calling on his behalf."},
        "refs": {"elevenlabs_voice_id": "cgSgspJ2msm6clMCkdW9"},
        "expected_route": [CONNECTION_ID, FROM, TO],
        "log_path": os.path.basename(LOG_PATH),
    }
    st, reg = http_json("POST", "http://127.0.0.1:8001/internal/register-media-job",
                        reg_body, {"X-Internal-Token": token})
    log(f"REGISTER -> {st} {reg}")
    if st != 200 or not reg.get("registered"):
        log("ABORT: media job registration failed; NOT dialing")
        return 2
    del token

    adapter = TelnyxAdapter(
        DB_PATH, read_pubkey(),
        AdapterConfig(authorized_destinations=(TO,)))
    approval = Approval(job_id=job_id, destination=TO, max_minutes=10,
                        estimated_cost_cents=100, approver_ref="nathan")
    adapter.prepare(job_id=job_id, connection_id=CONNECTION_ID,
                    from_number=FROM, to_number=TO,
                    attempt_id=attempt_id, approval=approval, max_minutes=10)
    claim = adapter.claim_submission(attempt_id)
    log("CLAIM ok: client_state_encoded=True")

    st, resp = telnyx_post(claim["path"], claim["json"])
    log(f"DIAL -> {st}")
    if st != 200:
        log(f"DIAL REFUSED: {resp}")
        log("NOT retrying (adapter single-claim model).")
        return 3
    try:
        control_id, _, _ = adapter.record_response(attempt_id, resp)
    except Exception as exc:
        log(f"RECORD_RESPONSE FAILED: {type(exc).__name__}: {exc}")
        return 3
    log(f"CALL_CONTROL_ID {control_id}")
    dial_t = time.time()

    answered = False
    state = "?"
    for _ in range(ANSWER_TIMEOUT_SECONDS):
        try:
            state = adapter.get_attempt(attempt_id).get("state")
        except Exception:
            state = "?"
        if state == "answered":
            answered = True
            break
        if state == "hangup":
            break
        time.sleep(1)
    log(f"ANSWERED={answered} state={state} after {time.time()-dial_t:.1f}s")
    if not answered:
        log(f"NO ANSWER within {ANSWER_TIMEOUT_SECONDS}s; hanging up")
        hangup(control_id, adapter, attempt_id)
        return 4
    spec = streaming_start_request(f"wss://{HOST}/telnyx/media/{attempt_id}")
    st, sresp = telnyx_post(spec["path"].format(call_control_id=control_id),
                            spec["json"])
    log(f"STREAMING_START -> {st} {str(sresp)[:300]}")
    if st != 200:
        log("STREAMING_START rejected; hanging up")
        hangup(control_id, adapter, attempt_id)
        return 5

    start = time.time()
    final_state = None
    while True:
        elapsed = time.time() - start
        try:
            final_state = adapter.get_attempt(attempt_id).get("state")
        except Exception:
            final_state = "?"
        if final_state == "hangup":
            log(f"MONITOR: hangup observed at {elapsed:.1f}s")
            break
        if (elapsed / 60.0) * RATE_PER_MIN >= CAP_DOLLARS:
            log(f"MONITOR: $1 projected cap reached at {elapsed:.1f}s; hanging up")
            hangup(control_id, adapter, attempt_id)
            break
        if elapsed >= MAX_SECONDS:
            log("MONITOR: 10-minute max reached; hanging up")
            hangup(control_id, adapter, attempt_id)
            break
        time.sleep(5)
    time.sleep(3)
    try:
        final_state = adapter.get_attempt(attempt_id).get("state")
    except Exception:
        pass
    log(f"FINAL_STATE {final_state} DURATION_S {time.time()-dial_t:.1f}")

    record = None
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("call_control_id") == control_id:
                    record = rec
    if record:
        log(f"RECORD outcome={record.get('outcome')} "
            f"minutes={record.get('minutes')} "
            f"estimated_cost_cents={record.get('estimated_cost_cents')}")
        for turn in record.get("transcript", []):
            log(f"TRANSCRIPT {turn.get('role')}: {turn.get('text')}")
    else:
        log("RECORD: no transcript/cost record found in log path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
