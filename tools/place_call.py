"""place_call.py — the one callable entry point for the ai-calling system.

Places ONE call through the already-running live stack (systemd
ai-calling-live + the media tunnel), reusing the adapter's gated
dial machinery: media-job registration, budget-reserving prepare(),
single-use claim, one dial, streaming start, and a monitored wait for
the signed hangup. There are no automatic retries: any failure exits
nonzero with the failing stage named, and an attempt whose outcome is
unknown stays unresolved for operator reconciliation, exactly as the
adapter's model requires.

Safety gates, in order, before any dial:
  1. destination: --to must be E.164 and on the allowlist
     (AI_CALLING_AUTHORIZED_DESTINATIONS, comma-separated, extends
     the built-in list). NOTE, Telnyx trial constraint: the carrier
     also refuses any number that is not a *verified* number on the
     trial account, whatever the allowlist says — a dial to an
     unverified number fails at the carrier, by design.
  2. stack: ai-calling-live and ai-calling-pinggy must be active
     (or pass --auto-restore to run tools/restore_stack.sh first).
  3. readiness: /readyz must report ready on localhost AND on the
     public tunnel host.
  4. preflight receipt: the service's provider-preflight receipt
     must exist, say ready, and be younger than its 1-hour TTL. A
     stale receipt is refreshed only by a service (re)start, whose
     prestart re-runs the real provider checks — this script never
     forges or backdates a receipt.
  5. one-call gate: no attempt may sit in submit_unknown, initiated
     or answered.
  6. budget: this call's reservation (--cap-cents) must fit the
     monthly cap next to the month's current reserved total. With
     the adapter's release rule, that total is in-flight estimates
     plus finished jobs' recorded spend.
  7. approval: a real dial requires the operator to have set
     AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL in the environment
     after the user's fresh per-call approval. --dry-run runs every
     check above and prints the would-dial summary without dialing
     and without requiring the approval variable.

The conversation always opens with the fixed disclosure line
"Hi, this is Nathan's assistant calling on his behalf." — it is not
a CLI option. Transcript lines print as they finalize into the
session record (the service writes the record at session end, so in
practice they surface the moment the call closes), followed by the
final record summary and the attempt/job ids.

Exit codes: 0 success (or a clean --dry-run); 2 a preflight or
approval stage refused; 3 the carrier refused the dial or the create
response could not be bound; 4 no answer; 5 streaming start refused;
6 the call ended but no session record landed.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from telnyx_adapter import AdapterConfig, TelnyxAdapter  # noqa: E402
from telnyx_adapter.config import Approval  # noqa: E402
from telnyx_media import streaming_start_request  # noqa: E402

DISCLOSURE = "Hi, this is Nathan's assistant calling on his behalf."
DEFAULT_CONNECTION_ID = "3081546725312169326"
DEFAULT_FROM = "+13214978649"
# Nathan's own number: the one destination approved and, on the
# Telnyx trial, verified. Extend via AI_CALLING_AUTHORIZED_DESTINATIONS.
DEFAULT_ALLOWLIST = ("+13218315278",)
DEFAULT_DB = "/home/hatch/workspace/ai-calling/logs/webhook.db"
DEFAULT_LOG_DIR = "/home/hatch/workspace/ai-calling/logs"
DEFAULT_SECRETS_DIR = "/home/hatch/workspace/ai-calling"
RECEIPT_TTL_SECONDS = 3600
MONTHLY_CAP_CENTS = 2000  # AdapterConfig default; Nathan changes it, not us.
RATE_CENTS_PER_MIN = 11.0  # conservative planning rate for the monitor cap


def log(msg):
    print(msg, flush=True)


class Abort(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage
        self.message = message


# -- small pure helpers (unit-tested) ------------------------------------

def allowlist_from_env(env=None):
    env = os.environ if env is None else env
    extra = [p.strip() for p in env.get(
        "AI_CALLING_AUTHORIZED_DESTINATIONS", "").split(",") if p.strip()]
    return tuple(dict.fromkeys((*DEFAULT_ALLOWLIST, *extra)))


def validate_destination(to, allowlist):
    if not isinstance(to, str) or not to.startswith("+") \
            or not to[1:].isdigit() or not 8 <= len(to) <= 16:
        raise ValueError(f"destination {to!r} is not an E.164 number")
    if to not in allowlist:
        raise ValueError(
            f"destination {to} is not on the allowlist "
            f"({', '.join(allowlist)}); extend "
            "AI_CALLING_AUTHORIZED_DESTINATIONS only with the user's approval, "
            "and remember the Telnyx trial also requires a verified number")
    return to


def approval_present(env=None):
    env = os.environ if env is None else env
    return env.get("AI_CALLING_DIAL_APPROVED", "") == "YES_THIS_TEST_CALL"


def _receipt_epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def receipt_status(path, now=None, ttl=RECEIPT_TTL_SECONDS):
    """(ok, detail) for the provider-preflight receipt at path."""
    now = time.time() if now is None else now
    path = Path(path)
    if not path.is_file():
        return False, f"no preflight receipt at {path}"
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return False, f"preflight receipt unreadable: {type(exc).__name__}"
    if data.get("ready") is not True:
        return False, "preflight receipt does not say ready"
    epoch = _receipt_epoch(data.get("checked_at"))
    if epoch is None:
        return False, "preflight receipt has no usable checked_at"
    age = now - epoch
    if age < 0:
        return False, "preflight receipt is dated in the future"
    if age > ttl:
        return False, (f"preflight receipt is {age / 60:.0f} minutes old "
                       f"(TTL {ttl // 60} minutes)")
    return True, f"preflight receipt ready, {age / 60:.0f} minutes old"


def _read_only_db(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def unresolved_attempts(db_path):
    conn = _read_only_db(db_path)
    try:
        rows = conn.execute(
            "SELECT attempt_id, state FROM attempts WHERE state IN "
            "('submit_unknown','initiated','answered') ORDER BY attempt_id"
        ).fetchall()
        return [{"attempt_id": r[0], "state": r[1]} for r in rows]
    finally:
        conn.close()


def monthly_reserved_cents(db_path, month=None):
    month = month or time.strftime("%Y-%m", time.gmtime())
    conn = _read_only_db(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(reserved_cents),0) FROM jobs "
            "WHERE billing_month=?", (month,)).fetchone()
        return int(row[0])
    finally:
        conn.close()


# -- http helpers (same proven shapes as the qualification dialer) -------

def http_json(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (ai-calling place_call)")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode("utf-8", "replace")[:500]}


def health_get(url):
    """Public (https) health goes through curl: the tunnel's HTTP/1.1
    path drops response bodies for urllib clients (proven Oct 30,
    2026); the gate still requires HTTP 200 AND the real JSON body."""
    if url.startswith("https://"):
        proc = subprocess.run(
            ["curl", "-sS", "-m", "20", "-w", "\n%{http_code}", url],
            capture_output=True, text=True, timeout=30)
        out = proc.stdout.rstrip("\n")
        raw, _, code = out.rpartition("\n")
        st = int(code) if code.isdigit() else 0
        return st, (json.loads(raw) if raw else {})
    return http_json("GET", url)


def _read_env_file_value(path, name):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""


def secret_value(name, secrets_dir):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    filenames = {
        "TELNYX_API_KEY": ".provider-credentials",
        "TELNYX_WEBHOOK_PUBLIC_KEY": ".webhook-public-key",
        "INTERNAL_TOKEN": ".internal-token",
    }
    filename = filenames.get(name)
    if filename:
        return _read_env_file_value(str(Path(secrets_dir) / filename), name)
    return ""


def telnyx_post(path, body, api_key):
    req = urllib.request.Request(
        "https://api.telnyx.com" + path,
        data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (ai-calling place_call)")
    req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode("utf-8", "replace")[:500]}


# -- preflight -------------------------------------------------------------

def _service_active(unit):
    proc = subprocess.run(["systemctl", "is-active", unit],
                          capture_output=True, text=True, timeout=15)
    return proc.stdout.strip() == "active"


def run_preflight(args):
    checks = []

    def ok(name, detail):
        checks.append((name, detail))
        log(f"CHECK {name}: ok — {detail}")

    if not _service_active("ai-calling-live") \
            or not _service_active("ai-calling-pinggy"):
        if args.auto_restore:
            script = ROOT / "tools" / "restore_stack.sh"
            log(f"stack is down; running {script} (--auto-restore)")
            proc = subprocess.run(["bash", str(script)],
                                  capture_output=True, text=True, timeout=300)
            sys.stdout.write(proc.stdout)
            if proc.returncode != 0:
                sys.stderr.write(proc.stderr)
                raise Abort("stack", "restore_stack.sh failed; see output above")
        else:
            raise Abort(
                "stack",
                "ai-calling-live / ai-calling-pinggy are not both active. "
                f"Restore with: bash {ROOT / 'tools' / 'restore_stack.sh'} "
                "(or re-run with --auto-restore). No dial attempted.")
    ok("stack", "ai-calling-live and ai-calling-pinggy active")

    for label, url in (("local", "http://127.0.0.1:8001/readyz"),
                       ("public", f"https://{args.host}/readyz")):
        try:
            st, body = health_get(url)
        except Exception as exc:
            raise Abort("readyz", f"{label} /readyz unreachable: "
                                  f"{type(exc).__name__}")
        if st != 200 or body.get("ready") is not True:
            raise Abort("readyz", f"{label} /readyz not ready: {st} {body}")
    ok("readyz", f"local and https://{args.host} report ready")

    receipt_path = Path(args.log_dir) / "provider-preflight.json"
    fine, detail = receipt_status(receipt_path)
    if not fine:
        raise Abort(
            "receipt", f"{detail}. A service restart refreshes the receipt "
            "via prestart (systemctl restart ai-calling-live, or "
            "tools/restore_stack.sh); receipts are never forged. No dial.")
    ok("receipt", detail)

    unresolved = unresolved_attempts(args.db)
    if unresolved:
        named = ", ".join(f"{u['attempt_id']} ({u['state']})"
                          for u in unresolved)
        raise Abort("gate", f"one-call gate is not clear: {named}. "
                            "Reconcile through signed/carrier evidence "
                            "or the operator-resolution tool first.")
    ok("gate", "no unresolved attempts")

    reserved = monthly_reserved_cents(args.db)
    if reserved + args.cap_cents > args.monthly_cap_cents:
        raise Abort(
            "budget",
            f"monthly budget headroom insufficient: {reserved} cents already "
            f"reserved this month + {args.cap_cents} for this call exceeds "
            f"the {args.monthly_cap_cents} cap")
    ok("budget", f"{reserved} cents reserved this month; "
                 f"cap {args.monthly_cap_cents}; this call reserves "
                 f"{args.cap_cents}")
    return checks


# -- execution ---------------------------------------------------------------

def hangup(control_id, adapter, attempt_id, api_key):
    try:
        adapter.request_stop(attempt_id=attempt_id, reason="place_call stop")
        cmd = adapter.hangup_command(attempt_id)
        if cmd is not None:
            st, response = telnyx_post(cmd["path"], cmd["json"], api_key)
            if st == 200:
                adapter.acknowledge_hangup(attempt_id, response)
            log(f"HANGUP via adapter command: status={st}")
            return st == 200
    except Exception as exc:
        log(f"HANGUP adapter command failed: {type(exc).__name__}")
    log("HANGUP not sent: canonical command unavailable; "
        "operator reconciliation required")
    return False


def _record_for(record_path, control_id):
    if not os.path.exists(record_path):
        return None
    found = None
    with open(record_path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("call_control_id") == control_id:
                found = rec
    return found


def _print_transcript(record, seen):
    turns = (record or {}).get("transcript", [])
    for turn in turns[seen:]:
        log(f"TRANSCRIPT {turn.get('role')}: {turn.get('text')}")
    return len(turns)


def execute(args):
    db_path = args.db
    host = args.host
    api_key = secret_value("TELNYX_API_KEY", args.secrets_dir)
    if not api_key:
        raise Abort("credentials", "Telnyx API key is not provisioned "
                                   "(env or .provider-credentials)")
    pubkey = secret_value("TELNYX_WEBHOOK_PUBLIC_KEY", args.secrets_dir)
    if not pubkey:
        raise Abort("credentials", "Telnyx webhook public key not found")
    token = secret_value("INTERNAL_TOKEN", args.secrets_dir)
    if not token:
        raise Abort("credentials", "internal token not found")

    attempt_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    log(f"ATTEMPT {attempt_id}")
    log(f"JOB {job_id}")

    record_path = str(Path(args.log_dir) / args.log_name)
    reg_body = {
        "attempt_id": attempt_id,
        "job": {"to": args.to, "business": args.business,
                "objective": args.objective, "test_call": True,
                "opening": DISCLOSURE, "max_minutes": args.max_minutes},
        "refs": {"elevenlabs_voice_id": "cgSgspJ2msm6clMCkdW9"},
        "expected_route": [args.connection_id, args.from_number, args.to],
        "log_path": args.log_name,
    }
    st, reg = http_json("POST", "http://127.0.0.1:8001/internal/register-media-job",
                        reg_body, {"X-Internal-Token": token})
    log(f"REGISTER -> {st} {reg}")
    if st != 200 or not reg.get("registered"):
        raise Abort("register", f"media job registration failed: {st} {reg}")
    del token

    adapter = TelnyxAdapter(
        db_path, pubkey,
        AdapterConfig(authorized_destinations=args.allowlist))
    approval = Approval(job_id=job_id, destination=args.to,
                        max_minutes=args.max_minutes,
                        estimated_cost_cents=args.cap_cents,
                        approver_ref="nathan")
    adapter.prepare(job_id=job_id, connection_id=args.connection_id,
                    from_number=args.from_number, to_number=args.to,
                    attempt_id=attempt_id, approval=approval,
                    max_minutes=args.max_minutes)
    claim = adapter.claim_submission(attempt_id)
    log("CLAIM ok: client_state_encoded=True")

    st, resp = telnyx_post(claim["path"], claim["json"], api_key)
    log(f"DIAL -> {st}")
    if st != 200:
        log(f"DIAL REFUSED: {resp}")
        log("NOT retrying (adapter single-claim model). The attempt stays "
            "submit_unknown for operator reconciliation.")
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
    for _ in range(args.answer_timeout):
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
    log(f"ANSWERED={answered} state={state} after {time.time() - dial_t:.1f}s")
    if not answered:
        log(f"NO ANSWER within {args.answer_timeout}s; hanging up")
        hangup(control_id, adapter, attempt_id, api_key)
        return 4
    spec = streaming_start_request(f"wss://{host}/telnyx/media/{attempt_id}")
    st, sresp = telnyx_post(spec["path"].format(call_control_id=control_id),
                            spec["json"], api_key)
    log(f"STREAMING_START -> {st} {str(sresp)[:300]}")
    if st != 200:
        log("STREAMING_START rejected; hanging up")
        hangup(control_id, adapter, attempt_id, api_key)
        return 5

    start = time.time()
    final_state = None
    seen_turns = 0
    while True:
        elapsed = time.time() - start
        try:
            final_state = adapter.get_attempt(attempt_id).get("state")
        except Exception:
            final_state = "?"
        seen_turns = _print_transcript(
            _record_for(record_path, control_id), seen_turns)
        if final_state == "hangup":
            log(f"MONITOR: hangup observed at {elapsed:.1f}s")
            break
        if elapsed / 60.0 * RATE_CENTS_PER_MIN >= args.cap_cents:
            log(f"MONITOR: projected cap of {args.cap_cents} cents reached "
                f"at {elapsed:.1f}s; hanging up")
            hangup(control_id, adapter, attempt_id, api_key)
            break
        if elapsed >= args.max_minutes * 60:
            log(f"MONITOR: {args.max_minutes}-minute max reached; hanging up")
            hangup(control_id, adapter, attempt_id, api_key)
            break
        time.sleep(2)
    # The session record lands a few seconds after the signed hangup,
    # when the media handler finishes teardown. Poll briefly for it.
    record = None
    for _ in range(10):
        record = _record_for(record_path, control_id)
        if record:
            break
        time.sleep(2)
    try:
        final_state = adapter.get_attempt(attempt_id).get("state")
    except Exception:
        pass
    log(f"FINAL_STATE {final_state} DURATION_S {time.time() - dial_t:.1f}")
    if not record:
        log("RECORD MISSING: the call ended but no session record landed "
            f"in {record_path}")
        return 6
    _print_transcript(record, seen_turns)
    log(f"RECORD outcome={record.get('outcome')} "
        f"minutes={record.get('minutes')} "
        f"estimated_cost_cents={record.get('estimated_cost_cents')}")
    return 0


# -- CLI ---------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Place one gated call through the ai-calling stack.")
    parser.add_argument("--to", required=True,
                        help="destination in E.164 form; must be allowlisted "
                             "and, on the Telnyx trial, a verified number")
    parser.add_argument("--objective", default="controlled live qualification",
                        help="conversation objective for the system prompt")
    parser.add_argument("--business", default="Nathan qualification call",
                        help="business/context label for the system prompt")
    parser.add_argument("--max-minutes", type=int, default=10)
    parser.add_argument("--cap-cents", type=int, default=100,
                        help="per-call reservation against the monthly cap")
    parser.add_argument("--monthly-cap-cents", type=int,
                        default=int(os.environ.get(
                            "AI_CALLING_MONTHLY_CAP_CENTS", MONTHLY_CAP_CENTS)))
    parser.add_argument("--host", default=os.environ.get(
        "AI_CALLING_PUBLIC_HOST", ""),
        help="public tunnel host (default: AI_CALLING_PUBLIC_HOST)")
    parser.add_argument("--from-number", default=DEFAULT_FROM)
    parser.add_argument("--connection-id", default=DEFAULT_CONNECTION_ID)
    parser.add_argument("--db", default=os.environ.get(
        "TELNYX_WEBHOOK_DB", DEFAULT_DB))
    parser.add_argument("--log-dir", default=os.environ.get(
        "AI_CALLING_LOG_DIR", DEFAULT_LOG_DIR))
    parser.add_argument("--log-name", default="place-call.jsonl")
    parser.add_argument("--secrets-dir", default=DEFAULT_SECRETS_DIR)
    parser.add_argument("--answer-timeout", type=int, default=int(
        os.environ.get("AI_CALLING_ANSWER_TIMEOUT_SECONDS", "45")))
    parser.add_argument("--auto-restore", action="store_true",
                        help="run tools/restore_stack.sh if the stack is down")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every preflight check and print the "
                             "would-dial summary; never dials")
    args = parser.parse_args(argv)
    args.allowlist = allowlist_from_env()
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        args.to = validate_destination(args.to, args.allowlist)
    except ValueError as exc:
        log(f"ABORT [destination] {exc}")
        return 2
    if not args.host or "/" in args.host:
        log("ABORT [host] set --host or AI_CALLING_PUBLIC_HOST to the "
            "public tunnel host (bare host, no scheme)")
        return 2
    if not 1 <= args.max_minutes <= 60:
        log("ABORT [args] --max-minutes must be 1..60")
        return 2
    if args.cap_cents <= 0:
        log("ABORT [args] --cap-cents must be positive")
        return 2
    try:
        run_preflight(args)
    except Abort as abort:
        log(f"ABORT [{abort.stage}] {abort.message}")
        return 2
    if args.dry_run:
        log("WOULD-DIAL summary (dry run; nothing was dialed):")
        log(f"  to={args.to} from={args.from_number} "
            f"connection={args.connection_id}")
        log(f"  business={args.business!r} objective={args.objective!r}")
        log(f"  opening={DISCLOSURE!r}")
        log(f"  max_minutes={args.max_minutes} cap_cents={args.cap_cents} "
            f"host={args.host}")
        log(f"  approval_present={approval_present()}")
        return 0
    if not approval_present():
        log("ABORT [approval] fresh per-call approval is not present: set "
            "AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL only after the "
            "user's fresh approval for this exact call")
        return 2
    try:
        return execute(args)
    except Abort as abort:
        log(f"ABORT [{abort.stage}] {abort.message}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
