#!/usr/bin/env bash
# call.sh — thin wrapper over tools/place_call.py with the candidate
# venv and the production environment files loaded, matching how the
# dialers were run on Oct 30, 2026. Credential values are loaded into
# the environment only; nothing is printed.
#
# Usage:
#   tools/call.sh --to +13218315278 --dry-run
#   AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL tools/call.sh --to +13218315278
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${AI_CALLING_VENV:-$HOME/.venvs/ai-calling-candidate-b849e45}"
SECRETS_DIR="${AI_CALLING_SECRETS_DIR:-/home/hatch/workspace/ai-calling}"
set -a
if [ -f "$HOME/.config/egress-proxy.env" ]; then
  . "$HOME/.config/egress-proxy.env"
fi
if [ -f "$SECRETS_DIR/.provider-credentials" ]; then
  . "$SECRETS_DIR/.provider-credentials"
fi
set +a
export TELNYX_WEBHOOK_DB="${TELNYX_WEBHOOK_DB:-/home/hatch/workspace/ai-calling/logs/webhook.db}"
export AI_CALLING_LOG_DIR="${AI_CALLING_LOG_DIR:-/home/hatch/workspace/ai-calling/logs}"
export AI_CALLING_PUBLIC_HOST="${AI_CALLING_PUBLIC_HOST:-aqlmfmjrsk.a.pinggy.link}"
exec "$VENV/bin/python" "$ROOT/tools/place_call.py" "$@"
