#!/usr/bin/env bash
# Pinggy persistent-subdomain tunnel for the AI calling server.
# Mirrors the tunl tunnel wrapper (tun|pinggy gateways behave best with a
# PTY, so the ssh client runs under `script`, equivalent to ssh -t).
# Auth is the account token as the SSH username, read at start from the
# 0600 file ~/workspace/ai-calling/.pinggy-token (never embedded here).
# Forwards public https://aqlmfmjrsk.a.pinggy.link to 127.0.0.1:8001.
# The token's persistent subdomain is assigned by Pinggy, so -R 0 is
# correct: the account always gets aqlmfmjrsk.a.pinggy.link back.
TOKEN="$(tr -d '[:space:]' < /home/hatch/workspace/ai-calling/.pinggy-token)"
exec script -qefc "ssh -tt -p 443 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o 'ProxyCommand=/home/hatch/.config/proxy-connect.sh pro.pinggy.io 443' -R 0:127.0.0.1:8001 ${TOKEN}@pro.pinggy.io" /dev/null
