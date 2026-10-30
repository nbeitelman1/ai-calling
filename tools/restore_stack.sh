#!/usr/bin/env bash
# restore_stack.sh — idempotently (re)install and verify the
# ai-calling stack from THIS worktree.
#
# Why this exists: the VM's /etc/systemd/system does not survive
# machine replacements (three on Oct 30, 2026 alone), while the
# worktree, the shared database and the credentials do. After any
# replacement — or whenever `systemctl is-active ai-calling-live`
# fails — run this script and the stack is back: candidate base unit
# + the corrected drop-in (systemd/90-audit-candidate.conf, the
# canonical source) + the Pinggy tunnel unit, daemon-reload,
# enable + start, then verification of readiness and of the live
# process environment.
#
# Safe to run when the stack is already healthy: units are
# reinstalled byte-identically, services are only restarted when a
# unit file actually changed, and the verification battery always
# runs. The service (re)start re-runs prestart (real provider
# preflight + phrase prewarm), which also refreshes the 1-hour
# preflight receipt.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="/etc/systemd/system"
DROPIN_DIR="$UNIT_DIR/ai-calling-live.service.d"
PUBLIC_HOST="${AI_CALLING_PUBLIC_HOST:-aqlmfmjrsk.a.pinggy.link}"
EXPECTED_DB="/home/hatch/workspace/ai-calling/logs/webhook.db"

install_if_changed() {
  # $1 = source, $2 = destination; echoes "changed" or "same".
  if [ -f "$2" ] && cmp -s "$1" "$2"; then
    echo "same"
  else
    install -m 644 "$1" "$2"
    echo "changed"
  fi
}

changed=0
[ "$(install_if_changed "$ROOT/systemd/ai-calling-live.service" \
    "$UNIT_DIR/ai-calling-live.service")" = "changed" ] && changed=1
mkdir -p "$DROPIN_DIR"
[ "$(install_if_changed "$ROOT/systemd/90-audit-candidate.conf" \
    "$DROPIN_DIR/90-audit-candidate.conf")" = "changed" ] && changed=1
[ "$(install_if_changed "$ROOT/systemd/ai-calling-pinggy.service" \
    "$UNIT_DIR/ai-calling-pinggy.service")" = "changed" ] && changed=1

if [ "$changed" = "1" ]; then
  echo "units changed: daemon-reload"
  systemctl daemon-reload
else
  echo "units unchanged"
fi

systemctl enable ai-calling-pinggy ai-calling-live >/dev/null 2>&1 || true
if [ "$changed" = "1" ]; then
  systemctl restart ai-calling-pinggy || systemctl start ai-calling-pinggy
  systemctl restart ai-calling-live || systemctl start ai-calling-live
else
  systemctl start ai-calling-pinggy ai-calling-live
fi

# --- verification battery -------------------------------------------------
fail=0
for unit in ai-calling-pinggy ai-calling-live; do
  state="$(systemctl is-active "$unit" || true)"
  echo "unit $unit: $state"
  [ "$state" = "active" ] || fail=1
done

ready=""
for _ in $(seq 1 30); do
  if body="$(curl -sS -m 5 http://127.0.0.1:8001/readyz 2>/dev/null)" \
      && printf '%s' "$body" | grep -q '"ready":true'; then
    ready="local"
    break
  fi
  sleep 2
done
if [ -n "$ready" ]; then
  echo "readyz local: $body"
else
  echo "readyz local: NOT READY"
  fail=1
fi

public=""
for _ in $(seq 1 15); do
  if body="$(curl -sS -m 10 "https://$PUBLIC_HOST/readyz" 2>/dev/null)" \
      && printf '%s' "$body" | grep -q '"ready":true'; then
    public="ok"
    break
  fi
  sleep 2
done
if [ -n "$public" ]; then
  echo "readyz public ($PUBLIC_HOST): $body"
else
  echo "readyz public ($PUBLIC_HOST): NOT READY"
  fail=1
fi

PID="$(systemctl show ai-calling-live -p MainPID --value || echo 0)"
if [ "$PID" != "0" ] && [ -r "/proc/$PID/environ" ]; then
  ENV_DUMP="$(tr '\0' '\n' < "/proc/$PID/environ")"
  check_env() {
    if printf '%s\n' "$ENV_DUMP" | grep -qx "$1=$2"; then
      echo "env $1=$2: ok"
    else
      echo "env $1: MISMATCH (expected $2)"
      fail=1
    fi
  }
  check_env TELNYX_WEBHOOK_DB "$EXPECTED_DB"
  check_env GEMINI_FAST_MODELS "gemini-3.1-flash-lite,gemini-3.5-flash-lite"
  check_env NO_PROXY "localhost,127.0.0.1"
  check_env no_proxy "localhost,127.0.0.1"
  check_env SSL_CERT_FILE "/etc/ssl/certs/ca-certificates.crt"
  check_env NLTK_DATA "/home/hatch/ai-calling-candidate/nltk_data"
  check_env AI_CALLING_ALLOW_CLOSE_HEADER_WORKAROUND "1"
else
  echo "env check: no live MainPID"
  fail=1
fi

if [ "$fail" = "0" ]; then
  echo "RESTORE OK: stack healthy (pid $PID)"
else
  echo "RESTORE INCOMPLETE: see failures above" >&2
  exit 1
fi
