#!/usr/bin/env bash
set -euo pipefail

PROJECT="${AI_CALLING_PROJECT:-/home/hatch/workspace/ai-calling}"
PYTHON="${PYTHON:-python3.12}"

test -d "$PROJECT/server"
test -f "$PROJECT/server/requirements.lock"
test -f "$PROJECT/.provider-credentials"
test -f "$PROJECT/.webhook-public-key"
test -f "$PROJECT/.internal-token"
for secret_file in "$PROJECT/.provider-credentials" "$PROJECT/.internal-token"; do
  mode="$(stat -c '%a' "$secret_file")"
  owner="$(stat -c '%U' "$secret_file")"
  test "$mode" = 600 -o "$mode" = 400
  test "$owner" = hatch -o "$owner" = root
done
public_mode="$(stat -c '%a' "$PROJECT/.webhook-public-key")"
test "$public_mode" = 600 -o "$public_mode" = 400 -o "$public_mode" = 644

umask 077
rm -rf "$PROJECT/.deploy-venv.new"
swapped=0
units_snapshotted=0
rollback() {
  rm -rf "$PROJECT/.deploy-venv.new"
  if test "$swapped" = 1 && test -d "$PROJECT/.deploy-venv.previous"; then
    rm -rf "$PROJECT/.deploy-venv"
    mv "$PROJECT/.deploy-venv.previous" "$PROJECT/.deploy-venv"
    sudo systemctl restart ai-calling-live.service || true
  fi
  if test "$units_snapshotted" = 1 && test -f "$PROJECT/.deploy-backup/ai-calling-live.service"; then
    sudo cp "$PROJECT/.deploy-backup/ai-calling-live.service" \
      /etc/systemd/system/ai-calling-live.service
  elif test "$units_snapshotted" = 1; then
    sudo rm -f /etc/systemd/system/ai-calling-live.service
  fi
  if test "$units_snapshotted" = 1 && test -f "$PROJECT/.deploy-backup/ai-calling-proxy-shim.service"; then
    sudo cp "$PROJECT/.deploy-backup/ai-calling-proxy-shim.service" \
      /etc/systemd/system/ai-calling-proxy-shim.service
  elif test "$units_snapshotted" = 1; then
    sudo rm -f /etc/systemd/system/ai-calling-proxy-shim.service
  fi
  if test "$swapped" = 1; then
    sudo systemctl daemon-reload || true
    sudo systemctl restart ai-calling-proxy-shim.service || true
    sudo systemctl restart ai-calling-live.service || true
  fi
}
trap rollback ERR
"$PYTHON" -m venv "$PROJECT/.deploy-venv.new"
"$PROJECT/.deploy-venv.new/bin/python" -m pip install \
  --require-hashes -r "$PROJECT/server/requirements.lock"
"$PROJECT/.deploy-venv.new/bin/python" -m unittest discover \
  -s "$PROJECT/tests" -v

rm -rf "$PROJECT/.deploy-venv.previous"
if test -d "$PROJECT/.deploy-venv"; then
  mv "$PROJECT/.deploy-venv" "$PROJECT/.deploy-venv.previous"
fi
mv "$PROJECT/.deploy-venv.new" "$PROJECT/.deploy-venv"
swapped=1

rm -rf "$PROJECT/.deploy-backup"
mkdir -p "$PROJECT/.deploy-backup"
sudo cp /etc/systemd/system/ai-calling-live.service \
  "$PROJECT/.deploy-backup/ai-calling-live.service" 2>/dev/null || :
sudo cp /etc/systemd/system/ai-calling-proxy-shim.service \
  "$PROJECT/.deploy-backup/ai-calling-proxy-shim.service" 2>/dev/null || :
units_snapshotted=1
sudo install -m 0644 "$PROJECT/systemd/ai-calling-live.service" \
  /etc/systemd/system/ai-calling-live.service
sudo install -m 0644 "$PROJECT/systemd/ai-calling-proxy-shim.service" \
  /etc/systemd/system/ai-calling-proxy-shim.service
sudo systemctl daemon-reload
sudo systemctl restart ai-calling-proxy-shim.service
sudo systemctl restart ai-calling-live.service

# Readiness wait (Oct 29, 2026): uvicorn needs seconds after process start
# to finish importing and bind 8001; curling immediately races it and
# fails a healthy deploy (exit 7). Wait up to 60s for healthz first.
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8001/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

curl --fail --silent http://127.0.0.1:8001/healthz
curl --fail --silent http://127.0.0.1:8001/readyz
rm -rf "$PROJECT/.deploy-venv.previous"
rm -rf "$PROJECT/.deploy-backup"
swapped=0
trap - ERR
echo 'DEPLOY_PASS (no dial performed)'
